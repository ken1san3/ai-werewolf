"""Validate persisted evidence before reuse; no provider invocation or repository writes."""
from __future__ import annotations
import copy
import re
from pathlib import Path

from autodev_lib.engine import Campaign
from .policy import (Error, ACTIVE, PAUSED, PHASES, STATE_KEYS, CALL_KEYS, RAW_KEYS, APPROVAL_KEYS,
                     digest, exact, integer, number, nonempty, loads, sha, allocation, inputs, proposal, plan_response)


class Evidence:
    def artifact(self, name):
        return self.b['task_contract'].safe_path(self.path, name)

    def read(self, name, cap=32 * 1024**2):
        path = self.artifact(name)
        if path.stat().st_size > cap: raise Error('artifact size limit: ' + name)
        return loads(path.read_bytes())

    def raw(self, key):
        r = self.read('calls/' + key + '/raw.json', self.m['limits']['output_bytes'] * 4)
        exact(r, RAW_KEYS)
        if r['exit_code'] is not None and type(r['exit_code']) is not int: raise Error('invalid raw exit')
        if r['error'] is not None and not isinstance(r['error'], str): raise Error('invalid raw error')
        for k in ('stdout', 'stderr'):
            if not isinstance(r[k], str): raise Error('invalid raw text')
        number(r['seconds'], 0, 1e12)
        for k in ('result', 'usage'):
            if r[k] is not None and not isinstance(r[k], dict): raise Error('invalid raw object')
        return r

    def response(self, stage, value, index):
        if stage == 'planner': plan_response(value, self.m['units'][index]['test_slot'], self.m['limits']['output_bytes'])
        else:
            exact(value, 'verdict reason'); nonempty(value['reason'])
            if value['verdict'] not in ('APPROVED', 'REVISE', 'NEEDS_DESIGN', 'BLOCKED'): raise Error('invalid reviewer verdict')

    def source_before(self, index):
        expected = dict(self.initial['expected'])
        for receipt in self.s['receipts'][:index]: expected.update(receipt['outputs'])
        return expected

    def packet_names(self, index):
        u = self.m['units'][index]; names = inputs(u)
        roots = u['template']['test_paths']
        names += [n for n, h in self.initial['expected'].items() if h is not None and
                  any(n == t or n.startswith(t + '/') for t in roots)]
        for prior in self.m['units'][:index]: names += prior['template']['allow_new'] + [prior['test_slot']]
        return list(dict.fromkeys(names))

    def packet(self, index, attempt, stage, files, proposed=None):
        feedback = ''
        if attempt > 1:
            previous = self.raw(f'u{index}-a{attempt-1}-reviewer')['result']
            if previous['verdict'] != 'REVISE': raise Error('retry lacks REVISE')
            feedback = previous['reason']
        value = {'version': 1, 'stage': stage, 'package_sha256': self.s['package_sha256'],
                 'unit_id': self.m['units'][index]['id'], 'index': index, 'attempt': attempt,
                 'unit': self.m['units'][index], 'completed': self.s['receipts'][:index],
                 'feedback': feedback, 'files': files, 'source_sha256': digest(self.source_before(index))}
        if stage == 'reviewer': value['proposal'] = proposed
        return value

    def verify_call(self, key, call):
        match = re.fullmatch(r'u([0-9]+)-a([0-9]+)-(planner|reviewer)', key)
        if not match: raise Error('invalid call identity')
        i, attempt, stage = int(match[1]), int(match[2]), match[3]
        integer(i, 0, min(self.s['index'], len(self.m['units']) - 1))
        integer(attempt, 1, self.m['limits']['plan_attempts'])
        exact(call, CALL_KEYS)
        if call['status'] not in ('INTENT', 'DONE', 'FAILED'): raise Error('invalid call status')
        config = self.m['providers'][stage]
        if call['provider'] != config['provider'] or call['model'] != config['model']: raise Error('call provider changed')
        sha(call['packet_sha256'])
        path = self.artifact('calls/' + key + '/packet.json')
        if path.stat().st_size > self.m['limits']['packet_bytes'] or digest(path.read_bytes()) != call['packet_sha256']:
            raise Error('saved packet changed')
        packet = self.read('calls/' + key + '/packet.json'); files = packet.get('files')
        integer(packet.get('version'), 1, 1); integer(packet.get('index'), i, i); integer(packet.get('attempt'), attempt, attempt)
        if not isinstance(files, dict) or set(files) != set(self.packet_names(i)): raise Error('packet file scope changed')
        expected = self.source_before(i)
        for n, text in files.items():
            if n not in config['cloud_read'] or not isinstance(text, str) or digest(text.encode('utf-8')) != expected.get(n):
                raise Error('packet source/authorization changed')
        prop = None
        if stage == 'reviewer':
            pk = f'u{i}-a{attempt}-planner'
            if pk not in self.s['calls'] or self.s['calls'][pk]['status'] != 'DONE': raise Error('review lacks completed planner')
            prop = proposal(self.m, i, self.raw(pk)['result'], self.s['receipts'][:i])
        if packet != self.packet(i, attempt, stage, files, prop): raise Error('packet authority changed')
        if call['status'] == 'INTENT':
            if call['raw_sha256'] is not None: raise Error('intent already has raw digest')
        else:
            sha(call['raw_sha256'])
            if digest(self.artifact('calls/' + key + '/raw.json').read_bytes()) != call['raw_sha256']: raise Error('saved raw changed')
            raw = self.raw(key); ok = raw['exit_code'] == 0 and raw['error'] is None
            if ok != (call['status'] == 'DONE'): raise Error('raw/call status mismatch')
            if ok: self.response(stage, raw['result'], i)
        return i

    def verify_proposal(self, p, index, require_approval=False):
        exact(p, 'attempt packet approval'); integer(p['attempt'], 1, self.m['limits']['plan_attempts'])
        key = f'u{index}-a{p["attempt"]}-planner'
        if key not in self.s['calls'] or self.s['calls'][key]['status'] != 'DONE': raise Error('proposal lacks planner evidence')
        self.verify_call(key, self.s['calls'][key])
        expected = proposal(self.m, index, self.raw(key)['result'], self.s['receipts'][:index])
        if p['packet'] != expected: raise Error('proposal authority/hash changed')
        approval = p['approval']
        if approval is None:
            if require_approval: raise Error('missing approval')
            return
        exact(approval, APPROVAL_KEYS)
        integer(approval['version'], 1, 1); integer(approval['attempt'], p['attempt'], p['attempt'])
        key = f'u{index}-a{p["attempt"]}-reviewer'; call = self.s['calls'].get(key)
        if not call or call['status'] != 'DONE': raise Error('approval lacks actual reviewer call')
        self.verify_call(key, call)
        raw = self.raw(key); c = self.m['providers']['reviewer']
        signed = {'version': 1, 'role': 'Reviewer', 'provider': c['provider'], 'model': c['model'],
                  'unit_id': self.m['units'][index]['id'], 'attempt': p['attempt'],
                  'package_sha256': self.s['package_sha256'], 'source_sha256': digest(self.source_before(index)),
                  'proposal_sha256': expected['proposal_sha256'], 'test_sha256': expected['test_sha256'],
                  'packet_sha256': call['packet_sha256'], 'response_sha256': call['raw_sha256'], **raw['result']}
        if approval != signed or approval['verdict'] != 'APPROVED': raise Error('approval binding changed')

    def verify_child(self, child, index, p):
        exact(child, 'root source manifest_sha256 path upper_grant qwen_grant')
        root = f'children/u{index}'
        if child['root'] != root or child['source'] != root + '/source.json': raise Error('child root escaped')
        if child['upper_grant'] != 3 or child['qwen_grant'] != allocation(self.m, self.m['units'][index])['qwen_max']:
            raise Error('child allocation changed')
        expected = self.child_manifest(index, p)
        if child['manifest_sha256'] != digest(expected) or self.read(child['source']) != expected: raise Error('child authority changed')
        self.artifact(root)
        if child['path'] is not None:
            path = self.artifact(child['path'])
            if path.parent != self.artifact(root + '/runs') or not re.fullmatch('campaign-[a-f0-9]{32}', path.name):
                raise Error('child path escaped')
            c = Campaign(path)
            if c.m != expected or Path(c.s['manifest_source']).resolve() != self.artifact(child['source']).resolve():
                raise Error('linked campaign source changed')
            if c.s['source_sha256'] != digest(self.artifact(child['source']).read_bytes()): raise Error('child source hash changed')
            return c
        return None

    def child_outputs(self, c, index, p):
        if c.s['phase'] != 'COMPLETE' or c.s['index'] != 1: raise Error('child is not complete')
        unit = self.m['units'][index]; job_id = unit['id']
        if set(c.s['receipts']) != {job_id}: raise Error('child receipt identity changed')
        job = loads((c.path / 'jobs' / (job_id + '.json')).read_bytes())
        run = Path(job['run'])
        if run.parent.resolve() != (c.path / 'runs').resolve(): raise Error('v1 run escaped child')
        rs = self.b['task_state'].read_state(run)
        actual = loads((run / 'contract.json').read_bytes()); expected = copy.deepcopy(p['packet']['contract'])
        timeout = actual['limits']['run_timeout_s']; integer(timeout, 1, min(300, expected['limits']['run_timeout_s']))
        expected['limits']['run_timeout_s'] = timeout
        if actual != expected or digest(actual) != job['approved_contract_sha256'] or rs['contract_sha256'] != job['run_contract_sha256']:
            raise Error('v1 contract differs from peer-approved proposal')
        if rs['phase'] != 'APPLIED' or self.b['task_source'].manifest(run / rs['candidate']) != rs['candidate_manifest']:
            raise Error('v1 applied candidate evidence changed')
        # D058 verifies call hashes on load; bind the actual completed scope again.
        step = c.s['steps'].get('j0-completion', {})
        raw = loads((c.path / 'calls/j0-completion/raw.json').read_bytes())
        packet = loads((c.path / 'calls/j0-completion/packet.json').read_bytes())
        subject = packet['payload']['subject']
        if (step.get('status') != 'DONE' or raw.get('result', {}).get('verdict') != 'APPROVED' or
            subject['candidate_sha256'] != rs['candidate_sha256'] or subject['contract_sha256'] != rs['contract_sha256'] or
            subject['handoff_sha256'] != digest((run / 'handoff.json').read_bytes()) or digest(subject) != step['subject_sha256']):
            raise Error('completion evidence changed')
        names = unit['template']['allow_edit'] + unit['template']['allow_new']
        output = {n: rs['candidate_manifest'][n] for n in names}
        if any(subject['applied_files'].get(n) != rs['candidate_manifest'][n] for n in rs['edits']): raise Error('applied evidence mismatch')
        output[unit['test_slot']] = p['packet']['test_sha256']
        if rs['candidate_manifest'].get(unit['test_slot']) != output[unit['test_slot']]: raise Error('approved test changed in candidate')
        return output

    def verify_state(self):
        s = self.s; exact(s, STATE_KEYS)
        integer(s['version'], 1, 1)
        if s['run_id'] != self.path.name or not re.fullmatch('overnight-[a-f0-9]{32}', s['run_id']): raise Error('run identity changed')
        if s['phase'] not in PHASES or s['resume_phase'] not in ACTIVE | {None}: raise Error('invalid phase')
        nonempty(s['reason']); integer(s['index'], 0, len(self.m['units'])); integer(s['attempt'], 1, self.m['limits']['plan_attempts'])
        for k in ('started_at', 'deadline', 'last_observed_at'): number(s[k], 0, 1e12)
        if s['deadline'] != min(self.m['expires_at'], s['started_at'] + self.m['limits']['seconds']): raise Error('deadline extended')
        for k in ('source_sha256', 'package_sha256'): sha(s[k])
        if s['package_sha256'] != digest(self.m): raise Error('package copy changed')
        if not isinstance(s['package_source'], str) or not Path(s['package_source']).is_absolute(): raise Error('invalid package source')
        if digest(Path(s['package_source']).read_bytes()) != s['source_sha256']: raise Error('original package changed')
        if not isinstance(s['events'], list) or not 1 <= len(s['events']) <= 2000: raise Error('invalid events')
        last = s['started_at']
        for i, event in enumerate(s['events']):
            exact(event, 'index phase at evidence')
            integer(event['index'], i, i)
            if event['index'] != i or event['phase'] not in PHASES: raise Error('event order/phase changed')
            number(event['at'], last, 1e12); last = event['at']
            if i == 0:
                if event['evidence'] != digest(self.artifact('source.json').read_bytes()): raise Error('initial source evidence changed')
            elif event['evidence'] is not None: raise Error('invalid event evidence')
        self.initial = self.read('source.json'); exact(self.initial, 'head expected')
        if s['head'] != self.initial['head'] or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', s['head']): raise Error('HEAD evidence changed')
        if not isinstance(self.initial['expected'], dict) or not 1 <= len(self.initial['expected']) <= 2000: raise Error('invalid source snapshot')
        mandatory = set()
        roots = [t for u in self.m['units'] for t in u['template']['test_paths']]
        for u in self.m['units']: mandatory.update(inputs(u) + u['template']['allow_new'] + [u['test_slot']])
        if not mandatory <= set(self.initial['expected']): raise Error('source scope omitted')
        future = {n for u in self.m['units'] for n in u['template']['allow_new'] + [u['test_slot']]}
        for n, h in self.initial['expected'].items():
            self.b['task_contract'].safe_path(self.repo, n)
            if n not in mandatory and not any(n == t or n.startswith(t + '/') for t in roots): raise Error('source scope expanded')
            if n in future:
                if h is not None: raise Error('future file was not initially absent')
            else: sha(h)
        if not isinstance(s['receipts'], list) or len(s['receipts']) != s['index']: raise Error('receipt/index mismatch')
        if not isinstance(s['calls'], dict) or len(s['calls']) > self.m['limits']['upper_calls']: raise Error('invalid calls')
        spent = {u['id']: allocation(self.m, u) for u in self.m['units']}
        for key, call in s['calls'].items():
            i = self.verify_call(key, call); spent[self.m['units'][i]['id']]['upper_spent'] += 1
        expected = dict(self.initial['expected'])
        for i, receipt in enumerate(s['receipts']):
            exact(receipt, 'unit_id proposal child outputs child_state_sha256')
            if receipt['unit_id'] != self.m['units'][i]['id']: raise Error('receipt order changed')
            self.verify_proposal(receipt['proposal'], i, True)
            c = self.verify_child(receipt['child'], i, receipt['proposal'])
            if c is None or digest((c.path / 'state.json').read_bytes()) != receipt['child_state_sha256']: raise Error('completed child state changed')
            if receipt['outputs'] != self.child_outputs(c, i, receipt['proposal']): raise Error('receipt outputs changed')
            expected.update(receipt['outputs'])
            spent[receipt['unit_id']]['upper_spent'] += 3
            spent[receipt['unit_id']]['qwen_spent'] += receipt['child']['qwen_grant']
        active = s['resume_phase'] if s['phase'] in PAUSED else s['phase']
        if s['proposal'] is not None:
            if s['index'] == len(self.m['units']): raise Error('proposal after completion')
            self.verify_proposal(s['proposal'], s['index'], active in ('INSTALLING_TEST', 'RUNNING'))
            if s['proposal']['attempt'] != s['attempt']: raise Error('proposal attempt changed')
            slot = self.m['units'][s['index']]['test_slot']
            # Persisted expected may be old/null only during atomic-install recovery.
            if s['proposal']['approval'] is not None and s['expected'].get(slot) == s['proposal']['packet']['test_sha256']:
                expected[slot] = s['proposal']['packet']['test_sha256']
        if s['child'] is not None:
            if s['proposal'] is None or s['proposal']['approval'] is None: raise Error('child lacks approval')
            self.verify_child(s['child'], s['index'], s['proposal'])
            a = spent[self.m['units'][s['index']]['id']]; a['upper_spent'] += 3; a['qwen_spent'] += s['child']['qwen_grant']
        if s['expected'] != expected: raise Error('expected source hashes not reconstructible')
        if not isinstance(s['allocations'], dict) or set(s['allocations']) != set(spent): raise Error('invalid allocation records')
        for a in s['allocations'].values():
            exact(a, 'upper_max qwen_max upper_spent qwen_spent')
            for value in a.values(): integer(value, 0, 100)
        if s['allocations'] != spent: raise Error('allocation/call evidence mismatch')
        for a in spent.values():
            integer(a['upper_spent'], 0, a['upper_max']); integer(a['qwen_spent'], 0, a['qwen_max'])
        integer(s['upper_reserved'], 1, self.m['limits']['upper_calls']); integer(s['qwen_reserved'], 1, self.m['limits']['qwen_calls'])
        if s['upper_reserved'] != sum(a['upper_max'] for a in spent.values()) or s['qwen_reserved'] != sum(a['qwen_max'] for a in spent.values()):
            raise Error('reservation totals changed')
        if s['phase'] == 'COMPLETE' and (s['index'] != len(self.m['units']) or s['proposal'] is not None or s['child'] is not None):
            raise Error('invalid completion state')
