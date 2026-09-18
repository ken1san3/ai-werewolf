"""Privacy and fail-closed tests for the public CI failure summary."""
from pathlib import Path
import json

import pytest

from scripts.ci_private_summary import private_projection_codes, private_result_codes, private_semantic_codes, summarize_private_junit


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


@pytest.mark.parametrize("message, expected", [
    ("tests.test_phase6_semantic_completion.P6FSemanticGameEndFailure", "P6FSemanticGameEndFailure"),
    ("PermissionError: SECRET_PATH", "PermissionError"),
    ("TimeoutError: SECRET_REQUEST", "TimeoutError"),
    ("SECRET TimeoutError", "UNCLASSIFIED"),
    ("TimeoutErrorSECRET", "UNCLASSIFIED"),
    ("SecretException: TimeoutError", "UNCLASSIFIED"),
])
def test_pytest_xunit2_message_only_is_allowlisted(source_root, message, expected):
    import xml.etree.ElementTree as ET
    root = ET.Element("testsuite")
    case = ET.SubElement(root, "testcase", classname="tests.test_public", name="test_one")
    failure = ET.SubElement(case, "failure", message=message)
    failure.text = "SECRET_TRACEBACK"
    report = source_root / "private.xml"
    ET.ElementTree(root).write(report, encoding="utf-8")
    result = summarize_private_junit(source_root, report)
    assert result["failures"] == [{"test": "tests.test_public::test_one", "kind": expected}]
    assert "SECRET" not in json.dumps(result)


def test_real_pytest_junit_without_type_attribute(source_root):
    import subprocess
    import sys
    test = source_root / "tests" / "test_public.py"
    test.write_text("class P6FSemanticGameEndFailure(AssertionError): pass\n"
                    "def test_one(): raise P6FSemanticGameEndFailure('SECRET_PAYLOAD')\n")
    report = source_root / "private.xml"
    run = subprocess.run([sys.executable, "-m", "pytest", "tests/test_public.py", "-q", "-p", "no:cacheprovider",
                          "--rootdir", str(source_root), "--junitxml", str(report)],
                         cwd=source_root, capture_output=True, timeout=30)
    assert run.returncode == 1
    result = summarize_private_junit(source_root, report)
    assert result["failures"] == [{"test": "tests.test_public::test_one", "kind": "P6FSemanticGameEndFailure"}]
    assert "SECRET" not in json.dumps(result)


def test_private_result_codes_require_exact_match():
    assert private_result_codes(["global peak backend concurrency is not one", "SECRET", {"SECRET": 1}]) == "PEAK_CONCURRENCY,UNKNOWN"
    assert private_result_codes("TimeoutError: SECRET") == "UNKNOWN"
    assert private_result_codes([]) == ""


def test_result_properties_are_reallowlisted(source_root):
    report = source_root / "private.xml"
    report.write_text('<testsuite><testcase classname="tests.test_public" name="test_one">'
        '<properties><property name="ci_failure_codes" value="PEAK_CONCURRENCY,SECRET"/>'
        '<property name="SECRET" value="SECRET"/></properties><failure message="AssertionError: SECRET"/>'
        '</testcase></testsuite>')
    result = summarize_private_junit(source_root, report)
    assert result['failures'][0]['codes'] == 'PEAK_CONCURRENCY,UNKNOWN'
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize("counts,expected", [
    ({"PROMPT_INVALID": 2, "PROMPT_TOO_LARGE": 1}, "PROJECTION_INVALID,PROJECTION_TOO_LARGE"),
    ({"PROMPT_INVALID": 0}, ""),
    ({"SECRET": 1, "PROMPT_INVALID": "SECRET"}, "UNKNOWN"),
    ({"PROMPT_TOO_LARGE": True}, "UNKNOWN"),
    ({"PROMPT_TOO_LARGE": -1}, "UNKNOWN"),
    ("SECRET", "UNKNOWN"),
])
def test_projection_codes_never_expose_private_values(counts, expected):
    assert private_projection_codes(counts) == expected


@pytest.mark.parametrize("semantic", [None, "SECRET", 3, [], {}])
def test_malformed_semantic_failure_does_not_break_diagnostics(semantic):
    assert private_semantic_codes(semantic) == "UNKNOWN"


def test_semantic_projection_counts_are_forwarded_safely():
    assert private_semantic_codes({"prompt_rejection_counts": {"PROMPT_TOO_LARGE": 1},
                                   "SECRET": "SECRET"}) == "PROJECTION_TOO_LARGE"


def test_oversized_report_is_unavailable(source_root: Path) -> None:
    report = source_root / "private.xml"
    report.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert summarize_private_junit(source_root, report) == {
        "summary_status": "UNAVAILABLE", "reason": "REPORT_TOO_LARGE",
    }
