from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_client import (
    AdmissionCredentials,
    GenerationAdmissionBroker,
    GenerationBrokerConfig,
    Phase5ClientRuntime,
    Phase5ClientRuntimeConfig,
    Phase5RuntimeExitReason,
    Phase5RuntimeLifecycle,
)
from ai_client.brain import FeatureControllerExit, FeatureControllerExitReason
from ai_client.llm import LLMBrainConfig, ShortChatConfig
from ai_client.network import (
    ClientExit,
    ClientExitReason,
    ClientLifecycle,
    FileCredentialStore,
    NetworkClientConfig,
    SessionCheckpoint,
)
from tests.fixtures.phase5_client_process import _DisposableCredentialStore
from tests.fixtures.completion_clock import publish_gate
from tests.fixtures.completion_diagnostics import timeout_summary
from ai_client.world import Freshness, WorldStateExit, WorldStateExitReason
from tests.fixtures.phase5_deterministic_backend import (
    Phase5DeterministicBrokerBackend,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER = PROJECT_ROOT / "tests" / "fixtures" / "phase5_server_process.py"
BROKER = PROJECT_ROOT / "tests" / "fixtures" / "phase5_broker_process.py"
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase5_client_process.py"


class _FakeNetwork:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.result: asyncio.Future[ClientExit] = asyncio.get_running_loop().create_future()

    async def run(self) -> ClientExit:
        return await self.result

    async def stop(self) -> None:
        self.order.append("network")
        if not self.result.done():
            self.result.set_result(
                ClientExit(
                    ClientExitReason.STOPPED,
                    True,
                    ClientLifecycle.ENDED,
                )
            )


class _FakeWorld:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.result: asyncio.Future[WorldStateExit] = asyncio.get_running_loop().create_future()

    async def run(self) -> WorldStateExit:
        return await self.result

    async def stop(self) -> None:
        self.order.append("world")
        if not self.result.done():
            self.result.set_result(
                WorldStateExit(WorldStateExitReason.STOPPED, Freshness.ENDED, 0)
            )


class _FakeFeature:
    def __init__(
        self, owner: str, order: list[str], *, start_error: BaseException | None = None
    ) -> None:
        self.owner = owner
        self.order = order
        self.start_error = start_error
        self.result: asyncio.Future[FeatureControllerExit] = (
            asyncio.get_running_loop().create_future()
        )

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error

    async def wait(self) -> FeatureControllerExit:
        return await self.result

    async def stop(self) -> None:
        self.order.append(self.owner)
        if not self.result.done():
            self.result.set_result(
                FeatureControllerExit(
                    self.owner,  # type: ignore[arg-type]
                    FeatureControllerExitReason.STOP_REQUESTED,
                )
            )


class _FakeCloser:
    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self.order = order

    async def aclose(self) -> None:
        self.order.append(self.name)


class _FakeArbiter:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def stop(self) -> None:
        self.order.append("arbiter")


class PhaseFiveRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, root: Path, *, shutdown: float = 0.2) -> Phase5ClientRuntimeConfig:
        return Phase5ClientRuntimeConfig(
            network=NetworkClientConfig(
                "ws://127.0.0.1:1",
                "game-runtime",
                "entry-secret",
                shutdown_timeout_seconds=shutdown,
            ),
            player_id="player-0",
            admission_host="127.0.0.1",
            admission_port=1,
            admission_credentials=AdmissionCredentials("opaque-0", "1" * 64),
            audit_path=root / "player-0.ai.jsonl",
            master_seed=5,
        )

    def _runtime(
        self,
        root: Path,
        *,
        shutdown: float = 0.2,
        reaction_start_error: BaseException | None = None,
    ):
        order: list[str] = []
        network = _FakeNetwork(order)
        world = _FakeWorld(order)
        reaction = _FakeFeature(
            "reaction_chat", order, start_error=reaction_start_error
        )
        vote = _FakeFeature("vote_ability", order)
        runtime = Phase5ClientRuntime(
            config=self._config(root, shutdown=shutdown),
            network=network,  # type: ignore[arg-type]
            world=world,  # type: ignore[arg-type]
            admission=object(),  # type: ignore[arg-type]
            backend=_FakeCloser("admission", order),  # type: ignore[arg-type]
            audit=_FakeCloser("audit", order),  # type: ignore[arg-type]
            brain=object(),  # type: ignore[arg-type]
            brain_controller=object(),  # type: ignore[arg-type]
            arbiter=_FakeArbiter(order),  # type: ignore[arg-type]
            reaction=reaction,  # type: ignore[arg-type]
            vote_ability=vote,  # type: ignore[arg-type]
        )
        return runtime, network, world, reaction, vote, order

    async def test_game_end_requires_bounded_natural_world_and_controller_drain(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            runtime, network, world, reaction, vote, order = self._runtime(
                Path(temporary_directory)
            )
            await runtime.start()
            waiter = asyncio.create_task(runtime.wait())
            network.result.set_result(
                ClientExit(
                    ClientExitReason.GAME_ENDED,
                    True,
                    ClientLifecycle.ENDED,
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(waiter.done())
            world.result.set_result(
                WorldStateExit(
                    WorldStateExitReason.CLIENT_ENDED,
                    Freshness.ENDED,
                    12,
                )
            )
            reaction.result.set_result(
                FeatureControllerExit(
                    "reaction_chat", FeatureControllerExitReason.WORLD_ENDED
                )
            )
            vote.result.set_result(
                FeatureControllerExit(
                    "vote_ability", FeatureControllerExitReason.WORLD_ENDED
                )
            )
            result = await waiter
            self.assertEqual(result.reason, Phase5RuntimeExitReason.GAME_ENDED)
            self.assertTrue(result.success)
            self.assertEqual(result.world.reason, WorldStateExitReason.CLIENT_ENDED)
            self.assertEqual(result.reaction.reason, FeatureControllerExitReason.WORLD_ENDED)
            self.assertEqual(result.vote_ability.reason, FeatureControllerExitReason.WORLD_ENDED)
            self.assertEqual(
                order,
                [
                    "vote_ability",
                    "reaction_chat",
                    "arbiter",
                    "audit",
                    "admission",
                    "world",
                    "network",
                ],
            )
            self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.ENDED)

    async def test_network_and_controller_failures_are_fail_fast_and_clean(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            for failure in ("network", "controller"):
                with self.subTest(failure=failure):
                    runtime, network, _, reaction, _, order = self._runtime(
                        Path(temporary_directory)
                    )
                    await runtime.start()
                    if failure == "network":
                        network.result.set_result(
                            ClientExit(
                                ClientExitReason.INTERNAL_ERROR,
                                False,
                                ClientLifecycle.FAILED,
                            )
                        )
                        expected = Phase5RuntimeExitReason.NETWORK_FAILED
                    else:
                        reaction.result.set_result(
                            FeatureControllerExit(
                                "reaction_chat",
                                FeatureControllerExitReason.FAILED,
                                "ControlledFailure",
                            )
                        )
                        expected = Phase5RuntimeExitReason.CONTROLLER_FAILED
                    result = await runtime.wait()
                    self.assertEqual(result.reason, expected)
                    self.assertFalse(result.success)
                    self.assertEqual(order.count("audit"), 1)
                    self.assertEqual(order.count("admission"), 1)
                    self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)

    async def test_game_end_drain_timeout_is_failure_and_cleans(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            runtime, network, _, _, _, order = self._runtime(
                Path(temporary_directory), shutdown=0.01
            )
            await runtime.start()
            network.result.set_result(
                ClientExit(
                    ClientExitReason.GAME_ENDED,
                    True,
                    ClientLifecycle.ENDED,
                )
            )
            result = await runtime.wait()
            self.assertEqual(result.reason, Phase5RuntimeExitReason.CONTROLLER_FAILED)
            self.assertEqual(result.detail, "natural game-end drain timed out")
            self.assertEqual(order.count("network"), 1)
            self.assertEqual(order.count("admission"), 1)

    async def test_startup_failure_closes_every_constructed_owner_once(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            runtime, _, _, _, _, order = self._runtime(
                Path(temporary_directory),
                reaction_start_error=RuntimeError("controlled startup failure"),
            )
            with self.assertRaisesRegex(RuntimeError, "controlled startup failure"):
                await runtime.start()
            self.assertEqual(runtime.lifecycle, Phase5RuntimeLifecycle.FAILED)
            self.assertEqual(order.count("audit"), 1)
            self.assertEqual(order.count("admission"), 1)
            self.assertEqual(order.count("network"), 1)

    async def test_connect_sets_private_audit_permissions_and_redacts_config(self) -> None:
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            root = Path(temporary_directory)
            token = "2" * 64
            broker_config = GenerationBrokerConfig(
                authentication_timeout_seconds=0.5,
                cancellation_grace_seconds=0.5,
                provider_drain_grace_seconds=1.0,
                shutdown_grace_seconds=2.0,
            )
            broker = GenerationAdmissionBroker(
                {"opaque-0": token},
                Phase5DeterministicBrokerBackend(),
                fairness_seed="runtime-permission",
                config=broker_config,
                backend_request_timeout_seconds=0.5,
            )
            ready = await broker.start()
            config = Phase5ClientRuntimeConfig(
                network=NetworkClientConfig(
                    "ws://127.0.0.1:1", "game-runtime", "entry-secret"
                ),
                player_id="player-0",
                admission_host=ready.host,
                admission_port=ready.port,
                admission_credentials=AdmissionCredentials("opaque-0", token),
                audit_path=root / "player-0.ai.jsonl",
                master_seed=7,
                broker=broker_config,
                llm=LLMBrainConfig(short_chat=ShortChatConfig()),
            )
            try:
                with patch("ai_client.runtime.os.chmod", wraps=os.chmod) as chmod:
                    runtime = await Phase5ClientRuntime.connect(
                        config,
                        FileCredentialStore(root / "credentials.json"),
                    )
                    await runtime.aclose()
                chmod.assert_any_call(
                    root, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                )
                chmod.assert_any_call(
                    config.audit_path, stat.S_IRUSR | stat.S_IWUSR
                )
                self.assertTrue(config.audit_path.is_file())
                self.assertNotIn(token, repr(config))
                self.assertNotIn("entry-secret", repr(config))
            finally:
                await broker.aclose()

    async def test_disposable_credential_store_preserves_checkpoint_contract(self) -> None:
        store = _DisposableCredentialStore()
        self.assertIsNone(await store.load())
        checkpoint = SessionCheckpoint("session-token", 17, "1.1")
        await store.save(checkpoint)
        self.assertIs(await store.load(), checkpoint)
        self.assertEqual(store.save_count, 1)


@dataclass
class _OwnedProcess:
    label: str
    process: asyncio.subprocess.Process
    stdout_path: Path
    stderr_path: Path


class PhaseFiveOfflineCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminated_process_classification_requires_exact_success_evidence(
        self,
    ) -> None:
        successful_status = {
            "runtime_success": True,
            "runtime_exit": "GAME_ENDED",
            "world_exit": "CLIENT_ENDED",
            "reaction_exit": "WORLD_ENDED",
            "vote_ability_exit": "WORLD_ENDED",
        }
        missing = object()
        vectors = (
            ("successful", 0, successful_status, None),
            ("nonzero", 1, successful_status, "nonzero_exit"),
            ("missing", 0, missing, "missing"),
            ("malformed", 0, "{", "malformed"),
            (
                "unsuccessful",
                0,
                {**successful_status, "runtime_success": False},
                "unsuccessful",
            ),
        )
        with TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
            root = Path(temporary_directory)
            successful_item: _OwnedProcess | None = None
            for label, returncode, status_value, expected_failure in vectors:
                with self.subTest(label=label):
                    stdout_path = root / f"{label}.stdout.log"
                    stderr_path = root / f"{label}.stderr.log"
                    stdout_path.write_text("", encoding="utf-8")
                    stderr_path.write_text("", encoding="utf-8")
                    if status_value is not missing:
                        status_path = root / f"{label}.status.json"
                        status_path.write_text(
                            (
                                status_value
                                if isinstance(status_value, str)
                                else json.dumps(status_value)
                            ),
                            encoding="utf-8",
                        )
                    item = _OwnedProcess(
                        label,
                        SimpleNamespace(returncode=returncode),  # type: ignore[arg-type]
                        stdout_path,
                        stderr_path,
                    )
                    failure = self._terminated_process_failure(item)
                    if expected_failure is None:
                        self.assertIsNone(failure)
                        successful_item = item
                    else:
                        self.assertIsNotNone(failure)
                        assert failure is not None
                        self.assertEqual(
                            failure["terminal_evidence"], expected_failure
                        )
            assert successful_item is not None
            later_artifact = root / "later-artifact.json"

            async def publish_artifact() -> None:
                await asyncio.sleep(0)
                later_artifact.write_text("{}", encoding="utf-8")

            await asyncio.gather(
                self._wait_for_files((later_artifact,), [successful_item], 0.2),
                publish_artifact(),
            )

    async def test_one_broker_nine_production_llm_clients_complete(self) -> None:
        started = time.monotonic()
        with TemporaryDirectory(
            dir=PROJECT_ROOT,
            prefix="tmp_phase5_completion_",
            delete=False,
        ) as temporary_directory:
            root = Path(temporary_directory)
            os.chmod(root, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            seed = 8525
            player_ids = [f"player-{index}" for index in range(9)]
            entry_tokens = [
                "entry_" + secrets.token_urlsafe(32) for _ in player_ids
            ]
            self.assertEqual(len(set(entry_tokens)), len(player_ids))
            opaque_client_ids = [secrets.token_hex(32) for _ in player_ids]
            self.assertEqual(len(set(opaque_client_ids)), len(player_ids))
            self.assertTrue(
                all(
                    "player" not in client_id
                    and "model" not in client_id
                    and "role" not in client_id
                    for client_id in opaque_client_ids
                )
            )
            player_to_opaque_client_id = dict(
                zip(player_ids, opaque_client_ids, strict=True)
            )
            admission_registry = {
                client_id: secrets.token_hex(32) for client_id in opaque_client_ids
            }
            self.assertEqual(len(set(admission_registry.values())), len(player_ids))
            sentinels = tuple(entry_tokens) + tuple(admission_registry.values())
            owned: list[_OwnedProcess] = []
            client_processes: dict[str, _OwnedProcess] = {}
            client_statuses: dict[str, Path] = {}
            ready_paths: dict[str, Path] = {}
            day_ready_paths: dict[str, Path] = {}
            server_stop = root / "server.stop"
            broker_stop = root / "broker.stop"
            client_stop = root / "clients.stop"
            server_state = root / "server.ready.json"
            server_result_path = root / "server.result.json"
            broker_ready_path = root / "broker.ready.json"
            broker_result_path = root / "broker.result.json"
            metrics_path = root / "admission.metadata.jsonl"
            clock_start = root / "clock-start"
            day_one_release = root / "day-one-release"

            async def spawn(label: str, *command: str) -> _OwnedProcess:
                stdout_path = root / f"{label}.stdout.log"
                stderr_path = root / f"{label}.stderr.log"
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=str(PROJECT_ROOT),
                        stdin=asyncio.subprocess.PIPE,
                        stdout=stdout,
                        stderr=stderr,
                    )
                item = _OwnedProcess(label, process, stdout_path, stderr_path)
                owned.append(item)
                return item

            async def bootstrap(item: _OwnedProcess, value: object) -> None:
                assert item.process.stdin is not None
                item.process.stdin.write(
                    (json.dumps(value, separators=(",", ":")) + "\n").encode()
                )
                await item.process.stdin.drain()
                item.process.stdin.close()

            server = await spawn(
                "server",
                os.sys.executable,
                str(SERVER),
                "--state",
                str(server_state),
                "--result",
                str(server_result_path),
                "--clock-start",
                str(clock_start),
                "--day-one-release",
                str(day_one_release),
                "--stop",
                str(server_stop),
                "--seed",
                str(seed),
            )
            await bootstrap(server, {"entry_tokens": entry_tokens})
            try:
                await self._wait_for_file(server_state, [server], 15.0)
                server_ready = json.loads(server_state.read_text(encoding="utf-8"))
                self.assertNotIn("entry_tokens", server_ready)
                self.assertEqual(server_ready["player_ids"], player_ids)

                broker = await spawn(
                    "broker",
                    os.sys.executable,
                    str(BROKER),
                    "--ready",
                    str(broker_ready_path),
                    "--result",
                    str(broker_result_path),
                    "--stop",
                    str(broker_stop),
                    "--metrics",
                    str(metrics_path),
                    "--fairness-seed",
                    str(seed),
                    "--clock-start",
                    str(clock_start),
                    "--day-one-release",
                    str(day_one_release),
                )
                await bootstrap(broker, admission_registry)
                await self._wait_for_file(broker_ready_path, [broker], 15.0)
                broker_ready = json.loads(broker_ready_path.read_text(encoding="utf-8"))
                self.assertNotIn("registry", broker_ready)
                self.assertIsNone(broker_ready["model_pid"])

                async def start_client(index: int, player_id: str) -> None:
                    player_root = root / player_id
                    player_root.mkdir(mode=0o700)
                    os.chmod(player_root, stat.S_IRWXU)
                    status = root / f"{player_id}.status.json"
                    ready = root / f"{player_id}.ready.json"
                    day_ready = root / f"{player_id}.day-ready.json"
                    audit = player_root / f"{player_id}.ai.jsonl"
                    item = await spawn(
                        player_id,
                        os.sys.executable,
                        str(CLIENT),
                        "--status",
                        str(status),
                        "--ready",
                        str(ready),
                        "--day-one-ready",
                        str(day_ready),
                        "--clock-start",
                        str(clock_start),
                        "--day-one-release",
                        str(day_one_release),
                        "--audit",
                        str(audit),
                        "--stop",
                        str(client_stop),
                        "--seed",
                        str(seed),
                    )
                    await bootstrap(
                        item,
                        {
                            "uri": server_ready["uri"],
                            "game_id": server_ready["game_id"],
                            "player_id": player_id,
                            "entry_token": entry_tokens[index],
                            "admission_host": broker_ready["host"],
                            "admission_port": broker_ready["port"],
                            "admission_client_id": player_to_opaque_client_id[player_id],
                            "admission_token": admission_registry[
                                player_to_opaque_client_id[player_id]
                            ],
                            "broker_config": broker_ready["config"],
                        },
                    )
                    client_processes[player_id] = item
                    client_statuses[player_id] = status
                    ready_paths[player_id] = ready
                    day_ready_paths[player_id] = day_ready

                await asyncio.gather(
                    *(start_client(index, player_id) for index, player_id in enumerate(player_ids))
                )
                self.assertEqual(len({item.process.pid for item in client_processes.values()}), 9)
                await self._wait_for_files(
                    ready_paths.values(), list(client_processes.values()), 15.0
                )
                publish_gate(clock_start)
                await self._wait_for_files(
                    day_ready_paths.values(), list(client_processes.values()), 15.0
                )
                publish_gate(day_one_release)

                await self._wait_for_file(
                    server_result_path, [server, *client_processes.values()], 120.0
                )
                await self._wait_for_files(
                    client_statuses.values(), list(client_processes.values()), 15.0
                )
                await asyncio.gather(
                    *(asyncio.wait_for(item.process.wait(), 15.0) for item in client_processes.values())
                )
                broker_stop.write_text("stop\n", encoding="utf-8")
                await self._wait_for_file(broker_result_path, [broker], 10.0)
                await asyncio.wait_for(broker.process.wait(), 10.0)
                await asyncio.wait_for(server.process.wait(), 10.0)

                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in client_statuses.items()
                }
                server_result = json.loads(server_result_path.read_text(encoding="utf-8"))
                broker_result = json.loads(broker_result_path.read_text(encoding="utf-8"))
                metrics = [
                    json.loads(line)
                    for line in metrics_path.read_text(encoding="utf-8").splitlines()
                ]
                self._assert_completion(
                    statuses,
                    server_ready,
                    server_result,
                    broker_ready,
                    broker_result,
                    metrics,
                    player_to_opaque_client_id,
                    owned,
                )

                manifest = self._write_manifest_atomic(
                    root,
                    statuses,
                    metrics_path,
                    player_to_opaque_client_id,
                )
                self._assert_manifest(
                    root,
                    manifest,
                    statuses,
                    metrics_path,
                    player_to_opaque_client_id,
                )
                evidence_paths = [
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                    and path.suffix in {".json", ".jsonl", ".log"}
                ]
                for path in evidence_paths:
                    value = path.read_text(encoding="utf-8", errors="replace")
                    for secret in sentinels:
                        self.assertNotIn(secret, value, path)
                self.assertLess(time.monotonic() - started, 180.0)
            except TimeoutError:
                raise TimeoutError(json.dumps(timeout_summary(root), sort_keys=True)) from None
            finally:
                client_stop.write_text("stop\n", encoding="utf-8")
                server_stop.write_text("stop\n", encoding="utf-8")
                broker_stop.write_text("stop\n", encoding="utf-8")
                await asyncio.gather(
                    *(self._stop_owned(item) for item in owned),
                    return_exceptions=False,
                )
                self.assertTrue(all(item.process.returncode is not None for item in owned))

    async def _wait_for_file(
        self, path: Path, processes: list[_OwnedProcess], timeout: float
    ) -> None:
        await self._wait_for_files((path,), processes, timeout)

    async def _wait_for_files(
        self, paths, processes: list[_OwnedProcess], timeout: float
    ) -> None:
        expected = tuple(paths)

        async def wait() -> None:
            while not all(path.exists() for path in expected):
                failed = {
                    item.label: failure
                    for item in processes
                    if (failure := self._terminated_process_failure(item)) is not None
                }
                if failed:
                    raise AssertionError(f"child exited before evidence: {failed}")
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait(), timeout)

    @staticmethod
    def _terminated_process_failure(item: _OwnedProcess) -> dict[str, object] | None:
        returncode = item.process.returncode
        if returncode is None:
            return None
        status_path = item.stdout_path.parent / f"{item.label}.status.json"
        status: object = None
        terminal_evidence = "missing"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            terminal_evidence = "malformed"
        else:
            terminal_evidence = "unsuccessful"
        exact_success = (
            returncode == 0
            and isinstance(status, dict)
            and status.get("runtime_success") is True
            and status.get("runtime_exit") == "GAME_ENDED"
            and status.get("world_exit") == "CLIENT_ENDED"
            and status.get("reaction_exit") == "WORLD_ENDED"
            and status.get("vote_ability_exit") == "WORLD_ENDED"
        )
        if exact_success:
            return None
        if returncode != 0:
            terminal_evidence = "nonzero_exit"
        return {
            "returncode": returncode,
            "terminal_evidence": terminal_evidence,
            "stdout": item.stdout_path.read_text(errors="replace")[-2048:],
            "stderr": item.stderr_path.read_text(errors="replace")[-2048:],
            "status": status,
        }

    async def _stop_owned(self, item: _OwnedProcess) -> None:
        if item.process.returncode is not None:
            await item.process.wait()
            return
        try:
            await asyncio.wait_for(item.process.wait(), 5.0)
            return
        except TimeoutError:
            pass
        item.process.terminate()
        try:
            await asyncio.wait_for(item.process.wait(), 5.0)
        except TimeoutError:
            item.process.kill()
            await item.process.wait()

    def _assert_completion(
        self,
        statuses,
        server_ready,
        server_result,
        broker_ready,
        broker_result,
        metrics,
        player_to_opaque_client_id,
        owned,
    ) -> None:
        client_pids = {item["pid"] for item in statuses.values()}
        all_pids = client_pids | {server_result["server_pid"], broker_result["pid"]}
        self.assertEqual(len(client_pids), 9)
        self.assertEqual(len(all_pids), 11)
        self.assertNotIn(os.getpid(), all_pids)
        self.assertEqual(server_ready["server_pid"], server_result["server_pid"])
        self.assertEqual(broker_ready["pid"], broker_result["pid"])
        self.assertTrue(server_result["success"])
        self.assertTrue(server_result["listener_closed"])
        self.assertTrue(broker_result["success"])
        self.assertTrue(broker_result["shutdown_clean"])
        self.assertTrue(all(item.process.returncode == 0 for item in owned), owned)
        self.assertTrue(all(item["brain_mode"] == "llm" for item in statuses.values()))
        self.assertTrue(all(item["game_end"] for item in statuses.values()))
        self.assertTrue(all(item["runtime_success"] for item in statuses.values()))
        self.assertTrue(all(item["runtime_exit"] == "GAME_ENDED" for item in statuses.values()))
        self.assertTrue(all(item["world_exit"] == "CLIENT_ENDED" for item in statuses.values()))
        self.assertTrue(all(item["reaction_exit"] == "WORLD_ENDED" for item in statuses.values()))
        self.assertTrue(all(item["vote_ability_exit"] == "WORLD_ENDED" for item in statuses.values()))
        self.assertTrue(all(item["production_import_guard"] for item in statuses.values()))
        self.assertTrue(all(item["owned_task_names_alive"] == [] for item in statuses.values()))
        self.assertTrue(all(item["model_pid"] is None for item in statuses.values()))
        self.assertTrue(
            all(
                item["credential_store"]["kind"]
                == "disposable_process_private"
                and item["credential_store"]["save_count"] > 0
                and item["credential_store"]["has_checkpoint"]
                and item["credential_store"]["last_seq"] >= 1
                and item["credential_store"]["protocol_version"] == "1.1"
                for item in statuses.values()
            )
        )
        self.assertIsNone(broker_result["model_pid"])

        for status in statuses.values():
            composition = status["composition"]
            self.assertTrue(composition["shared_arbiter"])
            self.assertTrue(composition["admission_shared"])
            self.assertEqual(composition["backend_type"], "BrokeredStructuredLLMBackend")
            self.assertEqual(composition["brain_type"], "LLMBrain")
            self.assertEqual(
                composition["frequency_policy_type"],
                "DeterministicSpeakingFrequencyPolicy",
            )
            self.assertEqual(composition["speaking_profile"]["talkativeness"], 1.0)
            self.assertEqual(composition["short_chat"]["max_text_chars"], 80)
            self.assertGreaterEqual(status["brain"]["backend_calls"], 1)
            self.assertGreaterEqual(status["audit"]["record_count"], 1)
            self.assertEqual(status["audit"]["player_ids"], [status["player_id"]])

        backend = broker_result["backend"]
        self.assertEqual(backend["peak_active"], 1)
        self.assertEqual(backend["active_after_close"], 0)
        self.assertTrue(backend["closed"])
        self.assertEqual(broker_result["metrics_dropped"], 0)
        self.assertLessEqual(broker_result["maximum_pending"], 9)
        after = broker_result["snapshot_after_close"]
        self.assertEqual(after["pending_total"], 0)
        self.assertIsNone(after["offered"])
        self.assertIsNone(after["claimed"])
        self.assertFalse(after["provider_call_active"])
        self.assertFalse(after["draining"])
        self.assertFalse(after["poisoned"])
        self.assertEqual(
            broker_result["provider_call_terminals"], backend["call_count"]
        )
        self.assertEqual(
            backend["call_count"],
            sum(item["brain"]["backend_calls"] for item in statuses.values()),
        )
        self.assertTrue(all(item["membership_valid"] for item in backend["calls"]))
        admission_failures = [
            item
            for item in metrics
            if item["terminal_status"] in {"OVERLOADED", "POISONED"}
        ]
        failure_diagnostics = []
        client_to_player = {
            client_id: player_id
            for player_id, client_id in player_to_opaque_client_id.items()
        }
        for failure in admission_failures:
            sequence = failure["sequence"]
            client_id = failure["client_id"]
            invocation_id = failure["invocation_id"]
            player_id = client_to_player.get(client_id)
            client_status = None if player_id is None else statuses[player_id]
            failure_diagnostics.append(
                {
                    "offending_metric": failure,
                    "global_sequence_window": [
                        item
                        for item in metrics
                        if sequence - 4 <= item["sequence"] <= sequence + 4
                    ],
                    "same_client_sequence_window": [
                        item
                        for item in metrics
                        if item["client_id"] == client_id
                        and sequence - 8 <= item["sequence"] <= sequence + 8
                    ],
                    "same_invocation_sequence": [
                        item
                        for item in metrics
                        if item["invocation_id"] == invocation_id
                    ],
                    "client": None
                    if client_status is None
                    else {
                        "player_id": player_id,
                        "runtime_success": client_status["runtime_success"],
                        "runtime_exit": client_status["runtime_exit"],
                        "world_exit": client_status["world_exit"],
                        "reaction_exit": client_status["reaction_exit"],
                        "vote_ability_exit": client_status["vote_ability_exit"],
                        "brain": client_status["brain"],
                        "reaction": {
                            key: client_status["reaction"][key]
                            for key in (
                                "lifecycle",
                                "chat_brain_invocations",
                                "send_count",
                                "accepted_count",
                                "rejected_count",
                                "deadline_suppressed_count",
                                "intentional_silence_count",
                                "frequency_suppressed_count",
                            )
                        },
                        "reservation": client_status["reservation"],
                    },
                }
            )
        failure_diagnostic = {
            "admission_failures": failure_diagnostics,
            "broker": {
                key: broker_result[key]
                for key in (
                    "success",
                    "failure",
                    "shutdown_clean",
                    "snapshot_before_close",
                    "snapshot_after_close",
                    "maximum_pending",
                    "metrics_count",
                    "metrics_dropped",
                    "provider_call_terminals",
                )
            },
            "backend": {
                key: backend[key]
                for key in (
                    "call_count",
                    "peak_active",
                    "active_after_close",
                    "closed",
                )
            },
        }
        failure_payload = json.dumps(
            failure_diagnostic,
            ensure_ascii=True,
            sort_keys=True,
        )
        if admission_failures:
            diagnostic_path = (
                PROJECT_ROOT
                / "_to_delete"
                / "t055_phase5_admission_terminal.json"
            )
            temporary_diagnostic_path = diagnostic_path.with_suffix(".json.tmp")
            with temporary_diagnostic_path.open(
                "w", encoding="utf-8", newline="\n"
            ) as handle:
                handle.write(failure_payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_diagnostic_path, diagnostic_path)
        self.assertFalse(admission_failures, failure_payload)
        terminal_ids = {
            item["invocation_id"]
            for item in metrics
            if item["event"] == "TERMINAL"
        }
        call_invocations = {
            item["invocation_id"]
            for item in metrics
            if item["event"] == "PROVIDER_CALL_TERMINAL"
        }
        self.assertTrue(call_invocations <= terminal_ids)

        request_owner = {}
        for player_id, status in statuses.items():
            for request_id in status["audit"]["request_ids"]:
                self.assertNotIn(request_id, request_owner)
                request_owner[request_id] = player_id
        self.assertEqual(set(request_owner), {item["request_id"] for item in backend["calls"]})
        self.assertTrue(
            all(request_owner[item["request_id"]] == item["self_player_id"] for item in backend["calls"])
        )

        day_one_chat = [
            item
            for item in server_result["accepted_chats"]
            if item["day"] == 1 and item["phase"] == "day"
        ]
        self.assertEqual({item["player_id"] for item in day_one_chat}, set(statuses))
        self.assertTrue(
            all(
                1 <= item["message_chars"] <= 80
                and 1 <= item["message_utf8_bytes"] <= 96
                and item["accepted_at"] < item["phase_deadline"]
                for item in day_one_chat
            )
        )
        chat_counts = Counter(
            (item["player_id"], item["day"], item["phase"])
            for item in server_result["accepted_chats"]
        )
        self.assertTrue(all(count <= 2 for count in chat_counts.values()))
        self.assertTrue(
            all(
                outcome["frequency_evaluation_ordinal"] is None
                or outcome["frequency_evaluation_ordinal"] <= 32
                for status in statuses.values()
                for outcome in status["reaction"]["outcomes"]
            )
        )

        expected = {
            (item["player_id"], item["day"], item["phase"], item["action"]): item
            for item in server_result["expected_reservations"]
        }
        accepted = {}
        for item in server_result["accepted_reservations"]:
            key = (item["player_id"], item["day"], item["phase"], item["action"])
            accepted.setdefault(key, []).append(item)
        self.assertEqual(set(accepted), set(expected))
        self.assertTrue(all(len(values) == 1 for values in accepted.values()))
        for key, values in accepted.items():
            item = values[0]
            expectation = expected[key]
            self.assertTrue(item["request_event_id"])
            self.assertLess(item["accepted_at"], item["phase_deadline"])
            if item["action"] == "vote.cast":
                if item["vote_target_player_id"] is not None:
                    self.assertIn(
                        item["vote_target_player_id"], expectation["valid_targets"]
                    )
            else:
                ability = next(
                    value
                    for value in expectation["abilities"]
                    if value["ability_id"] == item["ability_id"]
                )
                self.assertEqual(
                    len(item["ability_target_player_ids"]), ability["target_count"]
                )
                self.assertTrue(
                    all(
                        target in ability["valid_targets"]
                        for target in item["ability_target_player_ids"]
                    )
                )

    def _write_manifest_atomic(
        self,
        root: Path,
        statuses,
        metrics_path: Path,
        player_to_opaque_client_id: dict[str, str],
    ) -> Path:
        shards = {}
        for player_id, status in sorted(statuses.items()):
            audit_path = root / status["audit"]["path"]
            payload = audit_path.read_bytes()
            shards[player_id] = {
                "path": status["audit"]["path"],
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "record_count": len(payload.splitlines()),
                "terminal": True,
            }
        metadata = metrics_path.read_bytes()
        manifest_value = {
            "schema": "aiwolf.phase5-private-manifest.v1",
            "player_to_opaque_client_id": dict(
                sorted(player_to_opaque_client_id.items())
            ),
            "shards": shards,
            "metadata": {
                "path": metrics_path.name,
                "bytes": len(metadata),
                "sha256": hashlib.sha256(metadata).hexdigest(),
                "record_count": len(metadata.splitlines()),
                "terminal": True,
            },
            "terminal": True,
        }
        target = root / "phase5.manifest.json"
        temporary = root / "phase5.manifest.json.tmp"
        temporary.write_text(
            json.dumps(manifest_value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, target)
        return target

    def _assert_manifest(
        self,
        root: Path,
        manifest: Path,
        statuses,
        metrics: Path,
        player_to_opaque_client_id: dict[str, str],
    ) -> None:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertTrue(value["terminal"])
        self.assertEqual(
            value["player_to_opaque_client_id"], player_to_opaque_client_id
        )
        self.assertEqual(
            set(value["player_to_opaque_client_id"].values()),
            set(player_to_opaque_client_id.values()),
        )
        self.assertEqual(set(value["shards"]), set(statuses))
        for item in (*value["shards"].values(), value["metadata"]):
            path = root / item["path"]
            payload = path.read_bytes()
            self.assertTrue(item["terminal"])
            self.assertEqual(item["bytes"], len(payload))
            self.assertEqual(item["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(item["record_count"], len(payload.splitlines()))
        self.assertEqual(value["metadata"]["path"], metrics.name)
        self.assertFalse((root / "phase5.manifest.json.tmp").exists())
