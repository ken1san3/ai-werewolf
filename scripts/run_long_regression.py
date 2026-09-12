#!/usr/bin/env python3
"""Repeat ordinary pytest runs for a bounded duration and preserve evidence.

This is deliberately a test wrapper, not a development scheduler: it never edits source,
dispatches agents, resumes work, or merges changes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=7.0, help="overall hard limit")
    parser.add_argument(
        "--per-run-timeout",
        type=float,
        default=900.0,
        help="hard limit in seconds for each pytest process",
    )
    parser.add_argument("--max-runs", type=int, help="optional cycle cap")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="pytest file/node target; repeat for multiple targets",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="continue after a normal pytest assertion failure",
    )
    parser.add_argument(
        "--log-root",
        type=Path,
        default=ROOT / "logs" / "long-regression",
        help="evidence directory (defaults to ignored logs/)",
    )
    args = parser.parse_args()
    if args.hours <= 0:
        parser.error("--hours must be positive")
    if args.per_run_timeout <= 0:
        parser.error("--per-run-timeout must be positive")
    if args.max_runs is not None and args.max_runs <= 0:
        parser.error("--max-runs must be positive")
    return args


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout.strip() or result.stderr.strip() or "(unavailable)"


def summarize_git(status: str) -> str:
    lines = status.splitlines()
    if not lines:
        return "(unavailable)"
    dirty = sum(1 for line in lines[1:] if line.strip())
    return f"{lines[0]}; dirty entries: {dirty}"


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def write_summary(run_dir: Path, data: dict[str, object]) -> None:
    elapsed = float(data["elapsed_seconds"])
    first = data.get("first_failure") or "none"
    lines = [
        "# Long Regression Summary",
        "",
        f"Started: {data['started']}",
        f"Duration: {elapsed:.2f} seconds",
        f"Runs: {data['runs']}",
        f"Pass: {data['passed']}",
        f"Fail: {data['failed']}",
        f"Timeout: {data['timeouts']}",
        f"Crash: {data['crashes']}",
        f"Stop reason: {data['stop_reason']}",
        "",
        "First failure:",
        str(first),
        "",
        "Reproduction:",
        str(data["command"]),
        "",
        "Recommended next action:",
        "Inspect the first failing run logs; route an unclear cause to an Investigator."
        if first != "none"
        else "Record this evidence and select the next task; do not change source automatically.",
        "",
        f"Git before: {data['git_before_summary']}",
        f"Git after: {data['git_after_summary']}",
        f"Git status unchanged: {data['git_status_unchanged']}",
        "Full snapshots: `git-before.txt`, `git-after.txt`",
        "",
    ]
    (run_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.log_root.resolve() / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    stop_file = run_dir / "STOP"
    command = [sys.executable, "-m", "pytest", "-q", *args.target]
    command_text = subprocess.list2cmdline(command)
    started_utc = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    deadline = started + args.hours * 3600
    git_before = git_status()
    counts = {"runs": 0, "passed": 0, "failed": 0, "timeouts": 0, "crashes": 0}
    first_failure: str | None = None
    stop_reason = "overall timeout reached"
    active: subprocess.Popen[bytes] | None = None

    print(f"Evidence: {run_dir}")
    print(f"Graceful stop file: {stop_file}")
    print("Stop immediately with Ctrl+C.")

    try:
        while time.monotonic() < deadline:
            if stop_file.exists():
                stop_reason = "STOP file requested"
                break
            if args.max_runs is not None and counts["runs"] >= args.max_runs:
                stop_reason = "maximum run count reached"
                break

            counts["runs"] += 1
            index = counts["runs"]
            stdout_path = run_dir / f"run_{index:04d}.stdout.log"
            stderr_path = run_dir / f"run_{index:04d}.stderr.log"
            cycle_started = time.monotonic()
            remaining = max(0.1, deadline - cycle_started)
            timeout = min(args.per_run_timeout, remaining)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                active = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
                try:
                    exit_code = active.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    terminate_process_tree(active)
                    exit_code = None
                except KeyboardInterrupt:
                    # Preserve ownership until the child tree is gone.  The outer
                    # handler records the operator-interrupt stop reason and summary.
                    terminate_process_tree(active)
                    raise
                finally:
                    active = None

            duration = time.monotonic() - cycle_started
            record = {
                "run": index,
                "duration_seconds": round(duration, 3),
                "exit_code": exit_code,
                "stdout": stdout_path.name,
                "stderr": stderr_path.name,
            }
            (run_dir / f"run_{index:04d}.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8"
            )

            if exit_code is None:
                counts["timeouts"] += 1
                first_failure = first_failure or f"run {index}: TIMEOUT; {stdout_path.name} / {stderr_path.name}"
                stop_reason = "pytest process timeout"
                print(f"run {index}: TIMEOUT after {duration:.2f}s")
                break
            if exit_code == 0:
                counts["passed"] += 1
                print(f"run {index}: PASS in {duration:.2f}s")
                continue
            if exit_code == 1:
                counts["failed"] += 1
                kind = "FAIL"
            else:
                counts["crashes"] += 1
                kind = "CRASH"
            first_failure = first_failure or (
                f"run {index}: {kind} exit {exit_code}; {stdout_path.name} / {stderr_path.name}"
            )
            print(f"run {index}: {kind} exit {exit_code} in {duration:.2f}s")
            if not args.continue_on_failure or kind == "CRASH":
                stop_reason = f"{kind.lower()} in run {index}"
                break
    except KeyboardInterrupt:
        stop_reason = "operator interrupt"
        if active is not None:
            terminate_process_tree(active)
    finally:
        git_after = git_status()
        (run_dir / "git-before.txt").write_text(git_before + "\n", encoding="utf-8")
        (run_dir / "git-after.txt").write_text(git_after + "\n", encoding="utf-8")
        data: dict[str, object] = {
            "started": started_utc,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            **counts,
            "stop_reason": stop_reason,
            "first_failure": first_failure,
            "command": command_text,
            "git_before_summary": summarize_git(git_before),
            "git_after_summary": summarize_git(git_after),
            "git_status_unchanged": git_before == git_after,
        }
        write_summary(run_dir, data)
        print(f"Summary: {run_dir / 'SUMMARY.md'}")

    return 0 if counts["failed"] == counts["timeouts"] == counts["crashes"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
