"""Cumulative Phase 3.5 client process composition."""

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

from ai_client.brain import (
    BrainController,
    BrainInvocationArbiter,
    BrainRunConfig,
    FeatureControllerExitReason,
)
from ai_client.network import (
    ClientExitReason,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
)
from ai_client.reaction_chat import ReactionChatController, ReactionChatLifecycle
from ai_client.vote_ability import (
    VoteAbilityController,
    VoteAbilityLifecycle,
)
from ai_client.world import ActionRejectionObservation, Freshness, WorldState
from tests.fixtures.phase3_4_reaction_brain import CompletionReactionMode
from tests.fixtures.phase3_5_brain import CompletionPhase35Brain
from tests.fixtures.reservation_probe import ReservationProbe


class _BarrierClock:
    """Mirror server freezes for initial composition and the first day."""

    def __init__(self, clock_start_path: Path, day_one_release_path: Path) -> None:
        self._clock_start_path = clock_start_path
        self._day_one_release_path = day_one_release_path
        self._origin = time.monotonic()
        self._started_at: float | None = None
        self._day_one_released_at: float | None = None

    def __call__(self) -> float:
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


def _reaction_evidence(snapshot, observations) -> dict[str, object]:
    return {
        "lifecycle": snapshot.lifecycle.value,
        "chat_brain_invocations": snapshot.chat_brain_invocations,
        "send_count": snapshot.send_count,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "intentional_silence_count": snapshot.intentional_silence_count,
        "rejections": [
            {
                "action": observation.action,
                "reason": observation.reason,
                "seq": observation.seq,
                "connection_generation": observation.connection_generation,
            }
            for observation in observations
            if isinstance(observation, ActionRejectionObservation)
            and observation.action not in {"vote.cast", "ability.use"}
        ],
        "outcomes": [
            {
                "trigger": outcome.trigger.kind.value,
                "day": outcome.trigger.phase_key.day,
                "phase": outcome.trigger.phase_key.phase,
                "status": outcome.status.value,
            }
            for outcome in snapshot.outcomes
        ],
    }


def _reservation_evidence(snapshot, observations) -> dict[str, object]:
    return {
        "lifecycle": snapshot.lifecycle.value,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "unknown_count": snapshot.unknown_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "unresolved": snapshot.unresolved_reservation is not None,
        "rejections": [
            {
                "action": observation.action,
                "reason": observation.reason,
                "seq": observation.seq,
                "connection_generation": observation.connection_generation,
                "request_event_id": observation.request_event_id,
            }
            for observation in observations
            if isinstance(observation, ActionRejectionObservation)
            and observation.action in {"vote.cast", "ability.use"}
        ],
        "outcomes": [
            {
                "day": outcome.opportunity_key.reservation.day,
                "phase": outcome.opportunity_key.reservation.phase,
                "family": outcome.opportunity_key.reservation.family,
                "action": outcome.action,
                "ability_id": outcome.ability_id,
                "vote_target_player_id": outcome.vote_target_player_id,
                "ability_target_player_ids": list(outcome.ability_target_player_ids),
                "request_event_id": outcome.request_event_id,
                "send_connection_generation": outcome.send_connection_generation,
                "observation_connection_generation": outcome.observation_connection_generation,
                "status": outcome.status.value,
                "rejection_reason": outcome.rejection_reason,
                "attempts": outcome.attempts,
            }
            for outcome in snapshot.outcomes
        ],
    }


def _deadline_mapping_evidence(deadline) -> dict[str, object] | None:
    if deadline is None:
        return None
    return {
        "mapping_order": deadline.mapping_order,
        "phase": deadline.phase,
        "day": deadline.day,
        "connection_generation": deadline.connection_generation,
        "action_generation": deadline.action_generation,
        "local_deadline_monotonic": deadline.local_deadline_monotonic,
    }


async def run_driver(
    uri: str,
    game_id: str,
    player_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    seed: int,
    mode: CompletionReactionMode,
    ready_path: Path,
    day_one_ready_path: Path,
    clock_start_path: Path,
    day_one_release_path: Path,
) -> int:
    shared_clock = _BarrierClock(clock_start_path, day_one_release_path)
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
        clock=shared_clock,
        sleep=shared_clock.sleep,
    )
    world = WorldState(client)
    brain = CompletionPhase35Brain(player_id=player_id, seed=seed, mode=mode)
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
    reservation = VoteAbilityController(
        world=world,
        invoker=arbiter,
        clock=shared_clock,
    )
    reservation_probe = ReservationProbe(brain_controller, reservation)

    async def write_marker(path: Path, *, day: int, phase: str) -> None:
        while True:
            snapshot = world.snapshot()
            transport = world.transport_observations()
            deadline = transport.current_deadline
            reaction_snapshot = reaction.snapshot()
            reservation_snapshot = reservation.snapshot()
            if (
                snapshot.freshness is Freshness.CURRENT
                and snapshot.is_caught_up
                and snapshot.phase is not None
                and (snapshot.phase.day, snapshot.phase.phase) == (day, phase)
                and deadline is not None
                and (deadline.day, deadline.phase) == (day, phase)
                and reaction_snapshot.lifecycle is ReactionChatLifecycle.RUNNING
                and reservation_snapshot.lifecycle is VoteAbilityLifecycle.RUNNING
                and reaction_snapshot.transport_cursor >= deadline.mapping_order
                and reservation_snapshot.transport_cursor >= deadline.mapping_order
            ):
                path.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "player_id": player_id,
                            "day": day,
                            "phase": phase,
                            "world_version": snapshot.version,
                            "mapping_order": deadline.mapping_order,
                            "reservation_opportunity": (
                                reservation_snapshot.current_opportunity is not None
                            ),
                            "deadline_mapping": _deadline_mapping_evidence(deadline),
                            "reaction": _reaction_evidence(
                                reaction_snapshot, transport.observations
                            ),
                            "reservation": _reservation_evidence(
                                reservation_snapshot, transport.observations
                            ),
                        }
                    ),
                    encoding="utf-8",
                )
                return
            await asyncio.sleep(0.01)

    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    reaction.start()
    reservation.start()
    reaction_wait = asyncio.create_task(reaction.wait())
    reservation_wait = asyncio.create_task(reservation.wait())
    marker_tasks = (
        asyncio.create_task(write_marker(ready_path, day=0, phase="night0")),
        asyncio.create_task(write_marker(day_one_ready_path, day=1, phase="day")),
    )
    try:
        watched = {client_task, world_task, reaction_wait, reservation_wait}
        while not client_task.done():
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if reaction_wait in done:
                exit_value = reaction_wait.result()
                if exit_value.reason in {
                    FeatureControllerExitReason.FAILED,
                    FeatureControllerExitReason.WORLD_FAILED,
                }:
                    raise RuntimeError(f"Reaction controller failed: {exit_value}")
            if reservation_wait in done:
                exit_value = reservation_wait.result()
                if exit_value.reason in {
                    FeatureControllerExitReason.FAILED,
                    FeatureControllerExitReason.WORLD_FAILED,
                }:
                    raise RuntimeError(f"reservation controller failed: {exit_value}")
            if world_task in done and not world_task.result().success:
                raise RuntimeError(f"World failed: {world_task.result()}")
            if not client_task.done():
                await asyncio.sleep(0)
        client_exit = client_task.result()
        world_exit = await asyncio.wait_for(world_task, 10.0)
        reaction_exit, reservation_exit = await asyncio.wait_for(
            asyncio.gather(reaction_wait, reservation_wait), 10.0
        )
        if reaction_exit.reason in {
            FeatureControllerExitReason.FAILED,
            FeatureControllerExitReason.WORLD_FAILED,
        } or reservation_exit.reason in {
            FeatureControllerExitReason.FAILED,
            FeatureControllerExitReason.WORLD_FAILED,
        }:
            raise RuntimeError(
                f"feature controller failed: {reaction_exit!r}, {reservation_exit!r}"
            )
        transport = world.transport_observations()
        observations = transport.observations
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "player_id": player_id,
                    "mode": mode.value,
                    "game_end": world.snapshot().freshness is Freshness.ENDED
                    and client_exit.reason is ClientExitReason.GAME_ENDED,
                    "freshness": world.snapshot().freshness.value,
                    "client_exit": client_exit.reason.value,
                    "world_exit": world_exit.reason.value,
                    "brain_call_count": brain.call_count,
                    "brain_decisions": brain.decisions,
                    "reservation_probe": reservation_probe.snapshot(),
                    "reaction": _reaction_evidence(reaction.snapshot(), observations),
                    "reservation": _reservation_evidence(
                        reservation.snapshot(), observations
                    ),
                    "deadline_mapping": _deadline_mapping_evidence(
                        transport.current_deadline
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
        await reservation.stop()
        await reaction.stop()
        await arbiter.stop()
        await world.stop()
        await client.stop()
        for task in (*marker_tasks, reaction_wait, reservation_wait, world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            *marker_tasks,
            reaction_wait,
            reservation_wait,
            world_task,
            client_task,
            return_exceptions=True,
        )


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
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--day-one-ready", required=True, type=Path)
    parser.add_argument("--clock-start", required=True, type=Path)
    parser.add_argument("--day-one-release", required=True, type=Path)
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
