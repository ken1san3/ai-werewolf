"""Real game-server subprocess used by the Phase 5 offline completion."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
from random import Random
import stat
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client import _compat as _asyncio_compat  # noqa: F401
from server.aiwolf_core import (
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer


class _DayOneBarrierClock:
    def __init__(
        self, clock_start: Path, day_one_release: Path, *, day_seconds: int
    ) -> None:
        self._clock_start = clock_start
        self._day_one_release = day_one_release
        self._day_seconds = day_seconds
        self._started_at_ns: int | None = None
        self._day_one_released_at_ns: int | None = None
        self._day_one_chat_complete = False
        self._day_one_completed_at_ns: int | None = None
        self._day_one_completed_value: int | None = None

    def __call__(self) -> int:
        if not self._clock_start.exists():
            return 0
        if self._started_at_ns is None:
            self._started_at_ns = time.monotonic_ns()
        if not self._day_one_release.exists():
            elapsed = (time.monotonic_ns() - self._started_at_ns) // 1_000_000_000
            return min(elapsed, 1)
        if self._day_one_released_at_ns is None:
            self._day_one_released_at_ns = time.monotonic_ns()
        now = time.monotonic_ns()
        elapsed = (now - self._day_one_released_at_ns) // 1_000_000_000
        # Let due/jitter/cooldown work progress, but keep the synthetic clock one
        # second before the Day-1 deadline until every seat has an accepted chat.
        if not self._day_one_chat_complete:
            return 1 + min(elapsed, self._day_seconds - 1)
        assert self._day_one_completed_at_ns is not None
        assert self._day_one_completed_value is not None
        return self._day_one_completed_value + (
            now - self._day_one_completed_at_ns
        ) // 1_000_000_000

    def mark_day_one_chat_complete(self) -> None:
        if self._day_one_chat_complete:
            return
        now = time.monotonic_ns()
        if self._day_one_released_at_ns is None:
            self._day_one_released_at_ns = now
        elapsed = (now - self._day_one_released_at_ns) // 1_000_000_000
        self._day_one_completed_value = 1 + min(elapsed, self._day_seconds - 1)
        self._day_one_completed_at_ns = now
        self._day_one_chat_complete = True


def _private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _expected_from_state(player_id: str, state: dict[str, object]) -> dict[str, object] | None:
    actions = state["actions"]
    vote = [item for item in actions if item["type"] == "vote"]
    abilities = [
        item
        for item in actions
        if item["type"] == "ability"
        and item["uses_remaining"] != 0
        and len(item["valid_targets"]) >= item["target_count"]
    ]
    if vote:
        action = vote[0]
        return {
            "player_id": player_id,
            "day": state["day"],
            "phase": state["phase"],
            "action": "vote.cast",
            "valid_targets": list(action["valid_targets"]),
            "target_count": action["target_count"],
            "abilities": [],
        }
    if abilities:
        return {
            "player_id": player_id,
            "day": state["day"],
            "phase": state["phase"],
            "action": "ability.use",
            "valid_targets": [],
            "target_count": 0,
            "abilities": [dict(item) for item in abilities],
        }
    return None


async def run_server(
    state_path: Path,
    result_path: Path,
    clock_start: Path,
    day_one_release: Path,
    stop_path: Path,
    seed: int,
    entry_tokens: list[str],
) -> int:
    content = load_content(PROJECT_ROOT / "content")
    preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = replace(
        preset,
        rules=replace(
            preset.rules,
            night_seconds=1,
            silence_after_dawn_seconds=0,
            # Give nine subprocess clients enough CI scheduling headroom for Day 1 chat.
            day_seconds=day_seconds,
            vote_seconds=1,
        ),
    )
    players = tuple(
        PlayerConfig(f"player-{index}", f"Player {index}")
        for index in range(sum(preset.role_counts.values()))
    )
    game_id = f"123e4567-e89b-12d3-a456-{seed:012d}"
    day_seconds = 10
    clock = _DayOneBarrierClock(
        clock_start, day_one_release, day_seconds=day_seconds
    )
    game = GameState.create_from_preset(
        content,
        preset,
        players,
        game_id=game_id,
        event_sink=InMemoryEventSink(),
        rng=Random(seed),
        started_at=clock(),
    )
    token_source = iter(entry_tokens)
    registry = GameRegistry(
        {game_id: game}, entry_token_factory=token_source.__next__
    )
    sessions = SessionManager(registry, clock=clock)
    ticker = TickDriver(registry, clock=clock)
    server = WebSocketGameServer(
        registry,
        sessions=sessions,
        ticker=ticker,
        tick_interval_seconds=0.02,
    )
    expected: dict[tuple[str, int, str, str], dict[str, object]] = {}
    accepted: list[dict[str, object]] = []
    chats: list[dict[str, object]] = []
    active_request_id: str | None = None
    original_handle = sessions.handle_message
    original_vote = game.submit_vote
    original_action = game.submit_action

    def remember_expected(player_id: str) -> None:
        state = game.get_action_state(player_id)
        item = _expected_from_state(player_id, state)
        if item is not None:
            key = (player_id, item["day"], item["phase"], item["action"])
            expected.setdefault(key, item)

    def handle_spy(message, context=None):
        nonlocal active_request_id
        message_type = message.get("type") if isinstance(message, dict) else None
        request_id = message.get("event_id") if isinstance(message, dict) else None
        previous = active_request_id
        if (
            message_type in {"vote.cast", "ability.use"}
            and context is not None
            and context.player_id in game.players
        ):
            remember_expected(context.player_id)
            active_request_id = request_id
        try:
            result = original_handle(message, context)
            for submission in result.channel_messages:
                acceptance = submission.acceptance
                text = submission.message["message"]
                chats.append(
                    {
                        "player_id": acceptance.player_id,
                        "day": acceptance.day,
                        "phase": acceptance.phase,
                        "channel": submission.channel_id,
                        "accepted_at": acceptance.accepted_at,
                        "phase_deadline": acceptance.phase_deadline,
                        "message": text,
                        "message_chars": len(text),
                        "message_utf8_bytes": len(text.encode("utf-8")),
                    }
                )
                day_one_speakers = {
                    item["player_id"]
                    for item in chats
                    if item["day"] == 1 and item["phase"] == "day"
                }
                if day_one_speakers == set(game.players):
                    clock.mark_day_one_chat_complete()
            return result
        finally:
            active_request_id = previous

    def vote_spy(now, player_id, target_player_id):
        acceptance = original_vote(now, player_id, target_player_id)
        accepted.append(
            {
                "player_id": player_id,
                "day": acceptance.day,
                "phase": acceptance.phase,
                "action": "vote.cast",
                "ability_id": None,
                "vote_target_player_id": target_player_id,
                "ability_target_player_ids": [],
                "request_event_id": active_request_id,
                "accepted_at": acceptance.accepted_at,
                "phase_deadline": acceptance.phase_deadline,
            }
        )
        return acceptance

    def action_spy(now, player_id, ability_id, target_player_ids):
        day = game.day
        phase = game.phase.value
        deadline = game.phase_ends_at
        result = original_action(now, player_id, ability_id, target_player_ids)
        accepted.append(
            {
                "player_id": player_id,
                "day": day,
                "phase": phase,
                "action": "ability.use",
                "ability_id": ability_id,
                "vote_target_player_id": None,
                "ability_target_player_ids": list(target_player_ids),
                "request_event_id": active_request_id,
                "accepted_at": now,
                "phase_deadline": deadline,
            }
        )
        return result

    sessions.handle_message = handle_spy  # type: ignore[method-assign]
    game.submit_vote = vote_spy  # type: ignore[method-assign]
    game.submit_action = action_spy  # type: ignore[method-assign]
    listener = await server.start("127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    _private_json(
        state_path,
        {
            "uri": uri,
            "game_id": game_id,
            "player_ids": list(game.players),
            "server_pid": os.getpid(),
        },
    )
    failure: str | None = None
    try:
        async with asyncio.timeout(175.0):
            while game.game_result is None and not stop_path.exists():
                for player_id in game.players:
                    remember_expected(player_id)
                await asyncio.sleep(0.02)
            if stop_path.exists() and game.game_result is None:
                failure = "STOP_REQUESTED"
            else:
                await asyncio.sleep(0.25)
    except TimeoutError:
        failure = "GAME_TIMEOUT"
    finally:
        await server.close()

    phase_histogram = Counter(
        (event.payload.get("day"), event.payload.get("phase"))
        for event in game.event_bus.events
        if event.type == "PHASE_STARTED"
    )
    _private_json(
        result_path,
        {
            "server_pid": os.getpid(),
            "success": failure is None and game.game_result is not None,
            "failure": failure,
            "game_end": game.game_result is not None,
            "accepted_reservations": accepted,
            "accepted_chats": chats,
            "expected_reservations": list(expected.values()),
            "phase_histogram": [
                {"day": key[0], "phase": key[1], "count": count}
                for key, count in sorted(phase_histogram.items())
            ],
            "listener_closed": True,
        },
    )
    return 0 if failure is None and game.game_result is not None else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--clock-start", type=Path, required=True)
    parser.add_argument("--day-one-release", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    raw_bootstrap = sys.stdin.readline()
    if not raw_bootstrap:
        parser.error("private bootstrap pipe is required")
    bootstrap = json.loads(raw_bootstrap)
    if not isinstance(bootstrap, dict) or not isinstance(
        bootstrap.get("entry_tokens"), list
    ):
        parser.error("private entry-token bootstrap is invalid")
    raise SystemExit(
        asyncio.run(
            run_server(
                arguments.state,
                arguments.result,
                arguments.clock_start,
                arguments.day_one_release,
                arguments.stop,
                arguments.seed,
                [str(value) for value in bootstrap["entry_tokens"]],
            )
        )
    )


if __name__ == "__main__":
    main()
