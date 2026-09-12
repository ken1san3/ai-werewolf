"""Phase 3.4 client composition: Network -> World -> Brain -> Reaction Chat."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.brain import BrainController, BrainInvocationArbiter, BrainRunConfig
from ai_client.network import (
    ClientExitReason,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
)
from ai_client.reaction_chat import (
    ReactionChatController,
    ReactionChatLifecycle,
)
from ai_client.world import ActionRejectionObservation, Freshness, WorldState
from tests.fixtures.phase3_4_reaction_brain import (
    CompletionReactionBrain,
    CompletionReactionMode,
)


class _BarrierClock:
    """Mirror the completion server's initial-night and Day 1 release barriers."""

    def __init__(
        self,
        clock_start_path: Path | None,
        day_one_release_path: Path | None,
    ) -> None:
        self._clock_start_path = clock_start_path
        self._day_one_release_path = day_one_release_path
        self._origin = time.monotonic()
        self._started_at: float | None = None
        self._day_one_released_at: float | None = None

    def __call__(self) -> float:
        if self._clock_start_path is None or self._day_one_release_path is None:
            return time.monotonic()
        now = time.monotonic()
        if not self._clock_start_path.exists():
            return self._origin
        if self._started_at is None:
            self._started_at = now
        if not self._day_one_release_path.exists():
            return self._origin + min(now - self._started_at, 1.0)
        if self._day_one_released_at is None:
            self._day_one_released_at = now
        return self._origin + 1.0 + (now - self._day_one_released_at)

    async def sleep(self, delay: float) -> None:
        deadline = self() + delay
        while self() < deadline:
            await asyncio.sleep(min(0.02, deadline - self()))


async def run_driver(
    uri: str,
    game_id: str,
    player_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    seed: int,
    mode: CompletionReactionMode,
    ready_path: Path | None = None,
    day_one_ready_path: Path | None = None,
    clock_start_path: Path | None = None,
    day_one_release_path: Path | None = None,
) -> int:
    barrier_clock = _BarrierClock(clock_start_path, day_one_release_path)
    shared_clock = barrier_clock
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
        clock=shared_clock,
        sleep=barrier_clock.sleep,
    )
    world = WorldState(client)
    brain = CompletionReactionBrain(player_id=player_id, seed=seed, mode=mode)
    brain_controller = BrainController(
        world=world,
        sender=client,
        brain=brain,
        config=BrainRunConfig(max_decision_seconds=0.25),
        clock=shared_clock,
    )
    arbiter = BrainInvocationArbiter(controller=brain_controller, clock=shared_clock)
    reaction = ReactionChatController(
        world=world,
        invoker=arbiter,
        master_seed=seed,
        clock=shared_clock,
    )
    decisions: list[str] = []
    original_decide = brain.decide

    async def record_decision(request):
        decision = await original_decide(request)
        decisions.append(type(decision).__name__)
        return decision

    brain.decide = record_decision

    async def write_ready_marker(
        marker_path: Path | None, *, expected_day: int, expected_phase: str
    ) -> None:
        if marker_path is None:
            return
        while True:
            snapshot = world.snapshot()
            reaction_snapshot = reaction.snapshot()
            deadline = world.transport_observations().current_deadline
            phase_key = reaction_snapshot.current_phase_key
            if (
                snapshot.freshness is Freshness.CURRENT
                and snapshot.is_caught_up
                and snapshot.phase is not None
                and snapshot.phase.day == expected_day
                and snapshot.phase.phase == expected_phase
                and deadline is not None
                and reaction_snapshot.lifecycle is ReactionChatLifecycle.RUNNING
                and snapshot.phase.day == deadline.day
                and snapshot.phase.phase == deadline.phase
                and reaction_snapshot.transport_cursor >= deadline.mapping_order
                and (
                    (
                        phase_key is not None
                        and phase_key.day == deadline.day
                        and phase_key.phase == deadline.phase
                        and phase_key.connection_generation == deadline.connection_generation
                        and phase_key.action_generation == deadline.action_generation
                    )
                    or (snapshot.phase.day == 0 and phase_key is None)
                )
            ):
                marker_path.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "player_id": player_id,
                            "world": {
                                "version": snapshot.version,
                                "last_applied_seq": snapshot.last_applied_seq,
                                "day": snapshot.phase.day,
                                "phase": snapshot.phase.phase,
                            },
                            "reaction": {
                                "lifecycle": reaction_snapshot.lifecycle.value,
                                "transport_cursor": reaction_snapshot.transport_cursor,
                                "phase_key": (
                                    None
                                    if phase_key is None
                                    else {
                                        "connection_generation": phase_key.connection_generation,
                                        "day": phase_key.day,
                                        "phase": phase_key.phase,
                                        "action_generation": phase_key.action_generation,
                                    }
                                ),
                            },
                            "deadline": {
                                "mapping_order": deadline.mapping_order,
                                "connection_generation": deadline.connection_generation,
                                "day": deadline.day,
                                "phase": deadline.phase,
                                "action_generation": deadline.action_generation,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return
            await asyncio.sleep(0.01)

    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    reaction.start()
    ready_tasks = (
        asyncio.create_task(
            write_ready_marker(ready_path, expected_day=0, expected_phase="night0")
        ),
        asyncio.create_task(
            write_ready_marker(day_one_ready_path, expected_day=1, expected_phase="day")
        ),
    )
    try:
        client_exit = await asyncio.wait_for(client_task, 45.0)
        world_exit = await asyncio.wait_for(world_task, 10.0)
        for _ in range(1000):
            if reaction.snapshot().lifecycle is ReactionChatLifecycle.STOPPED:
                break
            await asyncio.sleep(0.01)
        await reaction.stop()
        await arbiter.stop()
        world_snapshot = world.snapshot()
        reaction_snapshot = reaction.snapshot()
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "player_id": player_id,
                    "mode": mode.value,
                    "game_end": world_snapshot.freshness is Freshness.ENDED
                    and client_exit.reason is ClientExitReason.GAME_ENDED,
                    "freshness": world_snapshot.freshness.value,
                    "client_exit": client_exit.reason.value,
                    "world_exit": world_exit.reason.value,
                    "brain_call_count": brain.call_count,
                    "brain_decisions": decisions,
                    "reaction": {
                        "lifecycle": reaction_snapshot.lifecycle.value,
                        "chat_brain_invocations": reaction_snapshot.chat_brain_invocations,
                        "send_count": reaction_snapshot.send_count,
                        "accepted_count": reaction_snapshot.accepted_count,
                        "rejected_count": reaction_snapshot.rejected_count,
                        "deadline_suppressed_count": reaction_snapshot.deadline_suppressed_count,
                        "intentional_silence_count": reaction_snapshot.intentional_silence_count,
                        "rejections": [
                            {
                                "action": observation.action,
                                "reason": observation.reason,
                                "seq": observation.seq,
                                "connection_generation": observation.connection_generation,
                            }
                            for observation in world.transport_observations().observations
                            if isinstance(observation, ActionRejectionObservation)
                        ],
                        "outcomes": [
                            {
                                "trigger": outcome.trigger.kind.value,
                                "day": outcome.trigger.phase_key.day,
                                "phase": outcome.trigger.phase_key.phase,
                                "source_order": outcome.trigger.source_order,
                                "attempt_ordinal": outcome.trigger.attempt_ordinal,
                                "scheduled_due_monotonic": outcome.scheduled_due_monotonic,
                                "brain_outcome": outcome.brain_outcome,
                                "action_kind": outcome.action_kind,
                                "status": outcome.status.value,
                            }
                            for outcome in reaction_snapshot.outcomes
                        ],
                    },
                    "current_deadline": (
                        None
                        if world.transport_observations().current_deadline is None
                        else {
                            "mapping_order": world.transport_observations().current_deadline.mapping_order,
                            "phase": world.transport_observations().current_deadline.phase,
                            "day": world.transport_observations().current_deadline.day,
                        }
                    ),
                    "production_import_guard": not any(
                        name == "server.aiwolf_core"
                        or name.startswith("server.aiwolf_core.")
                        or name == "server.network"
                        or name.startswith("server.network.")
                        for name in sys.modules
                    ),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return 0 if client_exit.reason is ClientExitReason.GAME_ENDED else 1
    finally:
        await reaction.stop()
        await arbiter.stop()
        for task in (*ready_tasks, world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(*ready_tasks, world_task, client_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--mode", required=True, choices=[mode.value for mode in CompletionReactionMode])
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--day-one-ready", type=Path)
    parser.add_argument("--clock-start", type=Path)
    parser.add_argument("--day-one-release", type=Path)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_driver(
                args.uri,
                args.game_id,
                args.player_id,
                args.entry_token,
                args.credentials,
                args.status,
                args.seed,
                CompletionReactionMode(args.mode),
                args.ready,
                args.day_one_ready,
                args.clock_start,
                args.day_one_release,
            )
        )
    )


if __name__ == "__main__":
    main()
