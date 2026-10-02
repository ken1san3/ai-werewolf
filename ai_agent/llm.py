"""One shared OpenAI-compatible LLM endpoint, with generation and wait timings."""
import asyncio
import time

import httpx


class SharedLLM:
    def __init__(self, url="http://127.0.0.1:8090/v1/chat/completions", temperature=0.8):
        self.url, self.temperature = url, temperature
        self.client = httpx.AsyncClient(timeout=180)
        self.lock = asyncio.Lock()
        self.calls = []

    async def complete(self, prompt, *, player_id, purpose, seed, schema=None, max_tokens=160):
        queued = time.monotonic()
        async with self.lock:
            started = time.monotonic()
            body = {
                "model": "local", "messages": prompt(), "max_tokens": max_tokens,
                "temperature": self.temperature if schema is None else 0.6,
                "top_p": 0.95, "seed": seed,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "choice", "strict": True, "schema": schema,
                }}
            result, status, error = {}, None, None
            try:
                response = await self.client.post(self.url, json=body)
                status = response.status_code
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPError as exception:
                error = type(exception).__name__
                raise
            finally:
                elapsed = time.monotonic() - started
                timing = result.get("timings") or {}
                generation = (timing.get("prompt_ms", 0) + timing.get("predicted_ms", 0)) / 1000
                self.calls.append({
                    "player_id": player_id, "purpose": purpose, "http_status": status,
                    "http_error": error, "completed": bool(result),
                    "total_sec": time.monotonic() - queued,
                    "generation_sec": generation or elapsed,
                    "wait_sec": started - queued + max(elapsed - generation, 0) if generation else started - queued,
                    "prompt_tokens": (result.get("usage") or {}).get("prompt_tokens"),
                    "completion_tokens": (result.get("usage") or {}).get("completion_tokens"),
                })
            return (result["choices"][0]["message"].get("content") or "").strip()

    async def close(self):
        await self.client.aclose()
