"""Trusted manifest validation and runtime integration; no model calls."""
from __future__ import annotations
import hashlib
import importlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Error(ValueError):
    pass


def loads(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise Error('duplicate JSON key: '+key)
            result[key]=value
        return result
    def constant(value):raise Error('non-finite JSON: '+value)
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
    if not isinstance(value,dict):raise Error('JSON object required')
    return value


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise Error('invalid fields: expected ' + keys)


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise Error('number outside allowed range')


def integer(value, low, high):
    number(value, low, high)
    if type(value) is not int:
        raise Error('integer required')


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise Error('nonempty text required')


def load_agent(path):
    path = Path(path)
    if not path.is_absolute() or not (path / 'lib/task_runner.py').is_file():
        raise Error('AIagent v1 installation unavailable')
    sys.path.insert(0, str(path / 'lib'))
    modules = {name: importlib.import_module(name) for name in
               ('task_contract', 'task_state', 'task_source', 'task_metrics', 'llm', 'conf')}
    for module in modules.values():
        if not Path(module.__file__).resolve().is_relative_to(path.resolve()):
            raise Error('different AIagent installation already loaded')
    return modules


def paths(value, repo, bridge):
    if not isinstance(value, list) or len(value) > 200 or len(set(value)) != len(value):
        raise Error('invalid path list')
    if len({p.casefold() for p in value}) != len(value):
        raise Error('case-duplicate paths')
    for name in value:
        bridge['task_contract'].safe_path(repo, name)


def validate(m, initial=True):
    exact(m, 'version issuer repo_root agent_root not_before expires_at max_seconds max_cloud_calls max_local_calls max_attempts max_packet_bytes max_output_bytes providers quota_policy jobs apply')
    integer(m['version'], 1, 1); nonempty(m['issuer'])
    for key, high in [('max_seconds',86400), ('max_cloud_calls',100), ('max_local_calls',100),
                      ('max_attempts',3), ('max_packet_bytes',262144), ('max_output_bytes',1048576)]:
        integer(m[key], 1, high)
    for key in ('not_before', 'expires_at'): number(m[key], 0, 1e12)
    if m['not_before'] >= m['expires_at'] or type(m['apply']) is not bool:
        raise Error('invalid authorization interval/apply')
    repo = Path(m['repo_root'])
    if not repo.is_absolute() or not (repo / '.git').exists(): raise Error('absolute git repo required')
    b = load_agent(m['agent_root'])
    b['task_contract'].safe_path(repo.parent, repo.name)
    providers = m['providers']
    if not isinstance(providers, dict) or not providers or set(providers) - {'gpt','claude'}:
        raise Error('invalid providers')
    for name,p in providers.items():
        exact(p, 'model executable cloud_read')
        if not isinstance(p['model'],str) or not p['model'].startswith('gpt-' if name=='gpt' else 'claude-'):
            raise Error('explicit model ID required')
        exe=Path(p['executable'])
        if not exe.is_absolute() or not exe.is_file() or exe.suffix.lower() in ('.cmd','.bat','.ps1'):
            raise Error('native CLI executable required')
        paths(p['cloud_read'],repo,b)
    q=m['quota_policy'];exact(q,'mode reserve_percent snapshot')
    if q['mode'] not in ('bounded_calls','strict_reserve'):raise Error('invalid quota mode')
    number(q['reserve_percent'],0,100)
    if q['snapshot'] is not None and not Path(q['snapshot']).is_absolute():raise Error('absolute quota path required')
    if not isinstance(m['jobs'],list) or not 1<=len(m['jobs'])<=20:raise Error('1..20 jobs required')
    ids=[]
    for j in m['jobs']:
        kind=j.get('kind') if isinstance(j,dict) else None
        if kind not in ('task','review'):raise Error('invalid job kind')
        exact(j,'id kind instruction read_list reviewer author_model'+(' template draft' if kind=='task' else ''))
        if not isinstance(j['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',j['id']):raise Error('invalid job ID')
        ids.append(j['id']); nonempty(j['instruction']);nonempty(j['author_model'])
        if j['author_model']!='local-qwen' and not re.fullmatch(r'(gpt|claude)-[a-z0-9][a-z0-9.-]*',j['author_model'],re.I):raise Error('explicit author model ID required')
        paths(j['read_list'],repo,b)
        if j['reviewer'] not in (*providers,'auto'):raise Error('reviewer not authorized')
        if kind=='task':
            if type(j['draft']) is not bool or j['author_model']!='local-qwen':raise Error('invalid task draft/author')
            if Path(j['template']['repo_root']).resolve()!=repo.resolve():raise Error('task repo mismatch')
            if initial:b['task_contract'].validate_contract(j['template'])
    if len(set(ids))!=len(ids):raise Error('duplicate jobs')
    return b


def input_paths(job):
    names=list(job['read_list'])
    if job['kind']=='task':
        c=job['template']
        names+=c['read_list']+c['allow_edit']+c['extra_read_context']
        if c['design_gate']['decision']=='REQUIRED':names.append(c['design_gate']['design'])
    return list(dict.fromkeys(names))


def check_design():
    meta=loads((ROOT/'Docs/ai/infra/autodev.json').read_text(encoding='utf-8'))
    for name in ('design','review'):
        p=ROOT/meta[name]
        if not p.resolve().is_relative_to(ROOT) or digest(p.read_bytes())!=meta[name+'_sha256']:
            raise Error('autodev design approval hash changed')
    design=(ROOT/meta['design']).read_text(encoding='utf-8')
    review=(ROOT/meta['review']).read_text(encoding='utf-8')
    if (not re.search(r'^Verdict: APPROVED\s*$',review,re.M) or meta['design_sha256'].lower() not in review.lower()
        or meta['design'] not in review or 'Reviewer / Sol' not in review
        or not re.match(r'Status: APPROVED(?:\s+—|\s*$)',design)):
        raise Error('autodev design unapproved')
