import json
import time
import unittest
from ai_agent.agent import Agent
from ai_agent.grounding import own_result_conflict
from ai_agent.metrics import measure
from ai_agent.prompts import discussion_context, messages
from ai_agent.quality import quality_reason
from ai_agent.repetition import RepetitionFilter
from ai_agent.state import PlayerState
from ai_agent.strategy import strategy_for
from tests.test_ai_agent import FakeLLM
from tests.test_network_sessions import make_game


class ConversationQualityTests(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.state = PlayerState('player-0', role_id='werewolf',
                                 players=['player-0', 'player-1', 'player-2'],
                                 alive={'player-0', 'player-1'}, teammates=['player-1'], day=1)
        self.roles = self.game.content.roles

    def test_alive_identity_and_meta_are_rejected_without_changing_strategy(self):
        for text, reason in [('なぜ私が処刑されたのでしょうか？', 'self_fact_confusion'),
                             ('私は昨日処刑されました。', 'self_fact_confusion'),
                             ('私自身は未確定役職ですが、疑いを述べます。', 'self_fact_confusion'),
                             ('申し訳ありませんが、このチャットへの参加をお断りします。', 'meta_refusal')]:
            with self.subTest(text=text):
                self.assertEqual(quality_reason(self.state, text, self.roles), reason)
        for text in ['私が処刑されたら人狼が有利です。', '私は処刑されていません。',
                     '私の役職はまだ公表しません。', '私は未確定の役職の人を疑っています。',
                     '私はplayer-2を疑っていましたが、player-2は処刑された。',
                     '私は占い師です。player-1は人狼でした。',
                     '私は人狼です。狂人の方、player-1へ票を合わせましょう。']:
            with self.subTest(text=text):
                self.assertIsNone(quality_reason(self.state, text, self.roles))

    def test_dead_addresses_and_current_votes_but_not_historical_arguments(self):
        for text in ['player-2さん、理由を教えてください。',
                     'その点は分かりました。player-2君、答えてください。',
                     'player-2: 根拠は何ですか？', 'player-2に投票します。',
                     'player-2を今日処刑すべきです。']:
            with self.subTest(text=text):
                self.assertEqual(quality_reason(self.state, text, self.roles), 'dead_player_address')
        for text in ['昨日player-2に投票しました。', 'player-2に投票しません。',
                     'player-2への昨日の投票理由を踏まえて、今日はplayer-1に投票します。',
                     'player-2を昨日処刑しましたが、今日はplayer-1を処刑します。',
                     'player-2: 霊能結果は人狼ではありませんでした。',
                     'player-2: 霊能結果は人狼ではありませんでした。player-1さん、投票先は誰ですか？',
                     'player-2: 霊能結果は人狼ではありませんでしたが、player-1はどう思いますか？',
                     'もしplayer-2が生きていたなら投票します。',
                     'player-2を処刑すべきではないと昨日述べました。',
                     'player-2の昨日の発言を根拠に、player-1を疑っています。',
                     'player-1さん、昨日のplayer-2への投票理由を教えてください。',
                     'player-1は「player-2さん、答えてください」と昨日言いました。']:
            with self.subTest(text=text):
                self.assertIsNone(quality_reason(self.state, text, self.roles))

    def test_self_fact_denials_and_other_reporters_are_not_the_speakers_assertion(self):
        for text in ['私は未確定役職ですとは言っていません。',
                     '私は昨日処刑されましたという意味ではありません。',
                     'player-1は私は昨日処刑されましたと発言しました。',
                     'player-1は私は未確定役職ですと主張しました。']:
            with self.subTest(text=text):
                self.assertIsNone(quality_reason(self.state, text, self.roles))
        for text in ['私は未確定役職ですとは言っていませんが、私は昨日処刑されました。',
                     'player-1は私は昨日処刑されましたと発言しました。私は未確定役職です。',
                     'player-1は私を疑いますが、私は昨日処刑されました。']:
            with self.subTest(text=text):
                self.assertEqual(quality_reason(self.state, text, self.roles), 'self_fact_confusion')

    def test_an_unpublished_role_is_different_from_an_unknown_own_role(self):
        for text in ['私の役職は未確定ですが、根拠を話します。',
                     '私の本当の役職は不明です。']:
            with self.subTest(text=text):
                self.assertEqual(quality_reason(self.state, text, self.roles), 'self_fact_confusion')
        for text in ['私の役職は未公開です。', '私の投票先は未確定です。',
                     '私の役職は未確定ですとは言っていません。',
                     'player-1は私の役職は未確定ですと発言しました。']:
            with self.subTest(text=text):
                self.assertIsNone(quality_reason(self.state, text, self.roles))

    def test_empty_agreement_needs_content_but_refutation_and_answers_are_free(self):
        self.assertEqual(quality_reason(self.state, '私も同意します。', self.roles), 'empty_agreement')
        self.assertIsNone(quality_reason(self.state, '同意します。player-1の投票理由も一致します。', self.roles))

    def test_history_first_person_is_bound_to_its_speaker_and_quotes_preserved(self):
        entries = [{'player_id': 'player-1', 'channel': 'public', 'day': 1,
                    'message': '私はplayer-0に投票した。player-2は「私は狩人です」と言った。'},
                   {'player_id': 'player-0', 'channel': 'public', 'day': 1,
                    'message': '私はplayer-1に質問します。'}]
        clarified = discussion_context(entries, 'player-0')
        self.assertEqual(clarified[0]['message'], entries[0]['message'])
        self.assertIn('「私は狩人です」', clarified[0]['message'])
        self.assertEqual(clarified[1]['message'], entries[1]['message'])
        self.assertEqual(clarified[1]['発言者本人'], 'player-0')
        self.assertNotIn('発言者', entries[0]['message'])

    def test_role_and_authorized_secret_information_remain_in_both_contexts(self):
        self.state.private = [{'type': 'CUSTOM_PRIVATE_NOTICE', 'text': '本人だけの結果'}]
        self.state.chats = [
            {'player_id': 'player-1', 'channel': 'public', 'day': 1, 'message': '私への投票理由は？'},
            {'player_id': 'player-1', 'channel': 'wolf', 'day': 1, 'message': '夜だけの相談案：次はplayer-2'},
            {'player_id': 'player-2', 'channel': 'other', 'day': 1, 'message': '別チャンネルの秘密'}]
        for channel in ['public', 'wolf']:
            prompt = json.dumps(messages(self.state, self.roles, {'werewolf': 2, 'madman': 1, 'villager': 6},
                                         '本文を書く', channel, rules=self.game.rules), ensure_ascii=False)
            self.assertIn('非公開の役職: 人狼', prompt)
            self.assertIn('本人だけの結果', prompt)
            self.assertIn('私への投票理由', prompt)
            self.assertIn('発言者本人', prompt)
            self.assertNotIn('あなた（あなた（', prompt)
            self.assertNotIn('別チャンネルの秘密', prompt)
            self.assertEqual('夜だけの相談案：次はplayer-2' in prompt, channel == 'wolf')

    def test_reported_co_without_quote_marks_keeps_the_reported_subject(self):
        entries = [{'player_id': 'player-1', 'message': 'player-2は私は狩人ですと発言しました。'}]
        clarified = discussion_context(entries, 'player-0')
        self.assertEqual(clarified[0]['message'], entries[0]['message'])
        self.assertEqual(clarified[0]['発言者本人'], 'player-1')

    def test_only_own_role_description_and_goal_are_in_both_speech_contexts(self):
        self.state.private = [{'type': 'CUSTOM_PRIVATE_NOTICE', 'text': '本人だけの判定'}]
        counts = {'werewolf': 2, 'madman': 1, 'seer': 1, 'medium': 1, 'guard': 1, 'villager': 3}
        for channel in ['public', 'wolf']:
            with self.subTest(channel=channel):
                system, user = messages(self.state, self.roles, counts, '相手の問いに答える', channel, rules=self.game.rules)
                self.assertIn(self.roles['werewolf'].description, system['content'])
                self.assertNotIn(self.roles['seer'].description, system['content'])
                self.assertNotIn(self.roles['seer'].description, user['content'])
                self.assertIn('本人だけの判定', system['content'])
                self.assertIn('あなたが知っている仲間: player-1', system['content'])
                goal = strategy_for(self.roles['werewolf'])['aim']
                self.assertIn(goal, user['content'])
                self.assertIn('初夜には占い師だけが', system['content'])
                self.assertIn('霊能結果は死因', system['content'])
                self.assertIn('偽の結果は秘密ではなく', system['content'])

    def test_q5_distinguishes_dead_result_reports_from_requests_to_the_dead(self):
        rows = [{'kind': 'PLAYER_DIED', 't': 1, 'payload': {'player_id': 'player-2'}},
                {'kind': 'chat', 't': 2, 'message': {'player_id': 'player-0', 'message': 'player-2: 霊能結果は人狼ではありませんでした。'}},
                {'kind': 'chat', 't': 3, 'message': {'player_id': 'player-0', 'message': 'player-2さん、結果を教えてください。'}}]
        measured = measure(rows, [], {'player-0': self.roles['villager']}, self.roles, self.game.rules, [])
        self.assertEqual(measured['metrics']['Q5'], 1)
        self.assertEqual(measured['metric_candidates']['Q5'][0]['row'], 2)

    def test_polite_request_stem_is_measured_and_blocked_for_a_dead_addressee(self):
        for request in ['根拠をお聞かせください。', '根拠を聞かせてください。',
                        '結果を聞かせてほしい。', '結果を聞かせてくれ。']:
            with self.subTest(request=request):
                text = 'player-2 さん、' + request
                self.assertEqual(quality_reason(self.state, text, self.roles), 'dead_player_address')
                self.assertIsNone(quality_reason(self.state, 'player-1 さん、' + request, self.roles))
                rows = [{'kind': 'PLAYER_DIED', 't': 1, 'payload': {'player_id': 'player-2'}},
                        {'kind': 'chat', 't': 2, 'message': {'player_id': 'player-0', 'message': text}}]
                measured = measure(rows, [], {'player-0': self.roles['villager']}, self.roles, self.game.rules, [])
                self.assertEqual(measured['metrics']['Q5'], 1)
        self.assertIsNone(quality_reason(self.state,
                                        'player-2の結果を踏まえ、player-1さん、根拠をお聞かせください。', self.roles))

    def test_current_living_opinion_question_is_separate_from_the_dead_result(self):
        rows = [{'kind': 'PLAYER_DIED', 't': 1, 'payload': {'player_id': 'player-2'}},
                {'kind': 'chat', 't': 2, 'message': {'player_id': 'player-0', 'message': 'player-2: 霊能結果は人狼ではありませんでしたが、player-1はどう思いますか？'}},
                {'kind': 'chat', 't': 3, 'message': {'player_id': 'player-0', 'message': 'player-2さん、結果はどうでしたか？player-1はどう思いますか？'}}]
        measured = measure(rows, [], {'player-0': self.roles['villager']}, self.roles, self.game.rules, [])
        self.assertEqual(measured['metrics']['Q5'], 1)
        self.assertEqual(measured['metric_candidates']['Q5'][0]['row'], 2)
        self.assertEqual(quality_reason(self.state, rows[2]['message']['message'], self.roles), 'dead_player_address')

    def test_public_guard_uses_quality_while_private_role_consultation_stays_free(self):
        agent = Agent('player-0', 'token', 'unused', self.game.game_id, self.roles, {}, FakeLLM(),
                      RepetitionFilter(), lambda *_: None, seed=1, rules=self.game.rules)
        agent.state = self.state
        self.state.phase, self.state.phase_ends_at = 'day', int(time.monotonic()) + 20
        self.assertEqual(agent.speech_rejection('私は処刑されました。', self.state.phase_key), 'self_fact_confusion')
        self.assertIsNone(agent.speech_rejection('私は人狼です。player-1へ相談します。', self.state.phase_key, channel='wolf'))

    def test_first_day_past_protection_is_checked_without_an_initial_night_word(self):
        self.state.role_id = 'guard'
        self.state.facts = [{'type': 'PHASE_STARTED', 'day': 0, 'phase': 'night0'},
                            {'type': 'PHASE_STARTED', 'day': 1, 'phase': 'day'}]
        for text in ['私が狩人としてplayer-1さんを護衛しました。',
                     '私はplayer-1を守りました。']:
            with self.subTest(text=text):
                self.assertTrue(own_result_conflict(self.state, text, self.roles, self.game.rules))
        for text in ['今夜は私がplayer-1を護衛します。',
                     'もし私がplayer-1を護衛したなら、襲撃は失敗します。',
                     '私はplayer-1を護衛したのではありません。',
                     'player-1がplayer-2を護衛しました。',
                     'player-1は「私がplayer-2を護衛しました」と言いました。']:
            with self.subTest(text=text):
                self.assertFalse(own_result_conflict(self.state, text, self.roles, self.game.rules))

    def test_past_protection_after_first_night_and_false_co_remain_free(self):
        self.state.role_id = 'guard'
        self.state.facts = [{'type': 'PHASE_STARTED', 'day': 1, 'phase': 'day'}]
        self.assertFalse(own_result_conflict(self.state, '私は占い師です。player-1は人狼でした。',
                                           self.roles, self.game.rules, 'seer'))
        self.state.facts.append({'type': 'PHASE_STARTED', 'day': 2, 'phase': 'day'})
        self.assertFalse(own_result_conflict(self.state, '私はplayer-1を護衛しました。', self.roles, self.game.rules))
