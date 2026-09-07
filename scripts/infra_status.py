"""Read-only infrastructure gate/status, independent of the local LLM installation."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def check_gate(root: Path) -> tuple[bool, str]:
    try:
        metadata = json.loads((root / 'Docs/ai/infra/runner.json').read_text(encoding='utf-8'))
        if metadata['design_gate'] != 'REQUIRED' or metadata['reviewer_model'] != 'gpt-5.6-sol':
            return False, 'invalid infrastructure gate/reviewer'
        for key in ('design', 'review'):
            path = root / metadata[key]
            if not path.resolve().is_relative_to(root.resolve()):
                return False, 'gate path escapes repository'
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != metadata[key + '_sha256'].lower():
                return False, key + ' changed since approval'
        design = (root / metadata['design']).read_text(encoding='utf-8')
        review = (root / metadata['review']).read_text(encoding='utf-8')
        if '\nStatus: APPROVED\n' not in design or 'Verdict: APPROVED' not in review:
            return False, 'missing approval'
        if metadata['design_sha256'].lower() not in review.lower():
            return False, 'review does not bind this design'
        return True, metadata['design']
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'missing or malformed infrastructure gate'


def display(root: Path, run: Path | None = None) -> int:
    valid, detail = check_gate(root)
    print('INFRASTRUCTURE — Qwen-first task runner')
    print('Design gate: ' + ('APPROVED' if valid else 'BLOCKED'))
    print(detail)
    print('Runner: python C:/AIagent/agent/tools/task.py run --contract <trusted-contract.json>')
    print('Guide: Docs/ai/spec/LOCAL_IMPLEMENTATION_RUNNER.md')
    print('Game Phase gate: separate; this does not approve game implementation.')
    if run:
        try:
            state = json.loads((run / 'state.json').read_text(encoding='utf-8'))
            print(json.dumps({key: state.get(key) for key in ('run_id', 'phase', 'risk', 'attempt', 'reason')}, ensure_ascii=False))
            print('Evidence: ' + str(run / 'handoff.json'))
        except (OSError, ValueError):
            print('Run state: unavailable')
            return 1
    return 0 if valid else 1
