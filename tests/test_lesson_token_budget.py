import copy
import json
import unittest

import httpx

from ai_agent.llm import SharedLLM
from ai_agent.prompts import messages
from ai_agent.state import PlayerState
from tests.test_network_sessions import make_game


LESSON_PREFIX = '本人向けの教訓（結果の通知ではありません）: '


def supplied_lessons(prompt):
    for message in prompt:
        if message['role'] == 'system':
            for line in message['content'].splitlines():
                if line.startswith(LESSON_PREFIX):
                    return json.loads(line[len(LESSON_PREFIX):])
    return []


class LessonPromptTests(unittest.TestCase):
    def test_lessons_are_json_with_complete_text_and_metadata_not_an_800_character_slice(self):
        game = make_game()
        state = PlayerState('player-0', role_id='seer', players=['player-0'], alive={'player-0'})
        text = '結果の出所を確認する。' * 160 + '教訓の末尾'
        lessons = [{'text': text, 'support_games': 12, 'games': ['g1', 'g2'], 'last_game': 15}]
        for value in (lessons, text):
            with self.subTest(legacy_string=isinstance(value, str)):
                prompt = messages(state, game.content.roles, {'seer': 1}, '議論してください', lessons=value)
                self.assertEqual(supplied_lessons(prompt), value)
                self.assertIn('教訓の末尾', prompt[0]['content'])
        without = messages(state, game.content.roles, {'seer': 1}, '議論してください')
        self.assertNotIn(LESSON_PREFIX, without[0]['content'])


class LessonTokenBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm = SharedLLM('http://fake/v1/chat/completions', context_limit=8192)
        await self.llm.client.aclose()
        self.requests = []
        self.extra_tokens = lambda _: 0

        def handler(request):
            body = json.loads(request.content)
            self.requests.append((request.url.path, body))
            if request.url.path == '/apply-template':
                # Template overhead depends on the actual request's thinking option.
                wrapper = 'THINKING' * 64 if body['chat_template_kwargs']['enable_thinking'] else 'CHAT'
                return httpx.Response(200, json={'prompt': wrapper + json.dumps(body['messages'], ensure_ascii=False)})
            if request.url.path == '/tokenize':
                self.assertTrue(body['add_special'])
                count = len(body['content']) + 7 + self.extra_tokens(body['content'])
                return httpx.Response(200, json={'tokens': list(range(count))})
            self.fail('Token fitting must not request a completion')

        self.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def asyncTearDown(self):
        await self.llm.close()

    def prompt(self, lessons, history=None):
        return [{'role': 'system', 'content': '本人の役職: 占い師\n本人の秘密: SELF-RESULT\n'
                 + (LESSON_PREFIX + json.dumps(lessons, ensure_ascii=False) if lessons else '') + '\n'},
                {'role': 'user', 'content': ('今日の最近のチャット: ' + json.dumps(history, ensure_ascii=False) + '\n'
                                            if history is not None else '') + '現在の質問: 理由を説明してください'}]

    async def test_whole_long_lessons_are_selected_in_support_order_with_output_and_template_reserve(self):
        high = {'text': '支持の多い教訓。' * 130 + '末尾まで維持', 'support_games': 20, 'last_game': 21}
        middle = {'text': '次の教訓。' * 130, 'support_games': 10, 'last_game': 22}
        low = {'text': '短い教訓', 'support_games': 1, 'last_game': 23}
        self.assertGreater(len(high['text']), 800)
        # Leave room for exactly the high-support prefix, even though the low item would fit.
        expected = self.prompt([high])
        output_reserve = 8192 - await self.llm.count_tokens(expected) - 32
        source = self.prompt([low, middle, high])
        original = copy.deepcopy(source)
        fitted = await self.llm.fit_context(source, output_reserve)
        self.assertEqual(supplied_lessons(fitted), [high])
        self.assertEqual(source, original)
        self.assertEqual(await self.llm.count_tokens(fitted) + output_reserve + 32, 8192)

    async def test_high_support_lesson_that_tokenizer_rejects_does_not_get_replaced_by_small_low_support(self):
        self.extra_tokens = lambda content: 6000 if '高密度トークン' in content else 0
        high = {'text': '高密度トークン', 'support_games': 50}
        low = {'text': '短くて収まる', 'support_games': 1}
        self.assertLess(await self.llm.count_tokens(self.prompt([low])) + 2048 + 32, 8192)
        fitted = await self.llm.fit_context(self.prompt([low, high]), 2048)
        self.assertEqual(supplied_lessons(fitted), [])
        self.assertNotIn('高密度トークン', str(fitted))

    async def test_baseline_history_is_fitted_first_and_identical_before_lesson_selection(self):
        history = [{'message': '古い議論' * 2600}, {'message': '必要な最新の質問'}]
        lesson = {'text': '推測と通知を分ける', 'support_games': 3}
        baseline = await self.llm.fit_context(self.prompt([], history), 1024)
        learned = await self.llm.fit_context(self.prompt([lesson], history), 1024)
        self.assertEqual(learned[1], baseline[1])
        self.assertIn('必要な最新の質問', learned[1]['content'])
        self.assertNotIn('古い議論', learned[1]['content'])
        self.assertIn('SELF-RESULT', learned[0]['content'])
        self.assertIn('本人の役職: 占い師', learned[0]['content'])
        self.assertIn('現在の質問', learned[1]['content'])
        self.assertEqual(supplied_lessons(learned), [lesson])
        self.assertLessEqual(await self.llm.count_tokens(learned) + 1024 + 32, 8192)

    async def test_own_role_teammates_results_and_accepted_actions_survive_fit_without_hidden_chat(self):
        game = make_game()
        state = PlayerState('player-0', role_id='werewolf', teammates=['player-1'],
                            players=['player-0', 'player-1', 'player-2'], alive={'player-0', 'player-1', 'player-2'},
                            private=[{'type': 'PRIVATE_NOTICE', 'text': 'SELF-PRIVATE'}],
                            own_actions=[{'ability_id': 'attack', 'target_player_ids': ['player-2']}], day=1)
        state.chats = [{'day': 1, 'channel': 'wolf', 'player_id': 'player-1', 'message': 'HIDDEN-CHAT'},
                       {'day': 1, 'channel': 'public', 'player_id': 'player-2', 'message': '最新の質問です'}]
        prompt = messages(state, game.content.roles, {'werewolf': 2, 'villager': 1}, '本人の現在の質問',
                          lessons=[{'text': '長い教訓' * 3000, 'support_games': 30}])
        fitted = await self.llm.fit_context(prompt, 4096)
        self.assertIn('あなたの非公開の役職: 人狼', fitted[0]['content'])
        self.assertIn('あなたが知っている仲間: player-1', fitted[0]['content'])
        self.assertIn('SELF-PRIVATE', fitted[0]['content'])
        self.assertIn('attack', fitted[1]['content'])
        self.assertIn('本人の現在の質問', fitted[1]['content'])
        self.assertNotIn('HIDDEN-CHAT', str(fitted))
        self.assertEqual(supplied_lessons(fitted), [])

    async def test_required_information_is_not_deleted_when_it_cannot_fit(self):
        prompt = [{'role': 'system', 'content': '本人の必須情報' * 2000}, {'role': 'user', 'content': '現在の質問'}]
        with self.assertRaisesRegex(ValueError, '本人の必須情報'):
            await self.llm.fit_context(prompt, 4096)

    async def test_no_lesson_control_prompt_stays_unchanged_and_legacy_text_is_atomic(self):
        control = self.prompt([])
        self.assertEqual(await self.llm.fit_context(control, 160), control)
        text = '従来形式の教訓' * 180 + '末尾'
        self.assertEqual(supplied_lessons(await self.llm.fit_context(self.prompt(text), 160)), text)
        # Legacy plain-text prompt rows are accepted too, and never sliced.
        raw = self.prompt([])
        raw[0]['content'] += LESSON_PREFIX + text + '\n'
        self.assertEqual(supplied_lessons(await self.llm.fit_context(raw, 160)), text)

    async def test_override_template_changes_token_count_and_reduces_remaining_lesson_space(self):
        lesson = {'text': '残り枠に置く教訓', 'support_games': 2}
        source = self.prompt([lesson])
        plain_count = await self.llm.count_tokens(source)
        reserve = 8192 - plain_count - 32 - 450
        self.assertEqual(supplied_lessons(await self.llm.fit_context(source, reserve)), [lesson])
        override = {'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_budget_tokens': 4096}
        # The mandatory prompt fits, but the enabled-thinking template consumes the lesson's room.
        fitted = await self.llm.fit_context(source, reserve, request_options=override)
        self.assertEqual(supplied_lessons(fitted), [])
        last_template = [body for path, body in self.requests if path == '/apply-template'][-1]
        self.assertEqual(last_template['chat_template_kwargs'], {'enable_thinking': True})
        self.assertEqual(last_template['reasoning_budget_tokens'], 4096)
        self.assertEqual(self.llm.request_options, {})
