import unittest

from ai_agent.metrics import measure, mechanical_conditions
from tests.test_agent_recording import chat
from tests.test_network_sessions import make_game


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game()

    def metrics(self, rows, roles=None, private=None, **kwargs):
        return measure(rows, private or [], roles or {}, self.game.content.roles, self.game.rules, [], **kwargs)

    def test_role_truth_is_used_for_detection_but_quotes_and_false_wolf_claims_are_separate(self):
        roles = {"player-0": self.game.content.roles["werewolf"], "player-1": self.game.content.roles["guard"]}
        rows = [chat("player-0", "私は人狼です。票を合わせましょう。", 1),
                chat("player-1", "私は人狼です。", 2),
                chat("player-0", "player-1さんが「私は人狼です」と言いました。", 3),
                chat("player-0", "人狼の特定に協力します。", 4),
                chat("player-1", "狩人です。処刑候補なので出ます。", 5)]
        result = self.metrics(rows, roles)
        self.assertEqual(result["metrics"]["Q1"], 1)
        self.assertEqual(result["metrics"]["Q3"], 1)
        self.assertEqual([r["row"] for r in result["role_team_disclosure_candidates"]], [0, 4])

    def test_meta_rejection_intro_and_dead_address_have_locations(self):
        rows = [{"kind": "PLAYER_DIED", "t": 0, "payload": {"player_id": "player-2"}},
                chat("player-0", "player-0 です。\n申し訳ありませんが、お断りします。", 1),
                chat("player-1", "player-2さん、理由を教えてください。", 2)]
        result = self.metrics(rows)
        self.assertEqual([result["metrics"][key] for key in ("Q2", "Q5", "Q6")], [1, 1, 1])
        self.assertEqual(result["metric_candidates"]["Q5"][0]["line"], 10)

    def test_own_id_wolf_predicate_and_attack_narrative_are_disclosure_candidates(self):
        roles = {"player-0": self.game.content.roles["werewolf"]}
        rows = [chat("player-0", "player-0さんは昨夜にplayer-1を倒した唯一の狼であり、連携しました。", 1),
                chat("player-0", "私でplayer-1を襲ったのです。", 2),
                chat("player-0", "私も狼の特定に協力します。", 3)]
        self.assertEqual(self.metrics(rows, roles)["metrics"]["Q1"], 2)

    def test_other_players_role_or_initial_action_is_not_the_speakers_claim(self):
        roles = {"player-0": self.game.content.roles["medium"], "player-1": self.game.content.roles["villager"]}
        rows = [{"kind": "PHASE_STARTED", "t": 0, "payload": {"day": 1, "phase": "day"}},
                chat("player-0", "占い師であるplayer-2さんも結果を確認済みです。", 1),
                chat("player-1", "player-2さんが初夜にplayer-3を占ったからです。", 2),
                chat("player-1", "霊能者として初夜にplayer-3を調べたそうですが、信用できません。", 3)]
        rows.append(chat("player-1", "player-2が狼でないなら、player-1が狼です。", 4))
        rows.append(chat("player-1", "player-1さんはplayer-2さんが人狼だったと説明しました。", 5))
        result = self.metrics(rows, roles)
        self.assertEqual(result["metrics"]["Q3"], 0)
        self.assertEqual(result["metrics"]["Q4"], 0)

    def test_result_mismatch_alive_medium_and_unavailable_initial_action(self):
        roles = {"player-0": self.game.content.roles["seer"], "player-1": self.game.content.roles["medium"],
                 "player-2": self.game.content.roles["guard"]}
        private = [{"t": 1, "player_id": "player-0", "event_payload": {"target_player_id": "player-3", "result": "not_wolf"}}]
        rows = [{"kind": "PHASE_STARTED", "t": 0, "payload": {"day": 1, "phase": "day"}},
                chat("player-0", "占い結果はplayer-3が人狼でした。", 2),
                chat("player-0", "占い結果はplayer-3が人狼ではありません。", 3),
                chat("player-1", "霊能判定でplayer-3が人狼と確定しました。", 4),
                chat("player-2", "初夜にplayer-3を護衛した。", 5)]
        result = self.metrics(rows, roles, private)
        self.assertEqual([c["row"] for c in result["metric_candidates"]["Q4"]], [1, 3, 4])
        # A future result must not retrospectively invalidate an earlier claim.
        private[0]["t"] = 6
        self.assertEqual(self.metrics(rows[:2], roles, private)["metrics"]["Q4"], 0)

    def test_reaction_question_pairs_and_rate_use_time_and_previous_five_speakers(self):
        rows = [chat("player-0", "player-2さん、投票理由は？", 1),
                chat("player-1", "player-0さん、私は賛成です。", 2),
                chat("player-2", "発言の矛盾が理由です。", 61),
                chat("player-0", "player-1さん、占い理由は？", 62),
                chat("player-1", "結果を確認しました。", 123)]
        result = self.metrics(rows, generated=10, discards={"similarity": 2, "phase_expired": 1})
        self.assertEqual(result["metrics"]["G1"], 5)
        self.assertEqual(result["metrics"]["G2"], 2 / 5)
        self.assertEqual(result["metrics"]["G3"], 1)
        self.assertEqual(result["metrics"]["G4"], 0.3)
        self.assertEqual(result["question_answer_pairs"][0]["delay_sec"], 60)
        self.assertIsNone(self.metrics(rows)["metrics"]["G4"])

    def test_http_unknown_is_not_machine_pass_and_own_disclosure_is_not_literal_leak(self):
        checks = {"completed": True, "server_rejections": 0, "crashes": 0,
                  "strategic_disclosure_review": {"status": "complete", "leaks": 0},
                  **{k: [] for k in ("private_channel_body_matches", "authentication_token_leaks",
                                    "other_private_result_literal_matches", "immediate_sentence_repetitions", "sentences_repeated_three_times")}}
        self.assertFalse(mechanical_conditions(checks)["llm_http_errors"])
        checks["llm_http_errors"] = 0
        self.assertTrue(all(mechanical_conditions(checks).values()))
        checks["strategic_disclosure_review"]["status"] = "pending"
        self.assertFalse(mechanical_conditions(checks)["strategic_disclosures"])
        checks["strategic_disclosure_review"]["status"] = "complete"
        checks["llm_http_errors"] = 1
        self.assertFalse(all(mechanical_conditions(checks).values()))
