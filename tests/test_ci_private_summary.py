"""Privacy and fail-closed tests for the public CI failure summary."""
from pathlib import Path
import json

import pytest

from scripts.ci_private_summary import summarize_private_junit


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_public.py").write_text(
        "def test_one(): pass\nclass Tests:\n    def test_two(self): pass\n",
        encoding="utf-8",
    )
    return tmp_path


def test_summary_excludes_private_values(source_root: Path) -> None:
    report = source_root / "private.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.test_public" name="test_one[SECRET_PARAM]">'
        '<failure type="builtins.AssertionError" message="SECRET_MESSAGE">SECRET_TRACE</failure>'
        '<system-out>SECRET_STDOUT</system-out><system-err>SECRET_STDERR</system-err>'
        '</testcase><testcase classname="SECRET_CLASS" name="SECRET_NAME">'
        '<error type="SECRET_TYPE">SECRET_OTHER</error></testcase></testsuite>',
        encoding="utf-8",
    )
    result = summarize_private_junit(source_root, report)
    assert "SECRET" not in json.dumps(result)
    assert result["failures"] == [
        {"test": "tests.test_public::test_one", "kind": "AssertionError"},
        {"test": "UNMAPPED_TEST", "kind": "UNCLASSIFIED"},
    ]


def test_summary_counts_success_and_skip_without_output(source_root: Path) -> None:
    report = source_root / "private.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_public.Tests" name="test_two"/>'
        '<testcase name="test_one"><skipped message="SECRET"/></testcase>'
        '</testsuite></testsuites>', encoding="utf-8",
    )
    result = summarize_private_junit(source_root, report)
    assert result["counts"] == {"tests": 2, "passed": 1, "failed": 0, "skipped": 1}
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("payload", [
    "not XML SECRET", '<!DOCTYPE x [<!ENTITY s "SECRET">]><x/>', '<x>&SECRET;</x>',
])
def test_bad_report_is_unavailable(source_root: Path, payload: str) -> None:
    report = source_root / "private.xml"
    report.write_text(payload, encoding="utf-8")
    result = summarize_private_junit(source_root, report)
    assert result["summary_status"] == "UNAVAILABLE"
    assert "SECRET" not in json.dumps(result)


def test_missing_report_is_unavailable(source_root: Path) -> None:
    result = summarize_private_junit(source_root, source_root / "SECRET_missing.xml")
    assert result == {"summary_status": "UNAVAILABLE", "reason": "REPORT_UNREADABLE"}


def test_oversized_report_is_unavailable(source_root: Path) -> None:
    report = source_root / "private.xml"
    report.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert summarize_private_junit(source_root, report) == {
        "summary_status": "UNAVAILABLE", "reason": "REPORT_TOO_LARGE",
    }
