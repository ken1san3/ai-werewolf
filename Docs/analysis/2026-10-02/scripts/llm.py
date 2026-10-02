"""Tiny client for the local llama-server used in this analysis (scratch only)."""
import json
import time
import urllib.request

ENDPOINT = "http://127.0.0.1:8090/v1/chat/completions"


def chat(body, timeout=120):
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    dt = time.time() - t0
    msg = out["choices"][0]["message"].get("content") or ""
    usage = out.get("usage", {})
    return msg, dt, usage, out["choices"][0].get("finish_reason")


def product_body(prompt, temperature=0.2, max_tokens=512, seed=None):
    """Rebuild the v1 product request body from a saved prompt_json (messages + output_schema)."""
    body = {
        "max_tokens": max_tokens,
        "messages": [{"content": m["content"], "role": m["role"]} for m in prompt["messages"]],
        "model": "Qwen3.5-9B-Q4_K_M.gguf",
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "aiwolf_brain_decision", "strict": True, "schema": prompt["output_schema"]},
        },
        "stream": False,
        "temperature": temperature,
        "reasoning_format": "deepseek",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if seed is not None:
        body["seed"] = seed
    return body


def free_body(system, user, temperature=0.7, max_tokens=200, seed=None, schema=None):
    body = {
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "model": "Qwen3.5-9B-Q4_K_M.gguf",
        "stream": False,
        "temperature": temperature,
        "top_p": 0.95,
        "reasoning_format": "deepseek",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "strict": True, "schema": schema}}
    if seed is not None:
        body["seed"] = seed
    return body
