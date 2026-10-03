"""Record a one-time calibration using pilot requests and actual game calls."""
from collections import Counter, defaultdict
import math
from pathlib import Path
import statistics

from .marathon_runtime import read_json, save_json, settings


def category(purpose):
    if purpose.endswith('_decision') or purpose == 'pilot_decision':
        return 'decision'
    return 'choice' if purpose in {'co', 'vote', 'ability', 'scene'} else 'speech'


def calibrate(directory, game_checks, destination):
    directory = Path(directory)
    reference = read_json(game_checks)
    actual = [c for c in reference['llm_calls'] if c.get('completed') and c.get('prompt_tokens')]
    counts = Counter(category(c['purpose']) for c in actual)
    tokens = {k: statistics.mean(c['prompt_tokens'] for c in actual if category(c['purpose']) == k) for k in counts}
    probes = {s['id']: read_json(directory / (s['id'].replace('/', '_') + '_probe.json')) for s in settings()}
    for model in {name.split('/')[0] for name in probes}:
        for result in read_json(directory / (model + '_pilot.json'))['results']:
            probes[result['setting']].update(load_sec=result['load_sec'], mode_supported=result['mode_supported'])

    def expected(probe):
        parts = defaultdict(list)
        for call in probe['calls']:
            extra_input = max(0, tokens[category(call['purpose'])] - call['prompt_tokens']) / probe['prompt_tps']
            parts[category(call['purpose'])].append(call['generation_sec'] + extra_input)
        return sum(statistics.mean(parts[k]) * counts[k] for k in counts) / sum(counts.values())

    base = expected(probes['qwen35-9b/off'])
    configurations = {}
    for name, probe in probes.items():
        # Round down and leave 10% service margin; qwen/off remains exactly 1x.
        rate = 1 if name == 'qwen35-9b/off' else max(.005, math.floor(min(1, base / expected(probe) * .9) * 1000) / 1000)
        configuration = {'rate': rate, 'generation_tps': probe['generation_tps'], 'prompt_tps': probe['prompt_tps'],
                         'load_sec': probe['load_sec'], 'expected_call_sec': expected(probe),
                         'supported': probe.get('mode_supported', True),
                         'expected_game_sec': round(750 / rate)}
        choice_calls = [c for c in probe['calls'] if c['purpose'] == 'scene']
        configuration['expected_scene_sec'] = round(60 * statistics.mean(c['generation_sec'] + max(0, tokens['choice']-c['prompt_tokens']) / probe['prompt_tps'] for c in choice_calls) * 1.3)
        configurations[name] = configuration
    save_json(destination, {'reference': str(game_checks), 'reference_public_messages': reference['public_messages'],
                            'reference_calls': len(reference['llm_calls']), 'weights': dict(counts),
                            'method': '実ゲームの判断・本文・選択の呼出比率で試運転の実測時間を重み付けし、入力長差を補正。10%の余裕。発言品質による破棄増は倍率で揃わないためREPORTで差を記す。',
                            'settings': configurations})
    return configurations
