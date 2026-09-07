"""Durable D059 parent; models cannot choose tools, write paths, or budgets."""
from __future__ import annotations
import copy
import json
import math
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from autodev_lib.engine import Campaign, Pause
from autodev_lib.policy import check_design as check_child_design
from . import provider
from .policy import (Error, ACTIVE, PAUSED, TERMINAL, PHASES, digest, loads, validate, check_design,
                     allocation, universe, head, proposal, test_files, runtime_paths)
from .evidence import Evidence


class Halt(Error):
    def __init__(self, phase, reason): self.phase = phase; super().__init__(reason)


class LinkedCampaign(Campaign):
    def __init__(self, directory, parent, **hooks):
        self.parent = parent
        super().__init__(directory, **hooks)

    def remaining(self): return min(super().remaining(), self.parent.remaining())

    def guard(self):
        super().guard()
        try: self.parent.temporal()
        except Halt as e: raise Pause('PAUSED' if e.phase == 'PAUSED' else 'NEEDS_USER', str(e)) from e


class Controller(Evidence):
    def __init__(self, directory, cloud=None, child_cloud=None, runner=None, ready=None):
        self.path = Path(directory).resolve(strict=True)
        self.m = self.read('package.json'); self.b = validate(self.m, initial=False)
        self.repo = Path(self.m['repo_root']).resolve()
        self.b['task_contract'].safe_path(self.path.parent, self.path.name)
        if self.path.is_relative_to(self.repo) or self.path.is_relative_to(Path(self.m['agent_root']).resolve()):
            raise Error('run must be outside repo and AIagent')
        self.s = self.read('state.json')
        self.cloud = cloud or provider.invoke
        self.child_hooks = {k: v for k, v in {'cloud': child_cloud, 'runner': runner}.items() if v is not None}
        self.ready = ready or (lambda: self.b['llm'].alive() and self.b['llm'].tokenizer_available())
        self.mono_deadline = time.monotonic() + max(0, self.s['deadline'] - time.time())
        self.verify_state()

    def artifact(self, name):
        # Bootstrap package.json is read before the installed v1 bridge is loaded.
        if hasattr(self, 'b'): return super().artifact(name)
        if name != 'package.json': raise Error('invalid bootstrap artifact')
        p = self.path / name
        if p.is_symlink() or p.resolve().parent != self.path: raise Error('package artifact escaped')
        return p

    @staticmethod
    def lock_path(m):
        key = digest(str(Path(m['repo_root']).resolve()).casefold().encode())
        return Path(m['agent_root']) / '.task-locks' / ('autodev-repo-' + key + '.lock')

    @classmethod
    def start(cls, source, runs_root, _held_repo=None, **hooks):
        check_design(); check_child_design()
        source = Path(source).resolve(strict=True); raw = source.read_bytes(); m = loads(raw); b = validate(m)
        repo = Path(m['repo_root']).resolve(); agent = Path(m['agent_root']).resolve()
        root = Path(runs_root).absolute()
        runtime_paths(m, root)
        if source.is_relative_to(agent) or root.resolve().is_relative_to(repo) or root.resolve().is_relative_to(agent):
            raise Error('package/run location not authorized')
        b['task_contract'].safe_path(root.parent, root.name)
        if _held_repo is not None and _held_repo != repo: raise Error('held repo mismatch')
        with (nullcontext() if _held_repo is not None else b['task_state'].lock(cls.lock_path(m))):
            now = time.time()
            if not m['not_before'] <= now < m['expires_at']: raise Error('package outside authorization interval')
            initial = {'head': head(repo), 'expected': universe(m, b)}
            path = root / ('overnight-' + uuid.uuid4().hex); path.mkdir(parents=True)
            path = path.resolve(strict=True)
            write = b['task_state'].write_json
            write(path / 'package.json', m); write(path / 'source.json', initial)
            allocations = {u['id']: allocation(m, u) for u in m['units']}
            s = {'version': 1, 'run_id': path.name, 'package_source': str(source), 'source_sha256': digest(raw),
                 'package_sha256': digest(m), 'started_at': now, 'deadline': min(m['expires_at'], now+m['limits']['seconds']),
                 'last_observed_at': now, 'phase': 'READY', 'resume_phase': None, 'reason': 'created', 'index': 0, 'attempt': 1,
                 'expected': initial['expected'], 'head': initial['head'], 'calls': {}, 'allocations': allocations,
                 'upper_reserved': sum(a['upper_max'] for a in allocations.values()),
                 'qwen_reserved': sum(a['qwen_max'] for a in allocations.values()), 'proposal': None, 'child': None, 'receipts': [],
                 'events': [{'index': 0, 'phase': 'READY', 'at': now, 'evidence': digest((path / 'source.json').read_bytes())}]}
            write(path / 'state.json', s)
            return cls(path, **hooks)

    @classmethod
    def launch(cls, source, runs_root, on_created=None, **hooks):
        m = loads(Path(source).read_bytes()); b = validate(m); repo = Path(m['repo_root']).resolve()
        with b['task_state'].lock(cls.lock_path(m)):
            c = cls.start(source, runs_root, _held_repo=repo, **hooks)
            print(json.dumps({'run': str(c.path)}, ensure_ascii=False), flush=True)
            if on_created: on_created(c.path)
            c.drive(_held_repo=repo)
            return c

    def write(self): self.b['task_state'].write_json(self.path / 'state.json', self.s)

    def mark(self, phase=None, reason=None):
        phase = phase or self.s['phase']
        if phase not in PHASES: raise Error('unknown phase')
        if len(self.s['events']) >= 2000: raise Error('event limit')
        self.s['phase'] = phase
        if reason is not None: self.s['reason'] = reason
        now = max(time.time(), self.s['events'][-1]['at'])
        self.s['events'].append({'index': len(self.s['events']), 'phase': phase, 'at': now, 'evidence': None})
        self.write()

    def remaining(self): return min(300, self.s['deadline']-time.time(), self.mono_deadline-time.monotonic())

    def temporal(self):
        now = time.time()
        if now < self.s['last_observed_at']-1: raise Halt('INVALID', 'wall clock moved backwards')
        if digest(Path(self.s['package_source']).read_bytes()) != self.s['source_sha256']:
            raise Halt('INVALID', 'original package changed/revoked')
        if (self.path / 'stop').exists(): raise Halt('PAUSED', 'stop requested; explicit --clear-stop required')
        if now < self.m['not_before'] or self.remaining() <= 0: raise Halt('LIMIT_REACHED', 'fixed deadline reached')
        self.s['last_observed_at'] = max(now, self.s['last_observed_at']); self.write()

    def source_guard(self):
        if head(self.repo) != self.s['head']: raise Halt('INVALID', 'Git HEAD changed')
        expected = dict(self.s['expected']); partial = {}
        active = self.s['resume_phase'] if self.s['phase'] in PAUSED else self.s['phase']
        if active == 'RUNNING' and self.s['child'] is not None and self.s['child']['path']:
            i = self.s['index']; c = self.verify_child(self.s['child'], i, self.s['proposal'])
            child_phase = c.s['resume_phase'] if c.s['phase'] in PAUSED else c.s['phase']
            if c.s['phase'] == 'COMPLETE':
                expected.update(self.child_outputs(c, i, self.s['proposal']))
            elif child_phase in ('APPLYING', 'COMPLETION_REVIEW', 'JOB_DONE') and c.s['job'].get('run'):
                rs = c.runner_state(); actual_contract = c.approved_contract(c.m['jobs'][0])
                fixed = copy.deepcopy(self.s['proposal']['packet']['contract'])
                fixed['limits']['run_timeout_s'] = actual_contract['limits']['run_timeout_s']
                if actual_contract != fixed: raise Halt('INVALID', 'partial child contract changed')
                if rs['phase'] not in ('APPLYING', 'APPLIED') or not rs.get('journal'):
                    raise Halt('INVALID', 'partial apply lacks journal')
                if self.b['task_source'].manifest(Path(c.s['job']['run']) / rs['candidate']) != rs['candidate_manifest']:
                    raise Halt('INVALID', 'partial candidate changed')
                required = set(actual_contract['read_list'] + actual_contract['allow_edit'] + actual_contract['allow_new'] + actual_contract['extra_read_context'])
                if any(rs['source_manifest'].get(n) != h for n, h in self.s['expected'].items() if n in rs['source_manifest'] or n in required):
                    raise Halt('INVALID', 'v1 source differs from parent baseline')
                allowed = set(fixed['allow_edit'] + fixed['allow_new'])
                for n, entry in rs['journal'].items():
                    if n not in allowed or entry['before'] != self.s['expected'][n] or entry['after'] != rs['candidate_manifest'].get(n):
                        raise Halt('INVALID', 'partial journal authority changed')
                    if child_phase == 'APPLYING': partial[n] = (entry['before'], entry['after'])
                    elif rs['phase'] == 'APPLIED': expected[n] = entry['after']
                    else: raise Halt('INVALID', 'completion before applied transaction')
        allowed_tests = {n for n, h in expected.items() if h is not None}
        p = self.s['proposal']
        if p and p['approval']:
            slot = self.m['units'][self.s['index']]['test_slot']; allowed_tests.add(slot)
        if test_files(self.m, self.b) - allowed_tests: raise Halt('INVALID', 'unexpected test file appeared')
        for n, wanted in expected.items():
            path = self.b['task_contract'].safe_path(self.repo, n)
            if path.exists() and (not path.is_file() or path.stat().st_size > 8*1024**2): raise Halt('INVALID', 'source type/size changed')
            actual = digest(path.read_bytes()) if path.exists() else None
            if n in partial and actual in partial[n]: continue
            if actual == wanted: continue
            if (active == 'INSTALLING_TEST' and p and p['approval'] and n == self.m['units'][self.s['index']]['test_slot']
                    and wanted is None and actual == p['packet']['test_sha256']): continue
            raise Halt('INVALID', 'source changed: ' + n)

    def files(self):
        self.source_guard(); result = {}
        for name in self.packet_names(self.s['index']):
            p = self.b['task_contract'].safe_path(self.repo, name)
            if p.stat().st_size > self.m['limits']['packet_bytes']: raise Halt('BLOCKED', 'packet source exceeds size cap')
            result[name] = p.read_bytes().decode('utf-8')
        self.source_guard()
        return result

    def call(self, stage):
        s = self.s; i = s['index']; attempt = s['attempt']; key = f'u{i}-a{attempt}-{stage}'
        self.temporal(); files = self.files()
        packet = self.packet(i, attempt, stage, files, s['proposal']['packet'] if stage == 'reviewer' else None)
        data = json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(data) > self.m['limits']['packet_bytes']: raise Halt('BLOCKED', 'packet exceeds cap; no truncation')
        config = self.m['providers'][stage]
        if (set(files) | {self.m['units'][i]['test_slot']}) - set(config['cloud_read']):
            raise Halt('BLOCKED', 'cloud_read does not authorize this complete packet')
        step = s['calls'].get(key); directory = self.artifact('calls/' + key)
        if step is None:
            if not self.ready(): raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable; no cloud dispatch')
            if not provider.quota(self.m, config, self.b): raise Halt('PAUSED_QUOTA', 'provider quota missing/exhausted under policy')
            a = s['allocations'][self.m['units'][i]['id']]
            if a['upper_spent'] + 1 > a['upper_max']: raise Halt('LIMIT_REACHED', 'unit allocation exhausted')
            self.b['task_state'].atomic_bytes(directory / 'packet.json', data)
            step = {'status': 'INTENT', 'provider': config['provider'], 'model': config['model'], 'packet_sha256': digest(data), 'raw_sha256': None}
            a['upper_spent'] += 1; s['calls'][key] = step; self.mark()
            try:
                raw = self.cloud(config['provider'], config, packet, directory, self.remaining(), self.m['limits']['output_bytes'])
            except Exception as error: raise Halt('UNKNOWN_DELIVERY', 'provider returned no durable response: ' + str(error)) from error
            if raw.get('error') is None and raw.get('exit_code') == 0:
                try: self.response(stage, raw.get('result'), i)
                except (ValueError, SyntaxError, TypeError) as error:
                    raw = {**raw, 'error': 'invalid structured response: ' + str(error)}
            self.b['task_state'].write_json(directory / 'raw.json', raw)
        elif step['packet_sha256'] != digest(data): raise Halt('INVALID', 'saved call packet changed')
        if not (directory / 'raw.json').exists(): raise Halt('UNKNOWN_DELIVERY', 'INTENT without durable raw; will not resend')
        if step['raw_sha256'] is not None and digest((directory / 'raw.json').read_bytes()) != step['raw_sha256']:
            raise Halt('INVALID', 'raw response changed')
        raw = self.raw(key)
        ok = raw['error'] is None and raw['exit_code'] == 0
        # Invalid structured output is a known failed call, with its spent reservation retained.
        if ok: self.response(stage, raw['result'], i)
        step.update(status='DONE' if ok else 'FAILED', raw_sha256=digest((directory / 'raw.json').read_bytes())); self.mark()
        if not ok: raise Halt('BLOCKED', 'provider failure: ' + str(raw['error'] or raw['stderr'])[-500:])
        self.temporal(); self.source_guard()
        return raw['result'], step

    def approve(self, result, step):
        p = self.s['proposal']; config = self.m['providers']['reviewer']; i = self.s['index']
        p['approval'] = {'version': 1, 'role': 'Reviewer', 'provider': config['provider'], 'model': config['model'],
                         'unit_id': self.m['units'][i]['id'], 'attempt': self.s['attempt'], 'package_sha256': self.s['package_sha256'],
                         'source_sha256': digest(self.source_before(i)), 'proposal_sha256': p['packet']['proposal_sha256'],
                         'test_sha256': p['packet']['test_sha256'], 'packet_sha256': step['packet_sha256'],
                         'response_sha256': step['raw_sha256'], **result}

    def install(self):
        self.temporal(); self.verify_proposal(self.s['proposal'], self.s['index'], True); self.source_guard()
        unit = self.m['units'][self.s['index']]; p = self.s['proposal']['packet']
        path = self.b['task_contract'].safe_path(self.repo, unit['test_slot']); data = p['response']['test_code'].encode('utf-8')
        if path.exists():
            if digest(path.read_bytes()) != p['test_sha256']: raise Halt('INVALID', 'allocated test slot is occupied')
        else:
            # The shared lock excludes other controllers; recheck the slot immediately before install.
            self.source_guard()
            self.b['task_state'].atomic_bytes(path, data)
        self.s['expected'][unit['test_slot']] = p['test_sha256']; self.mark('RUNNING', 'independently approved test installed')

    def child_manifest(self, index, p):
        u = self.m['units'][index]; c = self.m['providers']['reviewer']; limits = self.m['limits']
        template = p['packet']['contract']
        return {'version': 1, 'issuer': self.m['issuer'] + ' / D059 ' + self.s['run_id'],
                'repo_root': str(self.repo), 'agent_root': self.m['agent_root'], 'not_before': self.m['not_before'],
                'expires_at': self.s['deadline'], 'max_seconds': max(1, math.floor(self.s['deadline']-self.s['started_at'])),
                'max_cloud_calls': 3, 'max_local_calls': allocation(self.m, u)['qwen_max'], 'max_attempts': 1,
                'max_packet_bytes': limits['packet_bytes'], 'max_output_bytes': limits['output_bytes'],
                'providers': {c['provider']: {k: c[k] for k in ('model', 'executable', 'cloud_read')}},
                'quota_policy': copy.deepcopy(self.m['quota_policy']), 'apply': True,
                'jobs': [{'id': u['id'], 'kind': 'task', 'instruction': 'Implement and review this exact approved D059 unit: ' + u['id'],
                          'read_list': list(dict.fromkeys(template['read_list'] + self.packet_names(index))),
                          'reviewer': c['provider'], 'author_model': 'local-qwen', 'template': copy.deepcopy(template), 'draft': False}]}

    def linked_child(self):
        i = self.s['index']; p = self.s['proposal']; unit = self.m['units'][i]
        if self.s['child'] is None:
            self.temporal(); self.source_guard()
            root = f'children/u{i}'; source = root + '/source.json'; m = self.child_manifest(i, p)
            path = self.artifact(source)
            if path.exists():
                if self.read(source) != m: raise Halt('INVALID', 'orphan child source changed')
            else: self.b['task_state'].write_json(path, m)
            a = self.s['allocations'][unit['id']]; grant = a['qwen_max']
            if a['upper_spent'] + 3 > a['upper_max'] or a['qwen_spent'] + grant > a['qwen_max']:
                raise Halt('LIMIT_REACHED', 'child grant exceeds allocation')
            a['upper_spent'] += 3; a['qwen_spent'] += grant
            self.s['child'] = {'root': root, 'source': source, 'manifest_sha256': digest(m), 'path': None, 'upper_grant': 3, 'qwen_grant': grant}
            self.mark()
        child = self.s['child']; self.verify_child(child, i, p)
        if child['path'] is None:
            root = self.artifact(child['root'] + '/runs')
            found = list(root.iterdir()) if root.exists() else []
            if len(found) > 1: raise Halt('INVALID', 'multiple/unknown children; refusing replacement')
            if found:
                c = LinkedCampaign(found[0], parent=self, **self.child_hooks)
            else:
                self.temporal()
                c = LinkedCampaign.start(self.artifact(child['source']), root, _held_repo=self.repo, parent=self, **self.child_hooks)
            child['path'] = c.path.resolve(strict=True).relative_to(self.path).as_posix(); self.mark()
            self.verify_child(child, i, p)
        else: c = LinkedCampaign(self.artifact(child['path']), parent=self, **self.child_hooks)
        if (self.path / 'stop').exists(): self.b['task_state'].atomic_bytes(c.path / 'stop', b'parent stop\n')
        return c

    def run_child(self):
        if not self.ready(): raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable before child; no cloud dispatch')
        c = self.linked_child()
        if not self.ready(): raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable before child drive; no cloud dispatch')
        c.drive(_held_repo=self.repo)
        self.temporal()
        if c.s['phase'] in PAUSED: raise Halt(c.s['phase'], c.s['reason'])
        if c.s['phase'] == 'UNKNOWN_DELIVERY': raise Halt('UNKNOWN_DELIVERY', c.s['reason'])
        if c.s['phase'] != 'COMPLETE': raise Halt('BLOCKED', 'child ' + c.s['phase'] + ': ' + c.s['reason'])
        outputs = self.child_outputs(c, self.s['index'], self.s['proposal'])
        # Confirm the complete expected tree before atomically accepting candidate outputs.
        old = self.s['expected']; self.s['expected'] = {**old, **outputs}
        child = self.s['child']; self.s['child'] = None
        try: self.source_guard()
        finally: self.s['child'] = child; self.s['expected'] = old
        self.s['receipts'].append({'unit_id': self.m['units'][self.s['index']]['id'],
                                  'proposal': copy.deepcopy(self.s['proposal']), 'child': copy.deepcopy(child),
                                  'outputs': outputs, 'child_state_sha256': digest((c.path / 'state.json').read_bytes())})
        self.s['expected'].update(outputs); self.s['index'] += 1; self.s['attempt'] = 1
        self.s['proposal'] = None; self.s['child'] = None
        self.mark('READY', 'unit completed with independent review')

    def drive(self, _held_repo=None, clear_stop=False):
        check_design(); check_child_design()
        runtime_paths(self.m, self.path.parent)
        if _held_repo is not None and _held_repo != self.repo: raise Error('held repo mismatch')
        with (nullcontext() if _held_repo is not None else self.b['task_state'].lock(self.lock_path(self.m))):
            with self.b['task_state'].lock(self.path / 'lock'):
                self.s = self.read('state.json'); self.verify_state()
                if self.s['phase'] in TERMINAL: return self.s
                try:
                    if clear_stop: self.request_stop(clear=True)
                    self.temporal(); self.source_guard()
                    if self.s['phase'] in PAUSED:
                        previous = self.s['resume_phase']
                        if previous not in ACTIVE: raise Error('invalid resume phase')
                        self.s['resume_phase'] = None; self.mark(previous, 'resumed same run')
                    while self.s['phase'] not in TERMINAL:
                        self.temporal(); self.source_guard(); phase = self.s['phase']
                        if phase == 'READY':
                            if self.s['index'] == len(self.m['units']): self.mark('COMPLETE', 'all authorized units complete'); continue
                            if not self.ready(): raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable; no cloud dispatch')
                            self.mark('PLANNING'); continue
                        if phase == 'PLANNING':
                            value, _ = self.call('planner')
                            if value['action'] == 'NEEDS_DESIGN': raise Halt('NEEDS_DESIGN', value['reason'])
                            self.s['proposal'] = {'attempt': self.s['attempt'], 'packet': proposal(self.m, self.s['index'], value, self.s['receipts']), 'approval': None}
                            self.mark('REVIEWING'); continue
                        if phase == 'REVIEWING':
                            value, step = self.call('reviewer')
                            if value['verdict'] == 'APPROVED': self.approve(value, step); self.mark('INSTALLING_TEST'); continue
                            if value['verdict'] == 'REVISE' and self.s['attempt'] < self.m['limits']['plan_attempts']:
                                self.s['attempt'] += 1; self.s['proposal'] = None; self.mark('PLANNING', value['reason']); continue
                            raise Halt('NEEDS_DESIGN' if value['verdict'] == 'NEEDS_DESIGN' else 'BLOCKED', value['reason'])
                        if phase == 'INSTALLING_TEST': self.install(); continue
                        if phase == 'RUNNING': self.run_child(); continue
                        raise Error('unsupported phase')
                except Halt as e:
                    if self.s['phase'] in ACTIVE: self.s['resume_phase'] = self.s['phase']
                    self.mark(e.phase, str(e))
                except KeyboardInterrupt:
                    self.s['resume_phase'] = self.s['phase']; self.mark('PAUSED', 'interrupted; durable INTENT may have unknown delivery')
                except (Exception,) as e:
                    self.s['resume_phase'] = self.s['phase'] if self.s['phase'] in ACTIVE else self.s['resume_phase']
                    self.mark('INVALID', str(e))
                finally: self.handoff()
        return self.s

    def request_stop(self, clear=False):
        targets = [self.path / 'stop']; child = self.s['child']
        if child and child['path']: targets.append(self.artifact(child['path']) / 'stop')
        for target in targets:
            if clear: target.unlink(missing_ok=True)
            else: self.b['task_state'].atomic_bytes(target, b'operator requested stop\n')

    def handoff(self):
        self.b['task_state'].write_json(self.path / 'handoff.json', {
            'phase': self.s['phase'], 'reason': self.s['reason'], 'completed_units': self.s['index'],
            'deadline': self.s['deadline'], 'run': str(self.path), 'allocation': self.s['allocations'],
            'next_command': 'python scripts/run_overnight.py ' + ('resume' if self.s['phase'] in ACTIVE | PAUSED else 'status') + ' --run "' + str(self.path) + '"',
            'meaning': 'Scoped workpackage completion only; not canonical game Phase approval.'})
