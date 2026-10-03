"""Standalone protocol-only client used by the Phase 2 completion test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from websockets.asyncio.client import connect


ACTION_SEND_GUARD_SECONDS = 0.5


def request(message_type: str, game_id: str, payload: dict[str, object]) -> str:
    return json.dumps({
        "type": message_type,
        "protocol_version": "1.2",
        "event_id": str(uuid4()),
        "game_id": game_id,
        "timestamp": 0,
        "payload": payload,
    }, ensure_ascii=False, separators=(",", ":"))


def read_credentials(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value.get("connection_token"), str) or not isinstance(value.get("last_seq"), int):
        raise ValueError("invalid connection credential file")
    return value


def write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


async def send_available_action(
    socket,
    game_id: str,
    payload: dict[str, object],
    sent_day_actions: set[tuple[int, str]],
    action_timing: dict[str, int],
    server_timestamp: object = None,
) -> None:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return
    phase = payload.get("phase")
    day = payload.get("day")
    daytime = phase == "day" and isinstance(day, int)
    phase_ends_at = payload.get("phase_ends_at")

    async def send_if_fresh(message_type: str, request_payload: dict[str, object]) -> bool:
        # The server and this same-host completion fixture share the monotonic
        # clock.  Recheck immediately before every send so an action state that
        # sat behind Windows process scheduling cannot cross its one-second
        # authoritative deadline in the test harness.
        fresh = (
            isinstance(phase_ends_at, int)
            and isinstance(server_timestamp, int)
            and phase_ends_at > server_timestamp
            and time.monotonic() + ACTION_SEND_GUARD_SECONDS < phase_ends_at
        )
        if not fresh:
            action_timing["stale_suppressed"] += 1
            return False
        await socket.send(request(message_type, game_id, request_payload))
        action_timing["sent"] += 1
        return True

    for action in actions:
        if not isinstance(action, dict):
            continue
        if daytime and action.get("type") == "chat":
            channel_id = action.get("channel")
            key = (day, "chat")
            if isinstance(channel_id, str) and key not in sent_day_actions:
                sent = await send_if_fresh("chat.send", {
                    "channel_id": channel_id,
                    "message": "The discussion is open.",
                })
                if sent:
                    sent_day_actions.add(key)
            continue
        if daytime and action.get("type") == "co_declare":
            claimed_role_ids = action.get("claimed_role_ids")
            key = (day, "co_declare")
            if (
                isinstance(claimed_role_ids, list)
                and claimed_role_ids
                and isinstance(claimed_role_ids[0], str)
                and key not in sent_day_actions
            ):
                sent = await send_if_fresh("co.declare", {
                    "claimed_role_id": claimed_role_ids[0],
                    "comment": "I claim this role.",
                })
                if sent:
                    sent_day_actions.add(key)
            continue
        if action.get("type") == "vote":
            targets = action.get("valid_targets")
            if isinstance(targets, list) and targets:
                await send_if_fresh("vote.cast", {"target_player_id": targets[0]})
            return
        if action.get("type") == "ability":
            targets = action.get("valid_targets")
            count = action.get("target_count")
            ability_id = action.get("ability_id")
            uses_remaining = action.get("uses_remaining")
            if (
                isinstance(targets, list)
                and isinstance(count, int)
                and isinstance(ability_id, str)
                and uses_remaining != 0
                and len(targets) >= count
            ):
                await send_if_fresh("ability.use", {
                    "ability_id": ability_id,
                    "target_player_ids": targets[:count],
                })
                return


async def run(
    uri: str,
    game_id: str,
    entry_token: str,
    credentials_path: Path,
    readiness_path: Path,
    status_path: Path,
) -> None:
    credentials = read_credentials(credentials_path)
    resumed = credentials is not None
    last_seq = int(credentials["last_seq"]) if credentials is not None else 0
    connection_token = str(credentials["connection_token"]) if credentials is not None else ""
    sent_day_actions: set[tuple[int, str]] = set()
    co_declared = False
    action_rejections: list[dict[str, object]] = []
    chat_messages_received = 0
    action_timing = {"sent": 0, "stale_suppressed": 0}
    readiness_written = False
    async with connect(uri) as socket:
        if resumed:
            await socket.send(request("session.resume", game_id, {
                "connection_token": connection_token,
                "last_seq": last_seq,
            }))
        else:
            await socket.send(request("session.join", game_id, {"entry_token": entry_token}))
        await socket.send(request("session.ready", game_id, {}))
        async for raw_message in socket:
            message = json.loads(raw_message)
            sequence = message.get("seq")
            if isinstance(sequence, int):
                last_seq = sequence
            message_type = message.get("type")
            if message_type == "session.joined":
                connection_token = message["payload"]["connection_token"]
            if message_type in {"session.joined", "session.resumed"} and not readiness_written:
                write_json(readiness_path, {
                    "pid": os.getpid(),
                    "resumed": resumed,
                    "message_type": message_type,
                    "observed_at_monotonic": time.monotonic(),
                })
                readiness_written = True
            if connection_token:
                write_json(credentials_path, {
                    "connection_token": connection_token,
                    "last_seq": last_seq,
                })
            if message.get("type") == "game.state_sync":
                await send_available_action(
                    socket,
                    game_id,
                    message["payload"]["action_state"],
                    sent_day_actions,
                    action_timing,
                    message.get("timestamp"),
                )
            elif message.get("type") == "player.action_state":
                await send_available_action(
                    socket,
                    game_id,
                    message["payload"],
                    sent_day_actions,
                    action_timing,
                    message.get("timestamp"),
                )
            elif message.get("type") == "action.rejected":
                payload = message.get("payload")
                action_rejections.append(
                    dict(payload) if isinstance(payload, dict) else {"payload": payload}
                )
            elif message.get("type") == "chat.message":
                chat_messages_received += 1
            elif (
                message.get("type") == "game.event"
                and message["payload"].get("event_type") == "CO_DECLARED"
            ):
                co_declared = True
            elif (
                message.get("type") == "game.event"
                and message["payload"].get("event_type") == "GAME_ENDED"
            ):
                write_json(status_path, {
                    "game_end": True,
                    "pid": os.getpid(),
                    "resumed": resumed,
                    "last_seq": last_seq,
                    "co_declared": co_declared,
                    "action_rejections": action_rejections,
                    "chat_messages_received": chat_messages_received,
                    "action_timing": action_timing,
                })
                return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--readiness", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    arguments = parser.parse_args()
    asyncio.run(run(
        arguments.uri,
        arguments.game_id,
        arguments.entry_token,
        arguments.credentials,
        arguments.readiness,
        arguments.status,
    ))


if __name__ == "__main__":
    main()
