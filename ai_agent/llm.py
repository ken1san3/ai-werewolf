"""One shared OpenAI-compatible LLM endpoint, with generation and wait timings."""
import asyncio
import copy
import json
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

    def effective_options(self, request_options=None):
        options = {"chat_template_kwargs": {"enable_thinking": False}, **self.request_options}
        if request_options is not None:
            options.update(request_options)
        return options

    async def complete(self, prompt, *, player_id, purpose, seed, schema=None, max_tokens=160,
                       request_options=None, thinking_tokens=None):
        queued = time.monotonic()
        async with self.lock:
            started = time.monotonic()
            options = self.effective_options(request_options)
            thinking = self.thinking_tokens if thinking_tokens is None else thinking_tokens
            body = {
                "model": "local", "messages": prompt(), "max_tokens": max_tokens + thinking,
                "temperature": self.temperature if schema is None else 0.6,
                "top_p": 0.95, "seed": seed,
            }
            body.update(options)
            body['max_tokens'] = max_tokens + thinking
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "choice", "strict": True, "schema": schema,
                }}
            result, status, error = {}, None, None
            try:
                if self.context_limit:
                    body['messages'] = await self.fit_context(body['messages'], body['max_tokens'],
                                                              request_options=options)
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

    async def count_tokens(self, messages, *, request_options=None):
        """Count the loaded model's template, including the effective thinking settings."""
        formatted = await self.client.post(self.url.replace('/v1/chat/completions', '/apply-template'),
                                          json={'messages': messages, **self.effective_options(request_options)})
        formatted.raise_for_status()
        tokens = await self.client.post(self.url.replace('/v1/chat/completions', '/tokenize'),
                                       json={'content': formatted.json()['prompt'], 'add_special': True})
        tokens.raise_for_status()
        return len(tokens.json()['tokens'])

    async def fit_context(self, messages, output_tokens, *, request_options=None):
        """Fit own information/history first, then whole lessons in support order."""
        messages = copy.deepcopy(messages)
        lesson_prefix = '本人向けの教訓（結果の通知ではありません）: '
        lesson_location, lessons = None, []
        for i, message in enumerate(messages):
            if message['role'] != 'system':
                continue
            lines = message['content'].splitlines()
            for j, line in enumerate(lines):
                if line.startswith(lesson_prefix):
                    encoded = line[len(lesson_prefix):]
                    try:
                        lessons = json.loads(encoded)
                    except json.JSONDecodeError:
                        # Prompts saved by the old caller may still contain plain text.
                        lessons = encoded
                    lesson_location = i, j
                    lines[j] = ''
                    message['content'] = '\n'.join(lines) + ('\n' if message['content'].endswith('\n') else '')
                    break
            if lesson_location is not None:
                break
        history_labels = ('今日の最近のチャット', 'サーバが公開した最近の事実', '公称役職と日別の要約', '本人が以前に公開した発言')
        while True:
            if await self.count_tokens(messages, request_options=request_options) + output_tokens + 32 <= self.context_limit:
                break
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
        if lesson_location is None or not lessons:
            return messages
        ordered = (sorted(lessons, key=lambda lesson: lesson.get('support_games', 0), reverse=True)
                   if isinstance(lessons, list) else [lessons])
        selected = []
        i, j = lesson_location
        for lesson in ordered:
            candidate = copy.deepcopy(messages)
            lines = candidate[i]['content'].splitlines()
            # An empty final lesson line is omitted by splitlines().
            if j == len(lines):
                lines.append('')
            value = selected + [lesson] if isinstance(lessons, list) else lesson
            lines[j] = lesson_prefix + json.dumps(value, ensure_ascii=False)
            candidate[i]['content'] = '\n'.join(lines) + ('\n' if messages[i]['content'].endswith('\n') else '')
            if await self.count_tokens(candidate, request_options=request_options) + output_tokens + 32 > self.context_limit:
                break
            selected.append(lesson)
            messages = candidate
        return messages
