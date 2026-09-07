"""Read measured usage; no model calls and no subscription-to-dollar guesses."""
from pathlib import Path

from autodev_lib.engine import Campaign
from autodev_lib.policy import loads


def report(c):
    measurements = []; children = [r['child'] for r in c.s['receipts']]
    if c.s['child']: children.append(c.s['child'])
    for key, call in c.s['calls'].items():
        path = c.path / 'calls' / key / 'raw.json'
        usage = c.raw(key)['usage'] if path.exists() else None
        measurements.append((call['provider'], call['model'], usage))
    child_calls = child_local_reserved = 0
    for child in children:
        if not child['path']: continue
        cc = Campaign(c.artifact(child['path']))
        child_calls += cc.s['cloud_calls']; child_local_reserved += cc.s['local_calls']
        measurements += [(step['provider'], step['model'], step.get('usage')) for step in cc.s['steps'].values()]
        jobs = [loads((cc.path / 'jobs' / (j + '.json')).read_bytes()) for j in cc.s['receipts']]
        jobs.append(cc.s['job'])
        for job in jobs:
            if job.get('run'):
                state = c.b['task_state'].read_state(Path(job['run']))
                measurements += [('qwen', 'local-qwen', item.get('usage')) for item in state.get('usage', [])]
    totals = {}
    for provider, model, usage in measurements:
        row = totals.setdefault(model, {'provider': provider, 'calls_observed': 0, 'known_input_tokens': 0, 'known_output_tokens': 0, 'unknown_token_fields': 0})
        row['calls_observed'] += 1; usage = usage if isinstance(usage, dict) else {}
        inp = usage.get('input_tokens', usage.get('prompt_tokens')); out = usage.get('output_tokens', usage.get('completion_tokens'))
        if provider == 'claude' and type(inp) is int:
            inp += sum(usage[k] for k in ('cache_creation_input_tokens', 'cache_read_input_tokens') if type(usage.get(k)) is int)
        for field, value in [('known_input_tokens', inp), ('known_output_tokens', out)]:
            if type(value) is int and value >= 0: row[field] += value
            else: row['unknown_token_fields'] += 1
    total = sum(r['known_input_tokens'] + r['known_output_tokens'] for r in totals.values())
    unknown = sum(r['unknown_token_fields'] for r in totals.values()); completed = c.s['index']
    return {'phase': c.s['phase'], 'completed_units': completed, 'total_units': len(c.m['units']),
            'upper_max_reserved': c.s['upper_reserved'], 'qwen_max_reserved': c.s['qwen_reserved'],
            'upper_allocation_drawn': sum(a['upper_spent'] for a in c.s['allocations'].values()),
            'qwen_allocation_drawn': sum(a['qwen_spent'] for a in c.s['allocations'].values()),
            'upper_dispatch_intents': len(c.s['calls']) + child_calls, 'child_local_reserved': child_local_reserved,
            'by_model': totals, 'known_total_tokens': total, 'unknown_token_fields': unknown,
            'tokens_per_completed_unit': total/completed if completed and not unknown else None,
            'llm_calls_for_report': 0, 'cost_basis': 'subscription usage only; reservations are not actual calls; no dollar estimate',
            'meaning': 'Scoped unit completion, not canonical game Phase approval.'}
