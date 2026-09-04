from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server.aiwolf_core import GamePhase, GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, WebSocketGameServer, monotonic_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_3_brain_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174263"


class PhaseThreeThreeCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_brain_clients_complete_with_dummy(self) -> None:
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
        observed_pids: set[int] = set()

        with TemporaryDirectory() as temporary_directory, \
            patch.object(game, "advance_phase", side_effect=AssertionError("manual advance_phase")) as manual_advance, \
            patch.object(game, "resolve_votes", side_effect=AssertionError("manual resolve_votes")) as manual_votes:
            root = Path(temporary_directory)

            async def start_client(player_id: str) -> asyncio.subprocess.Process:
                process = await asyncio.create_subprocess_exec(
                    os.sys.executable,
                    str(CLIENT),
                    "--uri", uri,
                    "--game-id", GAME_ID,
                    "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                    "--credentials", str(root / f"{player_id}.credentials.json"),
                    "--status", str(root / f"{player_id}.status.json"),
                    "--seed", str(100 + len(processes)),
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
                    lambda: game.game_result is not None,
                    timeout=45,
                    description="the server ticker to complete the game",
                )
                await self._wait_for(
                    lambda: all(
                        (root / f"{player_id}.status.json").exists()
                        for player_id in game.players
                    ),
                    timeout=15,
                    description="all Brain clients to observe GAME_ENDED",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(
                    all(status["production_import_guard"] for status in statuses.values()),
                    statuses,
                )
                self.assertTrue(
                    all(status["client_exit"] == "GAME_ENDED" for status in statuses.values()),
                    statuses,
                )
                self.assertTrue(all(status["brain_call_count"] > 0 for status in statuses.values()), statuses)
                for status in statuses.values():
                    phases = [(item["day"], item["phase"]) for item in status["attempted_phases"]]
                    self.assertEqual(len(phases), len(set(phases)), status)
                self.assertEqual(len(observed_pids), len(game.players))
                self.assertIsNotNone(game.game_result)
                self.assertEqual(
                    len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]),
                    1,
                )
                manual_advance.assert_not_called()
                manual_votes.assert_not_called()
            finally:
                for process in processes.values():
                    if process.returncode is None:
                        process.terminate()
                await asyncio.gather(
                    *(self._finish_process(process) for process in processes.values()),
                    return_exceptions=True,
                )
                await server.close()

    async def _wait_for(self, condition, *, timeout: float, description: str) -> None:
        async def wait() -> None:
            while not condition():
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError as error:
            raise AssertionError(f"timed out waiting for {description}") from error

    async def _finish_process(self, process: asyncio.subprocess.Process) -> None:
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
        if process.returncode not in {0, -15, 1}:
            self.fail(
                f"Brain client {process.pid} exited {process.returncode}: "
                f"{stdout.decode(errors='replace')} {stderr.decode(errors='replace')}"
            )


if __name__ == "__main__":
    unittest.main()
