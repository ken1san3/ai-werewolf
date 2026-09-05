"""Production client composition used by the Phase 3.3 completion test."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import json
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.brain import DummyBrain, BrainController, BrainRunConfig, PhaseBrainCoordinator
from ai_client.network import (
    ClientExitReason,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
)
from ai_client.world import Freshness, WorldState


async def run_driver(
    uri: str,
    game_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    seed: int,
    ready_path: Path | None = None,
) -> int:
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
    )
    world = WorldState(client)
    brain = DummyBrain(seed=seed)
    controller = BrainController(
        world=world,
        sender=client,
        brain=brain,
        config=BrainRunConfig(max_decision_seconds=0.5),
    )
    coordinator = PhaseBrainCoordinator(world=world, controller=controller)
    decision_statuses: list[str] = []
    original_decide_and_send = controller.decide_and_send

    async def record_decision(request, *, timeout_seconds=None):
        outcome = await original_decide_and_send(
            request,
            timeout_seconds=timeout_seconds,
        )
        decision_statuses.append(outcome.status.value)
        return outcome

    controller.decide_and_send = record_decision

    async def write_ready_marker() -> None:
        if ready_path is None:
            return
        while True:
            snapshot = world.snapshot()
            if (
                snapshot.freshness is Freshness.CURRENT
                and snapshot.is_caught_up
                and snapshot.phase is not None
            ):
                ready_path.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "version": snapshot.version,
                            "last_applied_seq": snapshot.last_applied_seq,
                        }
                    ),
                    encoding="utf-8",
                )
                return
            await asyncio.sleep(0.01)

    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    coordinator_task = asyncio.create_task(coordinator.run())
    ready_task = asyncio.create_task(write_ready_marker())
    try:
        coordinator_exit = await asyncio.wait_for(coordinator_task, 45.0)
        world_exit = await asyncio.wait_for(world_task, 10.0)
        client_exit = await asyncio.wait_for(client_task, 10.0)
        snapshot = world.snapshot()
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "game_end": snapshot.freshness is Freshness.ENDED
                    and client_exit.reason is ClientExitReason.GAME_ENDED
                    and coordinator_exit.success,
                    "freshness": snapshot.freshness.value,
                    "client_exit": client_exit.reason.value,
                    "world_exit": world_exit.reason.value,
                    "coordinator_exit": coordinator_exit.reason.value,
                    "brain_call_count": brain.call_count,
                    "decision_statuses": decision_statuses,
                    "attempted_phases": [
                        {"day": phase.day, "phase": phase.phase}
                        for phase in coordinator_exit.attempted_phases
                    ],
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
        for task in (ready_task, coordinator_task, world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            ready_task,
            coordinator_task,
            world_task,
            client_task,
            return_exceptions=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--ready", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_driver(
                arguments.uri,
                arguments.game_id,
                arguments.entry_token,
                arguments.credentials,
                arguments.status,
                arguments.seed,
                arguments.ready,
            )
        )
    )


if __name__ == "__main__":
    main()
