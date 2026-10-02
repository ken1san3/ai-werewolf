"""Launch the real server and nine independent protocol-only agents."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from uuid import uuid4

from server.aiwolf_core import EventVisibility, GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, WebSocketGameServer, monotonic_seconds

from .agent import Agent
from .llm import SharedLLM
from .recording import Recorder
from .repetition import RepetitionFilter


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class GameRun:
    checks: dict
    agents: list[Agent]
    recorder: Recorder


async def run_game(*, seed=1, day=180, vote=60, night=60, llm=None,
                   timing_scale=1, timeout=2400, output=None, progress=False):
    if min(day, vote, night) < 2:
        raise ValueError("phase durations must be at least two seconds")
    content = load_content(ROOT / "content")
    preset = load_preset(ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = replace(preset, rules=replace(preset.rules, day_seconds=day, vote_seconds=vote,
                                          night_seconds=night, silence_after_dawn_seconds=0 if timing_scale < 1 else preset.rules.silence_after_dawn_seconds))
    players = [PlayerConfig(f"player-{i}", f"player-{i}") for i in range(sum(preset.role_counts.values()))]
    game_id = str(uuid4())
    recorder = Recorder(c.id for c in content.chat_channels.values() if c.is_public)
    game = GameState.create_from_preset(content, preset, players, game_id=game_id,
                                       event_sink=InMemoryEventSink(), rng=Random(seed), started_at=monotonic_seconds())
    for event in game.event_bus.events:
        if event.visibility is EventVisibility.PUBLIC:
            recorder.core_event(event)
    game.event_bus.subscribe(EventVisibility.PUBLIC, recorder.core_event)
    if progress:
        def report(event):
            if event.type in {"PHASE_STARTED", "GAME_ENDED"}:
                print(event.type, dict(event.payload), flush=True)
        game.event_bus.subscribe(EventVisibility.PUBLIC, report)
    registry = GameRegistry({game_id: game})
    server = WebSocketGameServer(registry, tick_interval_seconds=0.05)
    listener = await server.start("127.0.0.1", 0)
    uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    tokens = registry.entry_tokens_for(game_id)
    recorder.tokens.update(tokens.values())
    owned_llm = llm is None
    llm = SharedLLM() if owned_llm else llm
    repetition = RepetitionFilter()
    agents = [Agent(p.player_id, tokens[p.player_id], uri, game_id,
                    content.roles, dict(preset.role_counts), llm, repetition, recorder.observe,
                    seed=seed * 100 + i, timing_scale=timing_scale) for i, p in enumerate(players)]
    errors = []
    tasks = [asyncio.create_task(a.run()) for a in agents]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)
        errors.extend({"player_id": a.state.player_id, "error": repr(result)}
                      for a, result in zip(agents, results) if isinstance(result, BaseException))
    except asyncio.TimeoutError:
        errors.append({"error": "global game timeout"})
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()
        if owned_llm:
            await llm.close()
    checks = recorder.checks(game, agents, errors, getattr(llm, "calls", []))
    if output is not None:
        recorder.save(output, checks)
    return GameRun(checks, agents, recorder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--day", type=int, default=180)
    parser.add_argument("--vote", type=int, default=60)
    parser.add_argument("--night", type=int, default=60)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output = args.out or ROOT / "games" / (datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M%S_%f") + f"_seed{args.seed}")
    result = asyncio.run(run_game(seed=args.seed, day=args.day, vote=args.vote, night=args.night,
                                 output=output, progress=True))
    print("output", output.resolve(), flush=True)
    print({k: v for k, v in result.checks.items() if k != "llm_calls"}, flush=True)
    if not result.checks["completed"] or result.checks["crashes"] or result.checks["server_rejections"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
