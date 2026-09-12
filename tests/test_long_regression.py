from __future__ import annotations

from argparse import Namespace
from unittest.mock import Mock, patch

from scripts import run_long_regression


def test_keyboard_interrupt_terminates_owned_process_tree_and_writes_summary(tmp_path) -> None:
    process = Mock()
    process.wait.side_effect = KeyboardInterrupt
    args = Namespace(
        hours=1.0,
        per_run_timeout=60.0,
        max_runs=1,
        target=["tests/test_ai_status.py"],
        continue_on_failure=False,
        log_root=tmp_path,
    )

    with (
        patch.object(run_long_regression, "parse_args", return_value=args),
        patch.object(
            run_long_regression,
            "git_status",
            return_value="## main\n M existing-user-change.txt",
        ),
        patch.object(run_long_regression.subprocess, "Popen", return_value=process),
        patch.object(run_long_regression, "terminate_process_tree") as terminate,
    ):
        assert run_long_regression.main() == 0

    terminate.assert_called_once_with(process)
    run_dir = next(tmp_path.iterdir())
    summary = (run_dir / "SUMMARY.md").read_text(encoding="utf-8")
    assert "Stop reason: operator interrupt" in summary
    assert "Git status unchanged: True" in summary
