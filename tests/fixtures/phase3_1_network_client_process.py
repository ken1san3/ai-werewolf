"""LLM-free Phase 3.1 driver using only Network Client snapshots and handles."""

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
    ActionRejected,
    ChatAction,
    ClientExitReason,
    CoDeclareAction,
    FileCredentialStore,
    GameEnded,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
    ServerEvent,
    VoteAction,
)


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
    sent_action_generations: set[tuple[int, str]] = set()
    rejections: list[dict[str, str]] = []
    resumed = credentials_path.exists()
    game_ended = False

    async def run_client() -> object:
        return await client.run()

    run_task = asyncio.create_task(run_client())
    async for event in client.events():
        if isinstance(event, ActionRejected):
            rejections.append({"action": event.action, "reason": event.reason})
            continue
        if isinstance(event, GameEnded):
            game_ended = True
            continue
        if not isinstance(event, ServerEvent) or event.type not in {
            "game.state_sync",
            "player.action_state",
        }:
            continue
        snapshot = client.snapshot()
        for action in snapshot.actions:
            key = (action.action_generation, action.type)
            if key in sent_action_generations:
                continue
            try:
                if isinstance(action, ChatAction):
                    await client.send_chat(action, "The discussion is open.")
                elif isinstance(action, CoDeclareAction) and action.claimed_role_ids:
                    await client.send_co_declare(
                        action, action.claimed_role_ids[0], "I claim this role."
                    )
                elif isinstance(action, VoteAction) and action.valid_targets:
                    await client.send_vote(action, action.valid_targets[0])
                elif isinstance(action, AbilityAction):
                    if action.uses_remaining == 0:
                        continue
                    await client.send_ability(
                        action, list(action.valid_targets[: action.target_count])
                    )
                else:
                    continue
            except Exception:
                # A phase transition can make a handle stale between snapshot
                # and send. The next action-state event supplies a new handle.
                continue
            sent_action_generations.add(key)
            if isinstance(action, (VoteAction, AbilityAction)):
                break

    result = await run_task
    result_reason = getattr(result, "reason", None)
    status_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "resumed": resumed,
                "game_end": game_ended or result_reason == ClientExitReason.GAME_ENDED,
                "last_seq": client.snapshot().last_seq,
                "action_rejections": rejections,
                "exit_reason": getattr(result_reason, "value", result_reason),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0 if result_reason == ClientExitReason.GAME_ENDED else 1


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
