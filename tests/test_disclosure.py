import unittest

from ai_agent.disclosure import disclosure_reason
from ai_agent.state import PlayerState
from tests.test_network_sessions import make_game


class DisclosureTests(unittest.TestCase):
    def setUp(self):
        self.roles = make_game().content.roles
        self.state = PlayerState("player-0", role_id="werewolf", players=[f"player-{i}" for i in range(9)], day=1)
        self.state.alive = set(self.state.players)
        self.state.teammates = ["player-1"]

    def test_early_true_role_and_attack_are_blocked_but_false_co_and_quotes_are_free(self):
        self.assertEqual(disclosure_reason(self.state, "私は人狼です。", self.roles), "unjustified_self_disclosure")
        self.assertEqual(disclosure_reason(self.state, "ただし、私は人狼なので、この混乱に乗じて議論します。", self.roles), "unjustified_self_disclosure")
        self.assertEqual(disclosure_reason(self.state, "私 (player-0) は人狼ですが、村を惑わします。", self.roles), "unjustified_self_disclosure")
        self.assertEqual(disclosure_reason(self.state, "人狼 CO です。票を合わせましょう。", self.roles), "unjustified_self_disclosure")
        self.state.role_id = "madman"
        self.assertEqual(disclosure_reason(self.state, "霊能者が本物なら、狂人である私が騙ります。", self.roles), "unjustified_self_disclosure")
        self.state.role_id = "werewolf"
        self.assertEqual(disclosure_reason(self.state, "私がplayer-2を襲ったのです。", self.roles), "unjustified_self_disclosure")
        self.assertEqual(disclosure_reason(self.state, "人狼陣営です。", self.roles), "unjustified_self_disclosure")
        self.assertEqual(disclosure_reason(self.state, "結果を説明します。", self.roles, formal_claim="werewolf"), "unjustified_self_disclosure")
        self.assertIsNone(disclosure_reason(self.state, "占い師です。", self.roles, formal_claim="seer"))
        self.assertIsNone(disclosure_reason(self.state, "player-1さんの「私は人狼です」という発言が気になります。", self.roles))

    def test_majority_pp_uses_only_alive_known_and_publicly_claimed_supporters(self):
        self.state.alive = {f"player-{i}" for i in range(5)}
        self.assertIsNotNone(disclosure_reason(self.state, "私は人狼です。", self.roles))
        self.state.chats = [{"player_id": "player-2", "channel": "public", "day": 1, "message": "狂人です。"}]
        self.assertIsNone(disclosure_reason(self.state, "私は人狼です。player-4に票を合わせましょう。", self.roles))
        self.state.alive.remove("player-2")
        self.assertIsNotNone(disclosure_reason(self.state, "私は人狼です。", self.roles))

    def test_human_wolf_side_member_can_initiate_final_three_pp(self):
        self.state.role_id = "madman"
        self.state.teammates = []
        self.assertIsNotNone(disclosure_reason(self.state, "私は狂人です。", self.roles))
        self.state.alive = {"player-0", "player-1", "player-2"}
        self.assertIsNone(disclosure_reason(self.state, "狂人です。人狼の方、票を合わせましょう。", self.roles))

    def test_guard_can_counter_claim_or_avoid_actual_public_vote_pressure(self):
        self.state.role_id = "guard"
        self.assertIsNotNone(disclosure_reason(self.state, "私は狩人です。", self.roles))
        self.assertIsNotNone(disclosure_reason(self.state, "私が狩人としてplayer-3を護るべきですね。", self.roles))
        self.state.chats = [{"player_id": "player-2", "channel": "public", "day": 1, "message": "player-0さん、投票先は？"}]
        self.assertIsNotNone(disclosure_reason(self.state, "狩人です。", self.roles))
        self.state.chats[0]["message"] = "player-0さんを処刑したいです。"
        self.assertIsNone(disclosure_reason(self.state, "狩人です。私への投票を変えてください。", self.roles))
        self.state.chats[0]["message"] = "私が狩人です。"
        self.assertIsNone(disclosure_reason(self.state, "狩人です。そのCOは偽物です。", self.roles))

    def test_seer_own_result_is_allowed(self):
        self.state.role_id = "seer"
        self.assertIsNone(disclosure_reason(self.state, "占い師です。player-2は人狼ではありませんでした。", self.roles))
