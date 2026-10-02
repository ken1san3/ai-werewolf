"""Scratch probe: run one fast game with trivial clients and print one example of each server message type."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from random import Random
from uuid import uuid4

from websockets.asyncio.client import connect

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "server" / "aiwolf_core").is_dir())  # repository root
import sys
sys.path.insert(0, str(ROOT))
from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, WebSocketGameServer, monotonic_seconds

GAME_ID = str(uuid4())
seen = {}


def req(t, payload):
    return json.dumps({"type": t, "protocol_version": "1.1", "event_id": str(uuid4()), "game_id": GAME_ID,
                       "timestamp": 0, "payload": payload})


async def client(uri, token, pid, watch):
    async with connect(uri) as ws:
        await ws.send(req("session.join", {"entry_token": token}))
        await ws.send(req("session.ready", {}))
        sent = set()
        async for raw in ws:
            m = json.loads(raw)
            t = m.get("type")
            key = t
            if t == "game.event":
                key = t + ":" + str(m["payload"].get("event_type"))
            if watch and key not in seen:
                seen[key] = m
            p = m.get("payload", {})
            acts = None
            if t == "player.action_state":
                acts = p
            elif t == "game.state_sync":
                acts = p.get("action_state")
            if acts:
                for a in acts.get("actions", []):
                    k = (acts.get("day"), acts.get("phase"), a.get("type"))
                    if k in sent:
                        continue
                    sent.add(k)
                    if a["type"] == "chat" and acts.get("phase") == "day":
                        await ws.send(req("chat.send", {"channel_id": a["channel"], "message": f"hello from {pid}"}))
                    elif a["type"] == "vote":
                        await ws.send(req("vote.cast", {"target_player_id": a["valid_targets"][0]}))
                    elif a["type"] == "ability" and a.get("uses_remaining") != 0:
                        await ws.send(req("ability.use", {"ability_id": a["ability_id"], "target_player_ids": a["valid_targets"][:a["target_count"]]}))
                    elif a["type"] == "co_declare" and pid == "player-4":
                        await ws.send(req("co.declare", {"claimed_role_id": a["claimed_role_ids"][0], "comment": "I claim"}))
            if t == "game.event" and p.get("event_type") == "GAME_ENDED":
                return


async def main():
    content = load_content(ROOT / "content")
    preset = load_preset(ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = replace(preset, rules=replace(preset.rules, night_seconds=1, silence_after_dawn_seconds=0, day_seconds=2, vote_seconds=1))
    players = tuple(PlayerConfig(f"player-{i}", f"Player {i}") for i in range(sum(preset.role_counts.values())))
    game = GameState.create_from_preset(content, preset, players, game_id=GAME_ID, event_sink=InMemoryEventSink(),
                                        rng=Random(0), started_at=monotonic_seconds())
    registry = GameRegistry({GAME_ID: game})
    server = WebSocketGameServer(registry, tick_interval_seconds=0.02)
    listener = await server.start("127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    tokens = registry.entry_tokens_for(GAME_ID)
    roles = {p: game.players[p].role.id for p in game.players}
    print("ROLES", roles)
    await asyncio.wait_for(asyncio.gather(*(client(uri, tokens[p], p, p == "player-0") for p in game.players)), 120)
    for k, m in seen.items():
        print("=====", k)
        print(json.dumps(m, ensure_ascii=False)[:900])


asyncio.run(main())
