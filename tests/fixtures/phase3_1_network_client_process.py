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
    SequenceGapDetected,
    SequenceGapRecovered,
    ServerEvent,
    StaleActionError,
    VoteAction,
)


async def run_driver(
    uri: str,
    game_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    stop_after: str | None = None,
    stop_marker_path: Path | None = None,
    inject_rejection: bool = False,
) -> int:
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
    )
    sent_action_generations: set[tuple[int, str]] = set()
    rejections: list[dict[str, str]] = []
    resumed = False
    game_ended = False
    send_errors: list[dict[str, str]] = []
    chat_sent = 0
    chat_received = 0
    gap_detected = 0
    gap_recovered = 0
    rejection_injected = False
    stop_gate = asyncio.Event()

    async def run_client() -> object:
        return await client.run()

    run_task = asyncio.create_task(run_client())
    async for event in client.events():
        if isinstance(event, ActionRejected):
            rejections.append({"action": event.action, "reason": event.reason})
            continue
        if isinstance(event, SequenceGapDetected):
            gap_detected += 1
            continue
        if isinstance(event, SequenceGapRecovered):
            gap_recovered += 1
            continue
        if isinstance(event, GameEnded):
            game_ended = True
            continue
        if isinstance(event, ServerEvent) and event.type == "session.resumed":
            resumed = True
        if isinstance(event, ServerEvent) and event.type == "chat.message":
            chat_received += 1
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
                if isinstance(action, CoDeclareAction) and action.claimed_role_ids:
                    await client.send_co_declare(
                        action, action.claimed_role_ids[0], "I claim this role."
                    )
                    if inject_rejection and not rejection_injected:
                        for _ in range(3):
                            await client.send_co_declare(
                                action, action.claimed_role_ids[0], "I claim this role again."
                            )
                        rejection_injected = True
                elif isinstance(action, ChatAction):
                    await client.send_chat(action, "Protocol-only client speaking.")
                elif isinstance(action, VoteAction) and action.valid_targets:
                    await client.send_vote(action, action.valid_targets[0])
                elif isinstance(action, AbilityAction):
                    if action.uses_remaining == 0 or len(action.valid_targets) < action.target_count:
                        continue
                    await client.send_ability(
                        action, list(action.valid_targets[: action.target_count])
                    )
                else:
                    continue
            except StaleActionError:
                # A phase transition can make a handle stale between snapshot
                # and send. The next action-state event supplies a new handle.
                continue
            except Exception as error:
                send_errors.append(
                    {"type": type(error).__name__, "message": str(error)}
                )
                continue
            sent_action_generations.add(key)
            if isinstance(action, ChatAction):
                chat_sent += 1
                stop_kind = "chat"
            else:
                stop_kind = "action"
            if stop_after == stop_kind:
                if stop_marker_path is None:
                    raise RuntimeError("stop marker is required when stop_after is set")
                stop_marker_path.write_text(
                    json.dumps(
                        {"pid": os.getpid(), "kind": stop_kind, "last_seq": client.snapshot().last_seq},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                await stop_gate.wait()
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
                "send_errors": send_errors,
                "chat_sent": chat_sent,
                "chat_received": chat_received,
                "gap_detected": gap_detected,
                "gap_recovered": gap_recovered,
                "server_imports": sorted(
                    name for name in sys.modules
                    if name == "server.aiwolf_core"
                    or name.startswith("server.aiwolf_core.")
                    or name == "server.network"
                    or name.startswith("server.network.")
                ),
                "production_import_guard": not any(
                    name == "server.aiwolf_core"
                    or name.startswith("server.aiwolf_core.")
                    or name == "server.network"
                    or name.startswith("server.network.")
                    for name in sys.modules
                ),
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
    parser.add_argument("--stop-after", choices=("action", "chat"))
    parser.add_argument("--stop-marker", type=Path)
    parser.add_argument("--inject-rejection", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run_driver(
                arguments.uri,
                arguments.game_id,
                arguments.entry_token,
                arguments.credentials,
                arguments.status,
                arguments.stop_after,
                arguments.stop_marker,
                arguments.inject_rejection,
            )
        )
    )


if __name__ == "__main__":
    main()
