"""Standalone protocol-only client used by the Phase 2 completion test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from websockets.asyncio.client import connect


def request(message_type: str, game_id: str, payload: dict[str, object]) -> str:
    return json.dumps({
        "type": message_type,
        "protocol_version": "1.0",
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


async def send_available_action(socket, game_id: str, payload: dict[str, object]) -> None:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("type") == "vote":
            targets = action.get("valid_targets")
            if isinstance(targets, list) and targets:
                await socket.send(request(
                    "vote.cast", game_id, {"target_player_id": targets[0]}
                ))
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
                await socket.send(request("ability.use", game_id, {
                    "ability_id": ability_id,
                    "target_player_ids": targets[:count],
                }))
                return


async def run(uri: str, game_id: str, entry_token: str, credentials_path: Path, status_path: Path) -> None:
    credentials = read_credentials(credentials_path)
    resumed = credentials is not None
    last_seq = int(credentials["last_seq"]) if credentials is not None else 0
    connection_token = str(credentials["connection_token"]) if credentials is not None else ""
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
            if message.get("type") == "session.joined":
                connection_token = message["payload"]["connection_token"]
            if connection_token:
                write_json(credentials_path, {
                    "connection_token": connection_token,
                    "last_seq": last_seq,
                })
            if message.get("type") == "game.state_sync":
                await send_available_action(socket, game_id, message["payload"]["action_state"])
            elif message.get("type") == "player.action_state":
                await send_available_action(socket, game_id, message["payload"])
            elif (
                message.get("type") == "game.event"
                and message["payload"].get("event_type") == "GAME_ENDED"
            ):
                write_json(status_path, {
                    "game_end": True,
                    "pid": os.getpid(),
                    "resumed": resumed,
                    "last_seq": last_seq,
                })
                return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--entry-token", required=True)
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    arguments = parser.parse_args()
    asyncio.run(run(
        arguments.uri,
        arguments.game_id,
        arguments.entry_token,
        arguments.credentials,
        arguments.status,
    ))


if __name__ == "__main__":
    main()
