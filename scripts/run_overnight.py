"""Run an approved workpackage with Sol planning, independent review and Qwen implementation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

from autodev_lib.policy import ROOT, loads, check_design as check_child_design
from overnight_lib.policy import Error, validate, check_design, universe, allocation, head, runtime_paths
from overnight_lib.engine import Controller
from overnight_lib.report import report


def doctor(path):
    m = loads(path.read_bytes()); b = validate(m); check_design(); check_child_design()
    source = universe(m, b); head(Path(m['repo_root']))
    ready = b['llm'].alive() and b['llm'].tokenizer_available()
    allocations = {u['id']: allocation(m, u) for u in m['units']}
    needed = set(source)
    for role, config in m['providers'].items():
        if needed - set(config['cloud_read']): raise Error(role + ' cloud_read omits package inputs/test slots')
    return {'ready': ready, 'units': len(m['units']), 'allocations': allocations,
            'provider_calls': 0, 'reason': 'ready' if ready else 'Start the shared Qwen server first; health/tokenizer unavailable',
            'meaning': 'Readiness only; no implementation or Phase approval.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['start', 'resume', 'status', 'stop', 'report', 'doctor'])
    parser.add_argument('--package', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--runs-root', type=Path, default=Path.home() / '.aiwolf-runs')
    parser.add_argument('--clear-stop', action='store_true')
    args = parser.parse_args(argv)
    local = ROOT / 'overnight.local.json'; from_local = args.action is None
    if from_local:
        if args.package or args.run or args.clear_stop: raise Error('explicit action required with flags')
        if not local.is_file():
            raise Error('No workpackage configured. Use: python scripts/run_overnight.py doctor --package <approved-package.json>')
        config = loads(local.read_bytes())
        if set(config) == {'package'}: args.action = 'start'; args.package = Path(config['package'])
        elif set(config) == {'run'}: args.action = 'resume'; args.run = Path(config['run'])
        else: raise Error('overnight.local.json requires exactly package or run')
        target = args.package or args.run
        if not target.is_absolute(): raise Error('local config requires absolute paths')
    if args.clear_stop and args.action != 'resume': raise Error('--clear-stop is resume only')
    if args.action in ('start', 'doctor'):
        if not args.package or args.run: raise Error('--package required; no --run')
        runtime_paths(loads(args.package.read_bytes()), args.runs_root)
        result = doctor(args.package)
        if args.action == 'doctor' or not result['ready']:
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['ready'] else 3
        def created(path):
            if from_local:
                b = validate(loads(args.package.read_bytes()), initial=False)
                b['task_state'].write_json(local, {'run': str(path.resolve(strict=True))})
        c = Controller.launch(args.package, args.runs_root, on_created=created)
    else:
        if not args.run or args.package: raise Error('--run required; no --package')
        c = Controller(args.run)
        if args.action == 'resume':
            c.drive(clear_stop=args.clear_stop)
        elif args.action == 'stop': c.request_stop()
    result = report(c) if args.action == 'report' else {
        'run': str(c.path), 'phase': c.s['phase'], 'reason': c.s['reason'],
        'completed_units': c.s['index'], 'total_units': len(c.m['units']), 'deadline': c.s['deadline']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.action in ('start', 'resume'): return 0 if c.s['phase'] == 'COMPLETE' else 4 if c.s['phase'] == 'INVALID' else 3
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
    except Exception as error:
        print(str(error), file=sys.stderr); raise SystemExit(5 if 'lock busy' in str(error) else 4)
