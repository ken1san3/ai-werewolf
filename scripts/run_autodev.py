"""One invocation of the existing D058 CLI, followed by its token-free report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from autodev_lib.engine import ACTIVE, TERMINAL
from autodev_lib.policy import loads

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'autodev.local.json'


def target(args):
    if args.manifest or args.campaign:
        kind = 'manifest' if args.manifest else 'campaign'
        value = args.manifest or args.campaign
    else:
        if not CONFIG.is_file():
            raise ValueError('Create autodev.local.json using autodev.example.json, or specify --manifest FILE / --campaign DIR.')
        data = loads(CONFIG.read_text(encoding='utf-8'))
        if set(data) not in ({'manifest'}, {'campaign'}):
            raise ValueError('autodev.local.json must contain exactly one key: manifest or campaign')
        kind, value = next(iter(data.items()))
    if not isinstance(value, str) or not value.strip():
        raise ValueError('A nonempty target path is required')
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not (path.is_file() if kind == 'manifest' else path.is_dir()):
        raise ValueError('Target does not exist: ' + str(path))
    return kind, path


def invoke(*args):
    # Paths are arguments, never command text. Keep the original child exit code.
    argv = [sys.executable, str(ROOT / 'scripts/autodev.py'), *map(str, args)]
    # Inherit stderr so it cannot fill an unread pipe while stdout is streamed.
    lines = []
    with subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace', bufsize=1) as child:
        for line in child.stdout:
            print(line, end='', flush=True)
            lines.append(line)
        return subprocess.CompletedProcess(argv, child.wait(), ''.join(lines), '')


def run(kind, path, check=False, call=invoke):
    if kind == 'manifest':
        checked = call('doctor', '--manifest', path)
        if checked.returncode or check:
            return checked.returncode
        primary = call('start', '--manifest', path)
        # start emits an initial campaign receipt and then its final JSON line.
        campaign = None
        for line in primary.stdout.splitlines():
            try:
                item = loads(line)
            except ValueError:
                continue
            if isinstance(item.get('campaign'), str):
                candidate = Path(item['campaign'])
                if candidate.is_absolute():
                    campaign = candidate
        if campaign is None:
            return primary.returncode or 4
        print('For continuation, set autodev.local.json to: ' + json.dumps({'campaign': str(campaign)}, ensure_ascii=False))
    else:
        campaign = path
        checked = call('status', '--campaign', campaign)
        if checked.returncode or check:
            return checked.returncode
        phase = loads(checked.stdout).get('phase')
        if phase in TERMINAL:
            primary = subprocess.CompletedProcess([], 0 if phase == 'COMPLETE' else 4 if phase == 'INVALID' else 3)
        elif phase in ACTIVE | {'PAUSED', 'PAUSED_QUOTA'}:
            primary = call('resume', '--campaign', campaign)
        else:
            raise ValueError('Unknown campaign phase; nothing resumed')
    reported = call('report', '--campaign', campaign)
    return primary.returncode or reported.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--manifest')
    group.add_argument('--campaign')
    parser.add_argument('--check', action='store_true', help='only doctor/status; no execution')
    args = parser.parse_args(argv)
    try:
        return run(*target(args), check=args.check)
    except KeyboardInterrupt:
        print('Interrupted. Inspect the existing campaign before continuing.', file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
