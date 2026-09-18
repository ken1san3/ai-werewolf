"""Public phase acknowledgements for the offline coordinator fixture only."""
import json
from pathlib import Path


def attempt_path(ready_path: Path) -> Path:
    return ready_path.with_suffix(".attempt.json")


def acknowledge_attempt(ready_path: Path, day: int, phase: str) -> None:
    destination = attempt_path(ready_path)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps({"day": day, "phase": phase}), encoding="utf-8")
    temporary.replace(destination)


def phase_attempts_complete(ready_paths, day: int, phase: str, *, count: int) -> bool:
    paths = tuple(ready_paths)
    if not count or len(paths) != count or len(set(paths)) != count:
        return False
    for path in paths:
        try:
            value = json.loads(attempt_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if value != {"day": day, "phase": phase}:
            return False
    return True
