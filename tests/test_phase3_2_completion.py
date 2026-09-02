from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, WebSocketGameServer, monotonic_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_2_world_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174262"


class PhaseThreeTwoCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_world_clients_complete_with_bounded_event_consumers(self) -> None:
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
        preset = replace(
            preset,
            rules=replace(
                preset.rules,
                night_seconds=1,
                silence_after_dawn_seconds=0,
                day_seconds=1,
                vote_seconds=1,
            ),
        )
        players = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}")
            for index in range(sum(preset.role_counts.values()))
        )
        game = GameState.create_from_preset(
            content,
            preset,
            players,
            game_id=GAME_ID,
            event_sink=InMemoryEventSink(),
            rng=Random(0),
            started_at=monotonic_seconds(),
        )
        registry = GameRegistry({GAME_ID: game})
        server = WebSocketGameServer(registry, tick_interval_seconds=0.02)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        processes: dict[str, asyncio.subprocess.Process] = {}

        async def wait_for(condition, timeout: float, description: str) -> None:
            async def poll() -> None:
                while not condition():
                    await asyncio.sleep(0.02)

            try:
                await asyncio.wait_for(poll(), timeout)
            except TimeoutError as error:
                raise AssertionError(f"timed out waiting for {description}") from error

        with TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                for player_id in game.players:
                    processes[player_id] = await asyncio.create_subprocess_exec(
                        os.sys.executable,
                        str(CLIENT),
                        "--uri", uri,
                        "--game-id", GAME_ID,
                        "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                        "--credentials", str(root / f"{player_id}.credentials.json"),
                        "--status", str(root / f"{player_id}.status.json"),
                        cwd=str(PROJECT_ROOT),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                await wait_for(lambda: game.game_result is not None, 45, "game end")
                await wait_for(
                    lambda: all((root / f"{player_id}.status.json").exists() for player_id in game.players),
                    15,
                    "all world client statuses",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(all(status["freshness"] == "ENDED" for status in statuses.values()), statuses)
                self.assertTrue(all(status["players"] == 9 for status in statuses.values()), statuses)
                self.assertTrue(all(not status["send_errors"] for status in statuses.values()), statuses)
                self.assertTrue(all(status["unknown_event_count"] == 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["malformed_event_count"] == 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["history_records"] > 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["production_import_guard"] for status in statuses.values()), statuses)
            finally:
                for process in processes.values():
                    if process.returncode is None:
                        process.terminate()
                await asyncio.gather(
                    *(self._finish_process(process) for process in processes.values()),
                    return_exceptions=True,
                )
                await server.close()

    async def _finish_process(self, process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
