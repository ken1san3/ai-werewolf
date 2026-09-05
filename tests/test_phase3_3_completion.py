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
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from tests.fixtures.completion_process import (
    _CompletionClock,
    _ProcessOutput,
    finish_process,
    load_process_output,
)


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
        clock = _CompletionClock()
        game = GameState.create_from_preset(
            content,
            preset,
            players,
            game_id=GAME_ID,
            event_sink=InMemoryEventSink(),
            rng=Random(0),
            started_at=clock(),
        )
        registry = GameRegistry({GAME_ID: game})
        sessions = SessionManager(registry, clock=clock)
        ticker = TickDriver(registry, clock=clock)
        server = WebSocketGameServer(
            registry,
            sessions=sessions,
            ticker=ticker,
            tick_interval_seconds=0.02,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        processes: dict[str, asyncio.subprocess.Process] = {}
        outputs: dict[int, _ProcessOutput] = {}
        output_paths: dict[int, tuple[Path, Path]] = {}
        status_paths: dict[str, Path] = {}
        ready_paths: dict[str, Path] = {}

        with TemporaryDirectory() as temporary_directory, \
            patch.object(game, "advance_phase", side_effect=AssertionError("manual advance_phase")) as manual_advance, \
            patch.object(game, "resolve_votes", side_effect=AssertionError("manual resolve_votes")) as manual_votes:
            root = Path(temporary_directory)

            async def start_client(
                player_id: str, seed: int
            ) -> asyncio.subprocess.Process:
                status_path = root / f"{player_id}.status.json"
                ready_path = root / f"{player_id}.ready.json"
                stdout_path = root / f"{player_id}.stdout.log"
                stderr_path = root / f"{player_id}.stderr.log"
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    process = await asyncio.create_subprocess_exec(
                        os.sys.executable,
                        str(CLIENT),
                        "--uri", uri,
                        "--game-id", GAME_ID,
                        "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                        "--credentials", str(root / f"{player_id}.credentials.json"),
                        "--status", str(status_path),
                        "--ready", str(ready_path),
                        "--seed", str(seed),
                        cwd=str(PROJECT_ROOT),
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                output = _ProcessOutput(bytearray(), bytearray(), bytearray(), [])
                outputs[id(process)] = output
                output_paths[id(process)] = (stdout_path, stderr_path)
                status_paths[player_id] = status_path
                ready_paths[player_id] = ready_path
                return process

            try:
                launched = await asyncio.gather(
                    *(
                        start_client(player_id, 100 + index)
                        for index, player_id in enumerate(game.players)
                    )
                )
                processes = dict(zip(game.players, launched))
                self.assertEqual(len(processes), len(game.players))

                await self._wait_for(
                    lambda: all(path.exists() for path in ready_paths.values()),
                    timeout=15,
                    description="all Brain clients to connect and receive initial state",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                for player_id, process in processes.items():
                    ready = json.loads(ready_paths[player_id].read_text(encoding="utf-8"))
                    self.assertEqual(ready["pid"], process.pid)
                    self.assertIsNone(process.returncode)

                self.assertIsNone(game.game_result)
                clock.release()
                await self._wait_for(
                    lambda: game.game_result is not None,
                    timeout=45,
                    description="the server ticker to complete the game",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                await self._wait_for(
                    lambda: all(path.exists() for path in status_paths.values()),
                    timeout=15,
                    description="all Brain clients to observe GAME_ENDED",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in status_paths.items()
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
                server_phases = [
                    (event.payload["day"], event.payload["phase"])
                    for event in game.event_bus.events
                    if event.type == "PHASE_STARTED"
                    and event.payload["phase"] != GamePhase.GAME_END.value
                ]
                self.assertGreaterEqual(len(server_phases), 2)
                self.assertEqual(len(server_phases), len(set(server_phases)))
                server_phase_set = set(server_phases)
                first_server_phase = server_phases[0]
                last_server_phase = server_phases[-1]
                self.assertGreaterEqual(
                    len({day for day, _phase in server_phases}),
                    2,
                )
                for player_id, status in statuses.items():
                    attempted_phases = status["attempted_phases"]
                    brain_decisions = status["brain_decisions"]
                    decision_statuses = status["decision_statuses"]
                    self.assertGreater(status["brain_call_count"], 0, status)
                    self.assertEqual(status["brain_call_count"], len(attempted_phases), status)
                    self.assertEqual(status["brain_call_count"], len(brain_decisions), status)
                    self.assertEqual(len(decision_statuses), len(attempted_phases), status)
                    self.assertTrue(
                        all(decision == "NoDecision" for decision in brain_decisions),
                        status,
                    )
                    phases = [(item["day"], item["phase"]) for item in attempted_phases]
                    self.assertEqual(len(phases), len(set(phases)), status)
                    phases_set = set(phases)
                    self.assertTrue(phases_set <= server_phase_set, status)
                    self.assertIn(first_server_phase, phases_set, status)
                    self.assertIn(last_server_phase, phases_set, status)
                self.assertEqual(len(processes), len(game.players))
                self.assertIsNotNone(game.game_result)
                self.assertEqual(
                    len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]),
                    1,
                )
                manual_advance.assert_not_called()
                manual_votes.assert_not_called()
            finally:
                cleanup_results = await asyncio.gather(
                    *(
                        finish_process(
                            process,
                            output=outputs.get(id(process)),
                            label=f"Brain client {process.pid}",
                            status_path=status_paths.get(player_id),
                            output_paths=output_paths.get(id(process)),
                        )
                        for player_id, process in processes.items()
                    ),
                    return_exceptions=True,
                )
                await server.close()
                failures = [result for result in cleanup_results if isinstance(result, Exception)]
                if failures:
                    raise AssertionError(
                        "client cleanup failures: " + " | ".join(map(str, failures))
                    )

    async def _wait_for(
        self,
        condition,
        *,
        timeout: float,
        description: str,
        processes: dict[str, asyncio.subprocess.Process],
        outputs: dict[int, _ProcessOutput],
        output_paths: dict[int, tuple[Path, Path]],
        status_paths: dict[str, Path],
    ) -> None:
        async def diagnostics() -> str:
            for process in processes.values():
                if process.returncode is None:
                    continue
                output = outputs.get(id(process))
                if output is not None and output.readers:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*output.readers, return_exceptions=True),
                            timeout=1,
                        )
                    except TimeoutError:
                        pass
                if output is not None:
                    load_process_output(output, output_paths.get(id(process)))
            return repr(
                {
                    "wait": description,
                    "processes": {
                        player_id: {
                            "pid": process.pid,
                            "returncode": process.returncode,
                            "stdout": bytes(outputs[id(process)].stdout_tail).decode(errors="replace")
                            if id(process) in outputs
                            else None,
                            "stderr": bytes(outputs[id(process)].stderr_tail).decode(errors="replace")
                            if id(process) in outputs
                            else None,
                            "status_exists": status_paths[player_id].exists(),
                        }
                        for player_id, process in processes.items()
                    },
                }
            )

        async def wait() -> None:
            while not condition():
                for player_id, process in processes.items():
                    if process.returncode is not None and not status_paths[player_id].exists():
                        detail = await diagnostics()
                        raise AssertionError(
                            f"child exited while waiting for {description}: {detail}"
                        )
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError as error:
            detail = await diagnostics()
            raise AssertionError(f"timed out waiting for {description}: {detail}") from error


if __name__ == "__main__":
    unittest.main()
