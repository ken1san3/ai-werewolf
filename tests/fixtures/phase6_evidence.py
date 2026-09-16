from __future__ import annotations

import os
import re
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from typing import Literal


_PREFIX = "p6f-private-evidence-"
_REPARSE_POINT = 0x400
_PROJECT_ROOT = Path(__file__).absolute().parents[2]
_EVIDENCE_BASE = _PROJECT_ROOT / "logs" / "phase6-private-evidence"
_TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")


def _assert_plain_directory_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            raise ValueError(f"private evidence path component is missing: {current}") from None
        if not current.is_dir():
            raise ValueError(f"private evidence path component is not a directory: {current}")
        if os.path.islink(current) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ValueError(f"private evidence path component is a reparse point: {current}")


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def create_private_evidence_container(
    base: Path,
    *,
    pytest_basetemp: Path | None = None,
    evidence_kind: Literal["game", "synthetic"] | None = None,
    task_id: str | None = None,
    created_at_utc: datetime | None = None,
) -> Path:
    """Create one durable Windows-private container outside pytest temp ownership."""
    if os.name != "nt" or sys.version_info < (3, 13):
        raise RuntimeError("private evidence requires Windows Python 3.13 or newer")
    if base.absolute() != _EVIDENCE_BASE:
        raise ValueError("private evidence base is not the dedicated logs directory")
    _assert_plain_directory_components(base)
    base = base.resolve(strict=True)
    if base != _EVIDENCE_BASE.resolve(strict=True):
        raise ValueError("private evidence base escaped the repository")
    if pytest_basetemp is not None:
        work = pytest_basetemp.absolute().resolve(strict=False)
        if _overlaps(base, work):
            raise ValueError("pytest basetemp overlaps the durable evidence base")
    explicit = (evidence_kind, task_id, created_at_utc)
    if any(value is not None for value in explicit) and not all(
        value is not None for value in explicit
    ):
        raise ValueError("evidence_kind, task_id, and created_at_utc must be provided together")
    if evidence_kind is None:
        raw_target = Path(tempfile.mkdtemp(dir=base, prefix=_PREFIX))
        expected_parent = base
        expected_prefix = _PREFIX
    else:
        if evidence_kind not in {"game", "synthetic"}:
            raise ValueError("evidence_kind must be game or synthetic")
        assert task_id is not None and created_at_utc is not None
        if _TASK_ID_RE.fullmatch(task_id) is None:
            raise ValueError("task_id is invalid")
        if created_at_utc.tzinfo is None or created_at_utc.utcoffset() != timezone.utc.utcoffset(created_at_utc):
            raise ValueError("created_at_utc must be timezone-aware UTC")
        kind_dir = base / evidence_kind
        kind_dir.mkdir(mode=0o700, exist_ok=True)
        _assert_plain_directory_components(kind_dir)
        expected_parent = kind_dir.resolve(strict=True)
        timestamp = created_at_utc.strftime("%Y%m%dT%H%M%S%fZ")
        expected_prefix = f"{task_id}-{timestamp}"
        raw_target = expected_parent / expected_prefix
        raw_target.mkdir(mode=0o700)
    metadata = os.lstat(raw_target)
    if os.path.islink(raw_target) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
        raise RuntimeError("private evidence container is a reparse point")
    if not raw_target.is_dir() or raw_target.parent.absolute() != expected_parent:
        raise RuntimeError("private evidence container escaped its dedicated base")
    target = raw_target.resolve(strict=True)
    if target.parent != expected_parent or (
        target.name != expected_prefix if evidence_kind is not None else not target.name.startswith(expected_prefix)
    ):
        raise RuntimeError("private evidence container escaped its dedicated base")
    return target
