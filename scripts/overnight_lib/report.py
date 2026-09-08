"""Read measured usage; no model calls and no subscription-to-dollar guesses."""
from pathlib import Path

from autodev_lib.engine import Campaign
from autodev_lib.policy import loads


def report(c):
    measurements = []; unit_measurements = {u['id']: [] for u in c.m['units']}
    retries = {u['id']: 0 for u in c.m['units']}
    children = [(i,r['child']) for i,r in enumerate(c.s['receipts'])]
    if c.s['child']: children.append((c.s['index'],c.s['child']))
    def add(unit, provider, model, usage, seconds=0):
        measurements.append((provider,model,usage))
        unit_measurements[unit].append((provider,model,usage,seconds))
    for key, call in c.s['calls'].items():
        path = c.path / 'calls' / key / 'raw.json'
        raw = c.raw(key) if path.exists() else {}
        unit = c.m['units'][int(key.split('-')[0][1:])]['id']
        retries[unit] = max(retries[unit],int(key.split('-')[1][1:])-1)
        add(unit,call['provider'],call['model'],raw.get('usage'),raw.get('seconds',0))
    child_calls = child_local_reserved = 0
    for index, child in children:
        unit=c.m['units'][index]['id']
        if not child['path']: continue
        if c.m['version'] == 2:
            cc = c.verify_child(child,index,(c.s['receipts'][index]['proposal'] if index<len(c.s['receipts']) else c.s['proposal']))
            root = c.artifact(child['root'])
            if (root/'final/intent.json').exists():
                child_calls += 1
                raw=loads((root/'final/raw.json').read_bytes()) if (root/'final/raw.json').exists() else {}
                config=c.m['providers']['reviewer']
                add(unit,config['provider'],config['model'],raw.get('usage'),raw.get('seconds',0))
            for item in cc.s.get('usage',[]): add(unit,'qwen','local-qwen',item.get('usage'),item.get('seconds',0))
            child_local_reserved += child['qwen_grant']
            if (root/'execution.json').exists(): retries[unit]+=max(0,loads((root/'execution.json').read_bytes())['launches']-1)
            retries[unit]+=max(0,cc.s.get('attempt',1)-1)
            continue
        cc = Campaign(c.artifact(child['path']))
        child_calls += cc.s['cloud_calls']; child_local_reserved += cc.s['local_calls']
        for step in cc.s['steps'].values(): add(unit,step['provider'],step['model'],step.get('usage'))
        jobs = [loads((cc.path / 'jobs' / (j + '.json')).read_bytes()) for j in cc.s['receipts']]
        jobs.append(cc.s['job'])
        for job in jobs:
            if job.get('run'):
                state = c.b['task_state'].read_state(Path(job['run']))
                for item in state.get('usage', []): add(unit,'qwen','local-qwen',item.get('usage'),item.get('seconds',0))
                retries[unit]+=max(0,state.get('attempt',1)-1)
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
    per_unit=[]
    boundaries=[c.s['started_at']]+[e['at'] for i,e in enumerate(c.s['events']) if i and e['phase']=='READY' and c.s['events'][i-1]['phase']=='RUNNING']
    for index, unit in enumerate(c.m['units']):
        rows=unit_measurements[unit['id']]
        def token_sum(local, output):
            values=[]
            for provider,_,usage,_ in rows:
                if (provider=='qwen') != local: continue
                usage=usage if isinstance(usage,dict) else {}
                value=usage.get('output_tokens',usage.get('completion_tokens')) if output else usage.get('input_tokens',usage.get('prompt_tokens'))
                if type(value)!=int or value<0:return None
                if provider=='claude' and not output:value+=sum(usage[k] for k in ('cache_creation_input_tokens','cache_read_input_tokens') if type(usage.get(k))==int)
                values.append(value)
            return sum(values)
        per_unit.append({'unit_id':unit['id'],'outcome':'completed' if index<completed else c.s['phase'] if index==completed else 'not_started',
                         'cloud_calls':sum(p!='qwen' for p,_,_,_ in rows),'qwen_calls':sum(p=='qwen' for p,_,_,_ in rows),
                         'cloud_input_tokens':token_sum(False,False),'cloud_output_tokens':token_sum(False,True),
                         'qwen_input_tokens':token_sum(True,False),'qwen_output_tokens':token_sum(True,True),
                         'model_seconds':sum(s for _,_,_,s in rows if type(s) in (int,float)), 'retries':retries[unit['id']],
                         'wall_seconds':max(0,(boundaries[index+1] if index+1<len(boundaries) else c.s['events'][-1]['at'])-boundaries[index]) if index<len(boundaries) else 0})
    return {'phase': c.s['phase'], 'completed_units': completed, 'total_units': len(c.m['units']),
            'upper_max_reserved': c.s['upper_reserved'], 'qwen_max_reserved': c.s['qwen_reserved'],
            'upper_allocation_drawn': sum(a['upper_spent'] for a in c.s['allocations'].values()),
            'qwen_allocation_drawn': sum(a['qwen_spent'] for a in c.s['allocations'].values()),
            'upper_dispatch_intents': sum(v['provider']!='qwen' for v in c.s['calls'].values()) + child_calls, 'child_local_reserved': child_local_reserved,
            'by_model': totals, 'known_total_tokens': total, 'unknown_token_fields': unknown,
            'tokens_per_completed_unit': total/completed if completed and not unknown else None,
            'units':per_unit, 'wall_seconds':max(0,c.s['events'][-1]['at']-c.s['started_at']),
            'llm_calls_for_report': 0, 'cost_basis': 'subscription usage only; reservations are not actual calls; no dollar estimate',
            'meaning': 'Scoped unit completion, not canonical game Phase approval.'}
