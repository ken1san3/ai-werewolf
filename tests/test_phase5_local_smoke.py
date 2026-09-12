from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ai_client.llm import GenerationPriority, GenerationSettings
from scripts import run_phase5_local_smoke as runner


class _FakeStdin:
    def __init__(self) -> None:
        self.payload = b""
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.payload += payload

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FinishedProcess:
    def __init__(self, pid: int = 321, returncode: int | None = 0) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdin = _FakeStdin()
        self.terminate_count = 0
        self.kill_count = 0

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminate_count += 1
        self.returncode = -15

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9


class _BlockingProcess(_FinishedProcess):
    def __init__(self, pid: int = 654) -> None:
        super().__init__(pid, None)
        self.terminated = asyncio.Event()

    async def wait(self) -> int:
        await self.terminated.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminate_count += 1
        self.returncode = -15
        self.terminated.set()


class _ReleasedProcess(_FinishedProcess):
    def __init__(self, pid: int = 655) -> None:
        super().__init__(pid, None)
        self.release = asyncio.Event()

    async def wait(self) -> int:
        await self.release.wait()
        self.returncode = 0
        return 0


def _args(output_dir: Path, **changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "model": "local-test-model",
        "q8": False,
        "q8_provider_timing_diagnostic": False,
        "gpu_model_pid": None,
        "provider_serving_executable_sha256": None,
        "provider_profile_sha256": None,
        "provider_profile_model": None,
        "provider_profile_args": None,
        "provider_backend_config_sha256": None,
        "provider_implementation": None,
        "provider_version": None,
        "provider_build": None,
        "provider_commit": None,
        "provider_profile_perf_enabled": None,
        "seed": 8625,
        "max_seconds": 1200.0,
        "output_dir": output_dir,
    }
    values.update(changes)
    return argparse.Namespace(**values)


class PhaseFiveRunnerContractTests(unittest.TestCase):
    @staticmethod
    def _diagnostic_cleanup() -> tuple[dict[str, object], dict[str, object]]:
        return (
            {
                "label": "broker",
                "pid": 101,
                "returncode": 0,
                "alive": False,
                "action": "wait",
            },
            {
                "label": "worker-0",
                "pid": 102,
                "returncode": 0,
                "alive": False,
                "action": "wait",
            },
        )

    @staticmethod
    def _diagnostic_metrics(
        statuses: list[str], *, timing_complete: bool
    ) -> list[dict[str, object]]:
        metrics: list[dict[str, object]] = []
        for index, status in enumerate(statuses):
            invocation = f"invocation-{index}"
            state = (
                "NOT_OBSERVED"
                if status in {"ABSENT", "NOT_OBJECT"}
                else "VALID"
            )
            metrics.append(
                {
                    "event": "PROVIDER_CALL_TERMINAL",
                    "invocation_id": invocation,
                    "backend_code": None,
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "provider_prompt_microseconds": 1000 if timing_complete else None,
                    "provider_completion_microseconds": 2000 if timing_complete else None,
                    "provider_timing_diagnostic_status": status,
                    "provider_timing_prompt_n_state": state,
                    "provider_timing_prompt_ms_state": state,
                    "provider_timing_predicted_n_state": state,
                    "provider_timing_predicted_ms_state": state,
                    "provider_timing_extra_key_count": (
                        1 if status == "VALID_WITH_EXTRA" else 0
                    ),
                }
            )
            metrics.append(
                {
                    "event": "TERMINAL",
                    "invocation_id": invocation,
                    "terminal_status": "RELEASED",
                }
            )
        return metrics

    @staticmethod
    def _route(
        statuses: list[str], *, timing_complete: bool = False, **changes: object
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "row_errors": (() if timing_complete else ("PROVIDER_TIMING_UNAVAILABLE",)),
            "metrics_dropped": 0,
            "cleanup": PhaseFiveRunnerContractTests._diagnostic_cleanup(),
            "end_identity_matches": True,
            "external_evidence_clean": True,
        }
        values.update(changes)
        return runner._diagnostic_route(
            PhaseFiveRunnerContractTests._diagnostic_metrics(
                statuses, timing_complete=timing_complete
            ),
            **values,
        )

    def test_game_plan_is_the_exact_approved_profile(self) -> None:
        self.assertEqual(
            asdict(runner.GAME_PLAN),
            {
                "preset": "standard_9",
                "clients": 9,
                "brokers": 1,
                "servers": 1,
                "day_seconds": 60,
                "vote_seconds": 45,
                "night_seconds": 45,
                "silence_after_dawn_seconds": 0,
                "hard_limit_seconds": 1200,
                "max_chat_attempts_per_phase": 2,
                "talkativeness": 1.0,
                "initial_event_importance": 1.0,
                "direct_mention_importance": 1.0,
                "ordinary_event_importance": 0.5,
                "cooldown_seconds": 0.20,
                "max_trigger_evaluations_per_phase": 32,
                "repetition_window": 8,
            },
        )

    def test_q8_plan_is_exact_and_finite(self) -> None:
        self.assertEqual([item.row for item in runner.Q8_PLAN], ["Q8-A", "Q8-B", "Q8-C", "Q8-D"])
        self.assertEqual([item.requests for item in runner.Q8_PLAN], [9, 9, 9, 1])
        self.assertEqual([item.limit_seconds for item in runner.Q8_PLAN], [120, 120, 120, 1200])
        self.assertEqual(
            runner.Q8_PLAN[2].priorities,
            (GenerationPriority.REACTION,)
            + (GenerationPriority.RESERVATION,) * 4
            + (GenerationPriority.REACTION,) * 4,
        )

    def test_prompt_buckets_are_deterministic_bounded_and_strict(self) -> None:
        sizes = []
        for bucket in ("minimum", "median", "maximum"):
            one = runner._strict_short_request(bucket, "request-a")
            two = runner._strict_short_request(bucket, "request-a")
            self.assertEqual(one, two)
            self.assertEqual(one.output_schema["properties"]["message"]["maxLength"], 80)
            sizes.append(sum(len(item.content.encode("utf-8")) for item in one.messages))
        self.assertLess(sizes[0], sizes[1])
        self.assertLess(sizes[1], sizes[2])

    def test_strict_short_response_rejects_wrong_or_over_bound_output(self) -> None:
        self.assertEqual(runner._strict_short_response('{"kind":"chat","message":"hello"}'), (5, 5))
        with self.assertRaises(ValueError):
            runner._strict_short_response('{"kind":"none"}')
        with self.assertRaises(ValueError):
            runner._strict_short_response(json.dumps({"kind": "chat", "message": "x" * 81}))

    def test_phase5_broker_profile_is_96_tokens_and_q8_rejects_length(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            config = runner._prepare_run(_args(Path(temporary) / "new-output"), {})
            bootstrap = runner._broker_bootstrap(
                config, {f"opaque-{index}": f"token-{index}" for index in range(9)}, "seed"
            )
            broker_settings = runner._phase5_broker_settings(bootstrap)
        self.assertEqual(config.settings.generation.max_output_tokens, 96)
        self.assertEqual(broker_settings.backend_config().generation.max_output_tokens, 96)
        self.assertEqual(GenerationSettings().max_output_tokens, 128)
        complete = '{"kind":"chat","message":"hello"}'
        self.assertEqual(
            runner._strict_q8_response(SimpleNamespace(text=complete, finish_reason="stop")),
            (5, 5),
        )
        with self.assertRaisesRegex(ValueError, "length limit"):
            runner._strict_q8_response(
                SimpleNamespace(text=complete, finish_reason="length")
            )

    def test_nearest_rank_uses_integer_samples(self) -> None:
        values = [50, 10, 40, 20, 30]
        self.assertEqual(runner.nearest_rank(values, 0.5), 30)
        self.assertEqual(runner.nearest_rank(values, 0.95), 50)
        self.assertIsNone(runner.nearest_rank([], 0.95))
        with self.assertRaises(ValueError):
            runner.nearest_rank([1, -1], 0.5)

    def test_metric_aggregation_includes_row_priority_client_and_timing(self) -> None:
        metrics = [
            {
                "event": "ENQUEUED",
                "invocation_id": "i",
                "client_id": "opaque",
                "priority": 0,
                "monotonic_microseconds": 10,
            },
            {
                "event": "OFFERED",
                "invocation_id": "i",
                "client_id": "opaque",
                "priority": 0,
                "queue_wait_microseconds": 7,
                "monotonic_microseconds": 17,
            },
            {"event": "CLAIMED", "invocation_id": "i", "monotonic_microseconds": 20},
            {
                "event": "PROVIDER_CALL_TERMINAL",
                "invocation_id": "i",
                "backend_code": None,
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "provider_prompt_microseconds": 10,
                "provider_completion_microseconds": 20,
                "monotonic_microseconds": 30,
            },
            {"event": "TERMINAL", "invocation_id": "i", "terminal_status": "RELEASED", "monotonic_microseconds": 40},
        ]
        result = runner._metric_aggregates(metrics)
        self.assertEqual(result["queue_enqueue_to_offer_microseconds"]["median"], 7)
        self.assertEqual(result["queue_enqueue_to_claim_microseconds"]["median"], 10)
        self.assertEqual(result["request_end_to_end_microseconds"]["median"], 30)
        self.assertEqual(
            result["queue_by_priority"]["0"]["enqueue_to_offer_microseconds"]["count"],
            1,
        )
        self.assertEqual(
            result["queue_by_opaque_client"]["opaque"]["enqueue_to_claim_microseconds"]["count"],
            1,
        )
        self.assertEqual(result["provider_prompt_tokens"]["median"], 20)
        self.assertEqual(result["provider_completion_tokens"]["median"], 10)
        self.assertTrue(result["provider_timing_complete"])

    def test_q8_provider_timing_absence_is_explicit(self) -> None:
        result = runner._metric_aggregates(
            [{"event": "PROVIDER_CALL_TERMINAL", "backend_code": None}]
        )
        self.assertFalse(result["provider_timing_complete"])

    def test_gpu_parser_uses_only_the_exact_pid(self) -> None:
        output = "100, 200\n101, 300\n100, 25\ngarbage\n"
        self.assertEqual(runner._parse_nvidia_smi(output, 100), 225)
        self.assertIsNone(runner._parse_nvidia_smi(output, 999))

    def test_prepare_validates_before_output_creation_and_sanitizes_arguments(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            target = Path(temporary) / "new-output"
            config = runner._prepare_run(
                _args(target),
                {"AIWOLF_LLM_API_KEY": "private-api-key"},
            )
            self.assertFalse(target.exists())
            self.assertEqual(config.settings.api_key, "private-api-key")
            self.assertNotIn("private-api-key", json.dumps(config.sanitized_arguments))
            self.assertEqual(config.max_seconds, 1200.0)

    def test_prepare_rejects_existing_path_bad_bound_and_own_gpu_pid(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            existing = Path(temporary)
            with self.assertRaises(FileExistsError):
                runner._prepare_run(_args(existing), {})
            with self.assertRaises(ValueError):
                runner._prepare_run(_args(existing / "a", max_seconds=1200.1), {})
            with self.assertRaises(ValueError):
                runner._prepare_run(_args(existing / "b", gpu_model_pid=os.getpid()), {})

    def test_diagnostic_preflight_binds_exact_executable_profile_backend_and_identity(self) -> None:
        output = runner.PROJECT_ROOT / "t076-preflight-output-must-not-exist"
        self.assertFalse(output.exists())
        base = runner._prepare_run(_args(output), {})
        profile_args = "-ngl 99 -c 8192 --jinja"
        arguments = _args(
            output,
            q8_provider_timing_diagnostic=True,
            gpu_model_pid=777,
            provider_serving_executable_sha256=runner._sha256_file(runner._SCRIPT),
            provider_profile_sha256=runner._canonical_profile_fingerprint(
                "local-test-model", profile_args
            ),
            provider_profile_model="local-test-model",
            provider_profile_args=profile_args,
            provider_backend_config_sha256=base.settings.backend_config().config_fingerprint,
            provider_implementation="llama.cpp",
            provider_version="0.3.0-dev",
            provider_build=10697,
            provider_commit="093adb242",
            provider_profile_perf_enabled=False,
        )
        identity = runner.ProviderBuildIdentity(
            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
        )
        with (
            patch.object(
                runner, "_resolve_process_executable", return_value=runner._SCRIPT
            ),
            patch.object(
                runner, "_read_provider_build_identity", return_value=identity
            ),
        ):
            config = runner._prepare_run(arguments, {})
        self.assertFalse(output.exists())
        self.assertTrue(config.q8_provider_timing_diagnostic)
        self.assertFalse(config.q8)
        environment = config.diagnostic_environment
        self.assertEqual(environment.serving_pid, 777)
        self.assertEqual(environment.serving_executable, runner._SCRIPT)
        self.assertEqual(environment.identity, identity)
        self.assertEqual(
            environment.identity_binding_sha256,
            runner._provider_identity_binding_sha256(
                environment.serving_executable_sha256, identity
            ),
        )
        self.assertFalse(environment.provider_profile_perf_enabled)
        retained = json.dumps(config.sanitized_arguments)
        self.assertNotIn(profile_args, retained)
        self.assertNotIn(str(runner._SCRIPT), retained)

    def test_diagnostic_argv_round_trips_exact_lifecycle_facts_before_output(self) -> None:
        output = runner.PROJECT_ROOT / "t094-argv-output-must-not-exist"
        self.assertFalse(output.exists())
        base = runner._prepare_run(_args(output), {})
        serving_sha256 = "ab" * 32
        identity = runner.ProviderBuildIdentity(
            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
        )
        for perf_enabled in (False, True):
            with self.subTest(perf_enabled=perf_enabled):
                profile_args = "--ctx-size 8192 --jinja"
                if perf_enabled:
                    profile_args += " --perf"
                profile_sha256 = runner._canonical_profile_fingerprint(
                    "local-test-model", profile_args
                )
                argv = runner._build_q8_provider_timing_diagnostic_argv(
                    endpoint="http://127.0.0.1:8080/v1/chat/completions",
                    model="local-test-model",
                    output_dir=output,
                    seed=8625,
                    max_seconds=1200.0,
                    gpu_model_pid=777,
                    provider_serving_executable_sha256=serving_sha256,
                    provider_profile_sha256=profile_sha256,
                    provider_profile_model="local-test-model",
                    provider_profile_args=profile_args,
                    provider_backend_config_sha256=(
                        base.settings.backend_config().config_fingerprint
                    ),
                    provider_identity=identity,
                    provider_profile_perf_enabled=perf_enabled,
                )
                self.assertEqual(argv[:2], (sys.executable, str(runner._SCRIPT)))
                self.assertIn(f"--provider-profile-args={profile_args}", argv)
                self.assertNotIn(profile_args, argv)
                self.assertIn(
                    "--provider-profile-perf-enabled"
                    if perf_enabled
                    else "--no-provider-profile-perf-enabled",
                    argv,
                )

                parsed = runner._parser().parse_args(argv[2:])
                self.assertEqual(parsed.endpoint, _args(output).endpoint)
                self.assertEqual(parsed.model, "local-test-model")
                self.assertEqual(parsed.output_dir, output)
                self.assertEqual(parsed.seed, 8625)
                self.assertEqual(parsed.max_seconds, 1200.0)
                self.assertEqual(parsed.gpu_model_pid, 777)
                self.assertEqual(
                    parsed.provider_serving_executable_sha256, serving_sha256
                )
                self.assertEqual(parsed.provider_profile_sha256, profile_sha256)
                self.assertEqual(parsed.provider_profile_model, "local-test-model")
                self.assertEqual(parsed.provider_profile_args, profile_args)
                self.assertEqual(
                    parsed.provider_backend_config_sha256,
                    base.settings.backend_config().config_fingerprint,
                )
                self.assertEqual(parsed.provider_implementation, identity.implementation)
                self.assertEqual(parsed.provider_version, identity.version)
                self.assertEqual(parsed.provider_build, identity.build)
                self.assertEqual(parsed.provider_commit, identity.commit)
                self.assertIs(parsed.provider_profile_perf_enabled, perf_enabled)
                with (
                    patch.object(
                        runner,
                        "_resolve_process_executable",
                        return_value=runner._SCRIPT,
                    ) as resolver,
                    patch.object(
                        runner, "_sha256_file", return_value=serving_sha256
                    ) as hasher,
                    patch.object(
                        runner, "_read_provider_build_identity", return_value=identity
                    ) as identity_reader,
                ):
                    config = runner._prepare_run(parsed, {})
                self.assertFalse(output.exists())
                self.assertTrue(config.q8_provider_timing_diagnostic)
                self.assertFalse(config.q8)
                self.assertEqual(config.diagnostic_environment.identity, identity)
                self.assertEqual(
                    config.diagnostic_environment.serving_executable_sha256,
                    serving_sha256,
                )
                self.assertEqual(
                    config.diagnostic_environment.profile_sha256, profile_sha256
                )
                self.assertEqual(
                    config.diagnostic_environment.backend_config_sha256,
                    base.settings.backend_config().config_fingerprint,
                )
                self.assertIs(
                    config.diagnostic_environment.provider_profile_perf_enabled,
                    perf_enabled,
                )
                resolver.assert_called_once_with(777)
                self.assertEqual(hasher.call_count, 2)
                identity_reader.assert_called_once_with(runner._SCRIPT)

    def test_diagnostic_argv_rejects_every_malformed_fingerprint_before_output(self) -> None:
        output = runner.PROJECT_ROOT / "t094-bad-argv-output-must-not-exist"
        self.assertFalse(output.exists())
        valid_sha256 = "ab" * 32
        identity = runner.ProviderBuildIdentity(
            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
        )
        base = runner._prepare_run(_args(output), {})
        values: dict[str, object] = {
            "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
            "model": "local-test-model",
            "output_dir": output,
            "seed": 8625,
            "max_seconds": 1200.0,
            "gpu_model_pid": 777,
            "provider_serving_executable_sha256": valid_sha256,
            "provider_profile_sha256": valid_sha256,
            "provider_profile_model": "local-test-model",
            "provider_profile_args": "--ctx-size 8192 --jinja",
            "provider_backend_config_sha256": (
                base.settings.backend_config().config_fingerprint
            ),
            "provider_identity": identity,
            "provider_profile_perf_enabled": False,
        }
        malformed = {
            "length_63": valid_sha256[:-1],
            "length_65": valid_sha256 + "a",
            "duplicated_nibble": valid_sha256[:17] + valid_sha256[16:],
            "uppercase": valid_sha256[:1].upper() + valid_sha256[1:],
            "whitespace": valid_sha256[:32] + " " + valid_sha256[32:],
            "non_hex": valid_sha256[:-1] + "g",
        }
        fingerprint_names = (
            "provider_serving_executable_sha256",
            "provider_profile_sha256",
            "provider_backend_config_sha256",
        )
        with (
            patch.object(runner, "_resolve_process_executable") as resolver,
            patch.object(runner, "_sha256_file") as hasher,
            patch.object(runner, "_read_provider_build_identity") as identity_reader,
            patch.object(runner.subprocess, "Popen") as process_factory,
        ):
            for fingerprint_name in fingerprint_names:
                for mutation_name, malformed_value in malformed.items():
                    with self.subTest(
                        fingerprint=fingerprint_name, mutation=mutation_name
                    ):
                        changed = {**values, fingerprint_name: malformed_value}
                        with self.assertRaises(
                            runner.ProviderTimingDiagnosticPreflightError
                        ) as raised:
                            runner._build_q8_provider_timing_diagnostic_argv(**changed)
                        self.assertEqual(
                            str(raised.exception), runner._DIAGNOSTIC_INCOMPLETE
                        )
                        self.assertFalse(output.exists())
            with self.assertRaises(
                runner.ProviderTimingDiagnosticPreflightError
            ) as raised:
                runner._build_q8_provider_timing_diagnostic_argv(
                    **{**values, "provider_profile_perf_enabled": None}
                )
            self.assertEqual(str(raised.exception), runner._DIAGNOSTIC_INCOMPLETE)
            self.assertFalse(output.exists())
            resolver.assert_not_called()
            hasher.assert_not_called()
            identity_reader.assert_not_called()
            process_factory.assert_not_called()

    def test_diagnostic_preflight_rejects_all_identity_mismatches_before_output(self) -> None:
        output = runner.PROJECT_ROOT / "t076-bad-preflight-output-must-not-exist"
        base = runner._prepare_run(_args(output), {})
        profile_args = "-ngl 99 --perf"
        valid = {
            "q8_provider_timing_diagnostic": True,
            "gpu_model_pid": 777,
            "provider_serving_executable_sha256": runner._sha256_file(runner._SCRIPT),
            "provider_profile_sha256": runner._canonical_profile_fingerprint(
                "local-test-model", profile_args
            ),
            "provider_profile_model": "local-test-model",
            "provider_profile_args": profile_args,
            "provider_backend_config_sha256": base.settings.backend_config().config_fingerprint,
            "provider_implementation": "llama.cpp",
            "provider_version": "0.3.0-dev",
            "provider_build": 10697,
            "provider_commit": "093adb242",
            "provider_profile_perf_enabled": True,
        }
        cases = (
            {"provider_serving_executable_sha256": "0" * 64},
            {"provider_profile_sha256": "0" * 64},
            {"provider_backend_config_sha256": "0" * 64},
            {"provider_implementation": "bad identity value"},
            {"provider_version": ""},
            {"provider_build": True},
            {"provider_commit": "NOTHEX"},
            {"provider_profile_model": "wrong-model"},
            {"provider_profile_perf_enabled": False},
            {"provider_serving_executable_sha256": None},
            {"gpu_model_pid": None},
        )
        for change in cases:
            with self.subTest(change=change):
                with (
                    patch.object(
                        runner,
                        "_resolve_process_executable",
                        return_value=runner._SCRIPT,
                    ),
                    patch.object(
                        runner,
                        "_read_provider_build_identity",
                        return_value=runner.ProviderBuildIdentity(
                            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
                        ),
                    ),
                ):
                    with self.assertRaises(
                        runner.ProviderTimingDiagnosticPreflightError
                    ):
                        runner._prepare_run(_args(output, **{**valid, **change}), {})
                self.assertFalse(output.exists())
        with patch.object(
            runner,
            "_resolve_process_executable",
            side_effect=runner.ProviderTimingDiagnosticPreflightError(
                "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE"
            ),
        ):
            with self.assertRaises(runner.ProviderTimingDiagnosticPreflightError):
                runner._prepare_run(_args(output, **valid), {})
        self.assertFalse(output.exists())

    def test_provider_identity_reader_uses_fixed_bounded_private_command(self) -> None:
        class _VersionProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(
                    b"llama.cpp 0.3.0-dev, build 10697, commit 093adb242\n"
                    b"Clang 20.1.8, Windows x86_64\n"
                )
                self.returncode: int | None = None

            def wait(self, *, timeout: float) -> int:
                self.returncode = 0
                return 0

            def kill(self) -> None:
                self.returncode = -9

        observed: dict[str, object] = {}

        def factory(argv: tuple[str, str], **kwargs: object) -> _VersionProcess:
            observed["argv"] = argv
            observed.update(kwargs)
            return _VersionProcess()

        executable = Path("C:/private/provider/llama-server.exe")
        result = runner._run_bounded_provider_version(
            executable,
            process_factory=factory,
            environ={
                "SystemRoot": "C:/Windows",
                "AIWOLF_LLM_API_KEY": "raw-private-api-key",
                "PROMPT_SENTINEL": "raw-private-prompt",
            },
        )
        identity = runner._read_provider_build_identity(
            executable, command_runner=lambda _path: result
        )

        self.assertEqual(observed["argv"], (str(executable), "--version"))
        self.assertIs(observed["stdin"], runner.subprocess.DEVNULL)
        self.assertIs(observed["stdout"], runner.subprocess.PIPE)
        self.assertIs(observed["stderr"], runner.subprocess.STDOUT)
        self.assertIs(observed["shell"], False)
        self.assertEqual(observed["env"], {"SystemRoot": "C:/Windows"})
        self.assertEqual(
            identity,
            runner.ProviderBuildIdentity(
                "llama.cpp", "0.3.0-dev", 10697, "093adb242"
            ),
        )

    def test_provider_identity_reader_accepts_only_two_exact_first_line_grammars(
        self,
    ) -> None:
        expected = runner.ProviderBuildIdentity(
            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
        )

        def read(payload: bytes) -> runner.ProviderBuildIdentity:
            return runner._read_provider_build_identity(
                Path("unused"),
                command_runner=lambda _path: runner._BoundedProviderVersionOutput(
                    0, payload, False, False
                ),
            )

        accepted = (
            b"llama.cpp 0.3.0-dev, build 10697, commit 093adb242\n",
            b"version: 0.3.0-dev (build 10697, commit 093adb242)\n",
        )
        self.assertEqual(
            tuple(read(payload) for payload in accepted), (expected, expected)
        )

        raw_sentinel = b"RAW-VERSION-PRIVATE-SENTINEL"
        rejected = (
            ("missing field", b"version: 0.3.0-dev (build 10697)\n"),
            (
                "uppercase commit",
                b"version: 0.3.0-dev (build 10697, commit 093ADB242)\n",
            ),
            (
                "trailing text",
                b"version: 0.3.0-dev (build 10697, commit 093adb242) trailing\n",
            ),
            (
                "leading banner",
                b"provider banner\n"
                b"version: 0.3.0-dev (build 10697, commit 093adb242)\n",
            ),
            (
                "invalid version token",
                b"version: invalid/version (build 10697, commit 093adb242)\n",
            ),
            ("raw sentinel", raw_sentinel),
        )
        for case, payload in rejected:
            with self.subTest(case=case):
                with self.assertRaises(
                    runner.ProviderTimingDiagnosticPreflightError
                ) as raised:
                    read(payload)
                self.assertEqual(str(raised.exception), runner._DIAGNOSTIC_INCOMPLETE)
                self.assertNotIn(raw_sentinel.decode("ascii"), repr(raised.exception))

    def test_provider_identity_reader_fails_closed_without_raw_output(self) -> None:
        raw_sentinel = b"RAW-VERSION-PRIVATE-SENTINEL"
        cases = (
            runner._BoundedProviderVersionOutput(1, raw_sentinel, False, False),
            runner._BoundedProviderVersionOutput(0, raw_sentinel, False, False),
            runner._BoundedProviderVersionOutput(0, raw_sentinel, True, False),
            runner._BoundedProviderVersionOutput(0, raw_sentinel, False, True),
            runner._BoundedProviderVersionOutput(
                0,
                b"llama.cpp bad identity, build 10697, commit 093adb242\n",
                False,
                False,
            ),
        )
        for result in cases:
            with self.subTest(result=result):
                with self.assertRaises(
                    runner.ProviderTimingDiagnosticPreflightError
                ) as raised:
                    runner._read_provider_build_identity(
                        Path("C:/private/provider/llama-server.exe"),
                        command_runner=lambda _path, value=result: value,
                    )
                self.assertEqual(str(raised.exception), runner._DIAGNOSTIC_INCOMPLETE)
                self.assertNotIn(raw_sentinel.decode("ascii"), repr(raised.exception))
                self.assertNotIn("llama-server", repr(raised.exception))

        oversized = runner._BoundedProviderVersionOutput(
            0,
            b"x" * (runner._PROVIDER_VERSION_MAX_BYTES + 1),
            False,
            False,
        )
        with self.assertRaises(runner.ProviderTimingDiagnosticPreflightError):
            runner._read_provider_build_identity(
                Path("unused"), command_runner=lambda _path: oversized
            )

    def test_provider_identity_command_timeout_and_output_bound_are_injected(self) -> None:
        class _VersionProcess:
            def __init__(self, output: bytes, *, timeout: bool) -> None:
                self.stdout = io.BytesIO(output)
                self.timeout = timeout
                self.wait_count = 0
                self.kill_count = 0
                self.returncode: int | None = None

            def wait(self, *, timeout: float) -> int:
                self.wait_count += 1
                if self.timeout and self.wait_count == 1:
                    raise runner.subprocess.TimeoutExpired("private-command", timeout)
                self.returncode = -9 if self.kill_count else 0
                return self.returncode

            def kill(self) -> None:
                self.kill_count += 1

        timed_out = _VersionProcess(b"private-timeout-output", timeout=True)
        timeout_result = runner._run_bounded_provider_version(
            Path("C:/private/provider/llama-server.exe"),
            process_factory=lambda *_args, **_kwargs: timed_out,
            environ={},
        )
        self.assertTrue(timeout_result.timed_out)
        self.assertEqual(timed_out.kill_count, 1)

        oversized = _VersionProcess(
            b"x" * (runner._PROVIDER_VERSION_MAX_BYTES + 1), timeout=False
        )
        oversized_result = runner._run_bounded_provider_version(
            Path("C:/private/provider/llama-server.exe"),
            process_factory=lambda *_args, **_kwargs: oversized,
            environ={},
        )
        self.assertTrue(oversized_result.oversized)
        self.assertEqual(
            len(oversized_result.output), runner._PROVIDER_VERSION_MAX_BYTES
        )

    def test_diagnostic_preflight_rejects_alternate_allowlist_valid_identity(self) -> None:
        output = runner.PROJECT_ROOT / "t084-identity-output-must-not-exist"
        base = runner._prepare_run(_args(output), {})
        profile_args = "-ngl 99 -c 8192 --jinja"
        arguments = _args(
            output,
            q8_provider_timing_diagnostic=True,
            gpu_model_pid=777,
            provider_serving_executable_sha256=runner._sha256_file(runner._SCRIPT),
            provider_profile_sha256=runner._canonical_profile_fingerprint(
                "local-test-model", profile_args
            ),
            provider_profile_model="local-test-model",
            provider_profile_args=profile_args,
            provider_backend_config_sha256=base.settings.backend_config().config_fingerprint,
            provider_implementation="llama.cpp",
            provider_version="fabricated-valid-token",
            provider_build=10697,
            provider_commit="093adb242",
            provider_profile_perf_enabled=False,
        )
        with self.assertRaises(
            runner.ProviderTimingDiagnosticPreflightError
        ) as raised:
            runner._prepare_diagnostic_environment(
                arguments,
                base.settings,
                process_path_resolver=lambda _pid: runner._SCRIPT,
                identity_reader=lambda _path: runner.ProviderBuildIdentity(
                    "llama.cpp", "0.3.0-dev", 10697, "093adb242"
                ),
            )
        self.assertEqual(str(raised.exception), runner._DIAGNOSTIC_INCOMPLETE)
        self.assertFalse(output.exists())

    def test_diagnostic_mode_is_mutually_exclusive_and_parser_exposes_exact_mode(self) -> None:
        parser = runner._parser()
        parsed = parser.parse_args(["--q8-provider-timing-diagnostic"])
        self.assertTrue(parsed.q8_provider_timing_diagnostic)
        self.assertFalse(parsed.q8)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--q8", "--q8-provider-timing-diagnostic"])

    def test_diagnostic_routing_covers_all_post_completeness_branches(self) -> None:
        cases = (
            ("exact_complete", ["VALID_EXACT"] * 9, True, "PASS", "RUN_STANDARD_Q8"),
            (
                "exact_incomplete",
                ["VALID_EXACT"] * 9,
                False,
                "PROVIDER_TIMING_UNAVAILABLE",
                "REPAIR_PROVIDER_TIMING_PROPAGATION",
            ),
            (
                "extra_complete",
                ["VALID_WITH_EXTRA"] * 9,
                True,
                "PASS",
                "RUN_STANDARD_Q8",
            ),
            (
                "extra_incomplete",
                ["VALID_WITH_EXTRA"] * 9,
                False,
                "PROVIDER_TIMING_UNAVAILABLE",
                "REPAIR_ADAPTER_REQUIRED_SUBSET",
            ),
            (
                "mixed_complete",
                ["VALID_EXACT"] * 4 + ["VALID_WITH_EXTRA"] * 5,
                True,
                "PASS",
                "RUN_STANDARD_Q8",
            ),
            (
                "mixed_incomplete",
                ["VALID_EXACT"] * 4 + ["VALID_WITH_EXTRA"] * 5,
                False,
                "PROVIDER_TIMING_UNAVAILABLE",
                "REPAIR_ADAPTER_REQUIRED_SUBSET",
            ),
        )
        for name, statuses, timing_complete, expected_result, expected_route in cases:
            with self.subTest(name=name):
                result = self._route(statuses, timing_complete=timing_complete)
                self.assertEqual(result["result"], expected_result)
                self.assertEqual(result["route"], expected_route)
        extra = self._route(["VALID_WITH_EXTRA"] * 9, timing_complete=True)
        self.assertEqual(extra["extra_key_count"], {"count": 9, "min": 1, "max": 1})
        mixed = self._route(
            ["VALID_EXACT"] * 4 + ["VALID_WITH_EXTRA"] * 5,
            timing_complete=True,
        )
        self.assertEqual(mixed["extra_key_count"], {"count": 9, "min": 0, "max": 1})
        absent = self._route(["ABSENT"] * 9)
        self.assertEqual(absent["route"], "REVIEW_HOST_PROFILE_PERF")
        incompatible = self._route(["ABSENT"] * 8 + ["NOT_OBJECT"])
        self.assertEqual(
            incompatible["route"], "REVIEW_PROVIDER_CAPABILITY_OR_UPGRADE"
        )

    def test_diagnostic_completeness_has_precedence_over_every_shape(self) -> None:
        statuses = ["VALID_EXACT"] * 4 + ["VALID_WITH_EXTRA"] * 5

        exact_true = self._route(statuses, timing_complete=True)
        self.assertIs(exact_true["complete"], True)
        self.assertEqual(exact_true["result"], "PASS")
        self.assertEqual(exact_true["route"], "RUN_STANDARD_Q8")

        def assert_incomplete(
            metrics: list[dict[str, object]], **changes: object
        ) -> None:
            values: dict[str, object] = {
                "row_errors": (),
                "metrics_dropped": 0,
                "cleanup": self._diagnostic_cleanup(),
                "end_identity_matches": True,
                "external_evidence_clean": True,
            }
            values.update(changes)
            result = runner._diagnostic_route(metrics, **values)
            self.assertIs(result["complete"], False)
            self.assertEqual(
                result["result"], "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE"
            )
            self.assertIsNone(result["route"])

        argument_mutations = {
            "structural_row_error": {"row_errors": ("Q8 worker failed",)},
            "metrics_dropped_missing": {"metrics_dropped": None},
            "metrics_dropped_nonzero": {"metrics_dropped": 1},
            "metrics_dropped_string": {"metrics_dropped": "0"},
            "metrics_dropped_bool": {"metrics_dropped": False},
            "cleanup_unclean": {"cleanup": ({"alive": True},)},
            "end_identity_mismatch": {"end_identity_matches": False},
            "external_evidence_unclean": {"external_evidence_clean": False},
        }
        for name, changes in argument_mutations.items():
            with self.subTest(name=name):
                assert_incomplete(
                    self._diagnostic_metrics(statuses, timing_complete=True),
                    **changes,
                )

        boolean_lookalikes: tuple[object, ...] = (
            1,
            "true",
            [True],
            {"value": True},
        )
        for argument in ("end_identity_matches", "external_evidence_clean"):
            for value in boolean_lookalikes:
                with self.subTest(argument=argument, lookalike_type=type(value).__name__):
                    assert_incomplete(
                        self._diagnostic_metrics(statuses, timing_complete=True),
                        **{argument: value},
                    )

        metric_mutations = (
            "call_count",
            "call_id_missing",
            "call_id_duplicate",
            "backend_failure",
            "diagnostic_malformed",
            "terminal_count",
            "terminal_id_missing",
            "terminal_id_duplicate",
            "terminal_id_mismatch",
            "terminal_not_released",
        )
        for mutation in metric_mutations:
            with self.subTest(mutation=mutation):
                metrics = self._diagnostic_metrics(statuses, timing_complete=True)
                calls = [
                    item
                    for item in metrics
                    if item["event"] == "PROVIDER_CALL_TERMINAL"
                ]
                terminals = [item for item in metrics if item["event"] == "TERMINAL"]
                if mutation == "call_count":
                    metrics.remove(calls[-1])
                elif mutation == "call_id_missing":
                    calls[0]["invocation_id"] = None
                elif mutation == "call_id_duplicate":
                    calls[-1]["invocation_id"] = calls[0]["invocation_id"]
                elif mutation == "backend_failure":
                    calls[0]["backend_code"] = "MODEL_ERROR"
                elif mutation == "diagnostic_malformed":
                    del calls[0]["provider_timing_prompt_n_state"]
                elif mutation == "terminal_count":
                    metrics.remove(terminals[-1])
                elif mutation == "terminal_id_missing":
                    terminals[0]["invocation_id"] = None
                elif mutation == "terminal_id_duplicate":
                    terminals[-1]["invocation_id"] = terminals[0]["invocation_id"]
                elif mutation == "terminal_id_mismatch":
                    terminals[-1]["invocation_id"] = "different-invocation"
                elif mutation == "terminal_not_released":
                    terminals[0]["terminal_status"] = "FAILED"
                assert_incomplete(metrics)

    def test_diagnostic_cleanup_requires_exact_owned_terminal_shape(self) -> None:
        valid_cleanup = self._diagnostic_cleanup()
        positive = self._route(["ABSENT"] * 9, cleanup=valid_cleanup)
        self.assertTrue(positive["complete"])
        self.assertTrue(positive["cleanup_clean"])
        self.assertEqual(positive["route"], "REVIEW_HOST_PROFILE_PERF")

        broker, worker = valid_cleanup
        invalid_cleanups = {
            "missing_record": (broker,),
            "duplicate_label": (broker, {**worker, "label": "broker"}),
            "wrong_label": (broker, {**worker, "label": "worker-1"}),
            "missing_label": (
                broker,
                {key: value for key, value in worker.items() if key != "label"},
            ),
            "duplicate_pid": (broker, {**worker, "pid": broker["pid"]}),
            "invalid_pid_bool": (broker, {**worker, "pid": True}),
            "invalid_pid_zero": (broker, {**worker, "pid": 0}),
            "nonzero_returncode": (broker, {**worker, "returncode": 7}),
            "malformed_returncode": (broker, {**worker, "returncode": False}),
            "non_false_alive": (broker, {**worker, "alive": True}),
            "malformed_alive": (broker, {**worker, "alive": 0}),
            "wrong_action": (broker, {**worker, "action": "terminate"}),
            "missing_action": (
                broker,
                {key: value for key, value in worker.items() if key != "action"},
            ),
        }
        for mutation, cleanup in invalid_cleanups.items():
            with self.subTest(mutation=mutation):
                result = self._route(["ABSENT"] * 9, cleanup=cleanup)
                self.assertFalse(result["complete"])
                self.assertFalse(result["cleanup_clean"])
                self.assertEqual(
                    result["result"], "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE"
                )
                self.assertIsNone(result["route"])

    def test_diagnostic_malformed_missing_and_duplicate_metrics_fail_closed(self) -> None:
        metrics = self._diagnostic_metrics(["ABSENT"] * 9, timing_complete=False)
        calls = [item for item in metrics if item["event"] == "PROVIDER_CALL_TERMINAL"]
        calls[0]["provider_timing_prompt_n_state"] = None
        result = runner._diagnostic_route(
            metrics,
            row_errors=("PROVIDER_TIMING_UNAVAILABLE",),
            metrics_dropped=0,
            cleanup=self._diagnostic_cleanup(),
            end_identity_matches=True,
            external_evidence_clean=True,
        )
        self.assertEqual(result["result"], "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE")
        metrics = self._diagnostic_metrics(["ABSENT"] * 8, timing_complete=False)
        result = runner._diagnostic_route(
            metrics,
            row_errors=("PROVIDER_TIMING_UNAVAILABLE",),
            metrics_dropped=0,
            cleanup=self._diagnostic_cleanup(),
            end_identity_matches=True,
            external_evidence_clean=True,
        )
        self.assertEqual(result["result"], "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE")
        metrics = self._diagnostic_metrics(["ABSENT"] * 9, timing_complete=False)
        calls = [item for item in metrics if item["event"] == "PROVIDER_CALL_TERMINAL"]
        calls[-1]["invocation_id"] = calls[0]["invocation_id"]
        result = runner._diagnostic_route(
            metrics,
            row_errors=("PROVIDER_TIMING_UNAVAILABLE",),
            metrics_dropped=0,
            cleanup=self._diagnostic_cleanup(),
            end_identity_matches=True,
            external_evidence_clean=True,
        )
        self.assertEqual(result["result"], "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE")

    def test_child_environment_scrubs_all_runner_credentials(self) -> None:
        child = runner._child_environment(
            {
                "PATH": "kept",
                "AIWOLF_ENTRY_TOKEN": "entry-secret",
                "AIWOLF_ADMISSION_TOKEN": "admission-secret",
                "AIWOLF_LLM_API_KEY": "api-secret",
            }
        )
        self.assertEqual(child, {"PATH": "kept"})

    def test_private_json_and_jsonl_are_atomic(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            one = root / "one.json"
            two = root / "two.jsonl"
            runner._write_private_json_atomic(one, {"ok": True})
            runner._write_private_jsonl_atomic(two, ({"n": 1}, {"n": 2}))
            self.assertEqual(json.loads(one.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(len(two.read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse((root / "one.json.tmp").exists())
            self.assertFalse((root / "two.jsonl.tmp").exists())
            self.assertTrue(one.stat().st_mode & stat.S_IWRITE)

    def test_manifest_uses_opaque_shard_layout_and_exact_hashes(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            ai_dir = Path(temporary) / "ai"
            ai_dir.mkdir()
            shard = ai_dir / "opaque"
            shard.mkdir()
            audit = shard / "ai.jsonl"
            audit.write_text('{"record":1}\n', encoding="utf-8")
            metrics = ai_dir / "admission.jsonl"
            metrics.write_text('{"metric":1}\n', encoding="utf-8")
            manifest = runner._write_manifest(
                ai_dir,
                {"player-0": {"audit": {"path": "opaque/ai.jsonl"}}},
                metrics,
                {"player-0": "opaque"},
            )
            self.assertEqual(runner._validate_manifest(manifest, ai_dir), [])
            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(value["player_to_opaque_client_id"], {"player-0": "opaque"})
            self.assertNotIn("token", json.dumps(value).lower())

    def test_game_result_validation_checks_authority_privacy_and_cleanup(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            ai_dir = root / "ai"
            ai_dir.mkdir()
            statuses = {}
            mapping = {}
            owned = []
            chats = []
            expected = []
            accepted = []
            metrics = []
            for index in range(9):
                player = f"player-{index}"
                opaque = f"opaque-{index}"
                mapping[player] = opaque
                shard = ai_dir / opaque
                shard.mkdir()
                audit_path = shard / "ai.jsonl"
                audit_path.write_text('{"audit":true}\n', encoding="utf-8")
                statuses[player] = {
                    "pid": 1000 + index,
                    "player_id": player,
                    "runtime_success": True,
                    "runtime_exit": "GAME_ENDED",
                    "world_exit": "CLIENT_ENDED",
                    "reaction_exit": "WORLD_ENDED",
                    "vote_ability_exit": "WORLD_ENDED",
                    "brain": {"backend_calls": 1},
                    "audit": {
                        "path": f"{opaque}/ai.jsonl",
                        "record_count": 1,
                        "request_ids": [f"audit-request-{index}"],
                        "player_ids": [player],
                        "membership_valid": True,
                    },
                }
                chats.append(
                    {
                        "player_id": player,
                        "day": 1,
                        "phase": "day",
                        "message_chars": 5,
                        "message_utf8_bytes": 5,
                        "accepted_at": 1.0,
                        "phase_deadline": 2.0,
                    }
                )
                if index < 8:
                    expected.append(
                        {
                            "player_id": player,
                            "day": 1,
                            "phase": "vote",
                            "action": "vote.cast",
                            "valid_targets": [f"target-{index}"],
                            "target_count": 1,
                            "abilities": [],
                        }
                    )
                    accepted.append(
                        {
                            "player_id": player,
                            "day": 1,
                            "phase": "vote",
                            "action": "vote.cast",
                            "ability_id": None,
                            "vote_target_player_id": f"target-{index}",
                            "ability_target_player_ids": [],
                            "request_event_id": f"request-{index}",
                            "accepted_at": 3.0,
                            "phase_deadline": 4.0,
                        }
                    )
                else:
                    expected.append(
                        {
                            "player_id": player,
                            "day": 1,
                            "phase": "night",
                            "action": "ability.use",
                            "valid_targets": [],
                            "target_count": 0,
                            "abilities": [
                                {
                                    "ability_id": "ability-8",
                                    "valid_targets": ["target-8a", "target-8b", "target-8c"],
                                    "target_count": 2,
                                }
                            ],
                        }
                    )
                    accepted.append(
                        {
                            "player_id": player,
                            "day": 1,
                            "phase": "night",
                            "action": "ability.use",
                            "ability_id": "ability-8",
                            "vote_target_player_id": None,
                            "ability_target_player_ids": ["target-8a", "target-8b"],
                            "request_event_id": f"request-{index}",
                            "accepted_at": 3.0,
                            "phase_deadline": 4.0,
                        }
                    )
                invocation = f"invocation-{index}"
                metrics.extend(
                    (
                        {"event": "ENQUEUED", "invocation_id": invocation},
                        {
                            "event": "PROVIDER_CALL_TERMINAL",
                            "invocation_id": invocation,
                            "backend_code": None,
                        },
                        {
                            "event": "TERMINAL",
                            "invocation_id": invocation,
                            "terminal_status": "RELEASED",
                        },
                    )
                )
                stdout, stderr = root / f"{player}.out", root / f"{player}.err"
                stdout.write_bytes(b"")
                stderr.write_bytes(b"")
                owned.append(
                    runner.OwnedProcess(
                        player,
                        _FinishedProcess(pid=1000 + index),
                        stdout,
                        stderr,
                    )
                )
            metadata = ai_dir / "admission.jsonl"
            metadata.write_text("{}\n", encoding="utf-8")
            manifest = runner._write_manifest(ai_dir, statuses, metadata, mapping)
            for label, pid in (("server", 2000), ("broker", 2001)):
                stdout, stderr = root / f"{label}.out", root / f"{label}.err"
                stdout.write_bytes(b"")
                stderr.write_bytes(b"")
                owned.append(
                    runner.OwnedProcess(
                        label, _FinishedProcess(pid=pid), stdout, stderr
                    )
                )
            server = {
                "server_pid": 2000,
                "success": True,
                "game_end": True,
                "listener_closed": True,
                "accepted_chats": chats,
                "expected_reservations": expected,
                "accepted_reservations": accepted,
                "phase_wall_durations": [
                    {"day": 0, "phase": "night0", "wall_microseconds": 10}
                ],
                "total_game_wall_microseconds": 10,
            }
            broker = {
                "pid": 2001,
                "success": True,
                "shutdown_clean": True,
                "listener_closed": True,
                "peak_backend_concurrency": 1,
                "metrics_dropped": 0,
                "snapshot_after_close": {
                    "pending_total": 0,
                    "offered": None,
                    "claimed": None,
                    "provider_call_active": False,
                    "draining": False,
                    "poisoned": False,
                },
            }
            errors = runner._validate_game_evidence(
                statuses=statuses,
                server_result=server,
                broker_result=broker,
                metrics=metrics,
                manifest=manifest,
                ai_dir=ai_dir,
                owned=owned,
                sentinels=("not-present-secret",),
                evidence_root=root,
                expected_player_to_client=mapping,
            )
            self.assertEqual(errors, [])
            server["accepted_chats"] = chats[:-1]
            self.assertIn(
                "not every Day-1 living seat has accepted short chat",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )
            server["accepted_chats"] = chats
            mutations = (
                (0, "vote_target_player_id", "FABRICATED"),
                (0, "vote_target_player_id", None),
                (8, "ability_id", "unknown-ability"),
                (8, "ability_target_player_ids", ["target-8a"]),
                (8, "ability_target_player_ids", ["target-8a", "FABRICATED"]),
                (8, "ability_target_player_ids", ["target-8a", "target-8a"]),
            )
            for index, field, value in mutations:
                with self.subTest(index=index, field=field, value=value):
                    original = accepted[index][field]
                    accepted[index][field] = value
                    try:
                        self.assertIn(
                            "accepted reservation violates server-authoritative offer",
                            runner._validate_game_evidence(
                                statuses=statuses,
                                server_result=server,
                                broker_result=broker,
                                metrics=metrics,
                                manifest=manifest,
                                ai_dir=ai_dir,
                                owned=owned,
                                sentinels=(),
                                evidence_root=root,
                                expected_player_to_client=mapping,
                            ),
                        )
                    finally:
                        accepted[index][field] = original

            statuses["player-0"]["player_id"] = "player-1"
            self.assertIn(
                "client status player identity mismatch",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )
            statuses["player-0"]["player_id"] = "player-0"

            statuses["player-0"]["audit"]["player_ids"] = ["player-1"]
            self.assertIn(
                "audit shard player identity mismatch",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )
            statuses["player-0"]["audit"]["player_ids"] = ["player-0"]

            statuses["player-0"]["audit"]["request_ids"] = [
                "request-0",
                "request-0",
            ]
            self.assertIn(
                "audit shard request IDs are not unique",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )
            statuses["player-0"]["audit"]["request_ids"] = ["audit-request-0"]

            statuses["player-1"]["audit"]["request_ids"] = ["audit-request-0"]
            self.assertIn(
                "audit request IDs are not globally disjoint",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )
            statuses["player-1"]["audit"]["request_ids"] = ["audit-request-1"]

            manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_value["player_to_opaque_client_id"]["player-0"], manifest_value[
                "player_to_opaque_client_id"
            ]["player-1"] = (
                manifest_value["player_to_opaque_client_id"]["player-1"],
                manifest_value["player_to_opaque_client_id"]["player-0"],
            )
            manifest.write_text(json.dumps(manifest_value) + "\n", encoding="utf-8")
            self.assertIn(
                "manifest player-to-opaque shard mapping mismatch",
                runner._validate_game_evidence(
                    statuses=statuses,
                    server_result=server,
                    broker_result=broker,
                    metrics=metrics,
                    manifest=manifest,
                    ai_dir=ai_dir,
                    owned=owned,
                    sentinels=(),
                    evidence_root=root,
                    expected_player_to_client=mapping,
                ),
            )


class PhaseFiveDiagnosticExecutionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _config(output: Path, profile_args: str) -> runner.RunConfig:
        base = runner._prepare_run(_args(output), {})
        arguments = _args(
            output,
            q8_provider_timing_diagnostic=True,
            gpu_model_pid=777,
            provider_serving_executable_sha256=runner._sha256_file(runner._SCRIPT),
            provider_profile_sha256=runner._canonical_profile_fingerprint(
                "local-test-model", profile_args
            ),
            provider_profile_model="local-test-model",
            provider_profile_args=profile_args,
            provider_backend_config_sha256=base.settings.backend_config().config_fingerprint,
            provider_implementation="llama.cpp",
            provider_version="0.3.0-dev",
            provider_build=10697,
            provider_commit="093adb242",
            provider_profile_perf_enabled=False,
        )
        with (
            patch.object(
                runner, "_resolve_process_executable", return_value=runner._SCRIPT
            ),
            patch.object(
                runner,
                "_read_provider_build_identity",
                return_value=runner.ProviderBuildIdentity(
                    "llama.cpp", "0.3.0-dev", 10697, "093adb242"
                ),
            ),
        ):
            return runner._prepare_run(arguments, {})

    async def test_diagnostic_execution_runs_exactly_q8_a_and_retains_only_sanitized_evidence(
        self,
    ) -> None:
        class _Sampler:
            def __init__(self, exact_pid: int) -> None:
                self.exact_pid = exact_pid

            def start(self) -> None:
                return None

            async def stop(self) -> dict[str, object]:
                return {"status": "AVAILABLE", "pid": self.exact_pid, "samples": 1}

        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            output = Path(temporary) / "diagnostic"
            private_profile_sentinel = "-ngl 99 --private-profile-sentinel"
            config = self._config(output, private_profile_sentinel)
            metrics = PhaseFiveRunnerContractTests._diagnostic_metrics(
                ["VALID_EXACT"] * 9, timing_complete=True
            )
            row = {
                "row": "Q8-A",
                "workload": runner.Q8_PLAN[0].workload,
                "requests": 9,
                "limit_seconds": 120,
                "success": True,
                "errors": [],
                "metrics": runner._metric_aggregates(metrics),
                "maximum_pending": 1,
                "peak_backend_concurrency": 1,
                "artifacts": [],
                "cleanup": (
                    {"label": "broker", "pid": 1, "returncode": 0, "alive": False, "action": "wait"},
                    {"label": "worker-0", "pid": 2, "returncode": 0, "alive": False, "action": "wait"},
                ),
                "raw_metrics": metrics,
                "metrics_dropped": 0,
            }
            inventory = {
                "ownership": "EXTERNAL_UNOWNED",
                "endpoint": config.settings.endpoint,
                "listener_availability": "AVAILABLE",
                "model_pid": 777,
                "model_pid_availability": "AVAILABLE",
                "control_actions": [],
            }
            run_row = AsyncMock(return_value=row)
            with (
                patch.object(runner, "_run_q8_broker_row", run_row),
                patch.object(
                    runner,
                    "_external_model_inventory",
                    AsyncMock(side_effect=(inventory, inventory)),
                ),
                patch.object(runner, "_GpuSampler", _Sampler),
                patch.object(
                    runner, "_diagnostic_end_identity_matches", return_value=True
                ),
            ):
                code = await runner._execute(config, {})
            self.assertEqual(code, 0)
            self.assertEqual(run_row.await_count, 1)
            called = run_row.await_args.args
            self.assertIs(called[2], runner.Q8_PLAN[0])
            self.assertFalse((output / "q8-b").exists())
            self.assertFalse((output / "q8-c").exists())
            self.assertFalse((output / "q8-d").exists())
            summary_text = (output / "summary.json").read_text(encoding="utf-8")
            summary = json.loads(summary_text)
            self.assertEqual(summary["diagnostic"]["result"], "PASS")
            self.assertEqual(summary["diagnostic"]["route"], "RUN_STANDARD_Q8")
            self.assertEqual(summary["diagnostic"]["terminal_diagnostics"], 9)
            self.assertNotIn(private_profile_sentinel, summary_text)
            self.assertNotIn(str(runner._SCRIPT), summary_text)
            self.assertNotIn("endpoint", summary["external_model_availability"])

    async def test_q8_a_producer_projects_drop_count_to_diagnostic_executor(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary) / "q8-a"
            config = self._config(Path(temporary) / "unused", "-ngl 99")
            diagnostic_metrics = PhaseFiveRunnerContractTests._diagnostic_metrics(
                ["VALID_EXACT"] * 9, timing_complete=True
            )
            metrics: list[dict[str, object]] = []
            for index in range(9):
                invocation_id = f"invocation-{index}"
                metrics.extend(
                    (
                        {
                            "event": "ENQUEUED",
                            "invocation_id": invocation_id,
                            "monotonic_microseconds": index * 10,
                            "priority": int(GenerationPriority.REACTION),
                            "client_id": "client-0",
                        },
                        {
                            "event": "OFFERED",
                            "invocation_id": invocation_id,
                            "queue_wait_microseconds": 1,
                            "priority": int(GenerationPriority.REACTION),
                            "client_id": "client-0",
                        },
                        diagnostic_metrics[index * 2],
                        {
                            **diagnostic_metrics[index * 2 + 1],
                            "monotonic_microseconds": index * 10 + 2,
                        },
                    )
                )

            async def spawn(
                owned: list[runner.OwnedProcess],
                *,
                label: str,
                output_dir: Path,
                **_: object,
            ) -> runner.OwnedProcess:
                stdout_path = output_dir / f"{label}.stdout.log"
                stderr_path = output_dir / f"{label}.stderr.log"
                stdout_path.write_bytes(b"")
                stderr_path.write_bytes(b"")
                item = runner.OwnedProcess(
                    label,
                    _FinishedProcess(pid=101 + len(owned)),
                    stdout_path,
                    stderr_path,
                )
                owned.append(item)
                return item

            def read_json(path: Path) -> dict[str, object]:
                if path.name == "broker.ready.json":
                    return {"host": "127.0.0.1", "port": 1, "config": {}}
                if path.name == "worker-0.status.json":
                    return {"success": True, "outcomes": [{} for _ in range(9)]}
                if path.name == "broker.result.json":
                    return {
                        "success": True,
                        "maximum_pending": 1,
                        "peak_backend_concurrency": 1,
                        "metrics_dropped": 0,
                    }
                raise AssertionError(f"unexpected JSON read: {path}")

            with (
                patch.object(runner, "_spawn_owned", spawn),
                patch.object(runner, "_wait_for_paths", AsyncMock()),
                patch.object(runner, "_read_json", side_effect=read_json),
                patch.object(runner, "_read_jsonl", return_value=metrics),
                patch.object(
                    runner,
                    "_cleanup_owned_shielded",
                    AsyncMock(
                        return_value=list(
                            PhaseFiveRunnerContractTests._diagnostic_cleanup()
                        )
                    ),
                ),
            ):
                row = await runner._run_q8_broker_row(
                    config, root, runner.Q8_PLAN[0], environ={}
                )

            self.assertTrue(row["success"])
            self.assertEqual(row["metrics_dropped"], 0)
            diagnostic = runner._diagnostic_route(
                runner._raw_records("Q8-A", row["raw_metrics"]),
                row_errors=row["errors"],
                metrics_dropped=row.get("metrics_dropped"),
                cleanup=row["cleanup"],
                end_identity_matches=True,
                external_evidence_clean=True,
            )
            self.assertEqual(diagnostic["result"], "PASS")
            self.assertEqual(diagnostic["route"], "RUN_STANDARD_Q8")

    def test_end_identity_requires_same_path_bytes_identity_and_binding(self) -> None:
        path = runner._SCRIPT
        identity = runner.ProviderBuildIdentity(
            "llama.cpp", "0.3.0-dev", 10697, "093adb242"
        )
        executable_hash = runner._sha256_file(path)
        environment = runner.ProviderDiagnosticEnvironment(
            serving_pid=777,
            serving_executable=path,
            serving_executable_sha256=executable_hash,
            profile_sha256="1" * 64,
            backend_config_sha256="2" * 64,
            identity=identity,
            identity_binding_sha256=runner._provider_identity_binding_sha256(
                executable_hash, identity
            ),
            provider_profile_perf_enabled=False,
        )
        self.assertTrue(
            runner._diagnostic_end_identity_matches(
                environment,
                process_path_resolver=lambda _pid: path,
                identity_reader=lambda _path: identity,
            )
        )
        self.assertFalse(
            runner._diagnostic_end_identity_matches(
                environment,
                process_path_resolver=lambda _pid: path,
                file_hasher=lambda _path: "0" * 64,
                identity_reader=lambda _path: identity,
            )
        )
        self.assertFalse(
            runner._diagnostic_end_identity_matches(
                environment,
                process_path_resolver=lambda _pid: path.with_name("missing.exe"),
                identity_reader=lambda _path: identity,
            )
        )
        self.assertFalse(
            runner._diagnostic_end_identity_matches(
                environment,
                process_path_resolver=lambda _pid: path,
                identity_reader=lambda _path: runner.ProviderBuildIdentity(
                    "llama.cpp", "fabricated-valid-token", 10697, "093adb242"
                ),
            )
        )
        self.assertFalse(
            runner._diagnostic_end_identity_matches(
                environment,
                process_path_resolver=lambda _pid: path,
                identity_reader=lambda _path: identity,
                file_hasher=unittest.mock.Mock(
                    side_effect=(executable_hash, "0" * 64)
                ),
            )
        )
        self.assertFalse(
            runner._diagnostic_end_identity_matches(
                replace(environment, identity_binding_sha256="3" * 64),
                process_path_resolver=lambda _pid: path,
                identity_reader=lambda _path: identity,
            )
        )


class PhaseFiveRunnerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_wall_origin_is_captured_only_after_delayed_gate_release(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            start = root / "start"
            stop = root / "stop"
            sleeps = 0

            async def delayed_release(_delay: float) -> None:
                nonlocal sleeps
                sleeps += 1
                if sleeps == 3:
                    start.write_text("start\n", encoding="utf-8")

            origin = await runner._wait_for_start_gate(
                start,
                stop,
                monotonic_ns=lambda: 9_000_000,
                sleep=delayed_release,
            )
            self.assertEqual(sleeps, 3)
            self.assertEqual(origin, 9_000_000)
            self.assertEqual(runner._elapsed_microseconds(origin, 9_250_000), 250)

    async def test_external_model_inventory_is_read_only_and_rejects_owned_pid(self) -> None:
        listener_calls: list[str] = []
        pid_calls: list[int] = []

        async def listener_probe(endpoint: str) -> bool:
            listener_calls.append(endpoint)
            return True

        def pid_probe(exact_pid: int) -> bool:
            pid_calls.append(exact_pid)
            return True

        endpoint = "http://127.0.0.1:8080/v1/chat/completions"
        inventory = await runner._external_model_inventory(
            endpoint,
            777,
            listener_probe=listener_probe,
            pid_probe=pid_probe,
        )
        self.assertEqual(listener_calls, [endpoint])
        self.assertEqual(pid_calls, [777])
        self.assertEqual(inventory["listener_availability"], "AVAILABLE")
        self.assertEqual(inventory["model_pid_availability"], "AVAILABLE")
        self.assertEqual(inventory["control_actions"], [])
        self.assertEqual(
            runner._validate_external_model_evidence(inventory, inventory, (), 777),
            [],
        )
        self.assertIn(
            "external model PID entered the owned process collection",
            runner._validate_external_model_evidence(
                inventory,
                inventory,
                ({"cleanup": [{"pid": 777}]},),
                777,
            ),
        )
        controlled = dict(inventory)
        controlled["control_actions"] = ["terminate"]
        self.assertIn(
            "external model before inventory is malformed",
            runner._validate_external_model_evidence(controlled, inventory, (), 777),
        )

    async def test_gpu_sampler_reports_available_and_unavailable_without_control(self) -> None:
        values = iter((100, 120, 110))

        async def sample(_pid: int) -> int | None:
            try:
                return next(values)
            except StopIteration:
                return 110

        sampler = runner._GpuSampler(777, sample_once=sample, interval_seconds=0.001)
        sampler.start()
        await asyncio.sleep(0.005)
        result = await sampler.stop()
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["pid"], 777)
        self.assertEqual(result["start_used_mib"], 100)
        self.assertEqual(result["peak_used_mib"], 120)

        async def unavailable(_pid: int) -> None:
            return None

        missing = runner._GpuSampler(778, sample_once=unavailable)
        missing.start()
        await asyncio.sleep(0)
        self.assertEqual((await missing.stop())["status"], "UNAVAILABLE")

    async def test_spawn_uses_only_stdin_for_secrets_and_scrubs_environment(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            output = Path(temporary)
            captured: dict[str, object] = {}
            process = _FinishedProcess()

            async def factory(*arguments, **keywords):
                captured["arguments"] = arguments
                captured["keywords"] = keywords
                return process

            owned: list[runner.OwnedProcess] = []
            await runner._spawn_owned(
                owned,
                label="worker",
                arguments=("--_child-mode", "q8-worker"),
                output_dir=output,
                bootstrap={"token": "stdin-only-secret"},
                environ={"PATH": "kept", "AIWOLF_LLM_API_KEY": "env-secret"},
                process_factory=factory,
            )
            self.assertNotIn("stdin-only-secret", repr(captured["arguments"]))
            self.assertNotIn("env-secret", repr(captured["keywords"]))
            self.assertIn(b"stdin-only-secret", process.stdin.payload)
            self.assertTrue(process.stdin.closed)
            self.assertEqual(len(owned), 1)

    async def test_cleanup_waits_then_terminates_only_owned_process(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            stdout, stderr = root / "out", root / "err"
            stdout.write_bytes(b"")
            stderr.write_bytes(b"")
            process = _BlockingProcess()
            item = runner.OwnedProcess("owned", process, stdout, stderr)
            original = runner._STOP_GRACE_SECONDS
            runner._STOP_GRACE_SECONDS = 0.001
            try:
                inventory = await runner._cleanup_owned((item,), None)
            finally:
                runner._STOP_GRACE_SECONDS = original
            self.assertEqual(process.terminate_count, 1)
            self.assertEqual(process.kill_count, 0)
            self.assertFalse(inventory[0]["alive"])
            self.assertEqual(inventory[0]["action"], "terminate")

    async def test_cleanup_refuses_to_touch_exact_external_model_pid(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            stdout, stderr = root / "out", root / "err"
            stdout.write_bytes(b"")
            stderr.write_bytes(b"")
            process = _BlockingProcess(pid=999)
            item = runner.OwnedProcess("collision", process, stdout, stderr)
            inventory = await runner._cleanup_owned((item,), 999)
            self.assertEqual(process.terminate_count, 0)
            self.assertEqual(process.kill_count, 0)
            self.assertTrue(inventory[0]["alive"])
            self.assertEqual(inventory[0]["action"], "REFUSED_EXTERNAL_MODEL_PID")
            process.returncode = -1
            process.terminated.set()

    async def test_wait_paths_reports_nonzero_and_times_out_finitely(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            stdout, stderr = root / "out", root / "err"
            stdout.write_bytes(b"")
            stderr.write_bytes(b"")
            failed = runner.OwnedProcess(
                "failed", _FinishedProcess(returncode=3), stdout, stderr
            )
            with self.assertRaises(RuntimeError):
                await runner._wait_for_paths((root / "missing",), (failed,), 0.1)
            successful = runner.OwnedProcess(
                "successful", _FinishedProcess(returncode=0), stdout, stderr
            )
            with self.assertRaises(TimeoutError):
                await runner._wait_for_paths((root / "missing",), (successful,), 0.01)

    async def test_partial_spawn_retains_first_child_for_cleanup(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            owned: list[runner.OwnedProcess] = []
            first = _BlockingProcess(pid=701)
            calls = 0

            async def factory(*_arguments, **_keywords):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return first
                raise OSError("synthetic spawn failure")

            await runner._spawn_owned(
                owned,
                label="first",
                arguments=("--_child-mode", "q8-worker"),
                output_dir=root,
                bootstrap={"token": "secret"},
                environ={},
                process_factory=factory,
            )
            with self.assertRaises(OSError):
                await runner._spawn_owned(
                    owned,
                    label="second",
                    arguments=("--_child-mode", "q8-worker"),
                    output_dir=root,
                    bootstrap={"token": "secret"},
                    environ={},
                    process_factory=factory,
                )
            original = runner._STOP_GRACE_SECONDS
            runner._STOP_GRACE_SECONDS = 0.001
            try:
                inventory = await runner._cleanup_owned_shielded(owned, None)
            finally:
                runner._STOP_GRACE_SECONDS = original
            self.assertEqual(len(inventory), 1)
            self.assertFalse(inventory[0]["alive"])

    async def test_post_spawn_chmod_failure_retains_child_for_cleanup(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            owned: list[runner.OwnedProcess] = []
            process = _BlockingProcess(pid=702)

            async def factory(*_arguments, **_keywords):
                return process

            with patch.object(runner.os, "chmod", side_effect=OSError("synthetic chmod failure")):
                with self.assertRaisesRegex(OSError, "synthetic chmod failure"):
                    await runner._spawn_owned(
                        owned,
                        label="post-spawn-failure",
                        arguments=("--_child-mode", "q8-worker"),
                        output_dir=root,
                        bootstrap={"token": "secret"},
                        environ={},
                        process_factory=factory,
                    )
            self.assertEqual(len(owned), 1)
            self.assertIs(owned[0].process, process)
            original = runner._STOP_GRACE_SECONDS
            runner._STOP_GRACE_SECONDS = 0.001
            try:
                inventory = await runner._cleanup_owned_shielded(owned, None)
            finally:
                runner._STOP_GRACE_SECONDS = original
            self.assertEqual(len(inventory), 1)
            self.assertFalse(inventory[0]["alive"])
            self.assertEqual(process.terminate_count, 1)

    async def test_shielded_cleanup_completes_after_supervisor_cancellation(self) -> None:
        with TemporaryDirectory(dir=runner.PROJECT_ROOT) as temporary:
            root = Path(temporary)
            stdout, stderr = root / "out", root / "err"
            stdout.write_bytes(b"")
            stderr.write_bytes(b"")
            process = _ReleasedProcess()
            item = runner.OwnedProcess("owned", process, stdout, stderr)
            task = asyncio.create_task(runner._cleanup_owned_shielded((item,), None))
            await asyncio.sleep(0)
            task.cancel()
            process.release.set()
            inventory = await task
            self.assertFalse(inventory[0]["alive"])
            self.assertEqual(process.terminate_count, 0)


if __name__ == "__main__":
    unittest.main()
