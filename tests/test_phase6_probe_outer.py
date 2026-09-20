import json
import subprocess
import sys

import pytest

from scripts import phase6_probe_outer as outer


class Process:
    def __init__(self, pid, created, parent=0):
        self.pid, self.created, self.parent = pid, created, parent
        self.running, self.closed, self.kills = True, False, 0
        self.exit_ticks = created + 50

    def alive(self): return self.running
    def parent_pid(self): return self.parent
    def exited_at(self): return None if self.running else self.exit_ticks
    def kill(self): self.kills += 1; self.running = False
    def close(self): self.closed = True


class Table:
    def __init__(self, rows, objects): self.rows, self.objects = rows, objects
    def snapshot(self): return self.rows
    def open(self, pid): return self.objects.get(pid)


def test_stale_parent_pid_does_not_adopt_preexisting_unowned_process():
    root = Process(10, 100)
    owned = Process(11, 110, 10)
    unowned = Process(12, 20, 10)  # Original K1 failure: old parent PID.
    table = Table([(12, 10), (11, 10)], {11: owned, 12: unowned})
    tree = outer.OwnedTree(root, table)
    tree.observe()
    assert set(tree.owned) == {(10, 100), (11, 110)}
    assert unowned.closed and not unowned.kills
    assert tree.stop(1, clock=lambda: 0)
    assert root.kills == owned.kills == 1 and unowned.kills == 0


def test_reused_dead_parent_pid_cannot_authorize_new_children():
    root = Process(10, 100); root.running = False
    stranger = Process(11, 200, 10)
    table = Table([(11, 10)], {11: stranger})
    tree = outer.OwnedTree(root, table); tree.observe()
    assert set(tree.owned) == {(10, 100)}
    assert stranger.closed and not stranger.kills  # Read-only identity handle was released.


def test_child_pid_reused_between_snapshot_and_open_checks_actual_parent():
    root = Process(10, 100)
    stranger = Process(11, 200, 99)
    table = Table([(11, 10)], {11: stranger})
    tree = outer.OwnedTree(root, table); tree.observe()
    assert set(tree.owned) == {(10, 100)} and stranger.closed
    tree.stop(1, clock=lambda: 0)
    assert stranger.kills == 0


def test_parent_exits_during_child_open_cannot_authorize_reused_pid():
    root = Process(10, 100); stranger = Process(11, 200, 10)
    table = Table([(11, 10)], {11: stranger})
    def open_child(pid): root.running = False; return stranger
    table.open = open_child
    tree = outer.OwnedTree(root, table); tree.observe()
    assert set(tree.owned) == {(10, 100)} and stranger.closed
    assert stranger.kills == 0


def test_terminal_sweep_adopts_child_created_before_retained_parent_exit():
    root = Process(10, 100); root.running = False; root.exit_ticks = 150
    child = Process(11, 120, 10)
    table = Table([(11, 10)], {11: child})
    tree = outer.OwnedTree(root, table); tree.observe()
    assert set(tree.owned) == {(10, 100), (11, 120)}
    assert tree.stop(1, clock=lambda: 0) and child.kills == 1


def test_equal_exit_creation_timestamp_is_unknown_never_killed():
    root = Process(10, 100); root.running = False; root.exit_ticks = 150
    child = Process(11, 150, 10)
    tree = outer.OwnedTree(root, Table([(11, 10)], {11:child}))
    with pytest.raises(outer.ProcessObservationError, match='LIFETIME_AMBIGUOUS'):
        tree.observe()
    assert child.closed and child.kills == 0 and len(tree.owned) == 1


def test_supervisor_sweeps_child_spawned_between_last_snapshot_and_root_exit(tmp_path):
    root = Process(10, 100); child = Process(11, 120, 10)
    table = Table([], {11:child}); table.root = lambda _: root
    def sleep(_):
        table.rows = [(11, 10)]
        root.running = False
    class Popen:
        def poll(self): return 0
    result = outer.supervise([], tmp_path/'terminal', processes=table,
                             launch=lambda *a, **kw: Popen(), sleep=sleep)
    assert result['ownership_complete'] and result['owned_alive_after'] == 0
    assert len(result['owned_processes']) == 2 and child.kills == 1


def test_retained_child_handle_never_switches_to_new_process_at_same_pid():
    root = Process(10, 100); old = Process(11, 110, 10)
    table = Table([(11, 10)], {11: old})
    tree = outer.OwnedTree(root, table); tree.observe()
    old.running = False
    new = Process(11, 200, 99); table.objects[11] = new
    tree.observe(); tree.stop(1, clock=lambda: 0)
    assert tree.owned[(11, 110)] is old and new.kills == 0


def test_nested_owned_children_and_parent_query_failure():
    root = Process(10, 100); child = Process(11, 110, 10); grandchild = Process(12, 120, 11)
    table = Table([(12, 11), (11, 10)], {11: child, 12: grandchild})
    tree = outer.OwnedTree(root, table); tree.observe()
    assert len(tree.owned) == 3
    assert tree.stop(1, clock=lambda: 0)
    assert grandchild.kills == 1
    tree.close(); assert child.closed and grandchild.closed


def test_observation_failure_does_not_claim_zero_or_expose_exception(tmp_path):
    root = Process(10, 100)
    table = Table([], {})
    table.root = lambda _: root
    def fail(): raise RuntimeError('PRIVATE_PATH')
    table.snapshot = fail
    class Popen:
        def poll(self): return 2
    result = outer.supervise([], tmp_path/'outer', processes=table,
                             launch=lambda *a, **kw: Popen())
    assert not result['ownership_complete'] and result['error_kind'] == 'OUTER_OBSERVATION_FAILED'
    assert root.kills == 1 and 'PRIVATE_PATH' not in json.dumps(result)


def test_timeout_is_finite_and_runs_once(tmp_path):
    root = Process(10, 100); table = Table([], {}); table.root = lambda _: root
    now = [0]
    class Popen:
        def poll(self): return 2 if not root.running else None
    launches = []
    def launch(*a, **kw): launches.append(1); return Popen()
    result = outer.supervise([], tmp_path/'outer', limit_seconds=26, processes=table,
                            launch=launch, clock=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0]+n))
    assert result['outer_timeout'] and result['real_duration_sec'] <= 26
    assert result['owned_alive_after'] == 0 and len(launches) == 1
    with pytest.raises(FileExistsError):
        outer.supervise([], tmp_path/'outer', processes=table, launch=launch)
    assert len(launches) == 1


@pytest.mark.parametrize('limit', [True, 0, 25, 1321, float('nan'), float('inf')])
def test_invalid_outer_budget_rejected(limit, tmp_path):
    with pytest.raises(ValueError, match='OUTER_LIMIT_INVALID'):
        outer.supervise([], tmp_path/'outer', limit_seconds=limit)


def test_native_retained_process_handle(tmp_path):
    if sys.platform != 'win32':
        # The tool intentionally rejects unsupported hosts, with no launch.
        with pytest.raises(outer.ProcessObservationError, match='WINDOWS_REQUIRED'):
            outer.WindowsProcesses()
        return
    native = outer.WindowsProcesses()
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'],
                            creationflags=subprocess.CREATE_NO_WINDOW)
    root = handle = None
    try:
        root = native.root(proc); handle = native.open(proc.pid)
        assert handle.created == root.created and handle.alive()
        assert handle.parent_pid() > 0
        assert any(pid == proc.pid for pid, _ in native.snapshot())
        handle.kill(); proc.wait(timeout=5)
        assert not root.alive() and not handle.alive()
    finally:
        if proc.poll() is None: proc.kill(); proc.wait(timeout=5)
        if handle is not None: handle.close()
        if root is not None: root.close()


@pytest.mark.parametrize('root_delay', [0, 1.5])
def test_native_supervisor_cleans_only_observed_owned_child(tmp_path, root_delay):
    if sys.platform != 'win32':
        with pytest.raises(outer.ProcessObservationError, match='WINDOWS_REQUIRED'):
            outer.WindowsProcesses()
        return
    outsider = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    command = [sys.executable, '-c',
               'import subprocess,sys,time; '
               'subprocess.Popen([sys.executable,"-c","import time;time.sleep(15)"]); '
               f'time.sleep({root_delay})']
    try:
        result = outer.supervise(command, tmp_path/'supervised')
        assert result['exit_code'] == 0 and not result['outer_timeout']
        assert result['ownership_complete'] and result['owned_alive_after'] == 0
        assert len(result['owned_processes']) >= 2 and 'error_kind' not in result
        assert outsider.poll() is None
        assert outsider.pid not in {p['pid'] for p in result['owned_processes']}
    finally:
        if outsider.poll() is None: outsider.kill(); outsider.wait(timeout=5)
