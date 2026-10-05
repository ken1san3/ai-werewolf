import unittest
import json
import httpx

from ai_agent.llm import SharedLLM


class LLMRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_overrides_use_same_template_and_output_reserve_without_changing_defaults(self):
        defaults = {'chat_template_kwargs': {'enable_thinking': False}, 'reasoning_budget_tokens': 768}
        llm = SharedLLM('http://fake/v1/chat/completions', request_options=defaults,
                        thinking_tokens=768, context_limit=8192)
        await llm.client.aclose()
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            if request.url.path == '/apply-template':
                thinking = body['chat_template_kwargs']['enable_thinking']
                return httpx.Response(200, json={'prompt': ('thinking-template' if thinking else 'plain-template')
                                                + json.dumps(body['messages'], ensure_ascii=False)})
            if request.url.path == '/tokenize':
                return httpx.Response(200, json={'tokens': list(range(len(body['content'])))})
            return httpx.Response(200, json={'choices': [{'message': {'content': ' {"ok":true} ',
                                                                      'reasoning_content': 'PRIVATE-REASONING'},
                                                         'finish_reason': 'stop'}]})

        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
        prompt = lambda: [{'role': 'user', 'content': '日本語でJSONを返す'}]
        overrides = {'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_budget_tokens': 4096}
        try:
            result = await llm.complete(prompt, player_id='player-0', purpose='discussion', seed=1,
                                        schema=schema, max_tokens=640, request_options=overrides,
                                        thinking_tokens=4096)
            self.assertEqual(result, '{"ok":true}')
            self.assertEqual(llm.calls[-1]['reasoning_chars'], len('PRIVATE-REASONING'))
            await llm.complete(prompt, player_id='player-0', purpose='chat', seed=2, max_tokens=160)
            # Zero is an explicit override, rather than falling back to the constructor budget.
            await llm.complete(prompt, player_id='player-0', purpose='chat', seed=3, max_tokens=160,
                               thinking_tokens=0)
        finally:
            await llm.close()
        completions = [body for path, body in requests if path == '/v1/chat/completions']
        templates = [body for path, body in requests if path == '/apply-template']
        self.assertEqual([body['max_tokens'] for body in completions], [4736, 928, 160])
        for body, template in zip(completions, templates):
            for key in defaults:
                self.assertEqual(body[key], template[key])
        self.assertEqual(completions[0]['chat_template_kwargs'], {'enable_thinking': True})
        self.assertEqual(completions[0]['reasoning_budget_tokens'], 4096)
        self.assertEqual(completions[0]['response_format']['json_schema']['schema'], schema)
        self.assertTrue(completions[0]['response_format']['json_schema']['strict'])
        self.assertEqual(completions[1]['chat_template_kwargs'], defaults['chat_template_kwargs'])
        self.assertEqual(completions[1]['reasoning_budget_tokens'], 768)
        self.assertEqual(llm.request_options, defaults)
        self.assertEqual(llm.thinking_tokens, 768)
        self.assertNotIn('PRIVATE-REASONING', str(llm.calls))

    async def test_http_error_is_counted_without_response_body_or_prompt(self):
        llm = SharedLLM()
        await llm.client.aclose()
        llm.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(400, text="SECRET")))
        with self.assertRaises(httpx.HTTPStatusError):
            await llm.complete(lambda: [{"role": "user", "content": "PRIVATE"}], player_id="player-0", purpose="chat", seed=1)
        await llm.close()
        self.assertEqual(llm.calls[0]["http_status"], 400)
        self.assertEqual(llm.calls[0]["http_error"], "HTTPStatusError")
        self.assertNotIn("PRIVATE", str(llm.calls))
        self.assertNotIn("SECRET", str(llm.calls))
