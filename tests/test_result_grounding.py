import unittest

from ai_agent.grounding import own_result_conflict
from ai_agent.metrics import measure
from ai_agent.state import PlayerState
from tests.test_agent_recording import chat
from tests.test_network_sessions import make_game


class ResultGroundingTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.roles = self.game.content.roles
        self.state = PlayerState('player-0', role_id='seer', day=2, phase='day',
                                 players=['player-0', 'player-1', 'player-2'], alive={'player-0', 'player-1', 'player-2'})
        self.state.facts = [
            {'type': 'PHASE_STARTED', 'day': 0, 'phase': 'night0'},
            {'type': 'PHASE_STARTED', 'day': 1, 'phase': 'day'},
            {'type': 'PHASE_STARTED', 'day': 1, 'phase': 'night'},
            {'type': 'PHASE_STARTED', 'day': 2, 'phase': 'day'},
        ]
        self.state.private = [
            {'type': 'INSPECT_RESULT', 'received_day': 0, 'received_phase': 'night0', 'target_player_id': 'player-1', 'result': 'not_wolf'},
            {'type': 'INSPECT_RESULT', 'received_day': 1, 'received_phase': 'night', 'target_player_id': 'player-2', 'result': 'wolf'},
        ]

    def conflict(self, text, formal=None):
        return own_result_conflict(self.state, text, self.roles, self.game.rules, formal)

    def test_real_result_reports_keep_their_received_night_and_value(self):
        self.assertFalse(self.conflict('私は占い師です。昨夜の結果はplayer-2が人狼でした。'))
        self.assertFalse(self.conflict('初夜にplayer-1は人狼ではないと判定しました。'))
        self.assertTrue(self.conflict('私は占い師です。初夜にplayer-2は人狼と判定しました。'))
        self.assertTrue(self.conflict('私は占い師です。player-1は人狼と判定しました。'))

    def test_medium_cannot_report_a_living_target_and_can_report_received_dead_target(self):
        self.state.role_id = 'medium'
        self.state.private = [{'type': 'MEDIUM_RESULT', 'received_day': 1, 'received_phase': 'night', 'target_player_id': 'player-1', 'result': 'not_wolf'}]
        self.state.facts.append({'type': 'PLAYER_DIED', 'player_id': 'player-1', 'day': 1})
        self.assertFalse(self.conflict('私は霊能者です。player-1は人狼ではありませんでした。'))
        self.assertTrue(self.conflict('霊能判定でplayer-2は人狼と確定しました。'))
        self.assertTrue(self.conflict('初夜に私がplayer-1を人狼ではないと判定した。'))

    def test_false_co_and_other_players_second_person_reports_remain_free(self):
        self.state.role_id = 'medium'
        self.state.private = []
        self.assertFalse(self.conflict('私は占い師です。初夜にplayer-1は人狼と判定しました。', 'seer'))
        self.assertFalse(self.conflict('player-2さん、あなたが初夜にplayer-1を占った根拠は？'))
        self.state.role_id = 'madman'
        text = '初夜に私がplayer-1を占い、人狼ではないと判定した。'
        self.assertFalse(self.conflict(text))
        result = measure([chat('player-0', text, 1)], [], {'player-0': self.roles['madman']}, self.roles, self.game.rules, [])
        self.assertEqual(result['metrics']['Q4'], 0)

    def test_guard_initial_action_and_explicit_own_role_replacement_are_detected(self):
        self.state.role_id = 'guard'
        self.assertTrue(self.conflict('初夜に私がplayer-1を護衛した。'))
        self.assertFalse(self.conflict('私は占い師です。初夜にplayer-1は人狼でした。', 'seer'))
        text = '私は霊能者ではなく市民であり、私を処刑すべきです。'
        result = measure([chat('player-0', text, 1)], [], {'player-0': self.roles['medium']}, self.roles, self.game.rules, [])
        self.assertEqual(result['metrics']['Q3'], 1)

    def test_no_received_results_still_blocks_a_fabricated_own_verdict(self):
        self.state.role_id, self.state.private = 'medium', []
        self.state.facts.append({'type': 'PLAYER_DIED', 'player_id': 'player-1', 'day': 1, 'public_cause': 'died_in_night'})
        self.assertTrue(self.conflict('夜に死亡したplayer-1は人狼と判定しました。'))
        self.assertFalse(self.conflict('player-2さんがplayer-1は人狼と判定しました。'))

    def test_formal_and_informal_false_co_continue_until_a_real_co(self):
        self.state.role_id, self.state.private = 'medium', []
        for formal in (False, True):
            self.state.own_public = []
            self.state.receive({'type': 'game.event', 'payload': {'visibility': 'public', 'event_type': 'CO_DECLARED',
                                'event_payload': {'player_id': 'player-0', 'claimed_role_id': 'seer', 'comment': '占い師です。'}}}
                               if formal else {'type': 'chat.message', 'payload': {'channel': 'public',
                                               'message': {'player_id': 'player-0', 'message': '私は占い師です。'}}})
            self.assertFalse(self.conflict('初夜にplayer-1を占って人狼と判定しました。'))
            self.state.receive({'type': 'chat.message', 'payload': {'channel': 'public',
                                'message': {'player_id': 'player-0', 'message': '私は霊能者です。'}}})
            self.assertTrue(self.conflict('初夜にplayer-1は人狼と判定しました。'))

    def test_current_formal_true_co_overrides_an_earlier_false_co_in_the_guard(self):
        self.state.role_id, self.state.private = 'medium', []
        self.state.own_public = [{'text': '占い師です。', 'formal_claim': 'seer'}]
        text = 'player-1は人狼と判定しました。'
        self.assertFalse(self.conflict(text, 'seer'))
        self.assertTrue(self.conflict(text, 'medium'))
        rows = [{'kind': 'CO_DECLARED', 't': i, 'payload': {'player_id': 'player-0', 'claimed_role_id': role, 'comment': body}}
                for i, (role, body) in enumerate([('seer', '占い師です。'), ('medium', text)])]
        measured = measure(rows, [], {'player-0': self.roles['medium']}, self.roles, self.game.rules, [])
        self.assertEqual([c['row'] for c in measured['metric_candidates']['Q4']], [1])
