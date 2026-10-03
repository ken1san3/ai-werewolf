import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock

from ai_agent.agent import Agent
from ai_agent.disclosure import disclosure_reason
from ai_agent.claims import self_claims
from ai_agent.metrics import night_private_activity
from ai_agent.play import run_game
from ai_agent.prompts import messages
from ai_agent.repetition import RepetitionFilter
from ai_agent.state import PlayerState
from ai_agent.strategy import vote_pressure
from tests.test_ai_agent import FakeLLM, PLAN
from tests.test_network_sessions import make_game


class RoleStrategyTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.state = PlayerState('player-0', role_id='werewolf', players=['player-0', 'player-1', 'player-2'],
                                 alive={'player-0', 'player-1', 'player-2', 'player-3', 'player-4'}, teammates=['player-1'], day=1)

    def test_role_grammar_army_strategy_and_known_buddy_are_hidden_early(self):
        for text in ['私、狼です。', '私こそ人狼です。', '私には人狼の役職があります。',
                     '人狼は私とplayer-1です。', '昨夜の襲撃は私とplayer-1で行いました。',
                     '村を混乱させて人狼を救うのが私の戦略です。', 'player-1は私の仲間です。']:
            with self.subTest(text=text):
                self.assertEqual(disclosure_reason(self.state, text, self.game.content.roles), 'unjustified_self_disclosure')
        self.state.role_id = 'guard'
        self.state.teammates = []
        for text in ['私、狩人です。', 'player-2の占いが偽なら、私の護衛が有効です。']:
            self.assertIsNotNone(disclosure_reason(self.state, text, self.game.content.roles))

    def test_deception_quotes_denial_and_pressure_remain_free(self):
        for text in ['私は占い師です。player-2は人狼でした。', '私が人狼なら矛盾します。',
                     'player-2は「私は人狼です」と言いました。', '私は人狼ではありません。',
                     '私は人狼の勝利を助けていません。', '人狼は私と関係ありません。',
                     '昨夜の襲撃は私のみでした。']:
            self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))
        self.assertTrue(vote_pressure('player-0', 'player-0さんを処刑すべきではないでしょうか？'))
        self.assertFalse(vote_pressure('player-0', 'player-0さんを処刑すべきではない。'))
        self.state.role_id = 'guard'
        self.state.chats = [{'channel': 'public', 'day': 1, 'player_id': 'player-2',
                             'message': 'player-0さんを処刑すべきではないでしょうか？'}]
        self.assertIsNone(disclosure_reason(self.state, '狩人です。今日はまだ護衛していません。', self.game.content.roles))

    def test_possessed_role_with_explanatory_denial_is_not_disclosure(self):
        for text in ['私には人狼の役職があるわけではありません。',
                     '私には人狼の役職があるという意味ではない。',
                     '私には人狼の役職があるとは言っていません。',
                     '私には人狼の役職を与えられたわけではありません。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))
        self.assertIsNotNone(disclosure_reason(self.state, '私には人狼の役職があります。', self.game.content.roles))

    def test_affirmative_private_intent_survives_an_unrelated_following_hypothesis(self):
        for text in ['player-2さん、投票したのは私が村を混乱させる意図からで、理由はありません。',
                     '私の投票はただの村の混乱策に過ぎません。',
                     'この混乱に乗じて村の票を割く狙いですが、player-2の占いが真なら別です。']:
            with self.subTest(text=text):
                self.assertEqual(disclosure_reason(self.state, text, self.game.content.roles), 'unjustified_self_disclosure')
        for text in ['私が村を混乱させる意図からではありません。',
                     'player-2は私が村を混乱させる意図から投票したと言いました。',
                     'player-2は村の票を割く狙いですが、player-1の占いが真なら別です。',
                     '村の票を割く狙いですか？', 'もし村の票を割く狙いなら疑います。',
                     'player-2は「私の投票はただの村の混乱策に過ぎません」と言った。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))

    def test_truth_telling_preface_is_an_assertion_but_real_hypotheses_stay_free(self):
        statement = '真実を告げるなら、私は狼です。'
        self.assertEqual(self_claims('player-0', statement, ['人狼']), ['人狼'])
        self.assertEqual(disclosure_reason(self.state, statement, self.game.content.roles), 'unjustified_self_disclosure')
        for text in ['もし私が狼なら、その投票はしません。',
                     'player-2が真実を告げるなら、私は狼です。',
                     'player-2は「真実を告げるなら、私は狼です」と言った。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))
        self.state.alive = {'player-0', 'player-1', 'player-2'}
        self.assertIsNone(disclosure_reason(self.state, statement + 'player-2へ票を合わせましょう。', self.game.content.roles))

    def test_hidden_vote_disruption_intent_and_changed_army_co_are_guarded(self):
        for text in ['player-2さんに疑いを持たせ、混乱で票が割れるよう仕向けます。',
                     'player-2への疑いで混乱で票が割れるよう誘導します。',
                     '私は混乱で票を割るよう仕向けます。',
                     '私こそ市民COです。player-2を処刑して混乱させましょう。',
                     '私は人狼COではなく、狂人COです。']:
            with self.subTest(text=text):
                self.assertEqual(disclosure_reason(self.state, text, self.game.content.roles), 'unjustified_self_disclosure')
        for text in ['player-2は混乱で票が割れるよう仕向けます。',
                     'player-2さんが混乱で票が割れるよう誘導します。',
                     'player-2は「混乱で票が割れるよう仕向けます」と言った。',
                     '私は混乱で票を割るよう仕向けていません。',
                     'player-2は村を混乱させましょうと発言しました。',
                     '私は村を混乱させません。',
                     'もし私が混乱で票が割れるよう誘導するなら怪しいです。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))
        self.assertEqual(self_claims('player-0', '私は人狼COではなく、狂人COです。', ['人狼', '狂人']), ['狂人'])
        self.state.alive = {'player-0', 'player-2', 'player-3'}
        self.assertIsNone(disclosure_reason(self.state, '私は人狼COではなく、狂人COです。',
                                           self.game.content.roles,
                                           role_counts={'werewolf': 2, 'madman': 1, 'villager': 6}))
        self.assertIsNotNone(disclosure_reason(self.state, '混乱で票が割れるよう誘導します。',
                                              self.game.content.roles,
                                              role_counts={'werewolf': 2, 'madman': 1, 'villager': 6}))

    def test_a_negated_predicate_does_not_hide_another_positive_army_claim(self):
        for text in ['私は村を混乱させませんが、人狼を救うのが私の目的です。',
                     '私は村を混乱させませんが人狼を救うのが私の仕事です。',
                     '私は村を混乱させないけど、人狼陣営です。']:
            with self.subTest(text=text):
                self.assertEqual(disclosure_reason(self.state, text, self.game.content.roles), 'unjustified_self_disclosure')

    def test_question_about_someone_elses_unquoted_proposal_is_not_own_disclosure(self):
        for text in ['player-2さん、村を混乱させましょうと提案した理由を教えてください？',
                     'player-2さん、混乱で票が割れるよう仕向けますと発言した理由は？',
                     'player-2さん、村を混乱させましょうという提案は村に不利です。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))

    def test_own_proposal_is_detected_but_denied_or_questioned_proposals_are_not(self):
        for text in ['私は村を混乱させましょうと提案します。',
                     '村を混乱させましょうと提案します。',
                     'player-2さん、村を混乱させましょうと提案します。']:
            with self.subTest(text=text):
                self.assertIsNotNone(disclosure_reason(self.state, text, self.game.content.roles))
        for text in ['私は村を混乱させましょうと提案していません。',
                     'player-2さん、村を混乱させましょうと提案しますか？']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))

    def test_possible_late_pp_is_not_blocked_using_unreceived_role_truth(self):
        self.assertIsNone(disclosure_reason(self.state, '人狼です。狂人の方、player-2へ合わせてください。', self.game.content.roles,
                                           role_counts={'werewolf': 2, 'madman': 1, 'villager': 6}))
        self.state.alive = {'player-0', 'player-2', 'player-3'}
        self.assertIsNone(disclosure_reason(self.state, '人狼です。狂人の方、player-2へ合わせてください。', self.game.content.roles,
                                           role_counts={'werewolf': 2, 'madman': 1, 'villager': 6}))
        self.assertIsNotNone(disclosure_reason(self.state, '人狼です。', self.game.content.roles,
                                              role_counts={'werewolf': 2, 'villager': 7}))

    def test_publicly_claimed_helper_is_not_counted_twice(self):
        self.state.alive = {'player-0', 'player-2', 'player-3', 'player-4', 'player-5'}
        self.state.chats = [{'channel': 'public', 'day': 1, 'player_id': 'player-2', 'message': '狂人です。'}]
        counts = {'werewolf': 2, 'madman': 1, 'villager': 6}
        self.assertIsNotNone(disclosure_reason(self.state, '人狼です。', self.game.content.roles, role_counts=counts))
        self.state.chats = []
        self.state.facts = [{'type': 'CO_DECLARED', 'player_id': 'player-2', 'claimed_role_id': 'madman'}]
        self.assertIsNotNone(disclosure_reason(self.state, '人狼です。', self.game.content.roles, role_counts=counts))

    def test_role_and_buddy_denial_hypothesis_remain_free(self):
        for text in ['私の役職は人狼ではありません。', '私には人狼の役職がありません。',
                     'player-1は私の仲間ではありません。', '私の仲間はplayer-1ではありません。',
                     'もしplayer-1が私の仲間なら、私を疑うはずです。']:
            with self.subTest(text=text):
                self.assertIsNone(disclosure_reason(self.state, text, self.game.content.roles))
        self.assertIsNotNone(disclosure_reason(self.state, '私の役職は人狼です。', self.game.content.roles))

    def test_private_context_has_authorized_chats_and_public_information_only(self):
        self.state.chats = [{'channel': c, 'day': 1, 'player_id': 'player-1', 'message': text}
                            for c, text in [('public', 'PUBLIC'), ('wolf', 'OWN-CHANNEL'), ('fox', 'OTHER-CHANNEL')]]
        p = json.dumps(messages(self.state, self.game.content.roles, {}, '相談してください。', channel='wolf'), ensure_ascii=False)
        self.assertIn('PUBLIC', p)
        self.assertIn('OWN-CHANNEL', p)
        self.assertNotIn('OTHER-CHANNEL', p)
        self.assertNotIn('OWN-CHANNEL', json.dumps(messages(self.state, self.game.content.roles, {}, '公開発言してください。')))

    def test_night_measurement_uses_actual_alive_wolves_and_counts_missing_nights(self):
        roles = {'player-0': self.game.content.roles['werewolf'], 'player-1': self.game.content.roles['werewolf'],
                 'player-2': self.game.content.roles['madman']}
        rows = [{'kind': 'PHASE_STARTED', 't': 0, 'payload': {'day': 0, 'phase': 'night0'}},
                {'kind': 'PHASE_STARTED', 't': 10, 'payload': {'day': 1, 'phase': 'day'}},
                {'kind': 'PHASE_STARTED', 't': 20, 'payload': {'day': 1, 'phase': 'night'}},
                {'kind': 'PHASE_STARTED', 't': 30, 'payload': {'day': 2, 'phase': 'day'}},
                {'kind': 'PLAYER_DIED', 't': 31, 'payload': {'player_id': 'player-1'}},
                {'kind': 'PHASE_STARTED', 't': 40, 'payload': {'day': 2, 'phase': 'night'}},
                {'kind': 'GAME_ENDED', 't': 50, 'payload': {}}]
        private = [{'t': 2, 'message': {'player_id': 'player-0'}}, {'t': 22, 'message': {'player_id': 'player-2'}}]
        result = night_private_activity(rows, private, roles)
        self.assertEqual(result['required_nights'], 2)
        self.assertEqual(result['missing_required_nights'], 1)
        self.assertEqual(result['nights'][0]['speakers'], ['player-0'])


class PrivateChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_private_json_still_selects_ability_and_is_not_retried(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(),
                      RepetitionFilter(), lambda *_: None, seed=1, rules=game.rules)
        state = agent.state
        state.role_id, state.phase, state.day = 'werewolf', 'night', 1
        state.players, state.alive, state.teammates = ['player-0', 'player-1', 'player-2'], {'player-0', 'player-1', 'player-2'}, ['player-1']
        state.phase_ends_at = int(time.monotonic()) + 25
        state.actions = [{'type': 'ability', 'ability_id': 'attack', 'valid_targets': ['player-2'], 'target_count': 1},
                         {'type': 'chat', 'channel': 'wolf'}]
        agent.generate = AsyncMock(side_effect=['invalid', 'invalid', 'invalid', '{"target":"player-2"}'])
        agent.ws = AsyncMock()
        await agent.step()
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])['type'], 'ability.use')
        self.assertEqual(agent.generate.call_count, 4)
        self.assertEqual(agent.speech_discards['invalid_decision_json'], 3)
        await agent.step()
        self.assertEqual(agent.generate.call_count, 4)

    async def test_short_remaining_night_reserves_ability_time(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(),
                      RepetitionFilter(), lambda *_: None, seed=1, rules=game.rules)
        state = agent.state
        state.role_id, state.phase, state.day = 'werewolf', 'night', 1
        state.players, state.alive, state.teammates = ['player-0', 'player-1', 'player-2'], {'player-0', 'player-1', 'player-2'}, ['player-1']
        state.phase_ends_at = int(time.monotonic()) + 7
        state.actions = [{'type': 'ability', 'ability_id': 'attack', 'valid_targets': ['player-2'], 'target_count': 1},
                         {'type': 'chat', 'channel': 'wolf'}]
        agent.generate = AsyncMock(return_value='{"target":"player-2"}')
        agent.ws = AsyncMock()
        await agent.step()
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])['type'], 'ability.use')
        self.assertEqual(agent.speech_generations, 0)

    async def test_private_timeout_keeps_ability_time_and_counts_its_own_reason(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(),
                      RepetitionFilter(), lambda *_: None, seed=1, rules=game.rules)
        state = agent.state
        state.role_id, state.phase, state.day = 'werewolf', 'night', 1
        state.players, state.alive, state.teammates = ['player-0', 'player-1', 'player-2'], {'player-0', 'player-1', 'player-2'}, ['player-1']
        state.phase_ends_at = int(time.monotonic()) + 10
        state.actions = [{'type': 'ability', 'ability_id': 'attack', 'valid_targets': ['player-2'], 'target_count': 1},
                         {'type': 'chat', 'channel': 'wolf'}]
        async def generate(question, purpose, *args, **kwargs):
            if purpose == 'private_chat_decision':
                await asyncio.sleep(3)
                return PLAN
            return '{"target":"player-2"}'
        agent.generate = generate
        agent.ws = AsyncMock()
        await agent.step()
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])['type'], 'ability.use')
        self.assertEqual(agent.speech_discards['private_chat_budget'], 1)
        self.assertEqual(agent.speech_discards['phase_expired'], 0)

    async def test_private_chat_precedes_ability_and_public_filters_do_not_hide_own_role(self):
        game = make_game()
        agent = Agent('player-0', 'token', 'unused', game.game_id, game.content.roles, {}, FakeLLM(),
                      RepetitionFilter(), lambda *_: None, seed=1, rules=game.rules)
        state = agent.state
        state.role_id, state.phase, state.day = 'werewolf', 'night', 1
        state.players, state.alive, state.teammates = ['player-0', 'player-1', 'player-2'], {'player-0', 'player-1', 'player-2'}, ['player-1']
        state.phase_ends_at = int(time.monotonic()) + 25
        state.actions = [{'type': 'ability', 'ability_id': 'attack', 'valid_targets': ['player-2'], 'target_count': 1},
                         {'type': 'chat', 'channel': 'wolf'}]
        agent.generate = AsyncMock(side_effect=[PLAN, '私は人狼です。今夜はplayer-2を襲いましょう。', '{"target":"player-2"}'])
        agent.ws = AsyncMock()
        await agent.step()
        first = json.loads(agent.ws.send.call_args.args[0])
        self.assertEqual(first['type'], 'chat.send')
        self.assertEqual(first['payload']['channel_id'], 'wolf')
        self.assertEqual(agent.decisions[0]['status'], 'sent')
        self.assertEqual(agent.repetition.last, {})
        body = first['payload']['message']
        state.chats = [{'channel': 'wolf', 'day': 1, 'player_id': 'player-1', 'message': 'player-2に合わせます。'},
                       {'channel': 'wolf', 'day': 1, 'player_id': 'player-0', 'message': body}]
        self.assertEqual(agent.speech_rejection(body, state.phase_key), 'private_body_copy')
        await agent.step()
        self.assertEqual(json.loads(agent.ws.send.call_args.args[0])['type'], 'ability.use')
        self.assertEqual(agent.generate.call_count, 3)

    async def test_real_server_private_chat_goes_only_to_authorized_players_and_all_required_nights(self):
        result = await run_game(seed=1, day=2, vote=2, night=4, timing_scale=.001, timeout=55, llm=FakeLLM())
        self.assertTrue(result.checks['completed'])
        self.assertEqual(result.checks['server_rejections'], 0)
        self.assertEqual(result.checks['crashes'], 0)
        self.assertGreater(result.checks['private_messages_compared'], 0)
        self.assertEqual(result.checks['night_private_activity']['missing_required_nights'], 0,
                         result.checks['night_private_activity'])
        self.assertEqual(result.checks['private_channel_body_matches'], [])
        for agent in result.agents:
            private = [c for c in agent.state.chats if c['channel'] != 'public']
            if not agent.state.teammates:
                self.assertEqual(private, [])
