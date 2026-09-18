from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_client.reaction_chat.controller import ReactionChatController
from tests.fixtures.reaction_frontier import (
    FirstSpeakerFrontier,
    ReactionFrontierClock,
    pending_report,
    publish,
)


def report(revision, due, *, terminals=0, settled=True, kind="initial_chat", started=None):
    return dict(revision=revision, due=due, terminal_count=terminals,
                settled=settled, kind=None if due is None else kind, started=started)


def test_initial_accepted_does_not_release_before_selected_terminal(tmp_path):
    frontier = FirstSpeakerFrontier(tmp_path / "release", ("a",))
    assert not frontier.step({"a": report(0, 1.0)}, accepted=True)
    assert frontier.awaiting == {"a": 0}
    assert frontier.state["running_ns"] is None
    assert not frontier.step({"a": report(1, None)}, accepted=True)
    assert frontier.step({"a": report(1, None, terminals=1)}, accepted=True)


@pytest.mark.parametrize("already_terminal", [False, True])
def test_initial_accepted_zero_jitter_uses_observed_execution_baseline(tmp_path, already_terminal):
    frontier = FirstSpeakerFrontier(tmp_path / "release", ("a", "b"))
    started = {"due": 1.0, "terminal_count": 2}
    reports = {"a": report(0, None, terminals=3 if already_terminal else 2, started=started),
               "b": report(0, None, terminals=1)}
    assert not frontier.step(reports, accepted=True)
    assert frontier.awaiting == {"a": 2}
    assert frontier.state["running_ns"] is None
    # A refreshed cohort is required even when the first sample was terminal.
    assert not frontier.step(reports, accepted=True)
    reports = {"a": report(1, None, terminals=2, started=started),
               "b": report(1, None, terminals=1)}
    assert not frontier.step(reports, accepted=True)
    reports["a"] = report(1, None, terminals=3, started=started)
    assert frontier.step(reports, accepted=True)


def test_observation_uses_real_co_first_selector():
    controller = object.__new__(ReactionChatController)
    controller._mapping_ready = True
    controller._pending_co = SimpleNamespace(
        due=1.18, trigger=SimpleNamespace(kind=SimpleNamespace(value="co_action")))
    controller._pending_initial = SimpleNamespace(
        due=1.01, stale_wait_version=None, trigger=SimpleNamespace(kind=SimpleNamespace(value="initial_chat")))
    controller._pending_reaction = None
    controller.snapshot = lambda: SimpleNamespace(outcomes=())
    world = SimpleNamespace(snapshot=lambda: SimpleNamespace(
        is_caught_up=True, phase=SimpleNamespace(day=1, phase="day")))
    value = pending_report(controller, world, revision=0, executing=False)
    assert value["kind"] == "co_action" and value["due"] == 1.18
    assert not pending_report(controller, world, revision=0, executing=True)["settled"]
    controller._pending_co = None
    assert pending_report(controller, world, revision=0, executing=False)["due"] == 1.01


def test_frontier_requires_whole_cohort_and_selected_terminal(tmp_path):
    frontier = FirstSpeakerFrontier(tmp_path / "release", ("a", "b"))
    a, b = report(0, 1.18), report(0, 1.03)
    assert not frontier.step({"a": a}, accepted=False)
    assert frontier.state["value"] == 1.0
    assert not frontier.step({"a": a, "b": b}, accepted=False)
    assert frontier.state["value"] == 1.03 and frontier.awaiting == {"b": 0}
    # A stale sample, an executing controller, and a nonterminal invocation
    # must each prevent the next clock publication.
    for held in (report(0, 1.03), report(1, None, settled=False), report(1, None)):
        assert not frontier.step({"a": report(1, 1.18), "b": held}, accepted=False)
        assert frontier.state["revision"] == 1
    assert not frontier.step({"a": report(1, 1.18),
                              "b": report(1, 1.12, terminals=1)}, accepted=False)
    assert frontier.state["value"] == 1.12


def test_silent_co_terminal_unblocks_overdue_initial_without_clock_reversal(tmp_path):
    frontier = FirstSpeakerFrontier(tmp_path / "release", ("a", "b"))
    assert not frontier.step({"a": report(0, 1.08, kind="co_action"),
                              "b": report(0, 1.16, kind="co_action")}, accepted=False)
    # NoDecision is a terminal outcome: it does not require a chat receipt.
    assert not frontier.step({"a": report(1, 1.02, terminals=1),
                              "b": report(1, 1.16)}, accepted=False)
    assert frontier.state["value"] == 1.08
    assert frontier.awaiting == {"a": 1}
    # Even a server receipt cannot release the clock before local terminal.
    assert not frontier.step({"a": report(2, None, terminals=1),
                              "b": report(2, 1.16)}, accepted=True)
    with patch("tests.fixtures.reaction_frontier.time.monotonic_ns", return_value=42):
        assert frontier.step({"a": report(2, None, terminals=2),
                              "b": report(2, 1.16)}, accepted=True)
    assert frontier.state["running_ns"] == 42


def test_shared_epoch_hold_and_resume_ignore_clock_construction_time(tmp_path):
    start, release = tmp_path / "start", tmp_path / "release"
    first = ReactionFrontierClock(start, release)
    assert first() == 0
    publish(start, {"started_ns": 1_000_000_000})
    with patch("tests.fixtures.reaction_frontier.time.monotonic_ns", return_value=3_000_000_000):
        assert first() == 1
        late = ReactionFrontierClock(start, release)
        assert late() == first()
    frontier = FirstSpeakerFrontier(release, ("a",))
    frontier.step({"a": report(0, 1.125)}, accepted=False)
    assert first() == late() == 1.125
    with patch("tests.fixtures.reaction_frontier.time.monotonic_ns", return_value=9_000_000_000):
        frontier.step({"a": report(1, None, terminals=1)}, accepted=True)
        assert first() == late() == 1.125
    with patch("tests.fixtures.reaction_frontier.time.monotonic_ns", return_value=9_500_000_000):
        assert first() == late() == 1.625


def test_real_watchdog_expires_while_logical_sleep_is_held(tmp_path):
    start, release = tmp_path / "start", tmp_path / "release"
    publish(start, {"started_ns": 1})
    FirstSpeakerFrontier(release, ("a",))
    clock = ReactionFrontierClock(start, release)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(clock.sleep(0.2), timeout=0.01)
        assert clock() == 1.0

    asyncio.run(scenario())
