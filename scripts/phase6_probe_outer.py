"""Finite Windows probe supervisor; ownership follows retained process handles."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as wt
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LIMIT_SECONDS = 1320
CLEANUP_SECONDS = 25


class ProcessObservationError(RuntimeError):
    pass


class WindowsProcess:
    """Hold the kernel object, including through exit/PID reuse; never kill by PID."""

    def __init__(self, pid, handle, kernel, *, owns_handle=True):
        self.pid, self.handle, self.kernel = pid, handle, kernel
        self.owns_handle = owns_handle
        stamps = [wt.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in stamps)):
            if owns_handle:
                kernel.CloseHandle(handle)
            raise ProcessObservationError('PROCESS_TIMES_FAILED')
        self.created = (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime

    def parent_pid(self):
        # Query the retained object itself: a Toolhelp row can become stale
        # between snapshot and OpenProcess when a child PID is reused.
        class BasicInfo(ctypes.Structure):
            _fields_ = [('Reserved1', ctypes.c_void_p), ('PebBaseAddress', ctypes.c_void_p),
                        ('Reserved2', ctypes.c_void_p * 2), ('UniqueProcessId', ctypes.c_size_t),
                        ('InheritedFromUniqueProcessId', ctypes.c_size_t)]
        info = BasicInfo()
        query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
        query.argtypes = [wt.HANDLE, wt.ULONG, ctypes.c_void_p, wt.ULONG, ctypes.c_void_p]
        query.restype = wt.LONG
        if query(self.handle, 0, ctypes.byref(info), ctypes.sizeof(info), None) != 0:
            raise ProcessObservationError('PROCESS_PARENT_FAILED')
        return int(info.InheritedFromUniqueProcessId)

    def alive(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise ProcessObservationError('PROCESS_WAIT_FAILED')
        return result == 258

    def exited_at(self):
        stamps = [wt.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(v) for v in stamps)):
            raise ProcessObservationError('PROCESS_TIMES_FAILED')
        ticks = (stamps[1].dwHighDateTime << 32) | stamps[1].dwLowDateTime
        return ticks or None

    def kill(self):
        if self.alive() and not self.kernel.TerminateProcess(self.handle, 2):
            # Exit between observation and termination is harmless.
            if self.alive():
                raise ProcessObservationError('PROCESS_TERMINATE_FAILED')

    def close(self):
        if self.owns_handle and self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WindowsProcesses:
    def __init__(self):
        if os.name != 'nt':
            raise ProcessObservationError('WINDOWS_REQUIRED')
        self.kernel = k = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            'GetProcessTimes': ([wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
            'TerminateProcess': ([wt.HANDLE, wt.UINT], wt.BOOL),
            'CloseHandle': ([wt.HANDLE], wt.BOOL),
            'CreateToolhelp32Snapshot': ([wt.DWORD, wt.DWORD], wt.HANDLE),
        }
        for name, (args, result) in signatures.items():
            function = getattr(k, name)
            function.argtypes, function.restype = args, result

    def root(self, process):
        return WindowsProcess(process.pid, int(process._handle), self.kernel, owns_handle=False)

    def open(self, pid):
        handle = self.kernel.OpenProcess(0x100000 | 0x0400 | 0x0001, False, pid)
        if not handle:
            # Exited children are not ownership evidence. Access-denied is unknown.
            error = ctypes.get_last_error()
            if error == 87:
                return None
            raise ProcessObservationError('PROCESS_OPEN_FAILED')
        return WindowsProcess(pid, handle, self.kernel)

    def snapshot(self):
        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                       ('th32ProcessID', wt.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                       ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                       ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', wt.LONG),
                       ('dwFlags', wt.DWORD), ('szExeFile', wt.WCHAR * 260)]
        k = self.kernel
        for name in ('Process32FirstW', 'Process32NextW'):
            function = getattr(k, name)
            function.argtypes, function.restype = [wt.HANDLE, ctypes.POINTER(Entry)], wt.BOOL
        handle = k.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise ProcessObservationError('PROCESS_SNAPSHOT_FAILED')
        try:
            entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
            rows = []
            more = k.Process32FirstW(handle, ctypes.byref(entry))
            while more:
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
                more = k.Process32NextW(handle, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ProcessObservationError('PROCESS_SNAPSHOT_FAILED')
            return rows
        finally:
            k.CloseHandle(handle)


class OwnedTree:
    def __init__(self, root, processes):
        self.root, self.processes = root, processes
        self.owned = {(root.pid, root.created): root}

    def observe(self):
        rows = self.processes.snapshot()
        # A retained parent object proves its lifetime, including after exit.
        # This also discovers children spawned just before the parent's exit.
        for _ in range(len(rows) + 1):
            added = False
            parents = {}
            for process in self.owned.values():
                parents.setdefault(process.pid, []).append(process)
            for pid, parent_pid in rows:
                possible_parents = parents.get(parent_pid, ())
                if not possible_parents or pid == parent_pid:
                    continue
                if any(p.pid == pid and p.alive() for p in self.owned.values()):
                    continue
                child = self.processes.open(pid)
                if child is None:
                    continue
                identity = (child.pid, child.created)
                try:
                    accepted = False
                    if child.parent_pid() == parent_pid and identity not in self.owned:
                        for parent in possible_parents:
                            if child.created < parent.created:
                                continue
                            if parent.alive():
                                accepted = True
                                break
                            ended = parent.exited_at()
                            if ended is None or child.created == ended:
                                # Timestamp ambiguity cannot authorize a kill.
                                raise ProcessObservationError('PROCESS_LIFETIME_AMBIGUOUS')
                            if child.created < ended:
                                accepted = True
                                break
                except Exception:
                    child.close()
                    raise
                if not accepted:
                    child.close()
                    continue
                self.owned[identity] = child
                added = True
            if not added:
                return
        raise ProcessObservationError('PROCESS_TREE_UNSTABLE')

    def remaining(self):
        return [p for p in self.owned.values() if p.alive()]

    def stop(self, deadline, *, clock=time.monotonic, sleep=time.sleep):
        failed = False
        # Reconcile terminal children after each stop wave. A child born while
        # termination is pending still must match its retained parent's lifetime.
        while clock() < deadline:
            for process in list(self.owned.values()):
                try:
                    process.kill()
                except ProcessObservationError:
                    failed = True
            try:
                self.observe()
            except Exception:
                failed = True
            if not self.remaining():
                break
            sleep(min(0.05, max(0, deadline-clock())))
        return not failed and not self.remaining()

    def close(self):
        for process in self.owned.values():
            process.close()


def supervise(command, evidence, *, raw_directory=None, limit_seconds=LIMIT_SECONDS, processes=None,
              launch=subprocess.Popen, clock=time.monotonic, sleep=time.sleep):
    if type(limit_seconds) not in (int, float) or not CLEANUP_SECONDS < limit_seconds <= LIMIT_SECONDS:
        raise ValueError('OUTER_LIMIT_INVALID')
    processes = processes or WindowsProcesses()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    raw_directory = evidence if raw_directory is None else raw_directory
    start = clock()
    deadline = start + limit_seconds
    result = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'clock_domain': 'REAL', 'limit_seconds': limit_seconds,
              'outer_timeout': False, 'ownership_complete': False,
              'owned_alive_after': None, 'exit_code': None}
    process = tree = None
    try:
        with (raw_directory/'stdout.log').open('xb') as out, (raw_directory/'stderr.log').open('xb') as err:
            process = launch(command, cwd=ROOT, stdout=out, stderr=err,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        root = processes.root(process)
        tree = OwnedTree(root, processes)
        result.update(pid=root.pid, creation_time_ticks=root.created)
        while root.alive():
            tree.observe()
            if clock() >= deadline-CLEANUP_SECONDS:
                result['outer_timeout'] = True
                break
            sleep(min(0.25, max(0, deadline-CLEANUP_SECONDS-clock())))
        tree.observe()  # Root may have spawned a child between the last two polls.
        result['ownership_complete'] = True
    except Exception:
        result['error_kind'] = 'OUTER_OBSERVATION_FAILED'
    finally:
        if tree is not None:
            try:
                clean = tree.stop(deadline, clock=clock, sleep=sleep)
                result['owned_alive_after'] = len(tree.remaining())
                result['owned_processes'] = [dict(pid=pid, creation_time_ticks=created)
                                             for pid, created in tree.owned]
                if not clean:
                    result['ownership_complete'] = False
                    result.setdefault('error_kind', 'OUTER_CLEANUP_FAILED')
            except Exception:
                result['error_kind'] = 'OUTER_CLEANUP_FAILED'
                result['owned_alive_after'] = None
            finally:
                tree.close()
        elif process is not None:
            # Root capture failed: only Popen's original handle is authorized.
            try:
                process.kill()
                process.wait(timeout=max(0.001, min(CLEANUP_SECONDS, deadline-clock())))
            except Exception:
                result['error_kind'] = 'OUTER_CLEANUP_FAILED'
        if process is not None:
            result['exit_code'] = process.poll()
        result['real_duration_sec'] = clock()-start
        result['ended_at_utc'] = datetime.now(timezone.utc).isoformat()
        for name in ('stdout', 'stderr'):
            path = raw_directory/(name+'.log')
            if path.exists():
                result[name+'_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (evidence/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Fixed task command, no generic shell/daemon or arbitrary process cleanup.
    plan = json.loads((args.output/'plan.json').read_text(encoding='utf-8'))
    expected_task = {'intent_first_v1': 'T447', 'single_shape_v1': 'T451',
                     'two_call_v1': 'T454', 'intent_choice_v1': 'T458', 'grounding_closed_v1': 'T462'}.get(plan.get('experiment'))
    if expected_task is None or plan.get('task_id') != expected_task:
        raise ValueError('OUTER_PLAN_INVALID')
    from tests.fixtures.phase6_evidence import create_private_evidence_container
    private = create_private_evidence_container(ROOT/'logs/phase6-private-evidence',
        evidence_kind='synthetic', task_id=expected_task+'OUTER', created_at_utc=datetime.now(timezone.utc))
    with (args.output/'outer-private-locator.json').open('x', encoding='utf-8') as locator:
        json.dump({'path': str(private)}, locator)
    runner = {'two_call_v1': 'phase6_two_call_runner.py',
              'intent_choice_v1': 'phase6_intent_choice_runner.py',
              'grounding_closed_v1': 'phase6_grounding_closed_runner.py'}.get(
                  plan['experiment'], 'phase6_model_comparison.py')
    command = [sys.executable, str(ROOT/'scripts'/runner),
               '--output', str(args.output.resolve()), '--run', 'qw9']
    result = supervise(command, args.output/'outer-qw9', raw_directory=private)
    print(json.dumps({key: result.get(key) for key in
                     ('exit_code', 'outer_timeout', 'ownership_complete', 'owned_alive_after', 'error_kind')}))
    return 0 if (result['exit_code'] == 0 and not result['outer_timeout']
                 and result['ownership_complete'] and result['owned_alive_after'] == 0
                 and 'error_kind' not in result) else 2


if __name__ == '__main__':
    raise SystemExit(main())
