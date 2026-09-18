"""Emit only allowlisted test identities and counts from a private JUnit report."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
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
            error_type = error.get("type", "").rsplit(".", 1)[-1]
            failures.append({
                "test": public_id,
                "kind": error_type if error_type in _ERROR_TYPES else "UNCLASSIFIED",
            })
    return {
        "summary_status": "AVAILABLE",
        "counts": {name: counts[name] for name in ("tests", "passed", "failed", "skipped")},
        "failures": failures,
    }
