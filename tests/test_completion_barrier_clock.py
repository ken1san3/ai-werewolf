"""Shared epochs retain progress and budget even for a delayed ninth client."""
from unittest.mock import patch

from tests.fixtures.completion_clock import CompletionClock, DAY_SECONDS, publish_gate
from tests.fixtures.phase5_server_process import _DayOneBarrierClock
from ai_client.reaction_chat import ReactionChatConfig


def test_shared_clock_holds_all_participants_and_resumes(tmp_path):
    start, release = tmp_path / "start", tmp_path / "release"
    server = _DayOneBarrierClock(start, release)
    with patch("tests.fixtures.completion_clock.time.monotonic_ns") as now:
        now.return_value = 100_000_000_000
        assert server() == 0
        publish_gate(start)
        now.return_value += 10_000_000_000
        first = CompletionClock(start, release)
        assert server() == first() == 1
        publish_gate(release)
        now.return_value += 1_000_000_000
        late = CompletionClock(start, release)
        assert server() == first() == late() == 2
        now.return_value += 120_000_000_000
        assert server() == first() == late() == 6
        config = ReactionChatConfig()
        assert first() > 1 + max(config.initial_jitter_seconds)
        assert 1 + DAY_SECONDS - first() > config.deadline_guard_seconds + config.minimum_start_budget_seconds
        # Only the server calls this after observing all nine accepted chat receipts.
        server.mark_day_one_chat_complete()
        assert server() == first() == late() == 6
        now.return_value += 2_000_000_000
        assert server() == first() == late() == 8
        server.mark_day_one_chat_complete()
        now.return_value += 1_000_000_000
        assert server() == first() == late() == 9
