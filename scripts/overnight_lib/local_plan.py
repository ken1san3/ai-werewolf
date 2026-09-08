"""Bounded child process for schema-only Qwen planning. No tools or source writes."""
import json
import sys


def main():
    sys.path.insert(0, sys.argv[1])
    import llm
    request = json.loads(sys.stdin.read())
    payload = llm.build_payload(request['prompt'], schema=request['schema'],
                                max_tokens=request['max_tokens'])
    count = llm._token_count(payload)
    if count is None or count > llm.input_token_limit(request['max_tokens']):
        raise ValueError('Qwen planner input exceeds context or tokenizer unavailable; no generation')
    response = llm.raw(payload, timeout=float(sys.argv[2]))
    print(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    main()
