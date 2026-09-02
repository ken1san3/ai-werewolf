"""LLM-free World State driver for the Phase 3.2 completion test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.network import (
    AbilityAction,
    ChatAction,
    ClientExitReason,
    CoDeclareAction,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
    StaleActionError,
    VoteAction,
)
from ai_client.world import Freshness, WorldState


async def run_driver(
    uri: str,
    game_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
) -> int:
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
    )
    world = WorldState(client)
    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    sent: set[tuple[int, str]] = set()
    send_errors: list[str] = []

    try:
        while not world_task.done():
            actions = world.current_actions()
            for action in actions.actions:
                key = (getattr(action, "action_generation", -1), action.type)
                if key in sent:
                    continue
                try:
                    if isinstance(action, CoDeclareAction) and action.claimed_role_ids:
                        await client.send_co_declare(action, action.claimed_role_ids[0], "I claim this role.")
                    elif isinstance(action, ChatAction):
                        await client.send_chat(action, "World State driver speaking.")
                    elif isinstance(action, VoteAction) and action.valid_targets:
                        await client.send_vote(action, action.valid_targets[0])
                    elif isinstance(action, AbilityAction):
                        if action.uses_remaining == 0 or len(action.valid_targets) < action.target_count:
                            continue
                        await client.send_ability(action, list(action.valid_targets[: action.target_count]))
                    else:
                        continue
                    sent.add(key)
                except StaleActionError:
                    continue
                except Exception as error:
                    send_errors.append(type(error).__name__)
            await asyncio.sleep(0.005)
        world_exit = await world_task
        client_exit = await client_task
        snapshot = world.snapshot()
        status_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "game_end": snapshot.freshness is Freshness.ENDED
                    and client_exit.reason is ClientExitReason.GAME_ENDED,
                    "freshness": snapshot.freshness.value,
                    "last_applied_seq": snapshot.last_applied_seq,
                    "players": len(snapshot.players),
                    "alive": len(snapshot.alive_player_ids),
                    "actions_seen": len(sent),
                    "history_records": snapshot.history_retention.retained_count,
                    "unknown_event_count": snapshot.unknown_event_count,
                    "known_unmodeled_event_count": snapshot.known_unmodeled_event_count,
                    "malformed_event_count": snapshot.malformed_event_count,
                    "send_errors": send_errors,
                    "world_exit_reason": world_exit.reason.value,
                    "exit_reason": client_exit.reason.value,
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
        for task in (world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(world_task, client_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_driver(
                arguments.uri,
                arguments.game_id,
                arguments.entry_token,
                arguments.credentials,
                arguments.status,
            )
        )
    )


if __name__ == "__main__":
    main()
