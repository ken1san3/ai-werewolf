from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ai_agent.agent import Agent
from ai_agent.play import run_game
from ai_agent.prompts import japanese_message, messages, recent_json
from ai_agent.repetition import RepetitionFilter
from ai_agent.state import PlayerState
from tests.test_network_sessions import make_game


ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.count = 0

    async def complete(self, prompt, *, player_id, purpose, seed, schema=None, max_tokens=160):
        self.calls.append({"player_id": player_id, "purpose": purpose, "messages": prompt()})
        if schema:
            return json.dumps({"target": schema["properties"]["target"]["enum"][0]})
        self.count += 1
        return f"{player_id}からの質問です。根拠{self.count}について、どの主張を裏付けていますか？"


class AgentStateTests(unittest.TestCase):
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
    async def test_chat_retries_english_without_sending_it(self):
        game = make_game()
        agent = Agent("player-0", "token", "unused", game.game_id, game.content.roles, {},
                      FakeLLM(), RepetitionFilter(), lambda *_: None, seed=1)
        agent.state.receive({"type": "game.state_sync", "payload": game.get_state_sync("player-0")})
        agent.state.phase, agent.state.day = "day", 1
        agent.state.phase_ends_at = int(time.monotonic()) + 20
        agent.state.actions = [{"type": "chat", "channel": "public"}]
        agent.ws = AsyncMock()
        agent.generate = AsyncMock(side_effect=["I agree. 同意です。", "player-2さん、投票理由を教えてください。"])
        with patch("ai_agent.agent.speech_pause", new_callable=AsyncMock):
            await agent.step()
        sent = json.loads(agent.ws.send.call_args.args[0])
        agent.ws.send.assert_called_once()
        self.assertEqual(sent["payload"]["message"], "player-2さん、投票理由を教えてください。")

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
        agent.generate = AsyncMock(return_value="I am the Seer. 占い師です。")
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
            "event_type": "PHASE_STARTED", "event_payload": {
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
