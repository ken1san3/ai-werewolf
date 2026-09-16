from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.windows_private

from scripts import run_phase5_local_smoke as runner
from tests.fixtures.phase6_evidence import create_private_evidence_container
from tests.fixtures import phase6_evidence


WORK_A = b"T257-pytest-owned-work-a\x00\xff\n"
WORK_B = b"T257-pytest-owned-work-b\x00\xfe\n"
PRIVATE = b"T257-durable-private-raw\x00\xfd\n"


def test_private_evidence_session_a(tmp_path, tmp_path_factory):
    work = tmp_path / "work-a.raw"
    work.write_bytes(WORK_A)
    base = runner.PROJECT_ROOT / "logs" / "phase6-private-evidence"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    container = create_private_evidence_container(
        base,
        pytest_basetemp=tmp_path_factory.getbasetemp(),
        evidence_kind="synthetic",
        task_id="T293",
        created_at_utc=datetime.now(timezone.utc),
    )
    raw = container / "synthetic.raw"
    raw.write_bytes(PRIVATE)
    print(json.dumps({"work": str(work), "container": str(container), "raw": str(raw)}))


def test_private_evidence_session_b(tmp_path):
    work = tmp_path / "work-b.raw"
    work.write_bytes(WORK_B)
    print(json.dumps({"work": str(work)}))


def _run_pytest(node: str, basetemp: Path, observation: Path, stage: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "pytest", node, "-q", "-s", "-p", "no:cacheprovider",
            "--basetemp", str(basetemp)]
    started_at_utc = datetime.now(timezone.utc).isoformat()
    metadata = {"argv": argv, "started_at_utc": started_at_utc}
    runner._write_private_json_atomic(observation / f"{stage}-launch.json", metadata)
    stdout_path, stderr_path = observation / f"{stage}.stdout", observation / f"{stage}.stderr"
    with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
        process = subprocess.Popen(
            argv, cwd=runner.PROJECT_ROOT, stdout=stdout_file, stderr=stderr_file,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        metadata["pid"] = process.pid
        try:
            runner._write_private_json_atomic(observation / f"{stage}-started.json", metadata)
            process.wait(timeout=40)
        except BaseException:
            try:
                cleanup = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False, capture_output=True, text=True, timeout=10,
                )
                metadata["cleanup"] = {"exit": cleanup.returncode,
                                       "stdout": cleanup.stdout, "stderr": cleanup.stderr}
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            raise
        finally:
            metadata.update(ended_at_utc=datetime.now(timezone.utc).isoformat(),
                            returncode=process.poll())
            runner._write_private_json_atomic(observation / f"{stage}-finished.json", metadata)
    stdout, stderr = stdout_path.read_text(), stderr_path.read_text()
    completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    completed.pid = process.pid
    completed.started_at_utc = started_at_utc
    completed.ended_at_utc = datetime.now(timezone.utc).isoformat()
    return completed


def _payload(completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
    return json.loads(next(line for line in completed.stdout.splitlines() if line.startswith("{")))


def test_private_evidence_survives_real_pytest_cleanup(tmp_path):
    workroot = tmp_path / "owned-retention-workroot"
    workroot.mkdir()
    basetemp = workroot / "shared-basetemp"
    base = runner.PROJECT_ROOT / "logs" / "phase6-private-evidence"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    observation = create_private_evidence_container(base, pytest_basetemp=basetemp)

    session_a = _run_pytest(
        "tests/test_phase6_evidence_retention.py::test_private_evidence_session_a", basetemp,
        observation, "session-a",
    )
    assert session_a.returncode == 0, (session_a.stdout, session_a.stderr)
    a = _payload(session_a)
    work_a, container, raw = Path(a["work"]), Path(a["container"]), Path(a["raw"])
    expected_a = hashlib.sha256(WORK_A).hexdigest()
    expected_private = hashlib.sha256(PRIVATE).hexdigest()
    assert work_a.read_bytes() == WORK_A
    assert hashlib.sha256(work_a.read_bytes()).hexdigest() == expected_a
    assert raw.read_bytes() == PRIVATE
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == expected_private
    runner._write_private_json_atomic(observation / "after-a-before-b.json", {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "work_a": str(work_a), "work_a_bytes": len(work_a.read_bytes()),
        "work_a_sha256": hashlib.sha256(work_a.read_bytes()).hexdigest(),
        "private_raw": str(raw), "private_bytes": len(raw.read_bytes()),
        "private_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
    })

    session_b = _run_pytest(
        "tests/test_phase6_evidence_retention.py::test_private_evidence_session_b", basetemp,
        observation, "session-b",
    )
    assert session_b.returncode == 0, (session_b.stdout, session_b.stderr)
    b = _payload(session_b)
    work_b = Path(b["work"])
    assert not work_a.exists()
    assert work_b.read_bytes() == WORK_B
    assert hashlib.sha256(work_b.read_bytes()).hexdigest() == hashlib.sha256(WORK_B).hexdigest()
    assert raw.read_bytes() == PRIVATE
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == expected_private

    evidence = {
        "schema": "aiwolf.phase6-retention-probe.v2",
        "basetemp": str(basetemp),
        "container": str(container),
        "observation": str(observation),
        "observed_after_b_at_utc": datetime.now(timezone.utc).isoformat(),
        "work_a_removed_by_session_b": True,
        "work_a_bytes": len(WORK_A),
        "work_a_sha256": expected_a,
        "work_b_bytes": len(WORK_B),
        "work_b_sha256": hashlib.sha256(WORK_B).hexdigest(),
        "private_bytes": len(PRIVATE),
        "private_sha256": expected_private,
        "session_a_returncode": session_a.returncode,
        "session_b_returncode": session_b.returncode,
        "session_a_argv": session_a.args,
        "session_b_argv": session_b.args,
        "session_a_pid": session_a.pid,
        "session_b_pid": session_b.pid,
        "session_a_started_at_utc": session_a.started_at_utc,
        "session_b_started_at_utc": session_b.started_at_utc,
        "session_a_ended_at_utc": session_a.ended_at_utc,
        "session_b_ended_at_utc": session_b.ended_at_utc,
        "session_a_stdout": session_a.stdout,
        "session_a_stderr": session_a.stderr,
        "session_b_stdout": session_b.stdout,
        "session_b_stderr": session_b.stderr,
    }
    runner._write_private_json_atomic(container / "retention-evidence.json", evidence)


def test_private_evidence_rejects_wrong_anchor_and_overlap(tmp_path):
    fake = tmp_path / "logs" / "phase6-private-evidence"
    fake.mkdir(parents=True)
    with pytest.raises(ValueError, match="dedicated logs"):
        create_private_evidence_container(fake)
    base = runner.PROJECT_ROOT / "logs" / "phase6-private-evidence"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pytest.raises(ValueError, match="overlaps"):
        create_private_evidence_container(base, pytest_basetemp=base / "pytest-work")


def test_phase6_evidence_path_partition_and_legacy_compatibility(tmp_path, monkeypatch):
    durable_base = runner.PROJECT_ROOT / "logs" / "phase6-private-evidence"
    durable_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc)
    container = create_private_evidence_container(
        durable_base, pytest_basetemp=tmp_path,
        evidence_kind="synthetic", task_id="T296", created_at_utc=stamp,
    )
    base = container / "sandbox"
    base.mkdir(mode=0o700)
    monkeypatch.setattr(phase6_evidence, "_EVIDENCE_BASE", base.absolute())
    game = create_private_evidence_container(
        base,
        pytest_basetemp=tmp_path,
        evidence_kind="game",
        task_id="T296",
        created_at_utc=stamp,
    )
    synthetic = create_private_evidence_container(
        base,
        pytest_basetemp=tmp_path,
        evidence_kind="synthetic",
        task_id="T296",
        created_at_utc=stamp,
    )
    legacy = create_private_evidence_container(base, pytest_basetemp=tmp_path)
    assert game.parent.name == "game"
    assert synthetic.parent.name == "synthetic"
    assert game.name == synthetic.name == f"T296-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}"
    assert legacy.parent == base.resolve(strict=True)
    assert legacy.name.startswith("p6f-private-evidence-")
    with pytest.raises(FileExistsError):
        create_private_evidence_container(
            base,
            pytest_basetemp=tmp_path,
            evidence_kind="game",
            task_id="T296",
            created_at_utc=stamp,
        )
    for changes in (
        {"evidence_kind": "other", "task_id": "T296", "created_at_utc": stamp},
        {"evidence_kind": "synthetic", "task_id": "bad", "created_at_utc": stamp},
        {"evidence_kind": "synthetic", "task_id": "T296", "created_at_utc": stamp.replace(tzinfo=None)},
        {"evidence_kind": "synthetic", "task_id": None, "created_at_utc": stamp},
    ):
        with pytest.raises(ValueError):
            create_private_evidence_container(base, pytest_basetemp=tmp_path / "work", **changes)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="dedicated logs"):
        create_private_evidence_container(outside)
