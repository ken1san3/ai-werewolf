"""Run approved Qwen-first workpackages with independent review and durable recovery."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

from autodev_lib.policy import ROOT, loads, check_design as check_child_design
from overnight_lib.policy import Error, validate, check_design, universe, allocation, head, runtime_paths, check_version
from overnight_lib.engine import Controller
from overnight_lib.report import report
from overnight_lib.pointer import write_pointer, source_from_temps


def doctor(path):
    m = loads(path.read_bytes()); b = validate(m); check_design(); check_child_design()
    check_version(m)
    source = universe(m, b); head(Path(m['repo_root']))
    ready = b['llm'].alive() and b['llm'].tokenizer_available()
    allocations = {u['id']: allocation(m, u) for u in m['units']}
    needed = set(source)
    for role, config in m['providers'].items():
        if needed - set(config['cloud_read']): raise Error(role + ' cloud_read omits package inputs/test slots')
    return {'ready': ready, 'units': len(m['units']), 'allocations': allocations,
            'provider_calls': 0, 'reason': 'ready' if ready else 'Start the shared Qwen server first; health/tokenizer unavailable',
            'meaning': 'Readiness only; no implementation or Phase approval.'}


def controller_for(m):
    if m['version'] == 2:
        from overnight_lib.efficient import EfficientController
        return EfficientController
    return Controller


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['start', 'resume', 'status', 'stop', 'report', 'doctor', 'recover'])
    parser.add_argument('--package', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--runs-root', type=Path, default=Path.home() / '.aiwolf-runs')
    parser.add_argument('--clear-stop', action='store_true')
    args = parser.parse_args(argv)
    requested = args.action
    local = ROOT / 'overnight.local.json'
    from_local = args.action is None or args.action == 'recover' or (
        args.action in ('status','report','stop','resume') and args.run is None and args.package is None)
    recover = args.action == 'recover'
    if from_local:
        if args.package or args.run or (args.clear_stop and requested != 'resume'): raise Error('explicit action required with flags')
        if not local.is_file():
            if not list(local.parent.glob(local.name+'.*.tmp')):
                raise Error('No workpackage configured. Use: python scripts/run_overnight.py doctor --package <approved-package.json>')
            config = {'package':str(source_from_temps(local,args.runs_root,controller_for))}
        else: config = loads(local.read_bytes())
        if set(config) == {'package'}:
            if requested not in (None,'recover'): raise Error('NOT_STARTED: no run pointer; use Run-Overnight.cmd recover if a launch failed, otherwise Run-Overnight.cmd to start')
            args.action = 'start'; args.package = Path(config['package'])
        elif set(config) == {'run'}: args.action = 'resume' if requested in (None,'recover') else requested; args.run = Path(config['run'])
        else: raise Error('overnight.local.json requires exactly package or run')
        target = args.package or args.run
        if not target.is_absolute(): raise Error('local config requires absolute paths')
    if args.clear_stop and args.action != 'resume': raise Error('--clear-stop is resume only')
    if args.action in ('start', 'doctor'):
        if not args.package or args.run: raise Error('--package required; no --run')
        runtime_paths(loads(args.package.read_bytes()), args.runs_root)
        if args.action == 'doctor':
            result = doctor(args.package)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result['ready'] else 3
        def created(path):
            if from_local:
                write_pointer(local, path)
        cls = controller_for(loads(args.package.read_bytes()))
        c = cls.launch(args.package, args.runs_root, on_created=created,
                              local_pointer=local if from_local else None, prepare_only=recover)
    else:
        if not args.run or args.package: raise Error('--run required; no --package')
        c = controller_for(loads((args.run/'package.json').read_bytes()))(args.run)
        if args.action == 'resume' and not recover:
            c.drive(clear_stop=args.clear_stop)
        elif args.action == 'stop': c.request_stop()
    result = report(c) if args.action == 'report' else {
        'run': str(c.path), 'phase': c.s['phase'], 'reason': c.s['reason'],
        'completed_units': c.s['index'], 'total_units': len(c.m['units']), 'deadline': c.s['deadline']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.action in ('start', 'resume') and not recover: return 0 if c.s['phase'] == 'COMPLETE' else 4 if c.s['phase'] == 'INVALID' else 3
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'): stream.reconfigure(encoding='utf-8')
    try: raise SystemExit(main())
    except KeyboardInterrupt: raise SystemExit(130)
    except Exception as error:
        print(str(error), file=sys.stderr); raise SystemExit(5 if 'lock busy' in str(error) else 4)
