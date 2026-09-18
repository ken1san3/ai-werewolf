"""Shared logical-time frontier for the Phase 3.4 completion fixture only."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import time


def publish(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


class ReactionFrontierClock:
    def __init__(self, start: Path | None, release: Path | None) -> None:
        self.start = start
        self.release = release

    def state(self) -> dict | None:
        if self.release is None or not self.release.exists():
            return None
        return json.loads(self.release.read_text(encoding="utf-8"))

    def __call__(self) -> float:
        if self.start is None or self.release is None:
            return time.monotonic()
        if not self.start.exists():
            return 0.0
        state = self.state()
        if state is None:
            started = json.loads(self.start.read_text(encoding="utf-8"))["started_ns"]
            return min((time.monotonic_ns() - started) / 1e9, 1.0)
        running = state["running_ns"]
        return state["value"] + (
            0.0 if running is None else (time.monotonic_ns() - running) / 1e9
        )

    async def sleep(self, delay: float) -> None:
        deadline = self() + delay
        while self() < deadline:
            await asyncio.sleep(min(0.02, deadline - self()))


def pending_report(reaction, world, *, revision: int, executing: bool,
                   started: dict | None = None) -> dict:
    """Observe the controller's real CO-first selector without changing it."""
    state = reaction.snapshot()
    snapshot = world.snapshot()
    pending = reaction._next_pending()
    return {
        "revision": revision,
        "settled": (
            not executing
            and snapshot.is_caught_up
            and reaction._mapping_ready
            and snapshot.phase is not None
            and snapshot.phase.day == 1
            and snapshot.phase.phase == "day"
        ),
        "terminal_count": len(state.outcomes),
        "kind": None if pending is None else pending.trigger.kind.value,
        "due": None if pending is None else pending.due,
        "started": started,
    }


class FirstSpeakerFrontier:
    """Advance only from a complete cohort after the selected work terminates."""
    def __init__(self, path: Path, players: tuple[str, ...]) -> None:
        if path.exists():
            raise RuntimeError("completion Day 1 was released twice")
        self.path = path
        self.players = frozenset(players)
        self.state = {"revision": 0, "value": 1.0, "running_ns": None}
        self.awaiting: dict[str, int] = {}
        publish(path, self.state)

    def step(self, reports: dict[str, dict], *, accepted: bool) -> bool:
        if self.state["running_ns"] is not None:
            return True
        if set(reports) != self.players or any(
            report["revision"] != self.state["revision"] or not report["settled"]
            for report in reports.values()
        ):
            return False
        if any(reports[player]["terminal_count"] <= count
               for player, count in self.awaiting.items()):
            return False
        if accepted and self.awaiting:
            self.state = dict(self.state, running_ns=time.monotonic_ns())
            publish(self.path, self.state)
            return True
        if accepted:
            # A zero-jitter invocation can finish before the first cohort is
            # sampled. Select its observed pre-execution baseline, then require
            # a subsequent cohort to confirm the terminal increase.
            self.awaiting = {
                player: report["started"]["terminal_count"]
                for player, report in reports.items()
                if report.get("started") is not None
                and report["started"]["due"] <= self.state["value"]
            }
            if self.awaiting:
                self.state = dict(self.state, revision=self.state["revision"] + 1)
                publish(self.path, self.state)
                return False
        due = {player: report["due"] for player, report in reports.items()
               if report["due"] is not None}
        if not due:
            raise AssertionError("first-speaker frontier has no remaining opportunity")
        if any(not isinstance(value, (float, int)) or not math.isfinite(value)
               for value in due.values()):
            raise AssertionError("first-speaker frontier has an invalid due")
        frontier = max(self.state["value"], min(due.values()))
        self.awaiting = {player: reports[player]["terminal_count"]
                         for player, value in due.items() if value <= frontier}
        self.state = {"revision": self.state["revision"] + 1,
                      "value": frontier, "running_ns": None}
        publish(self.path, self.state)
        return False
