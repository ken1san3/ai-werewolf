from __future__ import annotations

import asyncio
from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import sys
import time
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from ai_client.brain import BrainController, DecisionStatus
from ai_client.llm import LLMBrain, LLMClientIdentity
from ai_client.network import AbilityAction
from scripts import run_phase4_local_smoke as local_smoke
from server.aiwolf_core import ChatSubmission, InteractionAcceptance
from server.network import ConnectionContext, SessionResult
from server.network.session import ServerReply
from tests.fixtures.phase3_4_reaction_brain import CompletionReactionMode
from tests.fixtures.phase4_fake_backend import Phase4FakeBackend
from tests.fixtures.phase4_client_process import _start_audit_or_close_resources
import tests.test_phase3_5_completion as phase35_completion
from tests.test_phase4_llm_brain import ControllerSender, ControllerWorld, MemoryAudit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PHASE4_CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase4_client_process.py"


@pytest.mark.completion
class PhaseFourCompletionTests(unittest.IsolatedAsyncioTestCase):
    _run_scenario = phase35_completion.PhaseThreeFiveCompletionTests._run_scenario
    _wait_for = phase35_completion.PhaseThreeFiveCompletionTests._wait_for

    async def test_one_llm_eight_rule_based_clients_complete_with_exact_evidence(self) -> None:
        started = time.monotonic()
        with TemporaryDirectory() as temporary_directory:
            evidence_root = Path(temporary_directory)
            environment = {
                "AIWOLF_PHASE4_LLM_PLAYER": "player-0",
                "AIWOLF_PHASE4_BACKEND": "fake",
                "AIWOLF_PHASE4_EVIDENCE_DIR": str(evidence_root),
            }
            with patch.dict(os.environ, environment), patch(
                "tests.test_phase3_5_completion.CLIENT", PHASE4_CLIENT
            ):
                await self._run_scenario("phase4-offline", 8424, None)

            status_paths = sorted(evidence_root.glob("player-*.json"))
            self.assertEqual(len(status_paths), 9, status_paths)
            statuses = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in status_paths
            ]
            self.assertEqual(len({item["pid"] for item in statuses}), 9, statuses)
            self.assertNotIn(os.getpid(), {item["pid"] for item in statuses})
            self.assertTrue(all(item["game_end"] for item in statuses), statuses)
            self.assertTrue(
                all(item["production_import_guard"] for item in statuses), statuses
            )

            llm_statuses = [item for item in statuses if item["brain_mode"] == "llm"]
            self.assertEqual(len(llm_statuses), 1, statuses)
            llm = llm_statuses[0]
            self.assertEqual(llm["player_id"], "player-0")
            self.assertEqual(llm["backend_mode"], "fake")
            self.assertGreaterEqual(llm["reaction"]["accepted_count"], 1, llm)
            accepted_reservations = [
                item
                for item in llm["reservation"]["outcomes"]
                if item["status"] == "ACCEPTED"
            ]
            accepted_votes = [
                item for item in accepted_reservations if item["action"] == "vote.cast"
            ]
            self.assertGreaterEqual(len(accepted_votes), 1, llm)
            self.assertTrue(
                all(
                    item["request_event_id"]
                    and item["attempts"] == 1
                    and item["send_connection_generation"]
                    == item["observation_connection_generation"]
                    for item in accepted_reservations
                ),
                accepted_reservations,
            )
            self.assertEqual(llm["reservation"]["rejected_count"], 0, llm)
            self.assertEqual(llm["reservation"]["unknown_count"], 0, llm)
            self.assertFalse(llm["reservation"]["unresolved"], llm)

            backend = llm["backend"]
            snapshot = llm["llm_snapshot"]
            audit = llm["audit"]
            self.assertGreaterEqual(backend["call_count"], 2, backend)
            self.assertEqual(backend["max_active"], 1, backend)
            self.assertEqual(backend["call_count"], snapshot["backend_calls"])
            self.assertEqual(snapshot["calls"], snapshot["backend_calls"])
            self.assertEqual(snapshot["repair_attempts"], 0, snapshot)
            self.assertEqual(snapshot["failures"], 0, snapshot)
            self.assertTrue(
                all(item["membership_valid"] for item in backend["calls"]), backend
            )
            self.assertTrue(
                all(
                    item["option_id"] is None
                    or item["option_id"] in item["offered_option_ids"]
                    for item in backend["calls"]
                ),
                backend,
            )
            self.assertEqual(audit["record_count"], backend["call_count"], (audit, backend))
            self.assertEqual(
                {item["request_id"] for item in audit["records"]},
                {item["request_id"] for item in backend["calls"]},
            )
            self.assertTrue(
                all(item["attempt_ordinal"] == 1 for item in audit["records"]), audit
            )
            audit_kinds = {item["kind"] for item in audit["records"]}
            self.assertIn("chat", audit_kinds, audit)
            self.assertIn("vote", audit_kinds, audit)
            audit_path = Path(llm["audit_path"])
            self.assertTrue(audit_path.is_file(), audit_path)
            self.assertGreater(audit_path.stat().st_size, 0)

        self.assertLess(time.monotonic() - started, 120.0)

    async def test_scripted_ability_crosses_llmbrain_and_braincontroller(self) -> None:
        action = AbilityAction(
            1,
            2,
            "night",
            1,
            "ability",
            "inspect",
            "look",
            ("p2", "p3"),
            1,
            1,
        )
        world = ControllerWorld(action)
        audit = MemoryAudit()
        backend = Phase4FakeBackend()
        brain = LLMBrain(
            backend=backend,
            audit=audit,
            identity=LLMClientIdentity("game-ability", "p1"),
            request_id_factory=lambda: "phase4-ability-1",
        )
        sender = ControllerSender(audit)
        controller = BrainController(world=world, sender=sender, brain=brain)
        request = controller.capture_input(allowed_handles=(action,))
        self.assertIsNotNone(request)
        outcome = await controller.decide_and_send(request, timeout_seconds=2.0)
        self.assertEqual(outcome.status, DecisionStatus.SENT)
        self.assertEqual(sender.calls, [("ability", (action, ("p2",)))])
        self.assertEqual(len(audit.records), 1)
        self.assertEqual(audit.records[0].decision.kind, "ability")
        self.assertEqual(backend.calls[0]["option_id"], "action:0")
        self.assertTrue(backend.calls[0]["membership_valid"])
        await controller.stop()
        await backend.aclose()

    async def test_real_smoke_rejects_missing_settings_before_starting_children(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "must-not-exist"
            args = local_smoke._parser().parse_args(
                ["--max-seconds", "300", "--output-dir", str(output_dir)]
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "model"):
                    await local_smoke._run_smoke(args)
            self.assertFalse(output_dir.exists())

    def test_real_smoke_settings_use_cli_over_env_and_preserve_env_only(self) -> None:
        endpoint = "http://127.0.0.1:8123/v1/chat/completions"
        cli_args = local_smoke._parser().parse_args(
            ["--endpoint", endpoint, "--model", "cli-model"]
        )
        cli = local_smoke._resolve_settings(
            cli_args,
            {
                "AIWOLF_LLM_ENDPOINT": "http://127.0.0.1:9000/v1/chat/completions",
                "AIWOLF_LLM_MODEL": "env-model",
                "AIWOLF_LLM_API_KEY": "secret-key",
            },
        )
        self.assertEqual(cli.endpoint, endpoint)
        self.assertEqual(cli.model, "cli-model")
        self.assertEqual(cli.api_key, "secret-key")

        env_args = local_smoke._parser().parse_args([])
        env = local_smoke._resolve_settings(
            env_args,
            {"AIWOLF_LLM_MODEL": "env-only-model"},
        )
        self.assertEqual(
            env.endpoint, "http://127.0.0.1:8080/v1/chat/completions"
        )
        self.assertEqual(env.model, "env-only-model")

        cli_only = local_smoke._resolve_settings(
            local_smoke._parser().parse_args(["--model", "cli-only-model"]),
            {},
        )
        self.assertEqual(cli_only.model, "cli-only-model")
        self.assertEqual(
            cli_only.endpoint, "http://127.0.0.1:8080/v1/chat/completions"
        )

    def test_real_smoke_private_launch_json_materializes_token_mappings(self) -> None:
        tokens = {"player-0": "token-0", "player-1": "token-1"}
        expected = {
            "uri": "ws://127.0.0.1:8123",
            "game_id": "game-json",
            "entry_tokens": tokens,
            "player_ids": ["player-0", "player-1"],
            "server_pid": 41234,
        }
        for entry_tokens in (tokens, MappingProxyType(tokens)):
            with self.subTest(mapping_type=type(entry_tokens).__name__):
                with TemporaryDirectory() as temporary_directory:
                    path = Path(temporary_directory) / "server-launch.json"
                    with patch.object(local_smoke.os, "chmod", wraps=os.chmod) as chmod:
                        local_smoke._write_private_server_launch(
                            path,
                            uri=expected["uri"],
                            game_id=expected["game_id"],
                            entry_tokens=entry_tokens,
                            player_ids=expected["player_ids"],
                            server_pid=expected["server_pid"],
                        )

                    actual = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(actual, expected)
                    self.assertIs(type(actual["entry_tokens"]), dict)
                    chmod.assert_called_once_with(
                        path, stat.S_IRUSR | stat.S_IWUSR
                    )
        self.assertEqual(tokens, expected["entry_tokens"])

    def test_real_smoke_records_each_acceptance_from_its_contract_surface(self) -> None:
        chat_result = SessionResult(
            reply=None,
            context=None,
            channel_messages=(
                ChatSubmission(
                    channel_id="public",
                    message={
                        "player_id": "untrusted-message-author",
                        "display_name": "Player 0",
                        "message": "hello",
                    },
                    acceptance=InteractionAcceptance(
                        action="chat.send",
                        player_id="player-0",
                        day=1,
                        phase="day",
                        accepted_at=12,
                        phase_deadline=20,
                    ),
                ),
            ),
        )
        context = ConnectionContext(
            connection_id="connection-0",
            game_id="game-evidence",
            player_id="player-0",
            connection_token="token-0",
        )

        def direct_result(action: str, request_event_id: str, seq: int) -> SessionResult:
            return SessionResult(
                reply=ServerReply(
                    type="action.accepted",
                    game_id="game-evidence",
                    seq=seq,
                    timestamp=13,
                    payload={
                        "action": action,
                        "request_event_id": request_event_id,
                    },
                    event_id=f"reply-{seq}",
                ),
                context=context,
            )

        evidence = local_smoke._accepted_action_evidence(chat_result)
        evidence.extend(
            local_smoke._accepted_action_evidence(
                direct_result("vote.cast", "vote-request", 7)
            )
        )
        evidence.extend(
            local_smoke._accepted_action_evidence(
                direct_result("ability.use", "ability-request", 8)
            )
        )

        self.assertEqual(
            evidence,
            [
                {
                    "player_id": "player-0",
                    "action": "chat.send",
                    "request_event_id": None,
                    "reply_seq": None,
                },
                {
                    "player_id": "player-0",
                    "action": "vote.cast",
                    "request_event_id": "vote-request",
                    "reply_seq": 7,
                },
                {
                    "player_id": "player-0",
                    "action": "ability.use",
                    "request_event_id": "ability-request",
                    "reply_seq": 8,
                },
            ],
        )

    async def test_audit_start_failure_closes_resources_without_task_or_file_leak(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "partial-audit.tmp"

            class FailingAudit:
                closed = False

                async def start(self) -> None:
                    marker.write_text("partial", encoding="utf-8")
                    raise RuntimeError("audit_start_failed")

                async def aclose(self) -> None:
                    self.closed = True
                    marker.unlink(missing_ok=True)

            class Backend:
                closed = False

                async def aclose(self) -> None:
                    self.closed = True

            audit = FailingAudit()
            backend = Backend()
            before = set(asyncio.all_tasks())
            with self.assertRaisesRegex(RuntimeError, "audit_start_failed"):
                await _start_audit_or_close_resources(audit, backend)
            self.assertTrue(audit.closed)
            self.assertTrue(backend.closed)
            self.assertFalse(marker.exists())
            self.assertEqual(set(asyncio.all_tasks()), before)

    async def test_partial_spawn_failure_cleans_owned_children_and_secrets(self) -> None:
        secret = "entry-token-must-not-appear"

        class FakeProcess:
            next_pid = 41000

            def __init__(self) -> None:
                self.pid = FakeProcess.next_pid
                FakeProcess.next_pid += 1
                self.returncode = None
                self.terminated = False
                self.waited = False

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = 15

            def kill(self) -> None:
                self.returncode = 9

            async def wait(self) -> int:
                self.waited = True
                while self.returncode is None:
                    await asyncio.sleep(0)
                return self.returncode

        class PartialSpawn:
            def __init__(self) -> None:
                self.calls = 0
                self.commands: list[tuple[object, ...]] = []
                self.processes: list[FakeProcess] = []
                self.state_path: Path | None = None

            async def __call__(self, *command, **_kwargs):
                self.calls += 1
                self.commands.append(command)
                if self.calls == 3:
                    raise OSError("spawn_failed")
                process = FakeProcess()
                self.processes.append(process)
                if self.calls == 1:
                    values = list(command)
                    self.state_path = Path(values[values.index("--state-path") + 1])
                    self.state_path.write_text(
                        json.dumps(
                            {
                                "uri": "ws://127.0.0.1:1",
                                "game_id": "game-test",
                                "entry_tokens": {
                                    f"player-{index}": secret for index in range(9)
                                },
                                "player_ids": [f"player-{index}" for index in range(9)],
                            }
                        ),
                        encoding="utf-8",
                    )
                return process

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "partial-spawn"
            args = local_smoke._parser().parse_args(
                [
                    "--endpoint",
                    "http://127.0.0.1:8080/v1/chat/completions",
                    "--model",
                    "explicit-model",
                    "--output-dir",
                    str(output_dir),
                ]
            )
            factory = PartialSpawn()
            before = set(asyncio.all_tasks())
            diagnostics = StringIO()
            with redirect_stderr(diagnostics):
                code = await local_smoke._run_smoke(
                    args, environ={}, process_factory=factory
                )
            self.assertEqual(code, 1)
            self.assertEqual(len(factory.processes), 2)
            first_client_command = factory.commands[1]
            mode_index = first_client_command.index("--mode") + 1
            mode_value = first_client_command[mode_index]
            self.assertEqual(mode_value, CompletionReactionMode.SPEAK.value)
            self.assertIs(
                CompletionReactionMode(mode_value), CompletionReactionMode.SPEAK
            )
            self.assertTrue(
                all(process.terminated and process.waited for process in factory.processes)
            )
            self.assertIsNotNone(factory.state_path)
            self.assertFalse(factory.state_path.exists())
            self.assertEqual(set(asyncio.all_tasks()), before)
            self.assertNotIn(secret, diagnostics.getvalue())
            self.assertTrue(output_dir.is_dir())
            self.assertFalse(any(secret in path.read_text(errors="replace") for path in output_dir.iterdir()))

    def test_real_smoke_rejects_nonzero_owned_child_with_success_evidence(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            (output_dir / "ai.jsonl").write_text("{}\n", encoding="utf-8")
            statuses = [
                {
                    "pid": 42000 + index,
                    "player_id": f"player-{index}",
                    "brain_mode": "llm" if index == 0 else "rule_based",
                    "game_end": True,
                    "llm_snapshot": {"decisions": 2} if index == 0 else None,
                    "audit": {
                        "records": [{"kind": "chat"}, {"kind": "vote"}]
                    }
                    if index == 0
                    else None,
                    "reservation": {"outcomes": []},
                }
                for index in range(9)
            ]
            processes = [SimpleNamespace(returncode=0) for _ in range(10)]
            processes[4].returncode = 7
            errors = local_smoke._validate_result(
                output_dir=output_dir,
                llm_player="player-0",
                server_result={
                    "game_end": True,
                    "accepted": [
                        {"player_id": "player-0", "action": "chat.send"},
                        {"player_id": "player-0", "action": "vote.cast"},
                    ],
                },
                statuses=statuses,
                processes=processes,
            )
            self.assertIn("owned child exited non-zero", errors)

    async def test_real_smoke_owned_child_cleanup_is_finite(self) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        )
        started = time.monotonic()
        await local_smoke._stop_owned(process)
        self.assertIsNotNone(process.returncode)
        self.assertLess(time.monotonic() - started, 5.0)
