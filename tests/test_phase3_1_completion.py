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

from server.aiwolf_core import (
    GamePhase,
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, WebSocketGameServer, monotonic_seconds


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_1_network_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174261"


class PhaseThreeOneCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_protocol_clients_complete_and_one_resumes(self) -> None:
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

        async def start_client(player_id: str, root: Path) -> asyncio.subprocess.Process:
            return await asyncio.create_subprocess_exec(
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

        async def wait_for(condition, timeout: float, description: str) -> None:
            async def poll() -> None:
                while not condition():
                    await asyncio.sleep(0.02)

            try:
                await asyncio.wait_for(poll(), timeout)
            except TimeoutError as error:
                raise AssertionError(f"timed out waiting for {description}") from error

        with TemporaryDirectory() as directory, \
             patch.object(game, "advance_phase", side_effect=AssertionError("manual advance_phase")) as manual_advance, \
             patch.object(game, "resolve_votes", side_effect=AssertionError("manual resolve_votes")) as manual_votes:
            root = Path(directory)
            try:
                for player_id in game.players:
                    processes[player_id] = await start_client(player_id, root)
                await wait_for(
                    lambda: all((root / f"{player_id}.credentials.json").exists() for player_id in game.players),
                    10,
                    "all connection credentials",
                )

                restarted_player = next(iter(game.players))
                first = processes[restarted_player]
                first.kill()
                await first.wait()
                processes[restarted_player] = await start_client(restarted_player, root)

                await wait_for(lambda: game.game_result is not None, 45, "game end")
                await wait_for(
                    lambda: all((root / f"{player_id}.status.json").exists() for player_id in game.players),
                    15,
                    "all client statuses",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(statuses[restarted_player]["resumed"], statuses)
                self.assertTrue(
                    all(
                        not status["resumed"]
                        for player_id, status in statuses.items()
                        if player_id != restarted_player
                    ),
                    statuses,
                )
                self.assertEqual(
                    sum(len(status["action_rejections"]) for status in statuses.values()),
                    0,
                    statuses,
                )
                self.assertEqual(
                    sum(len(status["send_errors"]) for status in statuses.values()),
                    0,
                    statuses,
                )
                self.assertGreater(
                    sum(status["chat_sent"] for status in statuses.values()),
                    0,
                    statuses,
                )
                self.assertGreater(
                    sum(status["chat_received"] for status in statuses.values()),
                    0,
                    statuses,
                )
                event_types = [event.type for event in game.event_bus.events]
                for event_type in ("CO_DECLARED", "VOTE_SUBMITTED", "ACTION_SUBMITTED"):
                    self.assertIn(event_type, event_types, event_types)
                self.assertIn("GAME_ENDED", event_types, event_types)
                self.assertIs(GamePhase.GAME_END, game.phase)
                manual_advance.assert_not_called()
                manual_votes.assert_not_called()
                self.assertTrue(
                    all(isinstance(status["action_rejections"], list) for status in statuses.values()),
                    statuses,
                )
            finally:
                for process in processes.values():
                    if process.returncode is None:
                        process.terminate()
                cleanup_results = await asyncio.gather(
                    *(self._finish_process(process) for process in processes.values()),
                    return_exceptions=True,
                )
                await server.close()
                failures = [result for result in cleanup_results if isinstance(result, Exception)]
                if failures:
                    raise AssertionError("client cleanup failures: " + " | ".join(map(str, failures)))

    async def _start_completion_server(
        self, *, replay_history_limit: int = 128
    ) -> tuple[GameState, GameRegistry, WebSocketGameServer, str]:
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
        server = WebSocketGameServer(
            registry,
            sessions=sessions,
            tick_interval_seconds=0.02,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        return game, registry, server, uri

    async def _start_client_process(
        self,
        *,
        player_id: str,
        registry: GameRegistry,
        uri: str,
        root: Path,
        stop_after: str | None = None,
        inject_rejection: bool = False,
    ) -> asyncio.subprocess.Process:
        marker = root / f"{player_id}.stop.json"
        arguments = [
            os.sys.executable,
            str(CLIENT),
            "--uri", uri,
            "--game-id", GAME_ID,
            "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
            "--credentials", str(root / f"{player_id}.credentials.json"),
            "--status", str(root / f"{player_id}.status.json"),
        ]
        if stop_after is not None:
            arguments.extend(["--stop-after", stop_after, "--stop-marker", str(marker)])
        if inject_rejection:
            arguments.append("--inject-rejection")
        return await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _run_restarted_process_scenario(
        self,
        *,
        replay_history_limit: int,
        stop_after: str,
        expected_gap_counts: tuple[int, int],
    ) -> None:
        game, registry, server, uri = await self._start_completion_server(
            replay_history_limit=replay_history_limit
        )
        processes: dict[str, asyncio.subprocess.Process] = {}
        stopped_player = next(iter(game.players))
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                for player_id in game.players:
                    processes[player_id] = await self._start_client_process(
                        player_id=player_id,
                        registry=registry,
                        uri=uri,
                        root=root,
                        stop_after=stop_after if player_id == stopped_player else None,
                    )
                marker_path = root / f"{stopped_player}.stop.json"
                await self._wait_for(
                    lambda: marker_path.exists(),
                    timeout=15,
                    description=f"{stop_after} stop marker",
                )
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                self.assertEqual(marker["kind"], stop_after)
                checkpoint_seq = marker["last_seq"]
                first_pid = processes[stopped_player].pid
                session = server.sessions.session_for(GAME_ID)
                if replay_history_limit == 1:
                    await self._wait_for(
                        lambda: session._replay_floor_by_player.get(stopped_player, 0) > checkpoint_seq,
                        timeout=10,
                        description="replay history to leave the retention window",
                    )
                else:
                    await self._wait_for(
                        lambda: any(
                            reply.seq > checkpoint_seq
                            for reply in session._history_by_player.get(stopped_player, ())
                        ),
                        timeout=10,
                        description="replay history to retain a missed event",
                    )
                processes[stopped_player].kill()
                await processes[stopped_player].wait()

                processes[stopped_player] = await self._start_client_process(
                    player_id=stopped_player,
                    registry=registry,
                    uri=uri,
                    root=root,
                )
                self.assertNotEqual(first_pid, processes[stopped_player].pid)
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
                    description="all client statuses",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(statuses[stopped_player]["resumed"], statuses)
                self.assertEqual(
                    (
                        statuses[stopped_player]["gap_detected"],
                        statuses[stopped_player]["gap_recovered"],
                    ),
                    expected_gap_counts,
                    statuses,
                )
                self.assertTrue(
                    all(status["production_import_guard"] for status in statuses.values()),
                    statuses,
                )
                self.assertTrue(
                    all(not status["server_imports"] for status in statuses.values()),
                    statuses,
                )
                self.assertEqual(
                    sum(len(status["send_errors"]) for status in statuses.values()),
                    0,
                    statuses,
                )
        finally:
            for process in processes.values():
                if process.returncode is None:
                    process.terminate()
            await asyncio.gather(
                *(self._finish_process(process) for process in processes.values()),
                return_exceptions=True,
            )
            await server.close()

    async def test_separate_process_action_stop_resumes_within_replay_retention(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=128,
            stop_after="action",
            expected_gap_counts=(0, 0),
        )

    async def test_separate_process_chat_stop_resumes_outside_retention_and_recovers_gap(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=1,
            stop_after="chat",
            expected_gap_counts=(1, 1),
        )

    async def test_separate_process_rejection_and_production_import_guard(self) -> None:
        game, registry, server, uri = await self._start_completion_server()
        processes: dict[str, asyncio.subprocess.Process] = {}
        injected_player = next(iter(game.players))
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                for player_id in game.players:
                    processes[player_id] = await self._start_client_process(
                        player_id=player_id,
                        registry=registry,
                        uri=uri,
                        root=root,
                        inject_rejection=player_id == injected_player,
                    )
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
                    description="all client statuses",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertGreater(
                    sum(len(status["action_rejections"]) for status in statuses.values()),
                    0,
                    statuses,
                )
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(
                    all(status["production_import_guard"] for status in statuses.values()),
                    statuses,
                )
                self.assertTrue(
                    all(not status["server_imports"] for status in statuses.values()),
                    statuses,
                )
                self.assertEqual(
                    sum(len(status["send_errors"]) for status in statuses.values()),
                    0,
                    statuses,
                )
                self.assertTrue(all(status["pid"] != os.getpid() for status in statuses.values()))
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
        if process.returncode not in {0, -15}:
            self.fail(
                f"Phase 3.1 client {process.pid} exited {process.returncode}: "
                f"{stdout.decode(errors='replace')} {stderr.decode(errors='replace')}"
            )


if __name__ == "__main__":
    unittest.main()
