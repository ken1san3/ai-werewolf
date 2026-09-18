"""Shared synthetic time for the offline nine-client completion fixture only."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

DAY_SECONDS = 10
# Progress beyond all configured initial/reaction jitter while retaining enough
# deadline budget for the last scheduled client and its broker reservation.
DAY_ONE_PROGRESS_SECONDS = DAY_SECONDS / 2


def publish_gate(path: Path) -> None:
    if path.exists():
        raise FileExistsError("completion gate already published")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="ascii") as handle:
        handle.write(str(time.monotonic_ns()))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class CompletionClock:
    def __init__(self, start: Path, release: Path) -> None:
        self.start = start
        self.release = release
        self.complete = release.with_name(release.name + ".accepted")

    def __call__(self) -> float:
        now = time.monotonic_ns()
        if not self.start.exists():
            return 0.0
        started = int(self.start.read_text(encoding="ascii"))
        if not self.release.exists():
            return min((now - started) / 1e9, 1.0)
        released = int(self.release.read_text(encoding="ascii"))
        if not self.complete.exists():
            return 1.0 + min((now - released) / 1e9, DAY_ONE_PROGRESS_SECONDS)
        completed = int(self.complete.read_text(encoding="ascii"))
        return (1.0 + min((completed - released) / 1e9, DAY_ONE_PROGRESS_SECONDS)
                + (now - completed) / 1e9)

    def mark_day_one_chat_complete(self) -> None:
        if not self.complete.exists():
            publish_gate(self.complete)

    async def sleep(self, delay: float) -> None:
        deadline = self() + delay
        while self() < deadline:
            await asyncio.sleep(min(0.02, deadline - self()))
