import unittest
import httpx

from ai_agent.llm import SharedLLM


class LLMRecordingTests(unittest.IsolatedAsyncioTestCase):
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
