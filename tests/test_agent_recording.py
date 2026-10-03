import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent.checks import text_checks, timing_summary
from ai_agent.recording import Recorder
from server.aiwolf_core import EventVisibility, GameEvent
from tests.test_network_sessions import make_game


def chat(player, text, t):
    return {"kind": "chat", "t": t, "channel": "public",
            "message": {"player_id": player, "display_name": player, "message": text}}


class RecordingTests(unittest.TestCase):
    def test_unknown_private_notice_is_recorded_without_entering_public_transcript(self):
        recorder = Recorder(['public'])
        recorder.visible('player-0', 'game.event', {'visibility': 'private', 'event_type': 'CUSTOM_NOTICE', 'event_payload': {'text': 'ONLY-SELF'}})
        self.assertEqual(recorder.rows, [])
        self.assertEqual(recorder.private_results[0]['event_type'], 'CUSTOM_NOTICE')
        recorder.visible('player-0', 'game.event', {'visibility': 'public', 'event_type': 'CUSTOM_ANNOUNCEMENT', 'event_payload': {'text': 'PUBLIC'}})
        self.assertEqual([r['kind'] for r in recorder.rows], ['CUSTOM_ANNOUNCEMENT'])

    def test_public_stream_follows_protocol_order_and_does_not_duplicate_other_viewers(self):
        recorder = Recorder(["public"])
        death = GameEvent("PLAYER_DIED", EventVisibility.PUBLIC, {"player_id": "player-2"})
        recorder.core_event(death)
        payload = {"channel": "public", "message": {"player_id": "player-1", "message": "player-2, explain?"}}
        recorder.visible("player-1", "chat.message", payload)
        self.assertEqual(recorder.rows, [])
        recorder.visible("player-0", "chat.message", payload)
        recorder.visible("player-0", "game.event", {"event_type": death.type, "event_payload": dict(death.payload), "visibility": "public"})
        self.assertEqual([row["kind"] for row in recorder.rows], ["chat", "PLAYER_DIED"])
        self.assertEqual(text_checks(recorder.rows, [], [], set(), {})["dead_player_address_candidates"], [])

    def test_private_copy_and_token_leaks_are_detected_but_credentials_are_redacted(self):
        secret = "abcdef-secret-auth-token"
        rows = [chat("player-0", f"The hidden channel said attack player-2 tonight. {secret}", 2)]
        private = [{"t": 1, "channel": "wolf", "message": {"player_id": "player-1", "message": "attack player-2 tonight."}}]
        checks = text_checks(rows, private, [], {secret}, {})
        self.assertEqual(len(checks["private_channel_body_matches"]), 1)
        self.assertEqual(len(checks["authentication_token_leaks"]), 1)
        self.assertNotIn(secret, json.dumps(checks))
        recorder = Recorder(["public"])
        recorder.tokens.add(secret)
        recorder.rows = rows
        with TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "game"
            recorder.save(output, checks)
            transcript = (output / "transcript.md").read_text(encoding="utf-8")
            self.assertNotIn(secret, transcript)
            self.assertIn("[REDACTED TOKEN]", transcript)

    def test_own_results_and_wolf_self_disclosures_are_candidates_not_leak_failures(self):
        content = make_game().content
        private = [{"t": 1, "player_id": "player-0", "event_type": "INSPECT_RESULT",
                    "event_payload": {"target_player_id": "player-2", "result": "not_wolf"}}]
        rows = [chat("player-0", "I inspected player-2 and they are not a wolf.", 2),
                chat("player-1", "Since I am a wolf, I support player-3.", 3)]
        checks = text_checks(rows, [], private, set(), {
            "player-0": content.roles["seer"], "player-1": content.roles["werewolf"],
        })
        self.assertEqual(len(checks["own_result_disclosure_candidates"]), 1)
        self.assertEqual(len(checks["wolf_side_self_disclosure_candidates"]), 1)
        self.assertEqual(checks["other_private_result_literal_matches"], [])
        self.assertEqual(checks["private_channel_body_matches"], [])
        self.assertEqual(checks["authentication_token_leaks"], [])

    def test_private_results_cannot_be_inferred_from_earlier_public_guesses(self):
        private = [{"t": 3, "player_id": "player-0", "event_type": "INSPECT_RESULT",
                    "event_payload": {"target_player_id": "player-2", "result": "wolf"}}]
        rows = [chat("player-0", "player-2 is a wolf.", 2)]
        self.assertEqual(text_checks(rows, [], private, set(), {})["own_result_disclosure_candidates"], [])

    def test_japanese_self_disclosures_do_not_match_a_goal_to_find_wolves_or_quotes(self):
        content = make_game().content
        rows = [chat("player-1", "私も人狼の特定に全力を尽くします。", 1),
                chat("player-2", "player-4です。人狼です。", 2),
                chat("player-1", "私は狂人役職ですが、まずは議論します。", 3),
                chat("player-2", "player-4さんの「人狼です」という発言が気になります。", 4),
                chat("player-2", "player-4さんが「私は人狼です」と言った点が気になります。", 5),
                chat("player-2", 'player-4 said "I am a wolf", which sounds suspicious.', 6)]
        checks = text_checks(rows, [], [], set(), {
            "player-1": content.roles["madman"], "player-2": content.roles["werewolf"],
        })
        self.assertEqual([item["row"] for item in checks["wolf_side_self_disclosure_candidates"]], [1, 2])

    def test_private_result_json_matches_independent_of_spacing_and_key_order(self):
        private = [{"t": 1, "player_id": "player-0", "event_type": "INSPECT_RESULT",
                    "event_payload": {"target_player_id": "player-2", "result": "wolf"}}]
        for text in ['{"target_player_id":"player-2","result":"wolf"}',
                     '共有します: { "result" : "wolf", "target_player_id" : "player-2" }',
                     '{"copied": {"result":"wolf","target_player_id":"player-2"}}']:
            with self.subTest(text=text):
                checks = text_checks([chat("player-1", text, 2)], [], private, set(), {})
                self.assertEqual(len(checks["other_private_result_literal_matches"]), 1)
        checks = text_checks([chat("player-1", '{"target_player_id":"player-2","result":"not_wolf"}', 2)], [], private, set(), {})
        self.assertEqual(checks["other_private_result_literal_matches"], [])

    def test_short_private_body_matches_words_rather_than_substrings(self):
        private = [{"t": 1, "channel": "wolf", "message": {"message": "OK"}}]
        rows = [chat("player-0", "I looked at the vote.", 2), chat("player-0", "OK!", 3)]
        checks = text_checks(rows, private, [], set(), {})
        self.assertEqual([item["row"] for item in checks["private_channel_body_matches"]], [1])

    def test_repeated_sentences_and_dead_addresses_have_locations(self):
        rows = [chat("player-0", "I suspect player-1. Explain the vote?", 1),
                chat("player-0", "I suspect player-1. What happened at night?", 2),
                chat("player-1", "I suspect player-1.", 3),
                {"kind": "PLAYER_DIED", "t": 4, "payload": {"player_id": "player-1"}},
                chat("player-0", "player-1, answer my question?", 5)]
        checks = text_checks(rows, [], [], set(), {})
        self.assertEqual(len(checks["immediate_sentence_repetitions"]), 1)
        self.assertEqual(checks["sentences_repeated_three_times"], [{"text": "i suspect player-1.", "count": 3}])
        self.assertEqual(checks["dead_player_address_candidates"][0]["row"], 4)
        self.assertEqual(checks["dead_player_address_candidates"][0]["target_player_id"], "player-1")

    def test_timing_summary_has_nonzero_coverage_or_explicitly_unknown_values(self):
        self.assertEqual(timing_summary([], "wait_sec")["count"], 0)
        self.assertIsNone(timing_summary([], "wait_sec")["p50"])
        self.assertEqual(timing_summary([{"wait_sec": 1}, {"wait_sec": 3}], "wait_sec")["mean"], 2)
