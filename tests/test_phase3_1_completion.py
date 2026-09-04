from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
from server.network import (
    GameRegistry,
    SessionManager,
    TickDriver,
    WebSocketGameServer,
)
from server.aiwolf_core.rejections import PLAYER_ACTION_REJECTION_REASONS
from tests.fixtures.completion_evidence import (
    ALL_REJECTION_REASONS,
    BOUNDARY_REJECTION_REASONS,
    DEFECT_REJECTION_REASONS,
    EVIDENCE_CONTRACT,
    EvidenceValidationError,
    STATUS_SCHEMA_VERSION,
    acceptance_quorum,
    assert_acceptance_quorum,
    assert_action_coverage,
    derive_interrupted_rounds,
    expected_opportunities,
    record_server_reply,
    validate_interruption_bounds,
    validate_rejection_vocabulary,
    validate_status,
    validate_stop_marker,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_1_network_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174261"


class _CompletionClock:
    """One integer clock shared by core, sessions, and ticker in a scenario."""

    def __init__(self) -> None:
        self.released_at_ns: int | None = None

    def __call__(self) -> int:
        if self.released_at_ns is None:
            return 0
        return (time.monotonic_ns() - self.released_at_ns) // 1_000_000_000

    def release(self) -> None:
        if self.released_at_ns is not None:
            raise RuntimeError("completion clock was released twice")
        self.released_at_ns = time.monotonic_ns()


@dataclass
class _ProcessOutput:
    stdout_tail: bytearray
    stderr_tail: bytearray
    stdout_buffer: bytearray
    observations: list[dict[str, object]]
    progress_seq: int | None = None
    checkpoint_seq: int | None = None
    parse_error: str | None = None
    readers: tuple[asyncio.Task[None], ...] = ()


def _co_acceptance_keys(events: list[object]) -> set[tuple[int, str]]:
    """Recover CO_DECLARED's day from the preceding PHASE_STARTED context."""

    current_day: int | None = None
    accepted: set[tuple[int, str]] = set()
    for event in events:
        if getattr(event, "type", None) == "PHASE_STARTED":
            day = event.payload.get("day")
            if isinstance(day, int) and not isinstance(day, bool):
                current_day = day
        elif getattr(event, "type", None) == "CO_DECLARED":
            if current_day is None:
                raise AssertionError("CO_DECLARED has no PHASE_STARTED day context")
            player_id = event.payload.get("player_id")
            if not isinstance(player_id, str) or not player_id:
                raise AssertionError("CO_DECLARED player_id is missing")
            accepted.add((current_day, player_id))
    return accepted


class PhaseThreeOneCompletionTests(unittest.IsolatedAsyncioTestCase):
    def test_co_acceptance_keys_keep_each_phase_day(self) -> None:
        events = [
            SimpleNamespace(type="PHASE_STARTED", payload={"day": 1}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p0"}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p1"}),
            SimpleNamespace(type="PHASE_STARTED", payload={"day": 2}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p0"}),
        ]

        self.assertEqual(
            _co_acceptance_keys(events),
            {(1, "p0"), (1, "p1"), (2, "p0")},
        )

    def test_completion_evidence_contract_is_fail_closed(self) -> None:
        self.assertEqual(
            ALL_REJECTION_REASONS,
            PLAYER_ACTION_REJECTION_REASONS
            | {"invalid_action", "game_mismatch", "unsupported_protocol_version"},
        )
        self.assertEqual(len(BOUNDARY_REJECTION_REASONS), 5)
        self.assertEqual(
            BOUNDARY_REJECTION_REASONS | DEFECT_REJECTION_REASONS,
            ALL_REJECTION_REASONS,
        )
        validate_rejection_vocabulary(["action_deadline_passed", "action_unavailable"])
        with self.assertRaises(EvidenceValidationError):
            validate_rejection_vocabulary(["co_limit_reached"])
        with self.assertRaises(EvidenceValidationError):
            validate_rejection_vocabulary(["future_reason"])
        status = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "pid": 123,
            "player_id": "p0",
            "resumed": False,
            "resume_events": [],
            "state_sync_events": [],
            "gap_events": [],
            "action_evidence": [
                {
                    "key": "p0|1|vote|vote",
                    "kind": "vote",
                    "day": 1,
                    "phase": "vote",
                    "action_generation": 1,
                    "after_resume": False,
                    "outcome": "sent",
                }
            ],
            "action_rejections": [],
            "chat_messages_received": [],
            "game_end": True,
            "last_seq": 4,
            "events_received": 1,
            "send_errors": [],
            "server_imports": [],
            "production_import_guard": True,
            "client_exit_reason": "GAME_ENDED",
            "exception_type": None,
            "exception_message": None,
        }
        validate_status(status, expected_player_id="p0")
        strict_status = {
            **status,
            "evidence_contract": EVIDENCE_CONTRACT,
            "initial_sync_seq": 1,
            "action_evidence": [{**status["action_evidence"][0], "source_seq": 3}],
        }
        validate_status(strict_status, expected_player_id="p0", require_addendum_c=True)
        with self.assertRaises(EvidenceValidationError):
            validate_status(
                {key: value for key, value in strict_status.items() if key != "evidence_contract"},
                require_addendum_c=True,
            )
        with self.assertRaises(EvidenceValidationError):
            validate_status(
                {**strict_status, "action_evidence": [{**strict_status["action_evidence"][0], "source_seq": 0}]},
                require_addendum_c=True,
            )
        invalid_outcome = {**status, "action_evidence": [
            {**status["action_evidence"][0], "outcome": "ignored"}
        ]}
        with self.assertRaises(EvidenceValidationError):
            validate_status(invalid_outcome)
        duplicate_rejection = {**status, "action_rejections": [
            {"action": "co.declare", "reason": "action_unavailable", "seq": 2, "after_resume": False},
            {"action": "co.declare", "reason": "action_unavailable", "seq": 2, "after_resume": False},
        ]}
        with self.assertRaises(EvidenceValidationError):
            validate_status(duplicate_rejection)

    def test_completion_evidence_quorum_and_interruption_boundaries(self) -> None:
        self.assertEqual(acceptance_quorum(0), 0)
        self.assertEqual(acceptance_quorum(1), 1)
        self.assertEqual(acceptance_quorum(9), 5)
        records = [
            {"voter_player_id": "p0"},
            {"voter_player_id": "p0"},
            {"voter_player_id": "p1"},
        ]
        with self.assertRaises(EvidenceValidationError):
            assert_acceptance_quorum(5, records, field="voter_player_id")
        self.assertEqual(
            assert_acceptance_quorum(
                3,
                records,
                field="voter_player_id",
            ),
            {"p0", "p1"},
        )
        rounds = [
            {"day": 1, "phase": "vote", "round_start_seq": 10},
            {"day": 1, "phase": "runoff", "round_start_seq": 20},
            {"day": 2, "phase": "vote", "round_start_seq": 30},
        ]
        self.assertEqual(
            derive_interrupted_rounds(rounds, stop_seq=10, resume_sync_seq=20),
            {(1, "runoff")},
        )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                {(1, "vote"), (1, "runoff"), (2, "vote")},
                stop_seq=10,
                resume_sync_seq=20,
            )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                set(),
                stop_seq=10,
                resume_sync_seq=20,
            )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                {(1, "night")},
                stop_seq=10,
                resume_sync_seq=20,
            )
        self.assertEqual(
            validate_interruption_bounds(
                rounds,
                {(1, "runoff")},
                stop_seq=10,
                resume_sync_seq=20,
            ),
            {(1, "runoff")},
        )

    def test_addendum_c_clock_is_single_release_and_integer_monotonic(self) -> None:
        gate = _CompletionClock()
        self.assertEqual(gate(), 0)
        gate.release()
        first = gate()
        second = gate()
        self.assertIsInstance(first, int)
        self.assertGreaterEqual(second, first)
        with self.assertRaises(RuntimeError):
            gate.release()

    def test_addendum_c_ledger_is_independent_and_seq_boundaries_are_exact(self) -> None:
        ledger: dict[str, object] = {"reply_headers": [], "offer_occurrences": []}
        record_server_reply(
            ledger,
            player_id="p0",
            seq=30,
            message_type="player.action_state",
            payload={
                "phase": "vote",
                "day": 1,
                "actions": [{"type": "vote", "valid_targets": ["p1"], "target_count": 1, "allows_abstain": False}],
            },
        )
        record_server_reply(
            ledger,
            player_id="p0",
            seq=31,
            message_type="player.action_state",
            payload={
                "phase": "day",
                "day": 1,
                "actions": [
                    {"type": "ability", "ability_id": "inspect", "valid_targets": ["p1"], "target_count": 1, "uses_remaining": 1},
                    {"type": "co_declare", "claimed_role_ids": ["seer"]},
                    {"type": "chat", "channel": "public"},
                ],
            },
        )
        record_server_reply(
            ledger,
            player_id="p0",
            seq=40,
            message_type="player.action_state",
            payload={
                "phase": "runoff",
                "day": 1,
                "actions": [{"type": "vote", "valid_targets": ["p1"], "target_count": 1, "allows_abstain": False}],
            },
        )
        record_server_reply(
            ledger,
            player_id="p1",
            seq=31,
            message_type="player.action_state",
            payload={"phase": "day", "day": 1, "actions": [{"type": "chat", "channel": "public"}]},
        )
        expected = expected_opportunities(ledger)
        self.assertEqual(expected["p0|1|vote|vote"]["seq"], 30)
        self.assertEqual(expected["p0|1|day|ability|inspect"]["seq"], 31)
        self.assertEqual(expected["p0|1|co_declare"]["seq"], 31)
        self.assertEqual(expected["p0|1|chat"]["seq"], 31)
        self.assertEqual(expected["p0|1|runoff|vote"]["seq"], 40)
        marker = {
            "pid": 101,
            "player_id": "p0",
            "last_seq": 30,
            "action_evidence": [
                {"key": "p0|1|vote|vote", "kind": "vote", "day": 1, "phase": "vote", "action_generation": 1, "source_seq": 30, "after_resume": False, "outcome": "sent"}
            ],
        }
        statuses = [
            {
                "pid": 202,
                "player_id": "p0",
                "action_evidence": [
                    {"key": "p0|1|runoff|vote", "kind": "vote", "day": 1, "phase": "runoff", "action_generation": 2, "source_seq": 40, "after_resume": True, "outcome": "sent"}
                ],
            },
            {
                "pid": 303,
                "player_id": "p1",
                "action_evidence": [
                    {"key": "p1|1|chat", "kind": "chat", "day": 1, "phase": "day", "action_generation": 1, "source_seq": 31, "after_resume": False, "outcome": "sent"}
                ],
            },
        ]
        coverage = assert_action_coverage(
            ledger,
            statuses,
            marker=marker,
            restarted_status=statuses[0],
            restarted_player_id="p0",
            resume_sync_seq=35,
        )
        self.assertEqual(
            coverage["interrupted"],
            frozenset({"p0|1|day|ability|inspect", "p0|1|co_declare", "p0|1|chat"}),
        )
        with self.assertRaises(EvidenceValidationError):
            assert_action_coverage(
                ledger,
                statuses,
                marker=marker,
                restarted_status=statuses[0],
                restarted_player_id="p0",
                resume_sync_seq=5,
            )

        bad_stale = dict(statuses[0])
        bad_stale["action_evidence"] = [
            {**statuses[0]["action_evidence"][0], "source_seq": 36, "key": "p0|1|runoff|vote"}
        ]
        with self.assertRaises(EvidenceValidationError):
            assert_action_coverage(
                ledger,
                [bad_stale, statuses[1]],
                marker=marker,
                restarted_status=bad_stale,
                restarted_player_id="p0",
                resume_sync_seq=35,
            )

    async def test_nine_protocol_clients_complete_and_one_resumes(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=128,
            stop_after="action",
            expected_gap_counts=(0, 0),
        )

    async def _start_completion_server(
        self, *, replay_history_limit: int = 128
    ) -> tuple[GameState, GameRegistry, WebSocketGameServer, str]:
        self._completion_scenario_started_ns = time.monotonic_ns()
        self._completion_clock = _CompletionClock()
        self._completion_process_outputs: dict[int, _ProcessOutput] = {}
        self._completion_process_labels: dict[int, str] = {}
        self._completion_processes: dict[str, asyncio.subprocess.Process] = {}
        self._completion_ignored_process_ids: set[int] = set()
        self._completion_artifacts: dict[tuple[str, int], dict[str, Path]] = {}
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
        preset = replace(
            preset,
            rules=replace(
                preset.rules,
                night_seconds=2,
                silence_after_dawn_seconds=0,
                day_seconds=2,
                vote_seconds=2,
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
            started_at=self._completion_clock(),
        )
        self._completion_game = game
        registry = GameRegistry({GAME_ID: game})
        sessions = SessionManager(
            registry,
            clock=self._completion_clock,
            replay_history_limit=replay_history_limit,
        )
        ticker = TickDriver(registry, clock=self._completion_clock)
        server = WebSocketGameServer(
            registry,
            sessions=sessions,
            ticker=ticker,
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
        incarnation: int = 1,
        stop_after: str | None = None,
        inject_rejection: bool = False,
        inject_driver_error: bool = False,
        ready: bool = False,
    ) -> asyncio.subprocess.Process:
        marker = root / f"{player_id}.incarnation-{incarnation}.stop.json"
        status_path = root / f"{player_id}.incarnation-{incarnation}.status.json"
        ready_path = root / f"{player_id}.incarnation-{incarnation}.ready.json"
        arguments = [
            os.sys.executable,
            str(CLIENT),
            "--uri", uri,
            "--game-id", GAME_ID,
            "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
            "--credentials", str(root / f"{player_id}.credentials.json"),
            "--status", str(status_path),
        ]
        if stop_after is not None:
            arguments.extend(["--stop-after", stop_after, "--stop-marker", str(marker)])
        if inject_rejection:
            arguments.append("--inject-rejection")
        if inject_driver_error:
            arguments.append("--inject-driver-error")
        if ready:
            arguments.extend(["--ready", str(ready_path)])
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        output = _ProcessOutput(bytearray(), bytearray(), bytearray(), [])
        self._completion_process_outputs[id(process)] = output
        self._completion_process_labels[id(process)] = f"{player_id}/incarnation-{incarnation}"
        output.readers = (
            asyncio.create_task(
                self._drain_process_stream(process.stdout, output, "stdout", process.pid)
            ),
            asyncio.create_task(
                self._drain_process_stream(process.stderr, output, "stderr", process.pid)
            ),
        )
        self._completion_processes[f"{player_id}/incarnation-{incarnation}"] = process
        self._completion_artifacts[(player_id, incarnation)] = {
            "status": status_path,
            "ready": ready_path,
            "marker": marker,
            "credentials": root / f"{player_id}.credentials.json",
        }
        return process

    async def _drain_process_stream(
        self,
        stream: asyncio.StreamReader,
        output: _ProcessOutput,
        stream_name: str,
        pid: int | None,
    ) -> None:
        buffer = output.stdout_buffer
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            tail = output.stdout_tail if stream_name == "stdout" else output.stderr_tail
            tail.extend(chunk)
            del tail[:-65536]
            if stream_name != "stdout":
                continue
            buffer.extend(chunk)
            if len(buffer) > 65536 and b"\n" not in buffer:
                output.parse_error = "stdout observation line exceeded 64 KiB"
                del buffer[:-65536]
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer[:] = rest
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    output.parse_error = f"malformed stdout observation: {type(error).__name__}"
                    continue
                if not isinstance(value, dict):
                    output.parse_error = "stdout observation is not an object"
                    continue
                if value.get("observation_version") != "phase3.1-observation/v1":
                    output.parse_error = "unsupported stdout observation version"
                    continue
                if value.get("pid") != pid:
                    output.parse_error = "stdout observation pid mismatch"
                    continue
                player_id = value.get("player_id")
                if player_id is not None and (not isinstance(player_id, str) or not player_id):
                    output.parse_error = "stdout observation player_id is invalid"
                    continue
                kind = value.get("kind")
                seq = value.get("seq")
                if kind not in {"checkpoint_saved", "progress"} or not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
                    output.parse_error = "stdout observation kind/seq is invalid"
                    continue
                if kind == "progress":
                    if output.progress_seq is not None and seq < output.progress_seq:
                        output.parse_error = "progress sequence moved backwards"
                    output.progress_seq = seq
                else:
                    if output.checkpoint_seq is not None and seq < output.checkpoint_seq:
                        output.parse_error = "checkpoint sequence moved backwards"
                    output.checkpoint_seq = seq
                output.observations.append(value)

    async def test_separate_process_action_stop_resumes_outside_retention_and_recovers_gap(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=1,
            stop_after="action",
            expected_gap_counts=(1, 1),
        )

    async def test_driver_failure_writes_diagnostics(self) -> None:
        game, registry, server, uri = await self._start_completion_server()
        process: asyncio.subprocess.Process | None = None
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                player_id = next(iter(game.players))
                stdout_path = root / "driver.stdout"
                stderr_path = root / "driver.stderr"
                status_path = root / "driver.status.json"
                arguments = [
                    os.sys.executable,
                    str(CLIENT),
                    "--uri", uri,
                    "--game-id", GAME_ID,
                    "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                    "--credentials", str(root / "driver.credentials.json"),
                    "--status", str(status_path),
                    "--inject-driver-error",
                ]
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    process = await asyncio.create_subprocess_exec(
                        *arguments,
                        cwd=str(PROJECT_ROOT),
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                    await process.wait()
                stdout = stdout_path.read_bytes()
                stderr = stderr_path.read_bytes()
                self.assertNotEqual(process.returncode, 0)
                status = json.loads(status_path.read_text(encoding="utf-8"))
                validate_status(status)
                self.assertEqual(status["exception_type"], "RuntimeError")
                self.assertEqual(status["exception_message"], "injected Phase 3.1 driver failure")
                self.assertEqual(status["events_received"], 0)
                self.assertEqual(status["actions_sent"], 0)
                self.assertIn("RuntimeError", stderr.decode(errors="replace"))
                self.assertEqual(stdout, b"")
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            await server.close()

    def _completion_diagnostics(self, description: str) -> str:
        game = getattr(self, "_completion_game", None)
        ledger = getattr(self, "_completion_ledger", {})
        expected = expected_opportunities(ledger)
        now = time.monotonic_ns()
        release_ns = getattr(getattr(self, "_completion_clock", None), "released_at_ns", None)
        elapsed = (now - release_ns) / 1_000_000_000 if release_ns is not None else None
        process_details = []
        for label, process in getattr(self, "_completion_processes", {}).items():
            output = getattr(self, "_completion_process_outputs", {}).get(id(process))
            process_details.append(
                {
                    "label": label,
                    "pid": process.pid,
                    "running": process.returncode is None,
                    "returncode": process.returncode,
                    "progress_seq": output.progress_seq if output else None,
                    "checkpoint_seq": output.checkpoint_seq if output else None,
                    "parse_error": output.parse_error if output else None,
                }
            )
        last_event = None
        if game is not None and game.event_bus.events:
            last_event = game.event_bus.events[-1].type
        phase = game.phase.value if game is not None else None
        result = game.game_result if game is not None else None
        return repr(
            {
                "wait": description,
                "elapsed_from_release": elapsed,
                "gate_released": release_ns is not None,
                "game": {"day": game.day if game is not None else None, "phase": phase, "result": result},
                "last_server_event": last_event,
                "ready_count": sum(
                    1
                    for artifact in getattr(self, "_completion_artifacts", {}).values()
                    if artifact["ready"].exists()
                ),
                "expected_by_kind": {
                    kind: sum(item.get("kind") == kind for item in expected.values())
                    for kind in ("vote", "ability", "co_declare", "chat")
                },
                "processes": process_details,
            }
        )

    async def _wait_for(
        self,
        condition,
        *,
        timeout: float,
        description: str,
        diagnostics=None,
    ) -> None:
        async def wait() -> None:
            while not condition():
                for process in getattr(self, "_completion_processes", {}).values():
                    label = getattr(self, "_completion_process_labels", {}).get(id(process), "")
                    player_id, separator, incarnation_text = label.partition("/incarnation-")
                    artifact = getattr(self, "_completion_artifacts", {}).get(
                        (player_id, int(incarnation_text) if separator and incarnation_text.isdigit() else -1),
                    )
                    expected_status = artifact is not None and artifact["status"].exists()
                    if (
                        id(process) not in getattr(self, "_completion_ignored_process_ids", set())
                        and process.returncode is not None
                        and not expected_status
                    ):
                        detail = diagnostics() if diagnostics is not None else self._completion_diagnostics(description)
                        raise AssertionError(f"child exited while waiting for {description}: {detail}")
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError as error:
            detail = diagnostics() if diagnostics is not None else self._completion_diagnostics(description)
            raise AssertionError(f"timed out waiting for {description}: {detail}") from error

    def _checkpoint_seq(self, root: Path, player_id: str) -> int:
        return int(
            json.loads(
                (root / f"{player_id}.credentials.json").read_text(encoding="utf-8")
            )["last_seq"]
        )

    async def _finish_process(
        self, process: asyncio.subprocess.Process, *, status_path: Path | None = None
    ) -> None:
        output = getattr(self, "_completion_process_outputs", {}).get(id(process))
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        if output is not None and output.readers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*output.readers, return_exceptions=True),
                    timeout=5,
                )
            except TimeoutError:
                for reader in output.readers:
                    reader.cancel()
                await asyncio.gather(*output.readers, return_exceptions=True)
        if output is not None and output.parse_error is not None:
            self.fail(
                f"Phase 3.1 client {process.pid} emitted invalid observation: {output.parse_error}"
            )
        if id(process) in getattr(self, "_completion_ignored_process_ids", set()):
            return
        if process.returncode not in {0, -15, -9}:
            diagnostics = [
                f"stdout={bytes(output.stdout_tail).decode(errors='replace')!r}" if output else "stdout=None",
                f"stderr={bytes(output.stderr_tail).decode(errors='replace')!r}" if output else "stderr=None",
            ]
            if output is not None and output.parse_error is not None:
                diagnostics.append(f"observation_error={output.parse_error!r}")
            if status_path is not None:
                try:
                    status = status_path.read_text(encoding="utf-8")
                except OSError as error:
                    diagnostics.append(
                        f"status_read_error={type(error).__name__}: {error}"
                    )
                else:
                    diagnostics.append(f"status={status}")
            self.fail(
                f"Phase 3.1 client {process.pid} exited {process.returncode}: "
                + " ".join(diagnostics)
            )

    # Addendum C implementation.  This later definition intentionally keeps
    # the original scenario helper above available in the review history while
    # making the executed path use the gated, passive-ledger contract.
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
        self._completion_ledger: dict[str, object] = {
            "reply_headers": [],
            "offer_occurrences": [],
        }
        session = server.sessions.session_for(GAME_ID)
        original_reply = session._reply

        def record_reply(player_id: str, message_type: str, payload: object) -> object:
            reply = original_reply(player_id, message_type, payload)
            record_server_reply(
                self._completion_ledger,
                player_id=player_id,
                seq=reply.seq,
                message_type=reply.type,
                payload=reply.payload,
            )
            return reply

        session._reply = record_reply
        self._completion_game = game
        stopped_player = tuple(game.players)[-1]
        processes: dict[str, asyncio.subprocess.Process] = {}
        started_processes: dict[tuple[str, int], asyncio.subprocess.Process] = {}
        self._completion_artifacts = {}
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                initial_phase = game.phase
                initial_day = game.day

                async def launch(player_id: str) -> None:
                    process = await self._start_client_process(
                        player_id=player_id,
                        registry=registry,
                        uri=uri,
                        root=root,
                        incarnation=1,
                        stop_after=stop_after if player_id == stopped_player else None,
                        ready=True,
                    )
                    processes[player_id] = process
                    started_processes[(player_id, 1)] = process

                launch_results = await asyncio.gather(
                    *(launch(player_id) for player_id in game.players),
                    return_exceptions=True,
                )
                failures = [result for result in launch_results if isinstance(result, Exception)]
                if failures:
                    raise AssertionError("parallel client startup failed: " + " | ".join(map(str, failures)))
                self.assertEqual(len(processes), 9)

                ready_paths = {
                    player_id: self._completion_artifacts[(player_id, 1)]["ready"]
                    for player_id in game.players
                }
                await self._wait_for(
                    lambda: all(path.exists() for path in ready_paths.values()),
                    timeout=15,
                    description="all nine initial readiness markers",
                )
                self.assertIs(game.phase, initial_phase)
                self.assertEqual(game.day, initial_day)
                self.assertIsNone(game.game_result)
                for player_id, process in processes.items():
                    ready = json.loads(ready_paths[player_id].read_text(encoding="utf-8"))
                    self.assertEqual(ready["schema_version"], STATUS_SCHEMA_VERSION)
                    self.assertEqual(ready["evidence_contract"], EVIDENCE_CONTRACT)
                    self.assertEqual(ready["pid"], process.pid)
                    self.assertEqual(ready["player_id"], player_id)
                    self.assertIsInstance(ready["sync_seq"], int)
                    sync_headers = [
                        header
                        for header in self._completion_ledger["reply_headers"]
                        if header["player_id"] == player_id and header["type"] == "game.state_sync"
                    ]
                    self.assertEqual(len(sync_headers), 1, (player_id, sync_headers))
                    self.assertEqual(ready["sync_seq"], sync_headers[0]["seq"])
                    self.assertIsNone(process.returncode)

                self._completion_clock.release()
                self.assertEqual(self._completion_clock(), 0)
                game_deadline = time.monotonic() + 45

                def remaining(limit: float) -> float:
                    return max(0.01, min(limit, game_deadline - time.monotonic()))

                marker_path = self._completion_artifacts[(stopped_player, 1)]["marker"]
                await self._wait_for(
                    lambda: marker_path.exists(),
                    timeout=remaining(15),
                    description=f"{stop_after} stop marker",
                )
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                validate_stop_marker(
                    marker,
                    expected_player_id=stopped_player,
                    expected_kind=stop_after,
                    require_addendum_c=True,
                )
                checkpoint_seq = marker["last_seq"]
                first_process = processes[stopped_player]
                first_pid = first_process.pid
                self.assertTrue(
                    any(
                        item["outcome"] == "sent" and item["source_seq"] <= checkpoint_seq
                        for item in marker["action_evidence"]
                    ),
                    marker,
                )

                def later_vote_occurrence() -> list[dict[str, object]]:
                    return [
                        item
                        for item in self._completion_ledger["offer_occurrences"]
                        if item["player_id"] == stopped_player
                        and item["kind"] == "vote"
                        and item["seq"] > checkpoint_seq
                    ]

                await self._wait_for(
                    lambda: bool(later_vote_occurrence()),
                    timeout=remaining(15),
                    description="the next server-presented vote opportunity",
                )
                next_round_seq = min(item["seq"] for item in later_vote_occurrence())
                first_output = self._completion_process_outputs[id(first_process)]
                await self._wait_for(
                    lambda: first_output.checkpoint_seq is not None
                    and first_output.checkpoint_seq >= next_round_seq
                    and first_output.checkpoint_seq > checkpoint_seq,
                    timeout=remaining(15),
                    description="checkpoint save completion after the next vote",
                )
                self.assertGreaterEqual(first_output.checkpoint_seq or 0, next_round_seq)

                credentials_path = self._completion_artifacts[(stopped_player, 1)]["credentials"]
                if replay_history_limit == 1:
                    await self._wait_for(
                        lambda: session._replay_floor_by_player.get(stopped_player, 0) > checkpoint_seq,
                        timeout=remaining(10),
                        description="replay history to leave the retention window",
                    )
                else:
                    await self._wait_for(
                        lambda: any(
                            reply.seq > checkpoint_seq
                            for reply in session._history_by_player.get(stopped_player, ())
                        ),
                        timeout=remaining(10),
                        description="replay history to retain a missed event",
                    )

                first_process.kill()
                self._completion_ignored_process_ids.add(id(first_process))
                await first_process.wait()
                checkpoint_before_rewind = json.loads(credentials_path.read_text(encoding="utf-8"))
                self.assertGreaterEqual(checkpoint_before_rewind["last_seq"], next_round_seq)
                self.assertGreater(checkpoint_before_rewind["last_seq"], checkpoint_seq)
                connection_token = checkpoint_before_rewind["connection_token"]
                checkpoint_before_rewind["last_seq"] = checkpoint_seq
                credentials_path.write_text(
                    json.dumps(checkpoint_before_rewind, ensure_ascii=False),
                    encoding="utf-8",
                )
                rewound = json.loads(credentials_path.read_text(encoding="utf-8"))
                self.assertEqual(rewound["connection_token"], connection_token)
                self.assertEqual(rewound["last_seq"], checkpoint_seq)

                replacement = await self._start_client_process(
                    player_id=stopped_player,
                    registry=registry,
                    uri=uri,
                    root=root,
                    incarnation=2,
                    ready=True,
                )
                processes[stopped_player] = replacement
                started_processes[(stopped_player, 2)] = replacement
                replacement_ready_path = self._completion_artifacts[(stopped_player, 2)]["ready"]
                await self._wait_for(
                    lambda: replacement_ready_path.exists(),
                    timeout=remaining(15),
                    description="replacement readiness marker",
                )
                replacement_ready = json.loads(replacement_ready_path.read_text(encoding="utf-8"))
                self.assertEqual(replacement_ready["pid"], replacement.pid)
                self.assertEqual(replacement_ready["player_id"], stopped_player)
                resume_sync_seq = replacement_ready["sync_seq"]
                self.assertGreater(resume_sync_seq, checkpoint_seq)
                self.assertNotEqual(first_pid, replacement.pid)

                await self._wait_for(
                    lambda: game.game_result is not None,
                    timeout=remaining(45),
                    description="server tick-only game completion",
                )
                current_status_paths = {
                    player_id: self._completion_artifacts[(player_id, 2 if player_id == stopped_player else 1)]["status"]
                    for player_id in game.players
                }
                await self._wait_for(
                    lambda: all(path.exists() for path in current_status_paths.values()),
                    timeout=remaining(15),
                    description="all final client statuses",
                )
                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in current_status_paths.items()
                }
                for player_id, status in statuses.items():
                    validate_status(
                        status,
                        expected_player_id=player_id,
                        require_addendum_c=True,
                    )
                    self.assertEqual(status["pid"], processes[player_id].pid)
                    self.assertTrue(status["game_end"], (player_id, status))
                    self.assertEqual(status["send_errors"], [], (player_id, status))
                self.assertTrue(statuses[stopped_player]["resumed"], statuses)
                self.assertEqual(
                    (statuses[stopped_player]["gap_detected"], statuses[stopped_player]["gap_recovered"]),
                    expected_gap_counts,
                    statuses,
                )
                self.assertTrue(
                    any(item["after_resume"] for item in statuses[stopped_player]["state_sync_events"]),
                    statuses[stopped_player],
                )
                self.assertTrue(all(status["production_import_guard"] for status in statuses.values()))
                self.assertTrue(all(not status["server_imports"] for status in statuses.values()))

                coverage = assert_action_coverage(
                    self._completion_ledger,
                    statuses.values(),
                    marker=marker,
                    restarted_status=statuses[stopped_player],
                    restarted_player_id=stopped_player,
                    resume_sync_seq=resume_sync_seq,
                )
                self.assertTrue(coverage["expected"], coverage)
                for kind in ("vote", "ability", "co_declare", "chat"):
                    self.assertTrue(
                        any(
                            item["kind"] == kind
                            for item in expected_opportunities(self._completion_ledger).values()
                        ),
                        (kind, coverage),
                    )

                for event in game.event_bus.events:
                    if event.type != "VOTE_RESOLVED":
                        continue
                    expected_voters = set(event.payload["tallies"])
                    round_key = (event.payload["day"], event.payload["phase"])
                    accepted = [
                        record.payload
                        for record in game.event_bus.events
                        if record.type == "VOTE_SUBMITTED"
                        and (record.payload["day"], record.payload["phase"]) == round_key
                    ]
                    assert_acceptance_quorum(len(expected_voters), accepted, field="voter_player_id")

                expected_ability = {
                    key for key, item in expected_opportunities(self._completion_ledger).items()
                    if item["kind"] == "ability"
                }
                ability_accepted = [
                    event.payload
                    for event in game.event_bus.events
                    if event.type == "ACTION_SUBMITTED"
                    and "ability_id" in event.payload
                    and f"{event.payload['actor_player_id']}|{event.payload['day']}|{event.payload['phase']}|ability|{event.payload['ability_id']}" in expected_ability
                ]
                assert_acceptance_quorum(
                    len({key.split("|", 1)[0] for key in expected_ability}),
                    ability_accepted,
                    field="actor_player_id",
                )

                expected_co = {
                    key for key, item in expected_opportunities(self._completion_ledger).items()
                    if item["kind"] == "co_declare"
                }
                accepted_co = _co_acceptance_keys(game.event_bus.events)
                self.assertTrue(
                    {(int(day), player) for player, day, _ in (key.split("|", 2) for key in expected_co)}
                    >= accepted_co,
                    (expected_co, accepted_co),
                )
                for day in {int(key.split("|", 2)[1]) for key in expected_co}:
                    eligible = {
                        key.split("|", 1)[0]
                        for key in expected_co
                        if int(key.split("|", 2)[1]) == day
                    }
                    accepted = {player for accepted_day, player in accepted_co if accepted_day == day}
                    self.assertGreaterEqual(len(accepted), acceptance_quorum(len(eligible)))

                expected_chat_players = {
                    key.split("|", 1)[0]
                    for key, item in expected_opportunities(self._completion_ledger).items()
                    if item["kind"] == "chat"
                }
                received_chat = [
                    message
                    for status in statuses.values()
                    for message in status["chat_messages_received"]
                ]
                assert_acceptance_quorum(
                    len(expected_chat_players),
                    received_chat,
                    field="sender_player_id",
                )
                await asyncio.gather(
                    *(
                        asyncio.wait_for(process.wait(), timeout=5)
                        for process in started_processes.values()
                        if process.returncode is None
                    ),
                    return_exceptions=True,
                )
        finally:
            for process in started_processes.values():
                if process.returncode is None:
                    process.terminate()
            cleanup_results = await asyncio.gather(
                *(
                    self._finish_process(
                        process,
                        status_path=self._completion_artifacts[key]["status"]
                        if key in self._completion_artifacts
                        else None,
                    )
                    for key, process in started_processes.items()
                ),
                return_exceptions=True,
            )
            failures = [result for result in cleanup_results if isinstance(result, Exception)]
            session._reply = original_reply
            await server.close()
            if failures:
                raise AssertionError("client cleanup failures: " + " | ".join(map(str, failures)))

    async def test_separate_process_rejection_and_production_import_guard(self) -> None:
        game, registry, server, uri = await self._start_completion_server()
        self._completion_ledger = {"reply_headers": [], "offer_occurrences": []}
        session = server.sessions.session_for(GAME_ID)
        original_reply = session._reply

        def record_reply(player_id: str, message_type: str, payload: object) -> object:
            reply = original_reply(player_id, message_type, payload)
            record_server_reply(
                self._completion_ledger,
                player_id=player_id,
                seq=reply.seq,
                message_type=reply.type,
                payload=reply.payload,
            )
            return reply

        session._reply = record_reply
        processes: dict[str, asyncio.subprocess.Process] = {}
        started_processes: dict[tuple[str, int], asyncio.subprocess.Process] = {}
        injected_player = next(iter(game.players))
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)

                async def launch(player_id: str) -> None:
                    process = await self._start_client_process(
                        player_id=player_id,
                        registry=registry,
                        uri=uri,
                        root=root,
                        ready=True,
                        inject_rejection=player_id == injected_player,
                    )
                    processes[player_id] = process
                    started_processes[(player_id, 1)] = process

                results = await asyncio.gather(
                    *(launch(player_id) for player_id in game.players),
                    return_exceptions=True,
                )
                failures = [result for result in results if isinstance(result, Exception)]
                if failures:
                    raise AssertionError("parallel rejection startup failed: " + " | ".join(map(str, failures)))
                ready_paths = {
                    player_id: self._completion_artifacts[(player_id, 1)]["ready"]
                    for player_id in game.players
                }
                await self._wait_for(
                    lambda: all(path.exists() for path in ready_paths.values()),
                    timeout=15,
                    description="all nine rejection-scenario readiness markers",
                )
                self.assertIsNone(game.game_result)
                self._completion_clock.release()
                await self._wait_for(
                    lambda: game.game_result is not None,
                    timeout=45,
                    description="rejection scenario server tick-only completion",
                )
                status_paths = {
                    player_id: self._completion_artifacts[(player_id, 1)]["status"]
                    for player_id in game.players
                }
                await self._wait_for(
                    lambda: all(path.exists() for path in status_paths.values()),
                    timeout=15,
                    description="rejection scenario final statuses",
                )
                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in status_paths.items()
                }
                for player_id, status in statuses.items():
                    validate_status(
                        status,
                        expected_player_id=player_id,
                        allow_defect_rejections=True,
                        require_addendum_c=True,
                    )
                    self.assertTrue(status["game_end"], (player_id, status))
                    self.assertEqual(status["send_errors"], [], (player_id, status))
                rejection_reasons = [
                    rejection["reason"]
                    for status in statuses.values()
                    for rejection in status["action_rejections"]
                ]
                self.assertIn("co_limit_reached", rejection_reasons, statuses)
                self.assertTrue(all(status["production_import_guard"] for status in statuses.values()))
                self.assertTrue(all(not status["server_imports"] for status in statuses.values()))
                self.assertTrue(all(status["pid"] != os.getpid() for status in statuses.values()))
        finally:
            for process in started_processes.values():
                if process.returncode is None:
                    process.terminate()
            cleanup_results = await asyncio.gather(
                *(
                    self._finish_process(
                        process,
                        status_path=self._completion_artifacts[key]["status"]
                        if key in self._completion_artifacts
                        else None,
                    )
                    for key, process in started_processes.items()
                ),
                return_exceptions=True,
            )
            session._reply = original_reply
            await server.close()
            failures = [result for result in cleanup_results if isinstance(result, Exception)]
            if failures:
                raise AssertionError("client cleanup failures: " + " | ".join(map(str, failures)))


if __name__ == "__main__":
    unittest.main()
