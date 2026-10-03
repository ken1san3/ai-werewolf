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

from .disclosure import disclosure_reason
from .prompts import japanese_message, messages, self_reference, strip_introduction
from .state import PlayerState
from .strategy import strategy_for, under_pressure
from .timing import fresh, speech_pause


def request(kind, game_id, payload):
    return json.dumps({"type": kind, "protocol_version": "1.1", "game_id": game_id,
                       "event_id": str(uuid4()), "timestamp": 0, "payload": payload})


class Agent:
    def __init__(self, player_id, token, uri, game_id, roles, role_counts, llm, repetition,
                 observe, *, seed, timing_scale=1):
        self.state = PlayerState(player_id)
        self.token, self.uri, self.game_id = token, uri, game_id
        self.roles, self.role_counts = roles, role_counts
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

    async def generate(self, question, purpose, schema=None, max_tokens=160):
        timeout = self.state.seconds_left() - 0.25
        if timeout <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(self.llm.complete(
            lambda: messages(self.state, self.roles, self.role_counts, question),
            player_id=self.state.player_id, purpose=purpose,
            seed=self.rng.randrange(1, 10**9), schema=schema, max_tokens=max_tokens,
        ), timeout)

    async def generate_speech(self, question, purpose, max_tokens):
        self.speech_generations += 1
        key = self.state.phase_key
        record = None
        try:
            schema = {"type": "object", "additionalProperties": False, "required": ["facts", "aim", "reason", "suspicion", "reveal_role"],
                      "properties": {"facts": {"type": "array", "maxItems": 2,
                                               "items": {"type": "string", "maxLength": 32}},
                                     "aim": {"type": "string", "maxLength": 32},
                                     "reason": {"type": "string", "maxLength": 48},
                                     "suspicion": {"type": "string", "enum": ["low", "medium", "high"]},
                                     "reveal_role": {"type": "boolean"}}}
            plan_text = await self.generate(
                question + ' 発言前の判断だけをJSONで返してください。factsは確認した事実を最大2件、aimは狙い、reasonは行動を選ぶ短い理由です。'
                'facts/aim/reasonは日本語で10〜20字にしてください。suspicionは自分への疑いの強さ(low/medium/high)、'
                'reveal_roleは今、本当の役職を明かすか(true/false)です。本人の戦略と公開の処刑圧力を考慮してください。'
                '自分を他人と取り違えず、他人の発言は公称と区別しサーバの事実を優先してください。このJSONは公開しません。',
                purpose + "_decision", schema, 256)
            record = {"player_id": self.state.player_id, "day": key[0], "phase": key[1],
                      "at_monotonic": time.monotonic(), "purpose": purpose, "status": "planned"}
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
            return await self.generate(question + "\n" + instruction + "本文だけを書き、JSONを会話に出さないでください。", purpose,
                                       max_tokens=max_tokens)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.speech_discards["phase_expired"] += 1
            if record is not None:
                record.update(status="discarded", discard_reason="phase_expired")
            raise

    def speech_rejection(self, text, key, formal_claim=None):
        if not fresh(self.state, key):
            return "phase_expired"
        if not japanese_message(text):
            return "japanese_check"
        if self_reference(text, self.state.player_id):
            return "self_id_confusion"
        disclosure = disclosure_reason(self.state, text, self.roles, formal_claim=formal_claim)
        if disclosure:
            return disclosure
        recent = [c["message"] for c in self.state.chats if c["channel"] == "public"]
        return self.repetition.rejection_reason(self.state.player_id, text, recent)

    async def speak(self, question, purpose, key, kind, payload, field, max_tokens):
        original_question = question
        for _ in range(3):
            if not fresh(self.state, key):
                return False
            generated = await self.generate_speech(question, purpose, max_tokens)
            if generated is None:
                if not fresh(self.state, key):
                    return False
                continue
            text = strip_introduction(generated, self.state.player_id)[:200 if field == "comment" else 400]
            record = self.decisions[-1]
            record["speech"] = text
            reason = self.speech_rejection(text, key, payload.get("claimed_role_id"))
            if reason:
                self.speech_discards[reason] += 1
                record.update(status="discarded", discard_reason=reason)
                if reason == "phase_expired":
                    self.stale_suppressed += 1
                    return False
                explanation = {"japanese_check": "日本語以外または空の本文", "own_previous_sentence": "自分の直前の文の再使用",
                               "third_sentence": "同じ文の3回目", "similarity": "直近の発言との過度な類似",
                               "self_id_confusion": "自分のIDを他人として書いていた。自分は『私』と書いて",
                               "unjustified_self_disclosure": "正体が分かる発言だった。隠して書き直して"}.get(reason, reason)
                question = original_question + f" 前の生成は送信しませんでした（理由: {explanation}）。その内容を避け、議論に使える別の発言を書いてください。"
                continue
            self.repetition.reserve(self.state.player_id, text)
            if await self.send(kind, {**payload, field: text}, key):
                record["status"] = "sent"
                return True
            self.speech_discards["phase_expired"] += 1
            record.update(status="discarded", discard_reason="phase_expired")
            return False
        return False

    async def choose(self, question, candidates, purpose):
        schema = {"type": "object", "additionalProperties": False, "required": ["target"],
                  "properties": {"target": {"type": "string", "enum": candidates}}}
        text = await self.generate(question + ' 候補のIDを選び、{"target":"選んだID"}のJSONだけを返してください。', purpose, schema, 40)
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
                target = await self.choose(question, candidates, "vote" if vote else "ability")
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
            candidates = ["none", *co["claimed_role_ids"]]
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
                            "chat.send", {"channel_id": "public"}, "message", 320):
            self.spoke[spoke_key] = spoken + 1
