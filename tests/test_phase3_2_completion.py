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
from server.network import GameRegistry, SessionManager, WebSocketGameServer, monotonic_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_2_world_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174262"


class PhaseThreeTwoCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_world_clients_recover_from_in_retention_replay(self) -> None:
        await self._complete_after_restart(replay_history_limit=128, restart_delay=0.0)

    async def test_nine_world_clients_recover_from_out_of_retention_sync(self) -> None:
        await self._complete_after_restart(replay_history_limit=1, restart_delay=1.0)

    async def _complete_after_restart(
        self, *, replay_history_limit: int, restart_delay: float
    ) -> None:
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
        sessions = SessionManager(registry, replay_history_limit=replay_history_limit)
        server = WebSocketGameServer(registry, sessions=sessions, tick_interval_seconds=0.02)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        processes: dict[str, asyncio.subprocess.Process] = {}
        observed_pids: set[int] = set()

        with TemporaryDirectory() as directory:
            root = Path(directory)

            async def start_client(player_id: str) -> asyncio.subprocess.Process:
                process = await asyncio.create_subprocess_exec(
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
                observed_pids.add(process.pid)
                return process

            try:
                for player_id in game.players:
                    processes[player_id] = await start_client(player_id)
                await self._wait_for(
                    lambda: all(
                        (root / f"{player_id}.credentials.json").exists()
                        for player_id in game.players
                    ),
                    10,
                    "all clients to store their connection tokens",
                )

                restarted_player = next(iter(game.players))
                first_process = processes[restarted_player]
                first_pid = first_process.pid
                first_process.kill()
                await first_process.wait()
                if restart_delay:
                    await asyncio.sleep(restart_delay)
                processes[restarted_player] = await start_client(restarted_player)
                self.assertNotEqual(first_pid, processes[restarted_player].pid)

                await self._wait_for(lambda: game.game_result is not None, 45, "game end")
                await self._wait_for(
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
                reference_player = next(
                    player_id for player_id in game.players if player_id != restarted_player
                )
                for field in ("freshness", "players", "alive", "unknown_event_count", "malformed_event_count"):
                    self.assertEqual(
                        statuses[restarted_player][field],
                        statuses[reference_player][field],
                        statuses,
                    )
                self.assertEqual(len(observed_pids), len(game.players) + 1)
            finally:
                for process in processes.values():
                    if process.returncode is None:
                        process.terminate()
                await asyncio.gather(
                    *(self._finish_process(process) for process in processes.values()),
                    return_exceptions=True,
                )
                await server.close()

    async def _wait_for(self, condition, timeout: float, description: str) -> None:
        async def poll() -> None:
            while not condition():
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(poll(), timeout)
        except TimeoutError as error:
            raise AssertionError(f"timed out waiting for {description}") from error

    async def _finish_process(self, process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
