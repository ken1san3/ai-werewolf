"""Emit only allowlisted test identities and counts from a private JUnit report."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import re
import xml.etree.ElementTree as ET

_MAX_XML_BYTES = 8 * 1024 * 1024
_ERROR_TYPES = frozenset({
    "AssertionError", "TimeoutError", "PermissionError", "FileExistsError",
    "TypeError", "ValueError", "RuntimeError", "OSError", "ImportError",
    "ModuleNotFoundError",
    "P6FSemanticGameEndFailure", "P6FSemanticCleanupFailure",
    "P6FSemanticResponsiveFailure", "P6FSemanticPreVoteFailure",
    "P6FSemanticChatCapFailure", "P6FSemanticAggregateFailure",
})
_RESULT_CODES = {
    "global peak backend concurrency is not one": "PEAK_CONCURRENCY",
    "not every Day-1 living seat has accepted short chat": "DAY1_CHAT_COVERAGE",
    "accepted short chat violates bound or deadline": "CHAT_BOUND_OR_DEADLINE",
    "accepted vote/ability correlation differs from exact expected set": "RESERVATION_CORRELATION",
    "expected vote/ability reservation evidence missing": "RESERVATION_MISSING",
    "not all broker entries are terminal": "BROKER_NONTERMINAL",
    "broker overload, poison, or backend failure": "BROKER_FAILURE",
    "client terminal evidence incomplete": "CLIENT_TERMINAL",
    "semantic requirements not met": "SEMANTIC_REQUIREMENTS",
    "TimeoutError": "WAIT_TIMEOUT",
    "ValueError": "VALUE_VALIDATION_UNKNOWN",
    "RuntimeError": "RUNTIME_UNKNOWN",
}
_SAFE_CODES = frozenset(_RESULT_CODES.values()) | {"UNKNOWN"}


def private_result_codes(errors: object) -> str:
    """Convert exact known errors to fixed enums before writing JUnit metadata."""
    if not isinstance(errors, list):
        return "UNKNOWN"
    return ",".join(sorted({_RESULT_CODES.get(item, "UNKNOWN") if isinstance(item, str)
                            else "UNKNOWN" for item in errors}))


def _error_kind(error: ET.Element) -> str:
    """Pytest xunit2 puts the exception name in message, without a type field.

    Match only an anchored, complete allowlisted class token. Nothing from the
    message body or traceback is returned, including unknown exception names.
    """
    error_type = error.get("type", "").rsplit(".", 1)[-1]
    if error_type in _ERROR_TYPES:
        return error_type
    match = re.match(r"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)(?=:|$)", error.get("message", ""))
    if match and match.group(1) in _ERROR_TYPES:
        return match.group(1)
    return "UNCLASSIFIED"


def _public_test_ids(root: Path) -> set[tuple[str, str]]:
    """Derive permitted identities from committed test code, not report strings."""
    allowed: set[tuple[str, str]] = set()
    for path in sorted((root / "tests").rglob("test_*.py")):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    allowed.add((module, node.name))
            elif isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test"):
                        allowed.add((f"{module}.{node.name}", method.name))
    return allowed


def summarize_private_junit(root: Path, report: Path) -> dict[str, object]:
    """Never return parameters, messages, tracebacks, stdout, stderr or locators."""
    counts: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    try:
        with report.open("rb") as handle:
            payload = handle.read(_MAX_XML_BYTES + 1)
        if len(payload) > _MAX_XML_BYTES:
            return {"summary_status": "UNAVAILABLE", "reason": "REPORT_TOO_LARGE"}
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            return {"summary_status": "UNAVAILABLE", "reason": "UNSAFE_XML"}
        xml = ET.fromstring(payload)
        allowed = _public_test_ids(root)
    except (OSError, ET.ParseError, SyntaxError, UnicodeError):
        return {"summary_status": "UNAVAILABLE", "reason": "REPORT_UNREADABLE"}

    for case in xml.iter("testcase"):
        counts["tests"] += 1
        errors = list(case.findall("failure")) + list(case.findall("error"))
        if not errors:
            counts["skipped" if case.find("skipped") is not None else "passed"] += 1
            continue
        counts["failed"] += 1
        identity = (case.get("classname", ""), case.get("name", "").split("[", 1)[0])
        public_id = "::".join(identity) if identity in allowed else "UNMAPPED_TEST"
        for error in errors:
            failure = {
                "test": public_id,
                "kind": _error_kind(error),
            }
            codes = set()
            for prop in case.findall("properties/property"):
                if prop.get("name") == "ci_failure_codes":
                    codes.update(code if code in _SAFE_CODES else "UNKNOWN"
                                 for code in prop.get("value", "").split(",") if code)
            if codes:
                failure["codes"] = ",".join(sorted(codes))
            failures.append(failure)
    return {
        "summary_status": "AVAILABLE",
        "counts": {name: counts[name] for name in ("tests", "passed", "failed", "skipped")},
        "failures": failures,
    }
