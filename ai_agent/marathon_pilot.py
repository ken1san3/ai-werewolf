"""Explicit, bounded pre-launch trial; never launches the marathon."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import time

from .marathon import LocalBackend
from .marathon_checkpoint import choose_checkpoint
from .marathon_eval import bounded_prompt, scene_prompt, solve_scenes
from .marathon_runtime import MODEL_MAP, ROOT, make_llm, read_json, save_json, settings


async def probe(setting, port, directory):
    llm = make_llm(setting, port)
    bank = read_json(ROOT / 'content/marathon_scenes.json')
    try:
        prompt = scene_prompt(bank[0])
        formatted = await llm.client.post(llm.url.replace('/v1/chat/completions', '/apply-template'),
                                         json={'messages': prompt, **llm.request_options})
        formatted.raise_for_status()
        template = formatted.json()['prompt']
        score = await solve_scenes(llm, bank[:2], directory / (setting['id'].replace('/', '_') + '.json'), repeats=1)
        decision_schema = {'type': 'object', 'additionalProperties': False,
                           'required': ['facts', 'aim', 'reason', 'suspicion', 'reveal_role'], 'properties': {
            'facts': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string', 'maxLength': 24}},
            'aim': {'type': 'string', 'maxLength': 24}, 'reason': {'type': 'string', 'maxLength': 32},
            'suspicion': {'type': 'string', 'enum': ['low', 'medium', 'high']}, 'reveal_role': {'type': 'boolean'}}}
        decision_prompt = scene_prompt(bank[0])
        decision_prompt[-1]['content'] = decision_prompt[-1]['content'].split('選択肢:')[0] + '発言前の確認した事実(facts最大2件)、狙い(aim)、短い理由(reason)、自分への疑い(suspicion)、今役職を明かすか(reveal_role)を指定JSONで返してください。'
        decision = await llm.complete(lambda: decision_prompt, player_id='player-0', purpose='pilot_decision',
                                      seed=2, schema=decision_schema, max_tokens=192)
        # Representative full-context speech, alongside JSON scene decisions.
        answer = await llm.complete(lambda: scene_prompt(bank[5])[:-1] + [{'role': 'user', 'content':
            scene_prompt(bank[5])[-1]['content'].split('選択肢:')[0] + '自分の結果の意味を日本語で80字程度の公開発言にしてください。'}],
            player_id='player-0', purpose='pilot_speech', seed=2, max_tokens=128)
        # Verify exact-token bounded reflection preparation against the real API.
        await bounded_prompt(llm, {'確認': ['入力のトークン制限を確かめる']}, '日本語の短いJSONだけ。', reserve=llm.thinking_tokens + 640)
        result = {'setting': setting['id'], 'scene': score, 'calls': llm.calls,
                  'speech': answer, 'decision': decision, 'template_sha256': hashlib.sha256(template.encode()).hexdigest(),
                  'median_generation_sec': statistics.median(c['generation_sec'] for c in llm.calls),
                  'generation_tps': statistics.median(c['predicted_per_second'] for c in llm.calls if c.get('predicted_per_second')),
                  'prompt_tps': statistics.median(c['prompt_per_second'] for c in llm.calls if c.get('prompt_per_second'))}
        save_json(directory / (setting['id'].replace('/', '_') + '_probe.json'), result)
        return result
    finally:
        await llm.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=list(MODEL_MAP), required=True)
    parser.add_argument('--directory', type=Path, default=ROOT / 'runs/stage3_pilot')
    parser.add_argument('--game', action='store_true')
    parser.add_argument('--checkpoint', action='store_true')
    args = parser.parse_args()
    backend = LocalBackend(args.directory, 8091)
    backend.server.recover()
    started = time.monotonic()
    results = []
    try:
        load = backend.server.start(MODEL_MAP[args.model])
        for setting in [s for s in settings() if s['model'] == args.model]:
            try:
                measured = asyncio.run(probe(setting, backend.port, args.directory))
                measured['load_sec'] = load
                measured['mode_supported'] = bool(measured['speech']) and all(c['finish_reason'] != 'length' for c in measured['calls'])
                results.append(measured)
                print(json.dumps({k: v for k, v in measured.items() if k not in {'calls', 'speech', 'decision'}}, ensure_ascii=False), flush=True)
            except Exception as exception:
                results.append({'setting': setting['id'], 'error': repr(exception), 'mode_supported': False, 'load_sec': load})
        if args.game:
            setting = next(s for s in settings() if s['model'] == args.model and s['mode'] == 'off')
            destination = args.directory / 'qwen_game'
            game = backend.game(setting, 1, destination, 1500, phases={'day': 60, 'vote': 20, 'night': 20})
            notes = backend.reflect(setting, destination, {}, args.directory / 'notes', 1)
            save_json(args.directory / 'game_result.json', game)
            save_json(args.directory / 'lesson_result.json', notes)
            print('game', json.dumps(game, ensure_ascii=False), flush=True)
        if args.checkpoint:
            game = read_json(args.directory / 'game_result.json')
            row = {**next(s for s in settings() if s['model'] == 'qwen35-9b' and s['mode'] == 'off'),
                   'status': 'completed', 'load_sec': load, 'scene': results[0]['scene'], 'game': game}
            print('checkpoint', json.dumps(choose_checkpoint([row], 12 * 3600, args.directory / 'checkpoint'), ensure_ascii=False), flush=True)
    finally:
        save_json(args.directory / (args.model + '_pilot.json'), {'elapsed_sec': time.monotonic() - started, 'results': results})
        backend.close()


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    main()
