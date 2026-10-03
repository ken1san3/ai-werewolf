"""One shared OpenAI-compatible LLM endpoint, with generation and wait timings."""
import asyncio
import time

import httpx


class SharedLLM:
    def __init__(self, url="http://127.0.0.1:8090/v1/chat/completions", temperature=0.8,
                 *, request_options=None, thinking_tokens=0, request_timeout=180, context_limit=0):
        self.url, self.temperature = url, temperature
        self.client = httpx.AsyncClient(timeout=request_timeout)
        self.request_options = request_options or {}
        self.thinking_tokens = thinking_tokens
        self.context_limit = context_limit
        self.lock = asyncio.Lock()
        self.calls = []

    async def complete(self, prompt, *, player_id, purpose, seed, schema=None, max_tokens=160):
        queued = time.monotonic()
        async with self.lock:
            started = time.monotonic()
            body = {
                "model": "local", "messages": prompt(), "max_tokens": max_tokens + self.thinking_tokens,
                "temperature": self.temperature if schema is None else 0.6,
                "top_p": 0.95, "seed": seed,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            body.update(self.request_options)
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "choice", "strict": True, "schema": schema,
                }}
            result, status, error = {}, None, None
            try:
                if self.context_limit:
                    body['messages'] = await self.fit_context(body['messages'], body['max_tokens'])
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
                    "predicted_per_second": timing.get("predicted_per_second"),
                    "prompt_per_second": timing.get("prompt_per_second"),
                    "reasoning_chars": len(((result.get("choices") or [{}])[0].get("message") or {}).get("reasoning_content") or ""),
                    "finish_reason": (result.get("choices") or [{}])[0].get("finish_reason"),
                })
            return (result["choices"][0]["message"].get("content") or "").strip()

    async def close(self):
        await self.client.aclose()

    async def fit_context(self, messages, output_tokens):
        """Trim public history only; preserve role, secrets and the current question."""
        import copy
        import json
        messages = copy.deepcopy(messages)
        history_labels = ('今日の最近のチャット', 'サーバが公開した最近の事実', '公称役職と日別の要約', '本人が以前に公開した発言')
        while True:
            formatted = await self.client.post(self.url.replace('/v1/chat/completions', '/apply-template'),
                                              json={'messages': messages, **self.request_options})
            formatted.raise_for_status()
            tokens = await self.client.post(self.url.replace('/v1/chat/completions', '/tokenize'),
                                           json={'content': formatted.json()['prompt'], 'add_special': True})
            tokens.raise_for_status()
            if len(tokens.json()['tokens']) + output_tokens + 32 <= self.context_limit:
                return messages
            candidates = [(len(line), i, j, line) for i, message in enumerate(messages) if message['role'] == 'user'
                          for j, line in enumerate(message['content'].splitlines())
                          if line.startswith(history_labels) and len(line) > 100]
            if not candidates:
                raise ValueError('本人の必須情報と出力上限がコンテキストに収まりません')
            _, i, j, line = max(candidates)
            prefix, encoded = line.split(': ', 1)
            value = json.loads(encoded)
            value = value[len(value)//2:] if isinstance(value, list) and len(value) > 1 else []
            lines = messages[i]['content'].splitlines()
            lines[j] = prefix + ': ' + json.dumps(value, ensure_ascii=False)
            messages[i]['content'] = '\n'.join(lines)
