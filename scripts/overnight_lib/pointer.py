"""Fail-closed local launch recovery. All selection runs under the repository lock."""
import json
import os
import time
import uuid
from pathlib import Path

from .policy import Error, digest, loads, validate


def source_from_temps(local, runs_root, class_for):
    """Recover the missing initial pointer only from one fully verified run hint."""
    local=Path(local);root=Path(runs_root).resolve()
    hints=list(local.parent.glob(local.name+'.*.tmp'))
    if not hints or len(hints)>100: raise Error('LOCAL_POINTER_RECOVERY_INVALID: missing/too many tmp hints')
    sources=set()
    for hint in hints:
        if hint.is_symlink() or hint.stat().st_size>16384: raise Error('LOCAL_POINTER_RECOVERY_INVALID: unsafe tmp')
        value=loads(hint.read_bytes())
        if set(value)!={'run'} or not isinstance(value['run'],str):raise Error('LOCAL_POINTER_RECOVERY_INVALID: invalid tmp')
        path=Path(value['run'])
        if not path.is_absolute() or path.resolve().parent!=root:raise Error('LOCAL_POINTER_RECOVERY_INVALID: run root mismatch')
        package=path/'package.json'
        if (path.is_symlink() or getattr(path,'is_junction',lambda:False)() or package.is_symlink() or
                package.resolve().parent!=path.resolve() or package.stat().st_size>4*1024**2):
            raise Error('LOCAL_POINTER_RECOVERY_INVALID: unsafe package')
        raw=package.read_bytes()
        m=loads(raw);b=validate(m,initial=False)
        b['task_contract'].safe_path(local.parent,hint.name)
        b['task_contract'].safe_path(root,path.name)
        if Path(m['repo_root']).resolve()!=local.parent.resolve():raise Error('LOCAL_POINTER_RECOVERY_INVALID: repo mismatch')
        c=class_for(m)(path)
        sources.add(Path(c.s['package_source']).resolve())
    if len(sources)!=1:raise Error('LOCAL_POINTER_RECOVERY_INVALID: ambiguous tmp sources')
    return sources.pop()


def write_pointer(local, run, replace=os.replace, sleep=time.sleep):
    local, run = Path(local), Path(run).resolve(strict=True)
    tmp = local.with_name(local.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('xb') as stream:
        stream.write((json.dumps({'run': str(run)}, indent=2) + '\n').encode('utf-8'))
        stream.flush(); os.fsync(stream.fileno())
    for attempt in range(4):
        try:
            replace(tmp, local)
            return
        except PermissionError as error:
            if attempt < 3:
                sleep(.1 * (attempt + 1))
                continue
            raise Error('LOCAL_POINTER_UPDATE_FAILED: run preserved at ' + str(run) +
                        '; resume with: python scripts/run_overnight.py resume --run "' +
                        str(run) + '"; recovery tmp: ' + str(tmp)) from error


def find_run(cls, source, runs_root, local=None, **hooks):
    source = Path(source).resolve(strict=True)
    raw = source.read_bytes(); m = loads(raw); wanted = digest(raw)
    b = validate(m, initial=False)
    safe = b['task_contract'].safe_path
    safe(Path(runs_root).absolute().parent, Path(runs_root).name)
    root = Path(runs_root).resolve()
    candidates = set(root.glob('overnight-*')) if root.exists() else set()
    if len(candidates) > 10000: raise Error('ORPHAN_RUN_INVALID: run discovery limit')
    if local is not None:
        hints = list(Path(local).parent.glob(Path(local).name + '.*.tmp'))
        if len(hints) > 100: raise Error('LOCAL_POINTER_RECOVERY_INVALID: tmp discovery limit')
        for tmp in hints:
            safe(tmp.parent, tmp.name)
            if tmp.is_symlink() or tmp.stat().st_size > 16384:
                raise Error('LOCAL_POINTER_RECOVERY_INVALID: unsafe tmp ' + str(tmp))
            try:
                hint = loads(tmp.read_bytes())
                if set(hint) != {'run'} or not isinstance(hint['run'], str): raise ValueError('invalid pointer')
                target = Path(hint['run'])
                if not target.is_absolute(): raise ValueError('relative run')
                # A stale tmp from another runs root is not authorization to switch roots.
                if target.resolve().parent == root: candidates.add(target)
            except (ValueError, TypeError) as error:
                raise Error('LOCAL_POINTER_RECOVERY_INVALID: ' + str(tmp)) from error
    found = []
    for path in sorted(candidates):
        safe(root, path.name)
        if path.is_symlink() or path.resolve().parent != root:
            raise Error('ORPHAN_RUN_INVALID: escaped run path')
        package = path / 'package.json'
        if not package.is_file():
            # An incomplete directory cannot safely be assumed unrelated.
            raise Error('ORPHAN_RUN_INVALID: incomplete run ' + str(path))
        try:
            if package.is_symlink() or package.stat().st_size > 4*1024**2: raise ValueError('unsafe package')
            saved = loads(package.read_bytes())
            if digest(saved) != digest(m): continue
            state_path = path / 'state.json'
            if state_path.is_symlink() or state_path.stat().st_size > 32*1024**2: raise ValueError('unsafe state')
            state = loads(state_path.read_bytes())
            if Path(state['package_source']).resolve() != source or state['source_sha256'] != wanted:
                raise ValueError('matching package has different source identity')
            found.append(cls(path, **hooks))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise Error('ORPHAN_RUN_INVALID: ' + str(path) + ': ' + str(error)) from error
    if len(found) > 1: raise Error('ORPHAN_RUN_AMBIGUOUS: multiple runs for the same package; no new run created')
    return found[0] if found else None
