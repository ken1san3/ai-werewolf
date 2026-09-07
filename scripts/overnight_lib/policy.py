"""D059 authority and deterministic proposal construction. No model calls."""
from __future__ import annotations
import ast
import copy
import os
import re
import subprocess
from pathlib import Path

from autodev_lib.policy import (Error, ROOT, digest, exact, integer, number,
                                nonempty, loads, load_agent, paths)

ACTIVE = {'READY', 'PLANNING', 'REVIEWING', 'INSTALLING_TEST', 'RUNNING'}
PAUSED = {'PAUSED', 'PAUSED_QUOTA'}
TERMINAL = {'COMPLETE', 'NEEDS_DESIGN', 'BLOCKED', 'UNKNOWN_DELIVERY', 'LIMIT_REACHED', 'INVALID'}
PHASES = ACTIVE | PAUSED | TERMINAL
CONTRACT_KEYS = ('contract_version task_id repo_root goal risk read_list allow_edit allow_new protected '
                 'test_paths test_command extra_read_context limits context invariants acceptance required_tests design_gate checks')
PLAN_KEYS = 'action reason goal context test_code required_tests'
CALL_KEYS = 'status provider model packet_sha256 raw_sha256'
RAW_KEYS = 'exit_code error stdout stderr seconds result usage'
APPROVAL_KEYS = ('version role provider model unit_id attempt package_sha256 source_sha256 proposal_sha256 '
                 'test_sha256 packet_sha256 response_sha256 verdict reason')
STATE_KEYS = ('version run_id package_source source_sha256 package_sha256 started_at deadline last_observed_at phase '
              'resume_phase reason index attempt expected head calls allocations upper_reserved qwen_reserved proposal child receipts events')


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise Error('invalid SHA256')


def check_design():
    meta = loads((ROOT / 'Docs/ai/infra/overnight.json').read_bytes())
    exact(meta, 'design review design_sha256 review_sha256')
    texts = {}
    for key in ('design', 'review'):
        name = meta[key]
        if not isinstance(name, str) or '\\' in name or '..' in name.split('/') or Path(name).is_absolute():
            raise Error('invalid D059 approval path')
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT) or path.suffix != '.md':
            raise Error('D059 approval path escaped')
        sha(meta[key + '_sha256'])
        raw = path.read_bytes()
        if digest(raw) != meta[key + '_sha256']:
            raise Error('D059 approval bytes changed')
        texts[key] = raw.decode('utf-8')
    if (not re.fullmatch(r'Status: APPROVED(?:\s+—.*)?', texts['design'].splitlines()[0]) or
        not re.search(r'^Verdict: APPROVED\s*$', texts['review'], re.M) or
        'Reviewer / Sol' not in texts['review'] or meta['design'] not in texts['review'] or
        meta['design_sha256'] not in texts['review'].lower()):
        raise Error('D059 lacks independent approval')


def deny_write(name):
    p = name.casefold()
    return (p in {'agents.md', 'run-autodev.cmd', 'run-overnight.cmd', 'autodev.local.json', 'overnight.local.json'} or
            p.startswith(('scripts/', 'docs/ai/', '.git/', '.codex/', '.agents/', '.infra-runs/',
                          'tests/test_autodev', 'tests/test_overnight', 'tests/test_run_autodev')))


def allocation(m, unit):
    return {'upper_max': 2 * m['limits']['plan_attempts'] + 3,
            'qwen_max': unit['runner_launches'] * 2 * (unit['template']['limits']['max_fixes'] + 1),
            'upper_spent': 0, 'qwen_spent': 0}


def runtime_paths(m, runs_root):
    """Native Windows cwd and downstream pytest paths cannot rely on Python long-path support."""
    if os.name != 'nt': return
    root = Path(runs_root).resolve() / ('overnight-' + '0' * 32)
    candidates = [root / 'calls/u19-a3-reviewer']
    for i, unit in enumerate(m['units']):
        child = root / f'children/u{i}/runs' / ('campaign-' + '0' * 32)
        candidates.append(child / 'calls/j0-completion')
        candidate = child / 'runs' / (unit['template']['task_id'] + '-' + '0' * 12) / 'candidate-2-00000000'
        candidates.append(candidate)
        candidates += [candidate / n for n in inputs(unit) + unit['template']['allow_new'] + [unit['test_slot']]]
    if max(len(str(p)) for p in candidates) >= 250:
        raise Error('Windows runtime path too long before dispatch; use a shorter --runs-root/task_id (default: %USERPROFILE%/.aiwolf-runs)')


def inputs(unit):
    c = unit['template']
    result = c['read_list'] + c['allow_edit'] + c['extra_read_context'] + unit['after_reads']
    if c['design_gate']['decision'] == 'REQUIRED':
        result = result + [c['design_gate']['design']]
    return list(dict.fromkeys(result))


def validate(m, initial=True):
    exact(m, 'version issuer repo_root agent_root not_before expires_at limits providers quota_policy units')
    integer(m['version'], 1, 1); nonempty(m['issuer'])
    for key in ('not_before', 'expires_at'): number(m[key], 0, 1e12)
    if m['not_before'] >= m['expires_at']: raise Error('invalid authorization interval')
    repo = Path(m['repo_root']); agent = Path(m['agent_root'])
    if not repo.is_absolute() or not (repo / '.git').exists(): raise Error('absolute Git repo required')
    if repo.resolve().is_relative_to(agent.resolve()) or agent.resolve().is_relative_to(repo.resolve()):
        raise Error('work repo must be separate from AIagent')
    b = load_agent(agent); tc = b['task_contract']; tc.safe_path(repo, 'root-check')
    limits = m['limits']; exact(limits, 'seconds upper_calls qwen_calls plan_attempts packet_bytes output_bytes')
    for k, lo, hi in [('seconds', 1, 28800), ('upper_calls', 1, 100), ('qwen_calls', 1, 100),
                      ('plan_attempts', 1, 3), ('packet_bytes', 1024, 262144), ('output_bytes', 1024, 1048576)]:
        integer(limits[k], lo, hi)
    exact(m['providers'], 'planner reviewer')
    for config in m['providers'].values():
        exact(config, 'provider model executable cloud_read')
        if config['provider'] not in ('gpt', 'claude'): raise Error('invalid provider')
        if not isinstance(config['model'], str) or not re.fullmatch(
                ('gpt-' if config['provider'] == 'gpt' else 'claude-') + r'[a-z0-9][a-z0-9.-]*', config['model']):
            raise Error('explicit model ID required')
        exe = Path(config['executable'])
        if not exe.is_absolute() or not exe.is_file() or exe.suffix.lower() in ('.cmd', '.bat', '.ps1'):
            raise Error('native provider executable required')
        paths(config['cloud_read'], repo, b)
    planner, reviewer = (m['providers'][k] for k in ('planner', 'reviewer'))
    if planner['model'].casefold() == reviewer['model'].casefold(): raise Error('independent models required')
    if reviewer['model'] not in b['conf'].machine().get('task_runner', {}).get('upper_review_models', []):
        raise Error('reviewer model is not authorized by v1 upper_review_models')
    q = m['quota_policy']; exact(q, 'mode reserve_percent snapshot')
    if q['mode'] not in ('strict_reserve', 'bounded_calls'): raise Error('invalid quota mode')
    number(q['reserve_percent'], 0, 100)
    if q['snapshot'] is not None and (not isinstance(q['snapshot'], str) or not Path(q['snapshot']).is_absolute()):
        raise Error('absolute quota snapshot required')
    units = m['units']
    if not isinstance(units, list) or not 1 <= len(units) <= 20: raise Error('1..20 units required')
    ids = set(); produced = set(); created = set(); edit_names = set(); slots = set()
    for u in units:
        exact(u, 'id template test_slot after_reads runner_launches')
        if not isinstance(u['id'], str) or not re.fullmatch('[A-Za-z0-9_-]{1,60}', u['id']) or u['id'] in ids:
            raise Error('invalid/duplicate unit ID')
        ids.add(u['id']); integer(u['runner_launches'], 1, 3)
        c = u['template']; exact(c, CONTRACT_KEYS)
        if Path(c['repo_root']).resolve() != repo.resolve() or c['risk'] != 'red': raise Error('unit repo/risk mismatch')
        if initial:
            tc.validate_contract(c)
        else:
            # The immutable original package was fully validated at creation. Recheck its
            # structure and safe paths without requiring already-applied new files absent.
            for k in ('read_list', 'allow_edit', 'allow_new', 'test_paths', 'extra_read_context'):
                paths(c[k], repo, b)
            exact(c['limits'], 'max_file_bytes max_total_edit_bytes max_model_output_bytes max_fixes run_timeout_s max_tokens')
            integer(c['limits']['max_fixes'], 0, 2)
        paths(u['after_reads'], repo, b)
        if any(p.casefold() not in produced for p in u['after_reads']): raise Error('after_reads requires earlier creator')
        slot = u['test_slot']; p = tc.safe_path(repo, slot)
        if (p.suffix != '.py' or p.name.casefold() in ('__init__.py', 'conftest.py') or deny_write(slot) or
            not any((repo / t).is_dir() and slot.casefold().startswith(t.casefold() + '/') for t in c['test_paths'])):
            raise Error('invalid allocated test slot')
        if initial and p.exists(): raise Error('test slot already exists')
        for name in c['allow_edit'] + c['allow_new']:
            tc.safe_path(repo, name)
            if deny_write(name) or tc.protected(name, c['protected']) or tc.in_tests(name, c):
                raise Error('infrastructure/protected write forbidden: ' + name)
            edit_names.add(name.casefold())
        for name in c['allow_new'] + [slot]:
            if name.casefold() in created: raise Error('multiple creators')
            created.add(name.casefold())
        slots.add(slot.casefold()); produced.update(p.casefold() for p in c['allow_new'] + [slot])
    if edit_names & slots: raise Error('test/source write overlap')
    all_new = {p.casefold() for u in units for p in u['template']['allow_new']}
    all_edit = {p.casefold() for u in units for p in u['template']['allow_edit']}
    if all_new & all_edit: raise Error('new targets cannot also be initial edit targets')
    alloc = [allocation(m, u) for u in units]
    if sum(a['upper_max'] for a in alloc) > limits['upper_calls'] or sum(a['qwen_max'] for a in alloc) > limits['qwen_calls']:
        raise Error('whole-package reservation exceeds call limits')
    return b


def test_files(m, b):
    repo = Path(m['repo_root']); found = set()
    for u in m['units']:
        for name in u['template']['test_paths']:
            p = b['task_contract'].safe_path(repo, name)
            if p.is_file(): found.add(name)
            elif p.is_dir():
                for root, dirs, files in os.walk(p, followlinks=False):
                    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.pytest_cache')]
                    for d in dirs: b['task_contract'].safe_path(repo, (Path(root) / d).relative_to(repo).as_posix())
                    for f in files:
                        rel = (Path(root) / f).relative_to(repo).as_posix()
                        b['task_contract'].safe_path(repo, rel); found.add(rel)
                        if len(found) > 2000: raise Error('source file count limit')
            else: raise Error('missing test path')
    return found


def universe(m, b):
    names = test_files(m, b)
    for u in m['units']:
        names.update(inputs(u) + u['template']['allow_new'] + [u['test_slot']])
    if len(names) > 2000 or len({n.casefold() for n in names}) != len(names): raise Error('invalid source universe')
    result = {}; total = 0; repo = Path(m['repo_root'])
    for n in sorted(names):
        p = b['task_contract'].safe_path(repo, n)
        if not p.exists(): result[n] = None; continue
        size = p.stat().st_size; total += size
        if not p.is_file() or size > 8 * 1024**2 or total > 64 * 1024**2: raise Error('source size limit')
        result[n] = digest(p.read_bytes())
    return result


def head(repo):
    raw = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, timeout=10, check=True)
    value = raw.stdout.decode().strip()
    if not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', value): raise Error('invalid Git HEAD')
    return value


def plan_response(value, slot, cap):
    exact(value, PLAN_KEYS); nonempty(value['reason'])
    if value['action'] not in ('IMPLEMENT', 'NEEDS_DESIGN'): raise Error('invalid planner action')
    for k in ('goal', 'context', 'test_code'):
        if not isinstance(value[k], str): raise Error('invalid planner text')
    nodes = value['required_tests']
    if not isinstance(nodes, list): raise Error('nodeid list required')
    if value['action'] == 'NEEDS_DESIGN':
        if any(value[k] for k in ('goal', 'context', 'test_code')) or nodes: raise Error('NEEDS_DESIGN must not propose implementation')
        return
    for k in ('goal', 'context', 'test_code'): nonempty(value[k])
    if len(value['test_code'].encode('utf-8')) >= cap: raise Error('generated test exceeds output limit')
    ast.parse(value['test_code'], filename=slot)
    if (not 1 <= len(nodes) <= 100 or any(not isinstance(n, str) or not n.startswith(slot + '::') or
        not n[len(slot) + 2:] or any(c.isspace() for c in n) for n in nodes) or len(set(nodes)) != len(nodes)):
        raise Error('generated tests must be exact allocated nodeids')


def proposal(m, index, response, receipts):
    unit = m['units'][index]; plan_response(response, unit['test_slot'], m['limits']['output_bytes'])
    if response['action'] != 'IMPLEMENT': raise Error('no implementation proposed')
    c = copy.deepcopy(unit['template']); c['repo_root'] = str(Path(c['repo_root']).resolve())
    for k in ('goal', 'context'): c[k] += '\n\nRuntime clarification:\n' + response[k]
    c['read_list'] += unit['after_reads'] + [unit['test_slot']]
    for i, receipt in enumerate(receipts):
        prior = m['units'][i]
        c['read_list'] += prior['template']['allow_new'] + [prior['test_slot']]
        c['test_paths'] += receipt['proposal']['packet']['contract']['test_paths'] + [prior['test_slot']]
        c['required_tests'] += receipt['proposal']['packet']['contract']['required_tests']
    c['read_list'] = list(dict.fromkeys(c['read_list']))
    c['test_paths'] = list(dict.fromkeys(c['test_paths']))
    c['required_tests'] = list(dict.fromkeys(c['required_tests'] + response['required_tests']))
    value = {'response': copy.deepcopy(response), 'contract': c, 'test_sha256': digest(response['test_code'].encode('utf-8'))}
    return {**value, 'proposal_sha256': digest(value)}
