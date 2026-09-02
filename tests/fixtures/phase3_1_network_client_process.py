"""LLM-free Phase 3.1 driver using only Network Client snapshots and handles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Mapping

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

DAYTIME_ACTION_SAFETY_SECONDS = 0.25
CHAT_SENDER_LIMIT = 3
PRIMARY_ACTION_SENDER_INDEX = 1


def _is_expired_daytime_action_state(
    payload: object, server_timestamp: object, *, received_at: float
) -> bool:
    """Reject action states that are already past the server's phase deadline.

    The server timestamp and deadline are compared in server-clock space. The
    local monotonic clock is used only for elapsed time after this event was
    received, so clocks from different hosts are never compared directly.
    """

    if not isinstance(payload, Mapping) or payload.get("phase") not in {
        "day",
        "night",
        "night0",
        "runoff",
        "vote",
    }:
        return False
    phase_ends_at = payload.get("phase_ends_at")
    if (
        not isinstance(phase_ends_at, int)
        or isinstance(phase_ends_at, bool)
        or not isinstance(server_timestamp, int)
        or isinstance(server_timestamp, bool)
    ):
        return False
    remaining_seconds = float(phase_ends_at - server_timestamp)
    elapsed_since_receive = max(0.0, time.monotonic() - received_at)
    return remaining_seconds - elapsed_since_receive <= DAYTIME_ACTION_SAFETY_SECONDS


def _is_preferred_chat_sender(snapshot: object) -> bool:
    """Limit simultaneous chat sends while retaining multiple client paths."""

    player_id = getattr(snapshot, "player_id", None)
    players = getattr(snapshot, "players", ())
    player_ids = tuple(
        item.get("player_id")
        for item in players
        if isinstance(item, Mapping) and isinstance(item.get("player_id"), str)
    )
    return isinstance(player_id, str) and player_id in player_ids[:CHAT_SENDER_LIMIT]


def _is_primary_action_sender(snapshot: object) -> bool:
    """Use one stable seat for completion-only action evidence."""

    player_id = getattr(snapshot, "player_id", None)
    players = getattr(snapshot, "players", ())
    player_ids = tuple(
        item.get("player_id")
        for item in players
        if isinstance(item, Mapping) and isinstance(item.get("player_id"), str)
    )
    return (
        len(player_ids) > PRIMARY_ACTION_SENDER_INDEX
        and player_id == player_ids[PRIMARY_ACTION_SENDER_INDEX]
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
    inject_driver_error: bool = False,
) -> int:
    client: NetworkClient | None = None
    run_task: asyncio.Task[object] | None = None
    sent_action_generations: set[tuple[int, str]] = set()
    sent_phase_actions: set[tuple[str, int, str, str]] = set()
    ability_action_sent = False
    vote_action_sent = False
    rejections: list[dict[str, str]] = []
    resumed = False
    game_ended = False
    send_errors: list[dict[str, str]] = []
    chat_sent = 0
    chat_received = 0
    gap_detected = 0
    gap_recovered = 0
    rejection_injected = False
    daytime_chat_sent = False
    daytime_co_declared = False
    events_received = 0
    actions_sent = 0
    result_reason: object | None = None
    driver_error: BaseException | None = None
    stop_gate = asyncio.Event()

    try:
        client = NetworkClient(
            NetworkClientConfig(uri, game_id, entry_token),
            FileCredentialStore(credentials_path),
            reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
        )

        async def run_client() -> object:
            assert client is not None
            return await client.run()

        run_task = asyncio.create_task(run_client())
        if inject_driver_error:
            raise RuntimeError("injected Phase 3.1 driver failure")

        async for event in client.events():
            events_received += 1
            received_at = time.monotonic()
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
            action_state = (
                event.payload.get("action_state")
                if event.type == "game.state_sync"
                else event.payload
            )
            snapshot = client.snapshot()
            for action in snapshot.actions:
                if resumed:
                    # A resumed process has already represented its pre-outage
                    # actions. Avoid replaying an action handle whose phase may
                    # have advanced while the resume sync was being assembled.
                    continue
                timed_action = getattr(action, "phase", None) in {
                    "day",
                    "night",
                    "night0",
                    "runoff",
                    "vote",
                }
                keep_chat_stop_scenario = stop_after == "chat" and isinstance(action, ChatAction)
                keep_action_stop_scenario = stop_after == "action" and isinstance(
                    action, (VoteAction, AbilityAction, CoDeclareAction)
                )
                keep_rejection_scenario = inject_rejection and isinstance(action, CoDeclareAction)
                if (
                    isinstance(action, ChatAction)
                    and not keep_chat_stop_scenario
                    and not _is_preferred_chat_sender(snapshot)
                ):
                    continue
                if (
                    isinstance(action, (VoteAction, AbilityAction, CoDeclareAction))
                    and not keep_action_stop_scenario
                    and not keep_rejection_scenario
                    and not _is_primary_action_sender(snapshot)
                ):
                    continue
                if isinstance(action, AbilityAction) and action.phase == "night0":
                    # standard_9 resolves the first-night seer inspection as a
                    # server-selected result, so no client ability submission is
                    # legal in this phase.
                    continue
                if isinstance(action, AbilityAction) and ability_action_sent:
                    continue
                if isinstance(action, VoteAction) and vote_action_sent:
                    continue
                if (
                    timed_action
                    and not keep_chat_stop_scenario
                    and not keep_rejection_scenario
                    and (
                        _is_expired_daytime_action_state(
                            action_state, event.timestamp, received_at=received_at
                        )
                    )
                ):
                    # One state push can expose more than one daytime action. A
                    # previous send or subprocess-pipe delay may consume the
                    # remaining phase time before this handle is reached.
                    continue
                key = (action.action_generation, action.type)
                if key in sent_action_generations:
                    continue
                if isinstance(action, ChatAction) and daytime_chat_sent:
                    continue
                if isinstance(action, CoDeclareAction) and daytime_co_declared:
                    continue
                phase_action_key: tuple[str, int, str, str] | None = None
                if isinstance(action, (VoteAction, AbilityAction)):
                    action_name = (
                        action.ability_id
                        if isinstance(action, AbilityAction)
                        else action.type
                    )
                    phase_action_key = (action.phase, action.day, action.type, action_name)
                    if phase_action_key in sent_phase_actions:
                        continue
                try:
                    if isinstance(action, CoDeclareAction) and action.claimed_role_ids:
                        await client.send_co_declare(
                            action, action.claimed_role_ids[0], "I claim this role."
                        )
                        actions_sent += 1
                        if inject_rejection and not rejection_injected:
                            for _ in range(3):
                                await client.send_co_declare(
                                    action, action.claimed_role_ids[0], "I claim this role again."
                                )
                                actions_sent += 1
                            rejection_injected = True
                    elif isinstance(action, ChatAction):
                        await client.send_chat(action, "Protocol-only client speaking.")
                        actions_sent += 1
                    elif isinstance(action, VoteAction) and action.valid_targets:
                        await client.send_vote(action, action.valid_targets[0])
                        actions_sent += 1
                    elif isinstance(action, AbilityAction):
                        if action.uses_remaining == 0 or len(action.valid_targets) < action.target_count:
                            continue
                        await client.send_ability(
                            action, list(action.valid_targets[: action.target_count])
                        )
                        actions_sent += 1
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
                if phase_action_key is not None:
                    sent_phase_actions.add(phase_action_key)
                if isinstance(action, ChatAction):
                    chat_sent += 1
                    daytime_chat_sent = True
                    stop_kind = "chat"
                else:
                    stop_kind = "action"
                    if isinstance(action, AbilityAction):
                        ability_action_sent = True
                    elif isinstance(action, VoteAction):
                        vote_action_sent = True
                    if isinstance(action, CoDeclareAction):
                        daytime_co_declared = True
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
        return 0 if result_reason == ClientExitReason.GAME_ENDED else 1
    except BaseException as error:
        driver_error = error
        raise
    finally:
        if run_task is not None and not run_task.done():
            run_task.cancel()
        if run_task is not None:
            try:
                await run_task
            except BaseException as cleanup_error:
                if driver_error is None:
                    driver_error = cleanup_error
        snapshot = client.snapshot() if client is not None else None
        reason_value = getattr(result_reason, "value", result_reason)
        status = {
            "pid": os.getpid(),
            "resumed": resumed,
            "game_end": game_ended or result_reason == ClientExitReason.GAME_ENDED,
            "last_seq": snapshot.last_seq if snapshot is not None else 0,
            "events_received": events_received,
            "actions_sent": actions_sent,
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
            "client_exit_reason": reason_value,
            "exit_reason": reason_value,
            "exception_type": type(driver_error).__name__ if driver_error is not None else None,
            "exception_message": str(driver_error) if driver_error is not None else None,
        }
        try:
            status_path.write_text(
                json.dumps(status, ensure_ascii=False),
                encoding="utf-8",
            )
        except BaseException as status_error:
            print(
                f"Phase 3.1 driver could not write status {status_path}: "
                f"{type(status_error).__name__}: {status_error}",
                file=sys.stderr,
            )


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
    parser.add_argument("--inject-driver-error", action="store_true")
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
                arguments.inject_driver_error,
            )
        )
    )


if __name__ == "__main__":
    main()
