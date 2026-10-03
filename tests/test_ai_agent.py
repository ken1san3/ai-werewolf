from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from dataclasses import replace
from server.aiwolf_core import EventVisibility, GameEvent

from ai_agent.agent import Agent
from ai_agent.play import run_game
from ai_agent.prompts import japanese_message, messages, recent_json, self_reference, strip_introduction
from ai_agent.repetition import RepetitionFilter
from ai_agent.state import PlayerState
from tests.test_network_sessions import make_game


ROOT = Path(__file__).resolve().parents[1]
PLAN = json.dumps({"facts": [], "aim": "投票理由を確認する", "reason": "根拠のある投票をしたい", "suspicion": "low", "reveal_role": False}, ensure_ascii=False)


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.count = 0

    async def complete(self, prompt, *, player_id, purpose, seed, schema=None, max_tokens=160):
        self.calls.append({"player_id": player_id, "purpose": purpose, "messages": prompt()})
        if schema and "target" in schema["properties"]:
            return json.dumps({"target": schema["properties"]["target"]["enum"][0]})
        if schema:
            return PLAN
        self.count += 1
        return f"私からの質問です。根拠{self.count}について、どの主張を裏付けていますか？"


class AgentStateTests(unittest.TestCase):
    def test_unknown_private_notice_stays_private_in_live_delivery_and_state_sync(self):
        game = make_game()
        game.event_bus.publish(GameEvent('CUSTOM_PRIVATE_NOTICE', EventVisibility.PRIVATE, {'text': 'ONLY-SELF'}, 'player-0'))
        game.event_bus.publish(GameEvent('CUSTOM_PUBLIC_NOTICE', EventVisibility.PUBLIC, {'text': 'PUBLIC'}))
        state = PlayerState('player-0')
        state.receive({'type': 'game.state_sync', 'payload': game.get_state_sync('player-0')})
        self.assertIn('ONLY-SELF', json.dumps(state.private))
        self.assertNotIn('ONLY-SELF', json.dumps(state.facts))
        self.assertIn('PUBLIC', json.dumps(state.facts))
        peer = PlayerState('player-1')
        peer.receive({'type': 'game.state_sync', 'payload': game.get_state_sync('player-1')})
        self.assertNotIn('ONLY-SELF', json.dumps(peer.private))
        state.receive({'type': 'game.event', 'payload': {'visibility': 'private', 'event_type': 'ANOTHER_UNKNOWN_NOTICE', 'event_payload': {'text': 'LIVE-ONLY-SELF'}}})
        state.receive({'type': 'game.event', 'payload': {'event_type': 'MISSING_VISIBILITY', 'event_payload': {'text': 'FAIL-CLOSED'}}})
        self.assertNotIn('LIVE-ONLY-SELF', json.dumps(state.facts))
        self.assertNotIn('FAIL-CLOSED', json.dumps(state.facts))
        prompt = messages(state, game.content.roles, {}, '発言してください。')
        self.assertIn('LIVE-ONLY-SELF', prompt[0]['content'])
        self.assertNotIn('LIVE-ONLY-SELF', prompt[1]['content'])

    def test_initial_rule_text_comes_from_preset_and_results_include_received_phase(self):
        game = make_game()
        state = PlayerState('player-0', role_id='seer', players=['player-0', 'player-1'], alive={'player-0', 'player-1'},
                            private=[{'type': 'INSPECT_RESULT', 'target_player_id': 'player-1', 'result': 'not_wolf', 'received_day': 0, 'received_phase': 'night0'}])
        for setting, expected in [('none', '占い結果を受け取りません'), ('free', '一人を選んで占います'), ('random_white', '初夜に人狼という結果は出ません')]:
            rules = replace(game.rules, first_night_seer=setting)
            text = messages(state, game.content.roles, {'seer': 1}, '発言してください。', rules=rules)[0]['content']
            self.assertIn(expected, text)
            self.assertIn('受信日', text)
            self.assertIn('night0', text)
            self.assertIn('狂人の可能性は残る', text)

    def test_snapshot_restores_only_received_teammates_and_private_results(self):
        game = make_game()
        wolves = [p.player_id for p in game.players.values() if p.role.id == "werewolf"]
        state = PlayerState(wolves[0])
        state.receive({"type": "game.state_sync", "timestamp": 0,
                       "payload": game.get_state_sync(wolves[0])})
        self.assertEqual(state.teammates, [wolves[1]])
        self.assertEqual(state.role_id, "werewolf")
        seer = next(p.player_id for p in game.players.values() if p.role.id == "seer")
        with self.assertRaises(ValueError):
            state.receive({"type": "game.state_sync", "payload": game.get_state_sync(seer)})

    def test_public_prompt_never_contains_hidden_chat_or_authentication(self):
        game = make_game()
        wolf = next(p.player_id for p in game.players.values() if p.role.id == "werewolf")
        state = PlayerState(wolf)
        state.receive({"type": "game.state_sync", "payload": game.get_state_sync(wolf)})
        state.receive({"type": "session.joined", "payload": {"connection_token": "AUTH-SECRET"}})
        state.receive({"type": "chat.message", "payload": {"channel": "wolf", "message": {
            "player_id": wolf, "message": "HIDDEN-CHAT-SENTINEL", "display_name": wolf,
        }}})
        prompt = json.dumps(messages(state, game.content.roles, {}, "Speak publicly"))
        self.assertNotIn("HIDDEN-CHAT-SENTINEL", prompt)
        self.assertNotIn("AUTH-SECRET", prompt)
        self.assertIn(state.teammates[0], prompt)

    def test_body_prompt_preserves_own_private_role_results_and_teammate_information(self):
        game = make_game()
        state = PlayerState("player-0", role_id="werewolf", players=[f"player-{i}" for i in range(9)],
                            teammates=["player-1"], private=[{"type": "NOTE", "text": "PRIVATE-FACT-SENTINEL"}])
        prompt = json.dumps(messages(state, game.content.roles, {}, "発言してください。"), ensure_ascii=False)
        self.assertIn("PRIVATE-FACT-SENTINEL", prompt)
        self.assertIn("あなたの非公開の役職", prompt)
        self.assertIn("あなたが知っている仲間", prompt)
        self.assertIn(game.content.roles["werewolf"].description, prompt)
        self.assertIn('目立たない', prompt)

    def test_self_id_in_history_is_shown_as_you_and_third_person_is_detected(self):
        game = make_game()
        state = PlayerState('player-4', role_id='werewolf', players=['player-4', 'player-8'], alive={'player-4', 'player-8'}, day=1,
                            facts=[{'type': 'CO_DECLARED', 'player_id': 'player-4', 'claimed_role_id': 'seer'}],
                            chats=[{'day': 1, 'channel': 'public', 'player_id': 'player-8', 'message': 'player-4さんを吊るべきです。'}])
        prompt = messages(state, game.content.roles, {}, '発言してください。')[1]['content']
        self.assertIn('あなた（player-4）さんを吊る', prompt)
        self.assertIn('自分への処刑・投票の呼びかけ: あり', prompt)
        self.assertEqual(state.chats[0]['message'], 'player-4さんを吊るべきです。')
        self.assertTrue(self_reference('player-4を即刻処刑して最後の人狼を排除しましょう。', 'player-4'))
        self.assertTrue(self_reference('player-4さん、何を知っていますか？', 'player-4'))
        self.assertFalse(self_reference('私（player-4）は狩人です。', 'player-4'))
        self.assertFalse(self_reference('player-8が「player-4は狼」と言いましたが反論します。', 'player-4'))
        self.assertFalse(self_reference('player-40さんを疑います。', 'player-4'))

    def test_repetition_filter_blocks_immediate_and_third_sentences(self):
        repetition = RepetitionFilter()
        self.assertTrue(repetition.allows("p1", "One question?", []))
        repetition.reserve("p1", "One question?")
        self.assertFalse(repetition.allows("p1", "One question?", []))
        self.assertTrue(repetition.allows("p2", "One question?", []))
        repetition.reserve("p2", "One question?")
        self.assertFalse(repetition.allows("p3", "One question?", []))
        self.assertFalse(RepetitionFilter().allows("p1", "Why now? Why now? Why now?", []))

    def test_prompt_uses_japanese_instructions_and_content_role_names(self):
        game = make_game()
        state = PlayerState("player-0")
        state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        prompt = messages(state, game.content.roles, {"seer": 1}, "日本語で発言してください。")
        system = prompt[0]["content"]
        self.assertIn("必ず日本語", system)
        self.assertIn('"占い師": 1', system)
        self.assertIn(game.content.roles[state.role_id].name, system)
        self.assertNotIn("English message", system)
        self.assertTrue(all(japanese_message(role.description) for role in game.content.roles.values()))

    def test_english_conversation_is_not_accepted_as_japanese_speech(self):
        self.assertTrue(japanese_message("player-2さん、占い師COの根拠を教えてください。"))
        self.assertTrue(japanese_message("占い師CO。player-2白。"))
        self.assertFalse(japanese_message("Player-2, what supports your claim?"))
        self.assertFalse(japanese_message("I am the Seer. 占い師です。"))
        self.assertFalse(japanese_message("I agree. 同意です。"))

    def test_long_japanese_history_keeps_newest_complete_entries_within_budget(self):
        history = [{"player_id": f"player-{i % 9}", "message": f"発言{i}:" + "議論の根拠を確認します。" * 35} for i in range(40)]
        encoded = recent_json(history, 3500)
        self.assertLessEqual(len(encoded), 3500)
        included = json.loads(encoded)
        self.assertTrue(included)
        self.assertEqual(included[-1], history[-1])
        self.assertNotIn(history[0], included)


class AgentCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_own_ability_history_records_only_server_accepted_requests(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.phase, agent.state.day = 'night', 1
        agent.state.alive = {'player-0', 'player-1'}
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.ws = AsyncMock()
        for accepted in (False, True):
            await agent.send('ability.use', {'ability_id': 'protect', 'target_player_ids': ['player-1']}, agent.state.phase_key)
            sent = json.loads(agent.ws.send.call_args.args[0])
            self.assertEqual(agent.state.own_actions, [])
            agent.ws.__aiter__.return_value = [json.dumps({'type': 'action.accepted' if accepted else 'action.rejected',
                                                          'payload': {'request_event_id': sent['event_id']}})]
            await agent.receive()
        self.assertEqual(agent.state.own_actions, [{'day': 1, 'phase': 'night', 'ability_id': 'protect', 'target_player_ids': ['player-1']}])
        self.assertEqual(agent.pending_actions, {})

    async def test_defensive_co_precedes_speech_quota_and_uses_actual_role(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        state = agent.state
        state.role_id, state.phase, state.day = 'guard', 'day', 2
        state.players, state.alive = ['player-0', 'player-1', 'player-2'], {'player-0', 'player-1', 'player-2'}
        state.phase_ends_at = int(time.monotonic()) + 20
        state.chats = [{'channel': 'public', 'day': 2, 'player_id': 'player-1', 'message': 'player-0さんを処刑します。'}]
        state.actions = [{'type': 'chat', 'channel': 'public'}, {'type': 'co_declare', 'claimed_role_ids': ['guard', 'seer']}]
        state.own_actions = [{'day': 1, 'ability_id': 'protect', 'target_player_ids': ['player-2']}]
        agent.spoke[(2, 'public')] = 5
        agent.ws = AsyncMock()
        agent.generate = AsyncMock(side_effect=[PLAN, '私は狩人です。昨夜はplayer-2を護衛しました。投票を変えてください。'])
        await agent.step()
        sent = json.loads(agent.ws.send.call_args.args[0])
        self.assertEqual(sent['type'], 'co.declare')
        self.assertEqual(sent['payload']['claimed_role_id'], 'guard')
        self.assertEqual(agent.spoke[(2, 'public')], 6)
        await agent.step()
        agent.ws.send.assert_called_once()

    async def test_both_decision_and_body_receive_own_private_information(self):
        game = make_game()
        llm = FakeLLM()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, llm, RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.role_id, agent.state.phase, agent.state.day = 'werewolf', 'day', 1
        agent.state.players = ['player-0', 'player-1', 'player-2']
        agent.state.alive = set(agent.state.players)
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.teammates = ['player-1']
        agent.state.private = [{'type': 'NOTE', 'text': 'OWN-PRIVATE-SENTINEL'}]
        await agent.generate_speech('公開発言してください。', 'chat', 160)
        self.assertEqual(len(llm.calls), 2)
        for call in llm.calls:
            prompt = json.dumps(call['messages'], ensure_ascii=False)
            self.assertIn('あなたの非公開の役職: 人狼', prompt)
            self.assertIn('あなたが知っている仲間: player-1', prompt)
            self.assertIn('OWN-PRIVATE-SENTINEL', prompt)
            self.assertIn('目立たない', prompt)
        self.assertIn('reveal_role', agent.decisions[0]['decision'])
    async def test_hidden_role_body_uses_own_context_and_full_brief_decision(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.role_id, agent.state.day, agent.state.phase = "werewolf", 1, "day"
        agent.state.alive = {f"player-{i}" for i in range(9)}
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        plan = {"facts": ["私は人狼", "player-1は仲間"], "aim": "人狼として襲撃を隠す", "reason": "仲間を守って勝つ", "suspicion": "low", "reveal_role": False}
        agent.generate = AsyncMock(side_effect=[json.dumps(plan, ensure_ascii=False), "player-2さん、根拠を教えてください。"])
        await agent.generate_speech("発言してください。", "chat", 160)
        body_call = agent.generate.call_args_list[1]
        self.assertIn("私は人狼", body_call.args[0])
        self.assertIn("player-1は仲間", body_call.args[0])
        self.assertIn("仲間を守って勝つ", body_call.args[0])
        self.assertEqual(agent.decisions[-1]["decision"], plan)
        self.assertEqual(agent.decisions[-1]["body_context"], "own_private")

    async def test_private_plan_body_can_discuss_the_real_aim_with_known_teammates(self):
        game = make_game()
        for channel in ['public', 'wolf']:
            with self.subTest(channel=channel):
                llm = FakeLLM()
                agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {},
                              llm, RepetitionFilter(), lambda *_: None, seed=1)
                agent.state.role_id, agent.state.phase, agent.state.day = 'werewolf', 'day', 1
                agent.state.players = ['player-0', 'player-1', 'player-2']
                agent.state.alive = set(agent.state.players)
                agent.state.teammates = ['player-1']
                agent.state.private = [{'type': 'NOTE', 'text': 'OWN-PRIVATE-SENTINEL'}]
                agent.state.phase_ends_at = int(time.monotonic()) + 20
                await agent.generate_speech('対象と理由を話す', 'chat', 160, channel=channel)
                for call in llm.calls:
                    prompt = json.dumps(call['messages'], ensure_ascii=False)
                    self.assertIn('非公開の役職: 人狼', prompt)
                    self.assertIn('あなたが知っている仲間: player-1', prompt)
                    self.assertIn('OWN-PRIVATE-SENTINEL', prompt)
                body = llm.calls[1]['messages'][1]['content']
                if channel == 'wolf':
                    self.assertIn('通知された仲間player-1へ', body)
                    self.assertIn('作戦の対象IDと理由を率直に相談', body)
                    self.assertNotIn('裏の狙いは公開せず', body)
                else:
                    self.assertIn('裏の狙いは公開せず', body)

    async def test_intro_is_removed_without_retry_and_expired_chat_never_regenerates(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "day", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "chat", "channel": "public"}]
        agent.ws = AsyncMock()
        agent.generate = AsyncMock(side_effect=[PLAN, "player-0 です。player-2さん、根拠を教えてください。"])
        with patch("ai_agent.agent.speech_pause", new_callable=AsyncMock):
            await agent.step()
        self.assertEqual(agent.generate.call_count, 2)
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])["payload"]["message"], "player-2さん、根拠を教えてください。")
        self.assertNotIn("player-0として", agent.generate.call_args.args[0])
        self.assertEqual(strip_introduction("占い師のplayer-0です。結果を伝えます。", "player-0"), "占い師のplayer-0です。結果を伝えます。")
        self.assertEqual(strip_introduction("こんにちは、player-0 です。根拠を教えてください。", "player-0"), "根拠を教えてください。")

        async def expire(*args, **kwargs):
            agent.state.phase = "vote"
            return PLAN
        agent.generate = AsyncMock(side_effect=expire)
        agent.ws.reset_mock()
        with patch("ai_agent.agent.speech_pause", new_callable=AsyncMock):
            await agent.step()
        agent.generate.assert_awaited_once()
        agent.ws.send.assert_not_called()
        self.assertEqual(agent.speech_discards["phase_expired"], 1)
        self.assertEqual(agent.speech_generations, 2)

    async def test_discard_counters_distinguish_text_checks(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "day", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "chat", "channel": "public"}]
        key = agent.state.phase_key
        self.assertEqual(agent.speech_rejection("Hello", key), "japanese_check")
        agent.repetition.reserve("player-0", "根拠を教えてください。結果を確認します。")
        self.assertEqual(agent.speech_rejection("根拠を教えてください。別の理由は？", key), "own_previous_sentence")
        agent.repetition.reserve("player-1", "結果を確認します。")
        self.assertEqual(agent.speech_rejection("別の意見です。結果を確認します。", key), "own_previous_sentence")
        self.assertEqual(agent.repetition.rejection_reason("player-2", "結果を確認します。", []), "third_sentence")
        agent.state.chats = [{"channel": "public", "message": "投票先の理由を説明してください。"}]
        self.assertEqual(agent.speech_rejection("投票先の理由を説明してください！", key), "similarity")

    async def test_chat_retries_english_without_sending_it(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "day", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "chat", "channel": "public"}]
        agent.ws = AsyncMock()
        agent.generate = AsyncMock(side_effect=[PLAN, "I agree. 同意です。", PLAN, "player-2さん、投票理由を教えてください。"])
        with patch("ai_agent.agent.speech_pause", new_callable=AsyncMock):
            await agent.step()
        sent = json.loads(agent.ws.send.call_args.args[0])
        agent.ws.send.assert_called_once()
        self.assertEqual(sent["payload"]["message"], "player-2さん、投票理由を教えてください。")
        self.assertEqual([d["status"] for d in agent.decisions], ["discarded", "sent"])
        self.assertTrue(all("decision" in d for d in agent.decisions))
        self.assertNotIn('"facts"', sent["payload"]["message"])

    async def test_english_co_comment_is_not_sent(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "day", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "chat", "channel": "public"},
                               {"type": "co_declare", "claimed_role_ids": ["seer"]}]
        agent.spoke[(1, "public")] = 1
        agent.ws = AsyncMock()
        agent.choose = AsyncMock(return_value="seer")
        agent.generate = AsyncMock(side_effect=[PLAN, "I am the Seer. 占い師です。"] * 3)
        await agent.step()
        agent.ws.send.assert_not_called()

    async def test_phase_start_waits_for_new_actions_instead_of_sending_a_previous_vote(self):
        game = make_game()
        llm = FakeLLM()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      llm, RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "vote", 1
        deadline = int(time.monotonic()) + 20
        agent.state.phase_ends_at = deadline
        agent.state.actions = [{"type": "vote", "valid_targets": ["player-1"], "target_count": 1}]
        agent.ws = AsyncMock()
        agent.state.receive({"type": "game.event", "payload": {
            "event_type": "PHASE_STARTED", "visibility": "public", "event_payload": {
                "phase": "night", "day": 1, "phase_ends_at": deadline + 20,
            },
        }})
        await agent.step()
        agent.ws.send.assert_not_called()
        self.assertEqual(llm.calls, [])
        agent.state.receive({"type": "player.action_state", "payload": {
            "phase": "night", "day": 1, "phase_ends_at": deadline + 20,
            "actions": [{"type": "ability", "ability_id": "inspect", "valid_targets": ["player-1"],
                         "target_count": 1, "uses_remaining": 1}],
        }})
        await agent.step()
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])["type"], "ability.use")

    async def test_phase_change_suppresses_a_generated_vote_before_sending(self):
        class BlockingLLM(FakeLLM):
            def __init__(self):
                super().__init__()
                self.started, self.release = asyncio.Event(), asyncio.Event()

            async def complete(self, prompt, **kwargs):
                self.started.set()
                await self.release.wait()
                return await super().complete(prompt, **kwargs)

        game = make_game()
        llm = BlockingLLM()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      llm, RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "vote", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "vote", "valid_targets": ["player-1"], "target_count": 1}]
        agent.ws = AsyncMock()
        pending = asyncio.create_task(agent.step())
        await llm.started.wait()
        agent.state.phase = "night"
        agent.state.actions = []
        llm.release.set()
        await pending
        agent.ws.send.assert_not_called()
        self.assertEqual(agent.stale_suppressed, 1)

    async def test_nine_protocol_agents_finish_real_server_game_with_fake_llm(self):
        llm = FakeLLM()
        result = await run_game(seed=1, day=2, vote=2, night=2, llm=llm, timing_scale=0.001, timeout=70)
        self.assertTrue(result.checks["completed"], result.checks)
        self.assertEqual(result.checks["players"], 9)
        self.assertEqual(result.checks["finished_agents"], 9)
        self.assertIn(result.checks["winner"], {"village", "wolf"})
        self.assertEqual(result.checks["crashes"], 0)
        self.assertEqual(result.checks["server_rejections"], 0)
        self.assertTrue(llm.calls)
        self.assertTrue(any(row["kind"] == "chat" for row in result.recorder.rows))
        for agent in result.agents:
            if agent.state.role_id != "werewolf":
                self.assertEqual(agent.state.teammates, [])
