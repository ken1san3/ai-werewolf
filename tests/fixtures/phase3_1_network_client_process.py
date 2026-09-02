"""LLM-free Phase 3.1 driver using only Network Client snapshots and handles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from collections.abc import Mapping
from typing import Any

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

from completion_evidence import STATUS_SCHEMA_VERSION


def _action_key(player_id: str | None, action: object) -> str:
    """Build a semantic key without role, channel, or token data."""

    if not isinstance(player_id, str) or not player_id:
        raise RuntimeError("authenticated player id is required before recording an action")
    if isinstance(action, AbilityAction):
        return f"{player_id}|{action.day}|{action.phase}|ability|{action.ability_id}"
    if isinstance(action, VoteAction):
        return f"{player_id}|{action.day}|{action.phase}|vote"
    if isinstance(action, CoDeclareAction):
        return f"{player_id}|{action.day}|co_declare"
    if isinstance(action, ChatAction):
        return f"{player_id}|{action.day}|chat"
    raise TypeError(f"unsupported completion action: {type(action).__name__}")


def _action_kind(action: object) -> str:
    if isinstance(action, VoteAction):
        return "vote"
    if isinstance(action, AbilityAction):
        return "ability"
    if isinstance(action, CoDeclareAction):
        return "co_declare"
    if isinstance(action, ChatAction):
        return "chat"
    raise TypeError(f"unsupported completion action: {type(action).__name__}")


def _action_evidence(player_id: str, action: object, *, resumed: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "key": _action_key(player_id, action),
        "kind": _action_kind(action),
        "day": action.day,
        "phase": action.phase,
        "action_generation": action.action_generation,
        "after_resume": resumed,
        "outcome": "sent",
    }
    if isinstance(action, AbilityAction):
        record["ability_id"] = action.ability_id
    return record


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


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
    evidence_by_key: dict[str, dict[str, Any]] = {}
    rejections: list[dict[str, Any]] = []
    resume_events: list[dict[str, int]] = []
    state_sync_events: list[dict[str, Any]] = []
    gap_events: list[dict[str, int | None]] = []
    chat_messages_received: list[dict[str, Any]] = []
    send_errors: list[dict[str, str]] = []
    resumed = False
    gap_pending = False
    game_ended = False
    rejection_injected = False
    events_received = 0
    actions_sent = 0
    chat_sent = 0
    result_reason: object | None = None
    driver_error: BaseException | None = None
    stop_written = False

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
            if isinstance(event, ActionRejected):
                rejections.append(
                    {
                        "action": event.action,
                        "reason": event.reason,
                        "seq": event.seq,
                        "after_resume": resumed,
                    }
                )
                continue
            if isinstance(event, SequenceGapDetected):
                gap_pending = True
                gap_events.append(
                    {
                        "expected_seq": event.expected_seq,
                        "received_seq": event.received_seq,
                        "recovered_seq": None,
                    }
                )
                continue
            if isinstance(event, SequenceGapRecovered):
                gap_pending = False
                for item in reversed(gap_events):
                    if item["recovered_seq"] is None:
                        item["recovered_seq"] = event.recovered_seq
                        break
                continue
            if isinstance(event, GameEnded):
                game_ended = True
                continue
            if isinstance(event, ServerEvent) and event.type == "session.resumed":
                resumed = True
                requested_last_seq = event.payload.get("last_seq")
                if not isinstance(requested_last_seq, int) or isinstance(requested_last_seq, bool):
                    raise RuntimeError("session.resumed omitted integer last_seq")
                resume_events.append(
                    {"seq": event.seq, "requested_last_seq": requested_last_seq}
                )
                continue
            if isinstance(event, ServerEvent) and event.type == "chat.message":
                message = event.payload.get("message")
                sender = message.get("player_id") if isinstance(message, Mapping) else None
                chat_messages_received.append(
                    {"seq": event.seq, "sender_player_id": sender}
                )
                continue
            if not isinstance(event, ServerEvent):
                continue
            if event.type == "game.state_sync":
                state_sync_events.append(
                    {
                        "seq": event.seq,
                        "after_resume": resumed,
                        "after_gap": gap_pending,
                    }
                )
            if event.type not in {"game.state_sync", "player.action_state"}:
                continue

            snapshot = client.snapshot()
            player_id = snapshot.player_id
            if not isinstance(player_id, str):
                raise RuntimeError("action state arrived before authentication")

            for action in snapshot.actions:
                if not isinstance(action, (VoteAction, AbilityAction, CoDeclareAction, ChatAction)):
                    continue
                if stop_written:
                    # The controller is interrupted, but the Network Client
                    # receiver keeps consuming and checkpointing later replies.
                    continue
                key = _action_key(player_id, action)
                if key in evidence_by_key:
                    continue

                evidence = _action_evidence(player_id, action, resumed=resumed)
                evidence_by_key[key] = evidence

                if isinstance(action, AbilityAction) and (
                    action.uses_remaining == 0
                    or len(action.valid_targets) < action.target_count
                ):
                    evidence["outcome"] = "no_legal_target"
                    continue
                if isinstance(action, CoDeclareAction) and not action.claimed_role_ids:
                    evidence["outcome"] = "no_legal_target"
                    continue
                if isinstance(action, VoteAction) and not action.valid_targets and not action.allows_abstain:
                    evidence["outcome"] = "no_legal_target"
                    continue

                try:
                    if isinstance(action, CoDeclareAction):
                        await client.send_co_declare(
                            action,
                            action.claimed_role_ids[0],
                            "I claim this role.",
                        )
                        actions_sent += 1
                        if inject_rejection and not rejection_injected:
                            for _ in range(3):
                                await client.send_co_declare(
                                    action,
                                    action.claimed_role_ids[0],
                                    "I claim this role again.",
                                )
                                actions_sent += 1
                            rejection_injected = True
                    elif isinstance(action, ChatAction):
                        await client.send_chat(action, "Protocol-only client speaking.")
                        actions_sent += 1
                        chat_sent += 1
                    elif isinstance(action, VoteAction):
                        target = action.valid_targets[0] if action.valid_targets else None
                        await client.send_vote(action, target)
                        actions_sent += 1
                    elif isinstance(action, AbilityAction):
                        await client.send_ability(
                            action,
                            list(action.valid_targets[: action.target_count]),
                        )
                        actions_sent += 1
                except StaleActionError:
                    evidence["outcome"] = "stale_before_send"
                except Exception as error:
                    evidence["outcome"] = "send_error"
                    send_errors.append(
                        {
                            "type": type(error).__name__,
                            "message": str(error),
                            "evidence_key": key,
                        }
                    )
                if evidence["outcome"] != "sent":
                    continue

                if stop_after == "action" and isinstance(action, VoteAction) and not stop_written:
                    if stop_marker_path is None:
                        raise RuntimeError("stop marker is required when stop_after is set")
                    _write_json_atomic(
                        stop_marker_path,
                        {
                            "schema_version": STATUS_SCHEMA_VERSION,
                            "pid": os.getpid(),
                            "player_id": player_id,
                            "kind": "action",
                            "last_seq": client.snapshot().last_seq,
                            "action_evidence": list(evidence_by_key.values()),
                        },
                    )
                    stop_written = True
                if stop_after == "chat" and isinstance(action, ChatAction) and not stop_written:
                    if stop_marker_path is None:
                        raise RuntimeError("stop marker is required when stop_after is set")
                    _write_json_atomic(
                        stop_marker_path,
                        {
                            "schema_version": STATUS_SCHEMA_VERSION,
                            "pid": os.getpid(),
                            "player_id": player_id,
                            "kind": "chat",
                            "last_seq": client.snapshot().last_seq,
                            "action_evidence": list(evidence_by_key.values()),
                        },
                    )
                    stop_written = True

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
            "schema_version": STATUS_SCHEMA_VERSION,
            "pid": os.getpid(),
            "player_id": snapshot.player_id if snapshot is not None else None,
            "resumed": resumed,
            "resume_events": resume_events,
            "state_sync_events": state_sync_events,
            "gap_events": gap_events,
            "action_evidence": list(evidence_by_key.values()),
            "action_rejections": rejections,
            "chat_messages_received": chat_messages_received,
            "game_end": game_ended or result_reason == ClientExitReason.GAME_ENDED,
            "last_seq": snapshot.last_seq if snapshot is not None else 0,
            "events_received": events_received,
            "send_errors": send_errors,
            "chat_sent": chat_sent,
            "chat_received": len(chat_messages_received),
            "gap_detected": len(gap_events),
            "gap_recovered": sum(
                item["recovered_seq"] is not None for item in gap_events
            ),
            "actions_sent": actions_sent,
            "server_imports": sorted(
                name
                for name in sys.modules
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
            _write_json_atomic(status_path, status)
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
