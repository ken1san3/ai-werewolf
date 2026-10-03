"""WebSocket player; never receives the orchestrator's game state or role table."""
import asyncio
from collections import Counter
from contextlib import suppress
import json
from random import Random
import re
import time
from uuid import uuid4

from websockets.asyncio.client import connect
from server.network.protocol import PROTOCOL_VERSION

from .checks import contains_body
from .disclosure import disclosure_reason
from .grounding import own_result_conflict
from .prompts import japanese_message, messages, own_result_summary, self_reference, strip_introduction
from .state import PlayerState
from .quality import quality_reason
from .repetition import normalize
from .strategy import STRATEGIES, strategy_for, under_pressure
from .timing import fresh, speech_pause


def request(kind, game_id, payload):
    return json.dumps({"type": kind, "protocol_version": PROTOCOL_VERSION, "game_id": game_id,
                       "event_id": str(uuid4()), "timestamp": 0, "payload": payload})


class Agent:
    def __init__(self, player_id, token, uri, game_id, roles, role_counts, llm, repetition,
                 observe, *, seed, timing_scale=1, rules=None):
        self.state = PlayerState(player_id)
        self.token, self.uri, self.game_id = token, uri, game_id
        self.roles, self.role_counts, self.rules = roles, role_counts, rules
        self.llm, self.repetition, self.observe = llm, repetition, observe
        self.rng, self.timing_scale = Random(seed), timing_scale
        self.mentioned, self.changed = asyncio.Event(), asyncio.Event()
        self.acted, self.co_decided, self.spoke = set(), set(), {}
        self.stale_suppressed = 0
        self.speech_generations = 0
        self.speech_discards = Counter()
        self.decisions = []
        self.pending_actions = {}
        self.defensive_co_days = set()
        self.private_spoke = {}

    async def run(self):
        async with connect(self.uri, max_size=2**22) as self.ws:
            await self.ws.send(request("session.join", self.game_id, {"entry_token": self.token}))
            await self.ws.send(request("session.ready", self.game_id, {}))
            receiver = asyncio.create_task(self.receive())
            behavior = asyncio.create_task(self.behave())
            try:
                done, _ = await asyncio.wait((receiver, behavior), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if not self.state.done:
                    raise RuntimeError("agent stopped before server game end")
            finally:
                for task in (receiver, behavior):
                    task.cancel()
                await asyncio.gather(receiver, behavior, return_exceptions=True)

    async def receive(self):
        async for raw in self.ws:
            message = json.loads(raw)
            self.observe(self.state.player_id, message)
            self.state.receive(message)
            if message['type'] in {'action.accepted', 'action.rejected'}:
                selected = self.pending_actions.pop(message['payload'].get('request_event_id'), None)
                if selected and message['type'] == 'action.accepted':
                    self.state.own_actions.append(selected)
            if message["type"] == "chat.message":
                data = message["payload"]["message"]
                if data["player_id"] != self.state.player_id and re.search(
                    rf"\b{re.escape(self.state.player_id)}\b", data["message"], re.I
                ):
                    self.mentioned.set()
            self.changed.set()
            if self.state.done:
                return

    async def generate(self, question, purpose, schema=None, max_tokens=160, *, channel="public"):
        timeout = self.state.seconds_left() - 0.25
        if timeout <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(self.llm.complete(
            lambda: messages(self.state, self.roles, self.role_counts, question + "\n" + self.disclosure_cue(channel), channel, rules=self.rules),
            player_id=self.state.player_id, purpose=purpose,
            seed=self.rng.randrange(1, 10**9), schema=schema, max_tokens=max_tokens,
        ), timeout)

    def disclosure_cue(self, channel):
        if channel != 'public':
            return STRATEGIES['disclosure_cues']['private']
        if strategy_for(self.roles[self.state.role_id])['disclosure'] == 'open':
            return STRATEGIES['disclosure_cues']['open']
        can_reveal = disclosure_reason(self.state, '', self.roles, formal_claim=self.state.role_id, role_counts=self.role_counts) is None
        return STRATEGIES['disclosure_cues']['allowed' if can_reveal else 'hide']

    async def generate_speech(self, question, purpose, max_tokens, *, channel='public'):
        self.speech_generations += 1
        key = self.state.phase_key
        record = None
        try:
            schema = {"type": "object", "additionalProperties": False, "required": ["facts", "aim", "reason", "suspicion", "reveal_role"],
                      "properties": {"facts": {"type": "array", "maxItems": 2,
                                               "items": {"type": "string", "maxLength": 24}},
                                     "aim": {"type": "string", "maxLength": 24},
                                     "reason": {"type": "string", "maxLength": 32},
                                     "suspicion": {"type": "string", "enum": ["low", "medium", "high"]},
                                     "reveal_role": {"type": "boolean"}}}
            plan_text = await self.generate(
                question + ' 発言前の判断だけをJSONで返してください。factsは確認した事実を最大2件、aimは狙い、reasonは行動を選ぶ短い理由です。'
                'facts/aim/reasonは日本語で10〜20字にしてください。suspicionは自分への疑いの強さ(low/medium/high)、'
                'reveal_roleは今、本当の役職を明かすか(true/false)です。本人の戦略と公開の処刑圧力を考慮してください。'
                '自分を他人と取り違えず、他人の発言は公称と区別しサーバの事実を優先してください。このJSONは公開しません。',
                purpose + "_decision", schema, 192, channel=channel)
            record = {"player_id": self.state.player_id, "day": key[0], "phase": key[1],
                      "at_monotonic": time.monotonic(), "purpose": purpose, "channel": channel, "status": "planned"}
            self.decisions.append(record)
            try:
                plan = json.loads(plan_text)
                if not isinstance(plan, dict) or set(plan) != {"facts", "aim", "reason", "suspicion", "reveal_role"} or not isinstance(plan["facts"], list) or not all(isinstance(f, str) for f in plan["facts"]) or not all(isinstance(plan[k], str) for k in ("aim", "reason")) or plan['suspicion'] not in {'low', 'medium', 'high'} or not isinstance(plan['reveal_role'], bool):
                    raise ValueError("invalid decision fields")
            except (ValueError, TypeError):
                record.update(status="discarded", discard_reason="invalid_decision_json", raw=plan_text)
                self.speech_discards["invalid_decision_json"] += 1
                return None
            record["decision"] = plan
            if not fresh(self.state, key):
                record.update(status="discarded", discard_reason="phase_expired")
                self.speech_discards["phase_expired"] += 1
                self.stale_suppressed += 1
                return None
            record["body_context"] = "own_private"
            instruction = f"発言前の判断記録: {json.dumps(plan, ensure_ascii=False)}。"
            confirmed = (f"\n判断記録よりサーバの事実を優先してください。本人の本当の役職は{self.roles[self.state.role_id].name}で変わりません。"
                         f"今の生存者は{', '.join(sorted(self.state.alive))}、死者は{', '.join(sorted(set(self.state.players) - self.state.alive)) or 'なし'}です。本文の私は本人で、死者の発言の私ではありません。"
                         f"本人の受信済み結果は{own_result_summary(self.state)}。この一覧にない自分の判定を作らないでください。"
                         "本当の役職と公開で名乗る役職は別です。偽COと偽結果は使ってよく、正体を明かすかは本人の戦略で決めてください。")
            return await self.generate(question + "\n" + instruction + confirmed +
                                       "本文は根拠1つを添えた1〜2文、80字程度で終えてください。本文だけを書き、JSONを会話に出さないでください。", purpose,
                                       max_tokens=min(max_tokens, 128), channel=channel)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            reason = 'private_chat_budget' if channel != 'public' and fresh(self.state, key) else 'phase_expired'
            self.speech_discards[reason] += 1
            if record is not None:
                record.update(status="discarded", discard_reason=reason)
            raise

    def speech_rejection(self, text, key, formal_claim=None, *, channel="public"):
        if not fresh(self.state, key):
            return "phase_expired"
        if not japanese_message(text):
            return "japanese_check"
        if self_reference(text, self.state.player_id):
            return "self_id_confusion"
        if channel == 'public':
            if any(c['channel'] != 'public' and contains_body(normalize(text), normalize(c['message'])) for c in self.state.chats):
                return 'private_body_copy'
            quality = quality_reason(self.state, text, self.roles)
            if quality:
                return quality
            disclosure = disclosure_reason(self.state, text, self.roles, formal_claim=formal_claim, role_counts=self.role_counts)
            if disclosure:
                return disclosure
            if own_result_conflict(self.state, text, self.roles, self.rules, formal_claim):
                return 'own_result_conflict'
        else:
            return None
        recent = [c["message"] for c in self.state.chats if c["channel"] == 'public']
        return self.repetition.rejection_reason(self.state.player_id, text, recent)

    async def speak(self, question, purpose, key, kind, payload, field, max_tokens):
        original_question = question
        channel = payload.get("channel_id", "public")
        for _ in range(3):
            if not fresh(self.state, key):
                return False
            generated = await self.generate_speech(question, purpose, max_tokens, channel=channel)
            if generated is None:
                if not fresh(self.state, key):
                    return False
                continue
            text = strip_introduction(generated, self.state.player_id)[:200 if field == "comment" else 400]
            record = self.decisions[-1]
            record["speech"] = text
            reason = self.speech_rejection(text, key, payload.get("claimed_role_id"), channel=channel)
            if reason:
                self.speech_discards[reason] += 1
                record.update(status="discarded", discard_reason=reason)
                if reason == "phase_expired":
                    self.stale_suppressed += 1
                    return False
                explanation = {"japanese_check": "日本語以外または空の本文", "own_previous_sentence": "自分の直前の文の再使用",
                               "third_sentence": "同じ文の3回目", "similarity": "直近の発言との過度な類似",
                               "self_id_confusion": "自分のIDを他人として書いていた。自分は『私』と書いて",
                               "own_result_conflict": "本人の受信済み結果または行動時期と矛盾した。結果一覧を確認し、未受信の自分の判定を作らず書き直して",
                               "meta_refusal": "人狼ゲームの参加者として、拒否やAIの説明ではなく議論に返答して",
                               "self_fact_confusion": "本人の役職と生存状態は受信済みで確定しています。公表するかとは別です。他人の発言の私を自分と取り違えず書き直して",
                               "dead_player_address": "死者は答えたり投票されたりできません。現在の生存者への発言を書いて",
                               "empty_agreement": "同意だけでした。自分の根拠か相手への具体的な答えを加えて",
                               "private_body_copy": "秘密の本文をそのまま使っていた。公開の議論から別の文を書いて",
                               "unjustified_self_disclosure": "正体が分かる発言だった。隠して書き直して"}.get(reason, reason)
                question = original_question + f" 前の生成は送信しませんでした（理由: {explanation}）。その内容を避け、議論に使える別の発言を書いてください。"
                continue
            if channel == "public":
                self.repetition.reserve(self.state.player_id, text)
            if await self.send(kind, {**payload, field: text}, key):
                record["status"] = "sent"
                return True
            self.speech_discards["phase_expired"] += 1
            record.update(status="discarded", discard_reason="phase_expired")
            return False
        return False

    async def choose(self, question, candidates, purpose, *, channel="public"):
        schema = {"type": "object", "additionalProperties": False, "required": ["target"],
                  "properties": {"target": {"type": "string", "enum": candidates}}}
        text = await self.generate(question + ' 候補のIDを選び、{"target":"選んだID"}のJSONだけを返してください。', purpose, schema, 40, channel=channel)
        try:
            choice = json.loads(text)["target"]
        except (ValueError, KeyError, TypeError):
            choice = None
        return choice if choice in candidates else self.rng.choice(candidates)

    async def send(self, kind, payload, key):
        if not fresh(self.state, key):
            self.stale_suppressed += 1
            return False
        encoded = request(kind, self.game_id, payload)
        if kind == 'ability.use':
            self.pending_actions[json.loads(encoded)['event_id']] = {'day': key[0], 'phase': key[1], **payload}
        await self.ws.send(encoded)
        return True

    async def behave(self):
        while not self.state.done:
            try:
                await self.step()
            except asyncio.TimeoutError:
                self.stale_suppressed += 1
            self.changed.clear()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=0.2)

    async def step(self):
        state = self.state
        key = state.phase_key
        if state.role_id is None or not fresh(state, key):
            return
        private_chat = next((a for a in state.actions if a['type'] == 'chat' and a['channel'] != 'public'), None)
        buddies = set(state.teammates) & state.alive
        if private_chat and buddies:
            channel = private_chat['channel']
            if key not in self.private_spoke:
                self.private_spoke[key] = None
                budget = min(14, state.seconds_left() - 8 * min(1, self.timing_scale))
                sent = False
                if budget > .25:
                    try:
                        available = state.action('ability')
                        targets = available['valid_targets'] if available else []
                        question = ('仲間へ、公開の発言を踏まえた襲撃・投票の方針と対象の案を日本語で短く伝えてください。'
                                    f'今の能力の対象候補は{targets}です。候補がなければ初夜は襲撃せず翌日以降を相談してください。'
                                    '通知された仲間は味方です。死者へ能力を使えません。')
                        sent = await asyncio.wait_for(self.speak(question,
                                                               'private_chat', key, 'chat.send', {'channel_id': channel}, 'message', 160), budget)
                    except asyncio.TimeoutError:
                        pass
                if sent:
                    self.private_spoke[key] = time.monotonic()
                    return
            heard = any(c['channel'] == channel and c['day'] == state.day and c['player_id'] in buddies for c in state.chats)
            sent_at = self.private_spoke[key]
            if sent_at is not None and not heard and time.monotonic() - sent_at < 4 and state.seconds_left() > 8:
                return
        vote = state.action("vote")
        ability = state.action("ability")
        action = vote or ability
        if action:
            action_key = (*key, action.get("ability_id", "vote"))
            if action_key in self.acted or action.get("uses_remaining") == 0:
                return
            self.acted.add(action_key)
            candidates = list(action["valid_targets"])
            count = action.get("target_count", 1)
            if len(candidates) < count:
                return
            chosen = []
            for _ in range(count):
                question = "今日の投票で誰を処刑しますか？" if vote else (
                    f"使用可能な能力（ID: {action['ability_id']}）の対象を選んでください。{action.get('description', '')}"
                )
                if private_chat and not vote:
                    question += ' 秘密相談の仲間の案を読んで生存対象へ合わせてください。案が割れたら生存する仲間のID順で最初の提案を優先し、通知された仲間を敵として扱わないでください。'
                target = await self.choose(question, candidates, "vote" if vote else "ability", channel=private_chat["channel"] if private_chat and not vote else "public")
                chosen.append(target)
                candidates.remove(target)
            payload = {"target_player_id": chosen[0]} if vote else {
                "ability_id": action["ability_id"], "target_player_ids": chosen,
            }
            await self.send("vote.cast" if vote else "ability.use", payload, key)
            return
        chat = state.action("chat")
        if state.phase != "day" or not chat or chat["channel"] != "public":
            return
        spoke_key = (state.day, "public")
        spoken = self.spoke.get(spoke_key, 0)
        co = state.action("co_declare")
        has_co = any(f['type'] == 'CO_DECLARED' and f['player_id'] == state.player_id and
                     f.get('claimed_role_id') == state.role_id for f in state.facts)
        if co and strategy_for(self.roles[state.role_id])['disclosure'] == 'pressure' and under_pressure(state) and not has_co and state.day not in self.defensive_co_days:
            if state.role_id in co['claimed_role_ids']:
                if await self.speak(f"自分への処刑・投票を避けるため、{self.roles[state.role_id].name}COして、受理済みの能力選択があればその対象を示してください。まだ選んでいなければそう伝え、自分を『私』と呼んでください。", 'defensive_co', key,
                                    'co.declare', {'claimed_role_id': state.role_id}, 'comment', 160):
                    self.defensive_co_days.add(state.day)
                    self.co_decided.add(state.day)
                    self.spoke[spoke_key] = spoken + 1
                return
        if spoken >= 5:
            return
        if co and state.day not in self.co_decided and spoken:
            self.co_decided.add(state.day)
            candidates = ["none", *(r for r in co["claimed_role_ids"] if disclosure_reason(state, "", self.roles, formal_claim=r, role_counts=self.role_counts) is None)]
            choice = await self.choose("役職を正式にCOするなら役職IDを、今はCOしないならnoneを選んでください。", candidates, "co")
            if choice != "none" and fresh(state, key):
                if await self.speak(f"{self.roles[choice].name}を正式にCOする短いコメントを日本語で書いてください。", "co_comment", key,
                                    "co.declare", {"claimed_role_id": choice}, "comment", 160):
                    self.spoke[spoke_key] = spoken + 1
            return
        await speech_pause(self.mentioned, self.rng, not spoken, self.timing_scale)
        if not fresh(state, key):
            return
        if await self.speak("次の公開チャットの発言を日本語で書いてください。", "chat", key,
                            "chat.send", {"channel_id": "public"}, "message", 160):
            self.spoke[spoke_key] = spoken + 1
