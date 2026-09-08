"""D060 version-2 direct v1 execution with hash-bound final review."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from autodev_lib import process
from autodev_lib.engine import Pause
from . import provider
from .engine import Controller, Halt
from .policy import Error, PAUSED, allocation, digest, exact, head, test_files


_V1_RESUMABLE = {
    'PLANNED', 'SNAPSHOT_READY', 'MODEL_RUNNING', 'MODEL_EDITED',
    'GATED', 'FIX_PENDING',
}

POST_WRAPPER = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import task_gate,task_state,task_source
r=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
def manifest(root):
    if Path(root).resolve()!=Path(r['contract']['repo_root']).resolve():
        raise ValueError('post gate repository changed')
    names=sorted(set(task_source.selection(r['contract'])) | set(r['contract']['allow_new']))
    if names!=r['scope']:raise ValueError('post gate source selection changed')
    return task_source.hashes(root,names)
task_gate.manifest=manifest
result={'gates':task_gate.gate(Path(r['run']),r['contract'],r['state']),'producer':sys.argv[4]}
task_state.write_json(Path(sys.argv[3]).parent/sys.argv[4]/'gate-result.json',result)
task_state.write_json(Path(sys.argv[3]),result)
'''


def repository_manifest(root: Path, task_source, task_contract, contract, scope=None):
    """Revalidate the v1 source selection and every newly approved output in the live tree."""
    if Path(root).resolve() != Path(contract['repo_root']).resolve():
        raise Error('post gate repository changed')
    names = sorted(set(task_source.selection(contract)) | set(contract['allow_new']))
    if scope is not None and names != scope: raise Error('post gate source selection changed')
    return task_source.hashes(root, names)


class EfficientController(Controller):
    """Opt-in D060 controller; version-1 Controller behavior stays untouched."""

    def child_manifest(self, index, p):
        contract = copy.deepcopy(p['packet']['contract'])
        contract['repo_root'] = str(Path(contract['repo_root']).resolve())
        return contract

    def _direct(self, path: Path, expected: dict):
        self.b['task_contract'].safe_path(path.parent, path.name)
        state = self.b['task_state'].read_state(path)
        raw = self.b['task_contract'].safe_path(path,'contract.json').read_bytes()
        actual = self.b['task_contract'].strict_json(raw.decode('utf-8'))
        if (actual != expected or state['contract_sha256'] != digest(raw) or
                state['run_id'] != path.name or state['risk'] != 'red'):
            raise Halt('INVALID', 'direct v1 contract changed')
        if state['phase'] == 'PREPARING':
            raise Halt('BLOCKED', 'direct v1 child is not recoverable: ' + state['phase'])
        return SimpleNamespace(path=path, s=state, m=actual)

    def verify_child(self, child, index, p):
        exact(child, 'root source manifest_sha256 path upper_grant qwen_grant')
        unit = self.m['units'][index]
        root = f'children/u{index}'
        expected = self.child_manifest(index, p)
        grant = allocation(self.m, unit)['qwen_max'] - 1
        if (child['root'] != root or child['source'] != root + '/source.json' or
                child['upper_grant'] != 1 or child['qwen_grant'] != grant):
            raise Halt('INVALID', 'direct child authority changed')
        if child['manifest_sha256'] != digest(expected) or self.read(child['source']) != expected:
            raise Halt('INVALID', 'direct child contract source changed')
        self.artifact(root)
        if child['path'] is None:
            return None
        path = self.artifact(child['path'])
        runs = self.artifact(root + '/runs')
        if path.parent != runs or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}-[a-f0-9]{12}', path.name):
            raise Halt('INVALID', 'direct v1 run path escaped')
        return self._direct(path, expected)

    def _tool(self, args, directory):
        self.temporal()
        directory = Path(directory)
        try:
            if self.child_hooks.get('runner') is not None:
                value = self.child_hooks['runner'](args, directory)
                if not isinstance(value, dict):
                    raise Error('runner hook returned non-object')
                self.b['task_state'].write_json(directory / 'result.json', value)
                return value
            argv = [sys.executable, str(Path(self.m['agent_root']) / 'tools/task.py'), *args]
            raw = process.execute(argv, Path(self.m['agent_root']), directory, '', self.remaining(),
                                  self.m['limits']['output_bytes'])
            self.b['task_state'].write_json(directory / 'result.json', raw)
            if raw['error'] or raw['exit_code'] not in (0, 3):
                phase = 'PAUSED' if raw['error'] in ('timeout', 'output limit') and args[0] in ('plan', 'resume', 'apply') else 'BLOCKED'
                raise Halt(phase, 'v1 runner stopped: ' + str(raw['error'] or raw['stderr'])[-500:])
            return self.b['task_contract'].strict_json(raw['stdout'])
        except Pause as error:
            raise Halt('PAUSED' if error.phase == 'PAUSED' else 'BLOCKED', str(error)) from error

    def _execution(self, root: str):
        path = self.artifact(root + '/execution.json')
        if not path.exists():
            self.b['task_state'].write_json(path, {'launches': 0})
        value = self.read(root + '/execution.json')
        exact(value, 'launches')
        if type(value['launches']) is not int or value['launches'] < 0:
            raise Halt('INVALID', 'invalid direct execution record')
        return value

    def _resume(self, direct, root: str, unit: dict):
        execution = self._execution(root)
        if execution['launches'] >= unit['runner_launches']:
            raise Halt('BLOCKED', 'direct v1 runner launch limit exhausted')
        execution['launches'] += 1
        self.b['task_state'].write_json(self.artifact(root + '/execution.json'), execution)
        self._tool(['resume', '--run', str(direct.path)],
                   self.artifact(root + f'/runner/resume-{execution["launches"]}'))
        return self._direct(direct.path, self.child_manifest(self.s['index'], self.s['proposal']))

    def linked_child(self):
        i = self.s['index']; proposal = self.s['proposal']; unit = self.m['units'][i]
        created = False
        if self.s['child'] is None:
            self.temporal(); self.source_guard()
            root = f'children/u{i}'; source = root + '/source.json'; contract = self.child_manifest(i, proposal)
            path = self.artifact(source)
            if path.exists():
                if self.read(source) != contract:
                    raise Halt('INVALID', 'orphan direct contract source changed')
            else:
                self.b['task_state'].write_json(path, contract)
            grant = allocation(self.m, unit)['qwen_max'] - 1
            account = self.s['allocations'][unit['id']]
            if account['upper_spent'] + 1 > account['upper_max'] or account['qwen_spent'] + grant > account['qwen_max']:
                raise Halt('LIMIT_REACHED', 'direct child grant exceeds allocation')
            account['upper_spent'] += 1; account['qwen_spent'] += grant
            self.s['child'] = {'root': root, 'source': source, 'manifest_sha256': digest(contract),
                               'path': None, 'upper_grant': 1, 'qwen_grant': grant}
            self.mark()
            self._execution(root)
        child = self.s['child']; direct = self.verify_child(child, i, proposal)
        root = child['root']; runs = self.artifact(root + '/runs'); runs.mkdir(parents=True, exist_ok=True)
        if direct is None:
            found = [p for p in runs.iterdir() if p.is_dir()]
            if len(found) > 1:
                raise Halt('INVALID', 'multiple direct v1 children; refusing replacement')
            if found:
                direct = self._direct(found[0], self.child_manifest(i, proposal))
            else:
                intent = self.artifact(root + '/intent.json')
                if intent.exists():
                    value = self.read(root + '/intent.json')
                    exact(value, 'contract_sha256')
                    if value['contract_sha256'] != child['manifest_sha256']:
                        raise Halt('INVALID', 'direct creation intent changed')
                    raise Halt('BLOCKED', 'direct v1 creation intent has no recoverable run')
                self.b['task_state'].write_json(intent, {'contract_sha256': child['manifest_sha256']})
                created = True
                self._tool(['plan', '--contract', str(self.artifact(child['source'])), '--runs-root', str(runs)],
                           self.artifact(root + '/runner/plan'))
                found = [p for p in runs.iterdir() if p.is_dir()]
                if len(found) != 1:
                    raise Halt('BLOCKED', 'direct v1 plan did not create exactly one run')
                direct = self._direct(found[0], self.child_manifest(i, proposal))
            child['path'] = direct.path.resolve(strict=True).relative_to(self.path).as_posix()
            self.mark()
            direct = self.verify_child(child, i, proposal)
        if created and direct is None:
            raise Halt('BLOCKED', 'direct v1 creation incomplete')
        return direct

    def _candidate(self, direct):
        state = self.b['task_state'].read_state(direct.path)
        if state['phase'] not in {'AWAITING_REVIEW', 'APPLYING', 'APPLIED'}:
            raise Halt('BLOCKED', 'direct v1 candidate is not reviewable: ' + state['phase'])
        candidate = self.b['task_contract'].safe_path(direct.path, state['candidate'])
        actual = self.b['task_source'].manifest(candidate)
        if (actual != state['candidate_manifest'] or
                self.b['task_state'].manifest_hash(actual) != state['candidate_sha256'] or
                not state.get('gates', {}).get('passed')):
            raise Halt('INVALID', 'direct v1 candidate/gates changed')
        return state, candidate

    def _evidence_text(self, run: Path):
        result = {}
        base = run / 'evidence'
        if not base.is_dir():
            raise Halt('INVALID', 'direct v1 gate evidence missing')
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(run).as_posix()
            self.b['task_contract'].safe_path(run, rel)
            try:
                result[rel] = path.read_text(encoding='utf-8')
            except UnicodeError as error:
                raise Halt('BLOCKED', 'non-UTF8 gate evidence: ' + rel) from error
        if not result:
            raise Halt('INVALID', 'empty direct v1 gate evidence')
        return result

    def _final_packet(self, direct, index, proposal):
        state, candidate = self._candidate(direct)
        contract = self.child_manifest(index, proposal)
        if direct.m != contract:
            raise Halt('INVALID', 'final contract differs from approved proposal')
        files = {}
        for name in self.packet_names(index):
            path = self.b['task_contract'].safe_path(direct.path / 'base', name)
            if not path.is_file() or state['source_manifest'].get(name) != digest(path.read_bytes()):
                raise Halt('INVALID', 'final input differs from source manifest: ' + name)
            files[name] = path.read_text(encoding='utf-8')
        changes = {}
        if not isinstance(state.get('edits'), dict):
            raise Halt('INVALID', 'direct v1 edit evidence missing')
        allowed = set(contract['allow_edit'] + contract['allow_new'])
        if not state['edits'] or set(state['edits']) - allowed:
            raise Halt('INVALID', 'direct v1 edit scope changed')
        for name in state['edits']:
            path = self.b['task_contract'].safe_path(candidate, name)
            if state['candidate_manifest'].get(name) != digest(path.read_bytes()):
                raise Halt('INVALID', 'candidate edit changed: ' + name)
            changes[name] = path.read_text(encoding='utf-8')
        reviewer = self.m['providers']['reviewer']
        if (set(files) | set(changes)) - set(reviewer['cloud_read']):
            raise Halt('BLOCKED', 'final packet exceeds reviewer cloud_read authority')
        return {'version': 1, 'stage': 'final', 'package_sha256': self.s['package_sha256'],
                'unit_id': self.m['units'][index]['id'], 'proposal': copy.deepcopy(proposal),
                'contract': contract, 'source_manifest': copy.deepcopy(state['source_manifest']),
                'candidate_manifest': copy.deepcopy(state['candidate_manifest']),
                'candidate_sha256': state['candidate_sha256'], 'files': files, 'changes': changes,
                'gates': copy.deepcopy(state['gates']),
                'gate_history': copy.deepcopy(state.get('gate_history', [])),
                'evidence': self._evidence_text(direct.path)}

    def _final(self, direct, index, proposal):
        root = f'children/u{index}'; final = self.artifact(root + '/final')
        packet = self._final_packet(direct, index, proposal)
        data = json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(data) > self.m['limits']['packet_bytes']:
            raise Halt('BLOCKED', 'final packet exceeds cap; no truncation')
        packet_path = final / 'packet.json'; intent_path = final / 'intent.json'
        raw_path = final / 'raw.json'; record_path = final / 'record.json'
        packet_sha = digest(data); config = self.m['providers']['reviewer']
        intent = {'packet_sha256': packet_sha, 'provider': config['provider'], 'model': config['model']}
        self.temporal(); self.source_guard()
        if packet_path.exists():
            if packet_path.read_bytes() != data:
                raise Halt('INVALID', 'saved final packet changed')
        else:
            self.b['task_state'].atomic_bytes(packet_path, data)
        if intent_path.exists():
            if self.read(root + '/final/intent.json') != intent:
                raise Halt('INVALID', 'saved final intent changed')
        else:
            if not provider.quota(self.m, config, self.b):
                raise Halt('PAUSED_QUOTA', 'reviewer quota missing/exhausted under policy')
            self.b['task_state'].write_json(intent_path, intent)
            try:
                raw = self.cloud(config['provider'], config, packet, final, self.remaining(),
                                 self.m['limits']['output_bytes'])
            except Exception as error:
                raise Halt('UNKNOWN_DELIVERY', 'final reviewer returned no durable response: ' + str(error)) from error
            self.b['task_state'].write_json(raw_path, raw)
        if not raw_path.exists():
            raise Halt('UNKNOWN_DELIVERY', 'final INTENT without durable raw; will not resend')
        raw = self.b['task_contract'].strict_json(raw_path.read_text(encoding='utf-8'))
        exact(raw, 'exit_code error stdout stderr seconds result usage')
        if raw['error'] is not None or raw['exit_code'] != 0:
            if not record_path.exists():
                self.b['task_state'].write_json(record_path, {**intent, 'raw_sha256': digest(raw_path.read_bytes())})
            raise Halt('BLOCKED', 'final reviewer failure: ' + str(raw['error'] or raw['stderr'])[-500:])
        self.response('reviewer', raw['result'], index)
        record = {**intent, 'raw_sha256': digest(raw_path.read_bytes())}
        if record_path.exists():
            if self.read(root + '/final/record.json') != record:
                raise Halt('INVALID', 'saved final record changed')
        else:
            self.b['task_state'].write_json(record_path, record)
        if raw['result']['verdict'] != 'APPROVED':
            raise Halt('BLOCKED' if raw['result']['verdict'] != 'NEEDS_DESIGN' else 'NEEDS_DESIGN', raw['result']['reason'])
        current = self._final_packet(direct, index, proposal)
        if current != packet:
            raise Halt('INVALID', 'final review subject changed after response')
        approval = {'approval_version': 1, 'run_id': direct.s['run_id'],
                    'contract_sha256': direct.s['contract_sha256'],
                    'candidate_sha256': direct.s['candidate_sha256'], 'verdict': 'APPROVED',
                    'reviewer_role': 'Reviewer', 'reviewer_model': config['model']}
        path = self.artifact(root + '/approval.json')
        if path.exists() and self.read(root + '/approval.json') != approval:
            raise Halt('INVALID', 'mapped v1 approval changed')
        if not path.exists():
            self.b['task_state'].write_json(path, approval)
        return approval

    def _verify_final(self, direct, index, proposal):
        root = f'children/u{index}'; final = self.artifact(root + '/final')
        for name in ('packet.json', 'intent.json', 'raw.json', 'record.json'):
            if not (final / name).is_file():
                raise Halt('INVALID', 'incomplete final review evidence')
        packet = self.read(root + '/final/packet.json'); intent = self.read(root + '/final/intent.json')
        raw = self.read(root + '/final/raw.json', self.m['limits']['output_bytes'] * 4)
        record = self.read(root + '/final/record.json')
        config = self.m['providers']['reviewer']; expected_intent = {
            'packet_sha256': digest((final / 'packet.json').read_bytes()),
            'provider': config['provider'], 'model': config['model']}
        if intent != expected_intent or record != {**expected_intent, 'raw_sha256': digest((final / 'raw.json').read_bytes())}:
            raise Halt('INVALID', 'final review binding changed')
        exact(raw, 'exit_code error stdout stderr seconds result usage')
        if raw['error'] is not None or raw['exit_code'] != 0:
            raise Halt('BLOCKED', 'saved final review failed')
        self.response('reviewer', raw['result'], index)
        if raw['result']['verdict'] != 'APPROVED' or packet != self._final_packet(direct, index, proposal):
            raise Halt('INVALID', 'saved final approval subject changed')
        approval = {'approval_version': 1, 'run_id': direct.s['run_id'],
                    'contract_sha256': direct.s['contract_sha256'],
                    'candidate_sha256': direct.s['candidate_sha256'], 'verdict': 'APPROVED',
                    'reviewer_role': 'Reviewer', 'reviewer_model': config['model']}
        if self.read(root + '/approval.json') != approval:
            raise Halt('INVALID', 'mapped v1 approval changed')
        return packet

    def _post_request(self, direct, index):
        root = f'children/u{index}'
        request = self.read(root + '/post-request.json')
        exact(request, 'run contract state scope scope_sha256 wrapper_sha256')
        expected_contract = copy.deepcopy(direct.m)
        timeout = request['contract']['limits']['run_timeout_s']
        if type(timeout) is not int or not 1 <= timeout <= expected_contract['limits']['run_timeout_s']:
            raise Halt('INVALID', 'post gate timeout changed')
        expected_contract['limits']['run_timeout_s'] = timeout
        manifest = request['state']['candidate_manifest']
        if any(manifest.get(n) != h for n,h in direct.s['candidate_manifest'].items()):
            raise Halt('INVALID', 'post input differs from approved candidate')
        expected_state = {'candidate': str(self.repo), 'candidate_manifest': manifest,
                          'baseline_nodeids': direct.s['baseline_nodeids'], 'edits': direct.s['edits'],
                          'attempt': direct.s['attempt']}
        if (request['contract'] != expected_contract or request['state'] != expected_state or
                request['scope'] != sorted(manifest) or request['scope_sha256'] != digest(request['scope']) or
                request['wrapper_sha256'] != digest(POST_WRAPPER.encode('utf-8')) or
                request['run'] != str(self.artifact(root + '/post-gate'))):
            raise Halt('INVALID', 'post gate scope/wrapper/contract changed')
        return request

    def _verify_post(self, direct, index):
        root = f'children/u{index}'; value = self.read(root + '/post.json')
        exact(value, 'candidate_sha256 source_manifest gates evidence')
        request = self._post_request(direct, index)
        if (value['candidate_sha256'] != direct.s['candidate_sha256'] or
                value['source_manifest'] != request['state']['candidate_manifest']):
            raise Halt('INVALID', 'post-apply source differs from approved candidate')
        if not value['gates'].get('passed'): raise Halt('BLOCKED', 'saved post-apply gates failed')
        result = self.read(root + '/post-result.json')
        if set(result) != {'gates', 'producer'} or result['gates'] != value['gates']:
            raise Halt('INVALID', 'post-apply result changed')
        if not {'post-request.json','post-result.json'} <= set(value['evidence']):
            raise Halt('INVALID', 'post evidence omitted request/result')
        evidence_root = self.artifact(root)
        required = {'post-request.json', 'post-result.json'}
        producer = result['producer']
        if not isinstance(producer, str) or not re.fullmatch(r'post-run-[1-9][0-9]*', producer):
            raise Halt('INVALID', 'post result producer changed')
        producer_request = self.read(root + '/' + producer + '/request.json')
        if producer_request != {'request_sha256': digest(self.artifact(root + '/post-request.json').read_bytes()),
                                'wrapper_sha256': request['wrapper_sha256']}:
            raise Halt('INVALID', 'post producer request changed')
        if self.read(root + '/' + producer + '/gate-result.json') != result:
            raise Halt('INVALID', 'post producer result changed')
        required.update({producer + '/request.json', producer + '/gate-result.json'})
        gates = value['gates']
        groups = [(gates['collection'], ['probe.json']),
                  (gates['execution'], ['probe.json', 'junit.xml'])]
        if len(gates['checks']) != len(request['contract']['checks']):
            raise Halt('INVALID', 'post checks omitted')
        groups += [(check, []) for check in gates['checks']]
        for gate, extras in groups:
            directory = Path(gate['evidence'])
            try: rel = directory.relative_to(evidence_root / 'post-gate' / 'evidence').as_posix()
            except ValueError: raise Halt('INVALID', 'post gate evidence escaped child')
            directory = self.b['task_contract'].safe_path(evidence_root, 'post-gate/evidence/' + rel)
            required.update((directory / name).relative_to(evidence_root).as_posix()
                            for name in ['result.json', 'stdout.log', 'stderr.log'] + extras)
            required.update(path.relative_to(evidence_root).as_posix()
                            for path in directory.rglob('*') if path.is_file())
        if not required <= set(value['evidence']):
            raise Halt('INVALID', 'post evidence omitted mandatory raw files')
        for name, wanted in value['evidence'].items():
            path = self.b['task_contract'].safe_path(evidence_root, name)
            if not path.is_file() or digest(path.read_bytes()) != wanted:
                raise Halt('INVALID', 'post-apply evidence changed: ' + name)
        return value

    def _post(self, direct, index, proposal):
        root = f'children/u{index}'; post_path = self.artifact(root + '/post.json')
        if post_path.exists(): return self._verify_post(direct, index)
        self.temporal(); self.source_guard()
        if direct.s['phase'] != 'APPLIED': raise Halt('INVALID', 'post gates require APPLIED child')
        request_path = self.artifact(root + '/post-request.json')
        result_path = self.artifact(root + '/post-result.json')
        gate_root = self.artifact(root + '/post-gate'); gate_root.mkdir(parents=True, exist_ok=True)
        if request_path.exists():
            request = self._post_request(direct, index)
        else:
            if result_path.exists(): raise Halt('INVALID', 'post result without request')
            contract = copy.deepcopy(self.child_manifest(index, proposal))
            contract['limits']['run_timeout_s'] = max(1, min(contract['limits']['run_timeout_s'], math.floor(self.remaining())))
            current = repository_manifest(self.repo, self.b['task_source'], self.b['task_contract'], contract)
            scope = sorted(current)
            request = {'run': str(gate_root), 'contract': contract,
                       'state': {'candidate': str(self.repo), 'candidate_manifest': current,
                                 'baseline_nodeids': direct.s['baseline_nodeids'], 'edits': direct.s['edits'],
                                 'attempt': direct.s['attempt']},
                       'scope': scope, 'scope_sha256': digest(scope),
                       'wrapper_sha256': digest(POST_WRAPPER.encode('utf-8'))}
            self.b['task_state'].write_json(request_path, request)
            self._post_request(direct, index)
        contract,scope=request['contract'],request['scope']
        current=request['state']['candidate_manifest']
        if repository_manifest(self.repo,self.b['task_source'],self.b['task_contract'],contract,scope) != current:
            raise Halt('INVALID','post request source changed')
        if not result_path.exists():
            count = len(list(self.artifact(root).glob('post-run-*'))) + 1
            directory = self.artifact(root + f'/post-run-{count}')
            self.b['task_state'].write_json(directory/'request.json',
                {'request_sha256':digest(request_path.read_bytes()),'wrapper_sha256':request['wrapper_sha256']})
            raw = process.execute([sys.executable, '-c', POST_WRAPPER, str(Path(self.m['agent_root']) / 'lib'),
                                   str(request_path), str(result_path), directory.name], Path(self.m['agent_root']), directory,
                                  '', self.remaining(), self.m['limits']['output_bytes'])
            self.b['task_state'].write_json(directory / 'result.json', raw)
            if raw['error'] or raw['exit_code'] != 0 or not result_path.is_file():
                raise Halt('PAUSED' if raw['error'] in ('timeout', 'output limit') else 'BLOCKED',
                           'post-apply gate runner stopped: ' + str(raw['error'] or raw['stderr'])[-500:])
        result = self.read(root + '/post-result.json'); exact(result, 'gates producer')
        if repository_manifest(self.repo,self.b['task_source'],self.b['task_contract'],contract,scope) != current:
            raise Halt('INVALID','source changed during post-apply gates')
        hashes = {}
        evidence_paths = [request_path, result_path]
        evidence_paths += [path for path in sorted((gate_root / 'evidence').rglob('*')) if path.is_file()]
        for directory in self.artifact(root).glob('post-run-*'):
            evidence_paths += [path for path in sorted(directory.rglob('*')) if path.is_file()]
        for path in evidence_paths:
            rel = path.relative_to(self.artifact(root)).as_posix()
            self.b['task_contract'].safe_path(self.artifact(root), rel)
            hashes[rel] = digest(path.read_bytes())
        value = {'candidate_sha256': direct.s['candidate_sha256'], 'source_manifest': current,
                 'gates': result['gates'], 'evidence': hashes}
        self.b['task_state'].write_json(post_path, value)
        if not result['gates'].get('passed'): raise Halt('BLOCKED','post-apply gates failed')
        return self._verify_post(direct,index)

    def child_outputs(self, direct, index, proposal):
        direct = self._direct(direct.path, self.child_manifest(index, proposal))
        if direct.s['phase'] != 'APPLIED':
            raise Halt('INVALID', 'direct child is not APPLIED')
        self._candidate(direct); self._verify_final(direct, index, proposal)
        post = self._verify_post(direct, index)
        if not post['gates'].get('passed'):
            raise Halt('BLOCKED', 'post-apply gates failed')
        unit = self.m['units'][index]
        outputs = {name: direct.s['candidate_manifest'][name]
                   for name in unit['template']['allow_edit'] + unit['template']['allow_new']}
        outputs[unit['test_slot']] = proposal['packet']['test_sha256']
        return outputs

    def source_guard(self):
        if head(self.repo) != self.s['head']:
            raise Halt('INVALID', 'Git HEAD changed')
        expected = dict(self.s['expected']); partial = {}
        active = self.s['resume_phase'] if self.s['phase'] in PAUSED else self.s['phase']
        if active == 'RUNNING' and self.s['child'] is not None and self.s['child']['path']:
            index = self.s['index']; proposal = self.s['proposal']
            direct = self.verify_child(self.s['child'], index, proposal)
            state = direct.s; contract = direct.m
            if state['phase'] in {'APPLYING', 'APPLIED'}:
                _state, _candidate = self._candidate(direct)
                self._verify_final(direct, index, proposal)
                if not isinstance(state.get('journal'), dict) or not state['journal']:
                    raise Halt('INVALID', 'direct partial apply lacks journal')
                allowed = set(contract['allow_edit'] + contract['allow_new'])
                for name, entry in state['journal'].items():
                    if (name not in allowed or entry.get('before') != self.s['expected'].get(name) or
                            entry.get('after') != state['candidate_manifest'].get(name)):
                        raise Halt('INVALID', 'direct partial journal authority changed')
                    if state['phase'] == 'APPLYING':
                        partial[name] = (entry['before'], entry['after'])
                    else:
                        expected[name] = entry['after']
                if state['phase'] == 'APPLIED':
                    expected[self.m['units'][index]['test_slot']] = proposal['packet']['test_sha256']
        allowed_tests = {name for name, value in expected.items() if value is not None}
        proposal = self.s['proposal']
        if proposal and proposal['approval']:
            allowed_tests.add(self.m['units'][self.s['index']]['test_slot'])
        if test_files(self.m, self.b) - allowed_tests:
            raise Halt('INVALID', 'unexpected test file appeared')
        for name, wanted in expected.items():
            path = self.b['task_contract'].safe_path(self.repo, name)
            if path.exists() and (not path.is_file() or path.stat().st_size > 8 * 1024**2):
                raise Halt('INVALID', 'source type/size changed')
            actual = digest(path.read_bytes()) if path.exists() else None
            if name in partial and actual in partial[name]:
                continue
            if actual == wanted:
                continue
            if (active == 'INSTALLING_TEST' and proposal and proposal['approval'] and
                    name == self.m['units'][self.s['index']]['test_slot'] and wanted is None and
                    actual == proposal['packet']['test_sha256']):
                continue
            raise Halt('INVALID', 'source changed: ' + name)

    def run_child(self):
        if not self.ready():
            raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable before direct child')
        direct = self.linked_child(); index = self.s['index']; proposal = self.s['proposal']
        unit = self.m['units'][index]
        if direct.s['phase'] in _V1_RESUMABLE:
            if not self.ready():
                raise Halt('BLOCKED', 'Qwen health/tokenizer unavailable before direct resume')
            direct = self._resume(direct, self.s['child']['root'], unit)
        if direct.s['phase'] in _V1_RESUMABLE:
            raise Halt('PAUSED', 'direct v1 runner requires another bounded resume')
        if direct.s['phase'] == 'AWAITING_REVIEW':
            self._final(direct, index, proposal)
            self.temporal(); self.source_guard()
            self._tool(['apply', '--run', str(direct.path), '--approval',
                        str(self.artifact(self.s['child']['root'] + '/approval.json'))],
                       self.artifact(self.s['child']['root'] + '/runner/apply'))
            direct = self._direct(direct.path, self.child_manifest(index, proposal))
        elif direct.s['phase'] in {'APPLYING', 'APPLIED'}:
            self._verify_final(direct, index, proposal)
            if direct.s['phase'] == 'APPLYING':
                self._tool(['apply', '--run', str(direct.path), '--approval',
                            str(self.artifact(self.s['child']['root'] + '/approval.json'))],
                           self.artifact(self.s['child']['root'] + '/runner/apply-recover'))
                direct = self._direct(direct.path, self.child_manifest(index, proposal))
        else:
            raise Halt('BLOCKED', 'direct v1 child stopped: ' + direct.s['phase'] + ': ' + str(direct.s.get('reason', '')))
        if direct.s['phase'] != 'APPLIED':
            raise Halt('BLOCKED', 'direct v1 apply did not complete: ' + direct.s['phase'])
        self._post(direct, index, proposal)
        outputs = self.child_outputs(direct, index, proposal)
        old = self.s['expected']; self.s['expected'] = {**old, **outputs}
        child = self.s['child']; self.s['child'] = None
        try:
            self.source_guard()
        finally:
            self.s['child'] = child; self.s['expected'] = old
        self.temporal()
        self._verify_post(direct, index)
        self.s['receipts'].append({'unit_id': unit['id'], 'proposal': copy.deepcopy(proposal),
                                   'child': copy.deepcopy(child), 'outputs': outputs,
                                   'child_state_sha256': digest((direct.path / 'state.json').read_bytes()),
                                   'post_sha256': digest(self.artifact(f'children/u{index}/post.json').read_bytes())})
        self.s['expected'].update(outputs); self.s['index'] += 1; self.s['attempt'] = 1
        self.s['proposal'] = None; self.s['child'] = None
        self.mark('READY', 'unit completed with independent final review and post-apply gates')
