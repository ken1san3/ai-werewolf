"""Finite opt-in Phase 5 real-model smoke and Q8 measurement runner.

The runner owns the game server, one production admission broker, and the AI
client/measurement processes that it starts.  The configured model service is
always external: this module never starts, stops, signals, or kills it.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from random import Random
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Literal
import base64
import ctypes
from contextlib import ExitStack
from urllib.parse import urlsplit
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.llm import (  # noqa: E402
    AdmissionCredentials,
    AdmissionMetrics,
    AdmissionRequest,
    AdmissionStatus,
    BrokerAdmissionSession,
    BrokeredStructuredLLMBackend,
    GenerationAdmissionBroker,
    GenerationBrokerConfig,
    GenerationPriority,
    GenerationSettings,
    LLMMessage,
    LocalLLMSettings,
    OpenAICompatibleBackend,
    ProviderTimingDiagnostic,
    ProviderTimingFieldState,
    ProviderTimingShapeStatus,
    StructuredGenerationRequest,
)
from ai_client.llm.types import LlamaCppStructuredOutputConfig  # noqa: E402


_SCRIPT = Path(__file__).resolve()
_PRIVATE_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
_PRIVATE_DIRECTORY_MODE = _PRIVATE_FILE_MODE | stat.S_IXUSR
_TAIL_BYTES = 4096
_READY_SECONDS = 30.0
_STOP_GRACE_SECONDS = 5.0
_SMOKE_HARD_LIMIT_SECONDS = 1200.0
_Q8_ROW_LIMIT_SECONDS = 120.0
_GPU_INTERVAL_SECONDS = 0.250
_EXTERNAL_INVENTORY_TIMEOUT_SECONDS = 0.500
_MAX_RAW_RECORDS = 20_000
PHASE6_MAX_OUTPUT_TOKENS = 512
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_IDENTITY_TOKEN_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_PROVIDER_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
_PROVIDER_VERSION_LINE_RE = re.compile(
    rb"^llama\.cpp ([A-Za-z0-9._+-]{1,64}), build ([0-9]+), commit ([0-9a-f]{7,64})$"
)
_PROVIDER_VERSION_PAREN_LINE_RE = re.compile(
    rb"^version: ([A-Za-z0-9._+-]{1,64}) \(build ([0-9]+), commit ([0-9a-f]{7,64})\)$"
)
_PROVIDER_VERSION_MAX_BYTES = 4096
_PROVIDER_VERSION_TIMEOUT_SECONDS = 2.0
_PROVIDER_VERSION_TERMINATION_SECONDS = 0.5
_DIAGNOSTIC_INCOMPLETE = "PROVIDER_TIMING_DIAGNOSTIC_INCOMPLETE"
_TIMING_UNAVAILABLE = "PROVIDER_TIMING_UNAVAILABLE"
_REMOVED_CHILD_SECRET_KEYS = frozenset(
    {
        "AIWOLF_ENTRY_TOKEN",
        "AIWOLF_ADMISSION_TOKEN",
        "AIWOLF_LLM_API_KEY",
    }
)


@dataclass(frozen=True)
class GamePlan:
    preset: Literal["standard_9"] = "standard_9"
    clients: Literal[9] = 9
    brokers: Literal[1] = 1
    servers: Literal[1] = 1
    day_seconds: int = 60
    vote_seconds: int = 45
    night_seconds: int = 45
    silence_after_dawn_seconds: Literal[0] = 0
    hard_limit_seconds: Literal[1200] = 1200
    max_chat_attempts_per_phase: Literal[2] = 2
    talkativeness: Literal[1.0] = 1.0
    initial_event_importance: Literal[1.0] = 1.0
    direct_mention_importance: Literal[1.0] = 1.0
    ordinary_event_importance: Literal[0.5] = 0.5
    cooldown_seconds: Literal[0.20] = 0.20
    max_trigger_evaluations_per_phase: Literal[32] = 32
    repetition_window: Literal[8] = 8


@dataclass(frozen=True)
class Q8RowPlan:
    row: str
    workload: str
    requests: int
    limit_seconds: int
    priorities: tuple[GenerationPriority, ...]


GAME_PLAN = GamePlan()
_FEATURE_BRAIN_TIMEOUT_SECONDS = 44.0
PHASE6_GAME_PLAN = replace(
    GAME_PLAN,
    day_seconds=180,
    vote_seconds=60,
    night_seconds=60,
)
_COMMAND_SECRET_SEGMENTS = frozenset(
    {"key", "token", "secret", "password", "passwd", "authorization", "credential"}
)


@dataclass(frozen=True)
class ProviderCommandObservation:
    command_observation: Literal[
        "OBSERVED", "ACCESS_DENIED", "PROCESS_NOT_FOUND", "EMPTY", "PARSE_FAILED",
        "REDACTION_INVALID", "REDACTION_BLOCKED", "OS_ERROR"
    ]
    command_argv_redacted: tuple[str, ...] | None = None
    command_canonical_sha256: str | None = None
    environment_observation: Literal["NOT_OBSERVABLE"] = "NOT_OBSERVABLE"


class ProviderObservationCleanupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("PROVIDER_OBSERVATION_CLEANUP_FAILED")


def _windows_command_line_to_argv(raw: str) -> tuple[str, ...]:
    if not isinstance(raw, str) or "\0" in raw or len(raw.encode("utf-16-le")) // 2 > 32767:
        raise ValueError("invalid Windows command line")
    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    pointer = shell32.CommandLineToArgvW(raw, ctypes.byref(argc))
    if not pointer:
        raise ValueError("CommandLineToArgvW failed")
    try:
        if not 1 <= argc.value <= 256:
            raise ValueError("argv count exceeded")
        values = tuple(pointer[index] for index in range(argc.value))
        encoded = [value.encode("utf-8") for value in values]
        if any(len(value) > 8192 for value in encoded) or sum(len(value) for value in encoded) > 65536:
            raise ValueError("argv size exceeded")
        return values
    finally:
        if kernel32.LocalFree(pointer):
            raise ValueError("LocalFree failed")


def _redact_provider_argv(argv: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    redact_next = False
    for argument in argv:
        if redact_next:
            if argument.startswith(("-", "/")):
                raise ValueError("REDACTION_INVALID")
            result.append("<redacted>")
            redact_next = False
            continue
        lowered = argument.casefold()
        name = lowered.lstrip("-/").split("=", 1)[0].split(":", 1)[0]
        secret_flag = argument.startswith(("--", "/")) and any(
            segment in name for segment in _COMMAND_SECRET_SEGMENTS
        )
        if secret_flag and "=" in argument:
            if not argument.split("=", 1)[1]:
                raise ValueError("REDACTION_INVALID")
            result.append(argument.split("=", 1)[0] + "=<redacted>")
        elif secret_flag and argument.startswith("/") and ":" in argument:
            if not argument.split(":", 1)[1]:
                raise ValueError("REDACTION_INVALID")
            result.append(argument.split(":", 1)[0] + ":<redacted>")
        elif secret_flag:
            result.append(argument)
            redact_next = True
        else:
            if re.search(
                r"(?i)\bbearer\s+\S+|[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@|"
                r"(?:key|token|secret|password|passwd|authorization|credential)[\"']?\s*[:=]\s*[\"']?[^\s,}\"]+",
                argument,
            ):
                raise PermissionError("REDACTION_BLOCKED")
            result.append(argument)
    if redact_next:
        raise ValueError("REDACTION_INVALID")
    return tuple(result)


def _provider_observation_from_raw(raw: str) -> ProviderCommandObservation:
    try:
        redacted = _redact_provider_argv(_windows_command_line_to_argv(raw))
    except PermissionError:
        return ProviderCommandObservation("REDACTION_BLOCKED")
    except ValueError as error:
        if str(error) == "REDACTION_INVALID":
            return ProviderCommandObservation("REDACTION_INVALID")
        return ProviderCommandObservation("PARSE_FAILED")
    canonical = json.dumps(
        redacted, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return ProviderCommandObservation(
        "OBSERVED", redacted, hashlib.sha256(canonical).hexdigest()
    )


def _public_provider_command_observation(
    observation: ProviderCommandObservation,
) -> dict[str, str | None]:
    return {
        "command_observation": observation.command_observation,
        "command_canonical_sha256": observation.command_canonical_sha256,
        "environment_observation": observation.environment_observation,
    }


async def _record_provider_command_observation(
    pid: int,
    path: Path,
    existing_observation: Mapping[str, bool],
) -> dict[str, str | None]:
    """Explicit preflight entrypoint; callers decide when observation is authorized."""
    if set(existing_observation) != {"command_ngl99", "command_context8192", "command_jinja"}:
        raise ValueError("existing provider observation is incomplete")
    if any(type(value) is not bool for value in existing_observation.values()):
        raise TypeError("existing provider observations must be bool")
    observation = await _observe_windows_process_command_line(pid)
    private_value: dict[str, object] = {
        **existing_observation,
        "command_observation": observation.command_observation,
        "command_argv_redacted": (
            list(observation.command_argv_redacted)
            if observation.command_argv_redacted is not None else None
        ),
        "command_canonical_sha256": observation.command_canonical_sha256,
        "environment_observation": observation.environment_observation,
    }
    payload = json.dumps(
        private_value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8") + b"\n"
    from scripts.phase6_private_review import _locked_path
    with _locked_path(path, create=True) as descriptor:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    return _public_provider_command_observation(observation)


async def _close_broker_metrics_resources(
    broker: GenerationAdmissionBroker,
    metrics_wrapper: object | None,
    metrics_resources: ExitStack,
) -> None:
    first_error: BaseException | None = None
    try:
        await broker.aclose()
    except BaseException as error:
        first_error = error
    if metrics_wrapper is not None:
        try:
            metrics_wrapper.flush()  # type: ignore[attr-defined]
            os.fsync(metrics_wrapper.fileno())  # type: ignore[attr-defined]
        except BaseException as error:
            if first_error is None:
                first_error = error
    try:
        metrics_resources.close()
    except BaseException as error:
        if first_error is None:
            first_error = error
    if first_error is not None:
        raise first_error


async def _cleanup_broker_child_after_error(
    original: BaseException,
    *,
    broker: GenerationAdmissionBroker | None,
    metrics: AdmissionMetrics | None,
    backend: object | None,
    metrics_wrapper: object | None,
    metrics_resources: ExitStack,
) -> None:
    try:
        if broker is not None:
            await _close_broker_metrics_resources(
                broker, metrics_wrapper, metrics_resources
            )
        else:
            if metrics is not None:
                try:
                    await metrics.aclose()
                except BaseException:
                    pass
            if backend is not None:
                try:
                    await backend.aclose()  # type: ignore[attr-defined]
                except BaseException:
                    pass
            try:
                metrics_resources.close()
            except BaseException:
                pass
    except BaseException:
        pass
    raise original


async def _observe_windows_process_command_line(
    pid: int,
    *,
    create_subprocess_exec: Callable[..., Awaitable[asyncio.subprocess.Process]] = asyncio.create_subprocess_exec,
) -> ProviderCommandObservation:
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive int")
    executable = shutil.which("powershell.exe")
    if executable is None:
        return ProviderCommandObservation("OS_ERROR")
    script = (
        f"try{{$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction Stop}}"
        "catch [Microsoft.Management.Infrastructure.CimException]{if($_.Exception.NativeErrorCode -eq [Microsoft.Management.Infrastructure.NativeErrorCode]::AccessDenied){exit 11}else{exit 13}}"
        "catch [System.UnauthorizedAccessException]{exit 11}catch{exit 13};"
        "if($null -eq $p){exit 10};if([string]::IsNullOrEmpty($p.CommandLine)){exit 12};"
        "$b=[Text.UTF8Encoding]::new($false,$true).GetBytes($p.CommandLine);"
        "@{status='OBSERVED';command_line_base64=[Convert]::ToBase64String($b)}|ConvertTo-Json -Compress"
    )
    process: asyncio.subprocess.Process | None = None
    reader_task: asyncio.Task[tuple[bytes, bool, bool]] | None = None
    process_wait: asyncio.Task[int] | None = None
    oversize_event = asyncio.Event()
    oversize_wait: asyncio.Task[bool] | None = None

    async def read_stdout() -> tuple[bytes, bool, bool]:
        assert process is not None and process.stdout is not None
        bounded = bytearray()
        oversize = False
        try:
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    return bytes(bounded), oversize, False
                if not oversize and len(bounded) + len(chunk) <= 131072:
                    bounded.extend(chunk)
                else:
                    oversize = True
                    oversize_event.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            return b"", False, True

    try:
        process = await create_subprocess_exec(
            executable, "-NoProfile", "-NonInteractive", "-Command", script,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process_wait = asyncio.create_task(process.wait())
        if process.stdout is None:
            raise OSError("stdout unavailable")
        reader_task = asyncio.create_task(read_stdout())
        oversize_wait = asyncio.create_task(oversize_event.wait())
        timed_out = False
        try:
            async with asyncio.timeout(2.0):
                while not (process_wait.done() and reader_task.done()):
                    pending = {
                        task for task in (process_wait, reader_task, oversize_wait)
                        if not task.done()
                    }
                    done, _ = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if oversize_wait in done and oversize_event.is_set():
                        break
                    if reader_task in done and reader_task.result()[2]:
                        break
        except TimeoutError:
            timed_out = True
        if timed_out:
            return ProviderCommandObservation("OS_ERROR")
        if not process_wait.done() or not reader_task.done():
            return ProviderCommandObservation("PARSE_FAILED")
        encoded, oversize, read_error = reader_task.result()
        if read_error or oversize:
            return ProviderCommandObservation("PARSE_FAILED")
        exit_states = {10: "PROCESS_NOT_FOUND", 11: "ACCESS_DENIED", 12: "EMPTY", 13: "OS_ERROR"}
        if process.returncode in exit_states:
            return ProviderCommandObservation(exit_states[process.returncode])  # type: ignore[arg-type]
        if process.returncode != 0:
            return ProviderCommandObservation("PARSE_FAILED")
        value = json.loads(encoded)
        if not isinstance(value, dict) or set(value) != {"status", "command_line_base64"} or value["status"] != "OBSERVED" or not isinstance(value["command_line_base64"], str):
            return ProviderCommandObservation("PARSE_FAILED")
        raw = base64.b64decode(value["command_line_base64"], validate=True).decode("utf-8", errors="strict")
        if len(raw.encode("utf-8")) > 131072:
            return ProviderCommandObservation("PARSE_FAILED")
        return _provider_observation_from_raw(raw)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        return ProviderCommandObservation("PARSE_FAILED" if process is not None else "OS_ERROR")
    finally:
        if oversize_wait is not None:
            oversize_wait.cancel()
            await asyncio.gather(oversize_wait, return_exceptions=True)
        if process is not None and process_wait is not None:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(asyncio.shield(process_wait), 1.0)
                except TimeoutError:
                    pass
            kill_deadline: float | None = None
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                kill_deadline = asyncio.get_running_loop().time() + 1.0
                remaining = max(0.0, kill_deadline - asyncio.get_running_loop().time())
                try:
                    await asyncio.wait_for(asyncio.shield(process_wait), remaining)
                except TimeoutError:
                    pass
            if kill_deadline is not None and reader_task is not None and not reader_task.done():
                remaining = max(0.0, kill_deadline - asyncio.get_running_loop().time())
                if remaining > 0:
                    try:
                        await asyncio.wait_for(asyncio.shield(reader_task), remaining)
                    except TimeoutError:
                        pass
            if reader_task is not None and not reader_task.done():
                reader_task.cancel()
                remaining = (
                    max(0.0, kill_deadline - asyncio.get_running_loop().time())
                    if kill_deadline is not None else 0.0
                )
                if remaining > 0:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(reader_task, return_exceptions=True), remaining
                        )
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0)
            if not process_wait.done():
                process_wait.cancel()
                remaining = (
                    max(0.0, kill_deadline - asyncio.get_running_loop().time())
                    if kill_deadline is not None else 0.0
                )
                if remaining > 0:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(process_wait, return_exceptions=True), remaining
                        )
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0)
            if process.returncode is None or (
                not process_wait.done()
                or (reader_task is not None and not reader_task.done())
            ):
                raise ProviderObservationCleanupError()


def _apply_game_plan(preset: Any, plan: GamePlan) -> Any:
    """Apply the selected runner plan to the exact preset passed to the server."""
    return replace(
        preset,
        rules=replace(
            preset.rules,
            day_seconds=plan.day_seconds,
            vote_seconds=plan.vote_seconds,
            night_seconds=plan.night_seconds,
            silence_after_dawn_seconds=plan.silence_after_dawn_seconds,
        ),
    )
Q8_PLAN = (
    Q8RowPlan(
        "Q8-A",
        "three deterministic prompt buckets, three sequential requests each",
        9,
        120,
        (GenerationPriority.REACTION,) * 9,
    ),
    Q8RowPlan(
        "Q8-B",
        "nine simultaneous reaction tickets",
        9,
        120,
        (GenerationPriority.REACTION,) * 9,
    ),
    Q8RowPlan(
        "Q8-C",
        "one active reaction then four reservation and four reaction tickets",
        9,
        120,
        (GenerationPriority.REACTION,)
        + (GenerationPriority.RESERVATION,) * 4
        + (GenerationPriority.REACTION,) * 4,
    ),
    Q8RowPlan("Q8-D", "one nine-real-client game", 1, 1200, ()),
)


@dataclass(frozen=True)
class RunConfig:
    settings: LocalLLMSettings
    output_dir: Path
    q8: bool
    q8_provider_timing_diagnostic: bool
    gpu_model_pid: int | None
    seed: int
    max_seconds: float
    sanitized_arguments: tuple[str, ...]
    diagnostic_environment: ProviderDiagnosticEnvironment | None = None
    phase6: bool = False


@dataclass(frozen=True)
class ProviderBuildIdentity:
    implementation: str
    version: str
    build: int
    commit: str


@dataclass(frozen=True)
class _BoundedProviderVersionOutput:
    returncode: int | None
    output: bytes
    timed_out: bool
    oversized: bool


@dataclass(frozen=True)
class ProviderDiagnosticEnvironment:
    serving_pid: int
    serving_executable: Path
    serving_executable_sha256: str
    profile_sha256: str
    backend_config_sha256: str
    identity: ProviderBuildIdentity
    identity_binding_sha256: str
    provider_profile_perf_enabled: bool


class ProviderTimingDiagnosticPreflightError(ValueError):
    """Fail-closed diagnostic preflight without creating evidence output."""


@dataclass
class OwnedProcess:
    label: str
    process: Any
    stdout_path: Path
    stderr_path: Path
    touched: Literal["none", "wait", "terminate", "kill"] = "none"


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False)
    os.chmod(path, _PRIVATE_DIRECTORY_MODE)


def _write_private_bytes_atomic(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary output already exists: {temporary}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, _PRIVATE_FILE_MODE)
    os.replace(temporary, path)
    os.chmod(path, _PRIVATE_FILE_MODE)


def _write_private_json_atomic(path: Path, value: object) -> None:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    _write_private_bytes_atomic(path, payload)


def _write_private_jsonl_atomic(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    if len(records) > _MAX_RAW_RECORDS:
        raise ValueError("raw evidence exceeds the fixed record bound")
    payload = b"".join(
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
        for record in records
    )
    _write_private_bytes_atomic(path, payload)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object records: {path.name}")
        records.append(value)
    return records


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_tail(path: Path) -> str:
    try:
        return path.read_bytes()[-_TAIL_BYTES:].decode("utf-8", errors="replace")
    except OSError:
        return ""


def _child_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    for key in _REMOVED_CHILD_SECRET_KEYS:
        result.pop(key, None)
    return result


def _sanitized_arguments(args: argparse.Namespace) -> tuple[str, ...]:
    values = [
        "--endpoint",
        str(args.endpoint or "<environment/default>"),
        "--model",
        str(args.model or "<environment>"),
        "--output-dir",
        str(args.output_dir),
        "--seed",
        str(args.seed),
        "--max-seconds",
        str(args.max_seconds),
    ]
    if args.q8:
        values.append("--q8")
    if bool(getattr(args, "phase6", False)):
        values.append("--phase6")
        for name in ("phase6_read_timeout_seconds", "phase6_request_timeout_seconds"):
            values.extend(("--" + name.replace("_", "-"), str(getattr(args, name, None))))
    if bool(getattr(args, "q8_provider_timing_diagnostic", False)):
        values.extend(
            (
                "--q8-provider-timing-diagnostic",
                "--provider-serving-executable-sha256",
                str(getattr(args, "provider_serving_executable_sha256", "")),
                "--provider-profile-sha256",
                str(getattr(args, "provider_profile_sha256", "")),
                "--provider-backend-config-sha256",
                str(getattr(args, "provider_backend_config_sha256", "")),
                "--provider-implementation",
                str(getattr(args, "provider_implementation", "")),
                "--provider-version",
                str(getattr(args, "provider_version", "")),
                "--provider-build",
                str(getattr(args, "provider_build", "")),
                "--provider-commit",
                str(getattr(args, "provider_commit", "")),
            )
        )
    if args.gpu_model_pid is not None:
        values.extend(("--gpu-model-pid", str(args.gpu_model_pid)))
    return tuple(values)


def _canonical_profile_fingerprint(model: str, arguments: str) -> str:
    encoded = json.dumps(
        {"args": arguments, "model": model},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_process_executable(exact_pid: int) -> Path:
    """Resolve one exact process image read-only; never retain it as evidence."""

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            query_image = kernel32.QueryFullProcessImageNameW
            query_image.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            )
            query_image.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, exact_pid)
            if not handle:
                raise OSError("serving process is unavailable")
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not query_image(handle, 0, buffer, ctypes.byref(size)):
                    raise OSError("serving executable cannot be resolved")
                return Path(buffer.value).resolve(strict=True)
            finally:
                close_handle(handle)
        except (AttributeError, OSError, ValueError) as error:
            raise ProviderTimingDiagnosticPreflightError(
                _DIAGNOSTIC_INCOMPLETE
            ) from error
    proc_image = Path("/proc") / str(exact_pid) / "exe"
    try:
        return proc_image.resolve(strict=True)
    except OSError as error:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from error


def _require_sha256_input(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    return value


def _build_q8_provider_timing_diagnostic_argv(
    *,
    endpoint: str,
    model: str,
    output_dir: Path,
    seed: int,
    max_seconds: float,
    gpu_model_pid: int,
    provider_serving_executable_sha256: str,
    provider_profile_sha256: str,
    provider_profile_model: str,
    provider_profile_args: str,
    provider_backend_config_sha256: str,
    provider_identity: ProviderBuildIdentity,
    provider_profile_perf_enabled: bool,
) -> tuple[str, ...]:
    """Build one diagnostic subprocess argv from already-sanitized lifecycle facts."""

    serving_sha256 = _require_sha256_input(provider_serving_executable_sha256)
    profile_sha256 = _require_sha256_input(provider_profile_sha256)
    backend_sha256 = _require_sha256_input(provider_backend_config_sha256)
    if type(provider_profile_perf_enabled) is not bool:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    perf_flag = (
        "--provider-profile-perf-enabled"
        if provider_profile_perf_enabled
        else "--no-provider-profile-perf-enabled"
    )
    return (
        sys.executable,
        str(_SCRIPT),
        "--q8-provider-timing-diagnostic",
        "--endpoint",
        endpoint,
        "--model",
        model,
        "--output-dir",
        str(output_dir),
        "--seed",
        str(seed),
        "--max-seconds",
        str(max_seconds),
        "--gpu-model-pid",
        str(gpu_model_pid),
        "--provider-serving-executable-sha256",
        serving_sha256,
        "--provider-profile-sha256",
        profile_sha256,
        "--provider-profile-model",
        provider_profile_model,
        f"--provider-profile-args={provider_profile_args}",
        "--provider-backend-config-sha256",
        backend_sha256,
        "--provider-implementation",
        provider_identity.implementation,
        "--provider-version",
        provider_identity.version,
        "--provider-build",
        str(provider_identity.build),
        "--provider-commit",
        provider_identity.commit,
        perf_flag,
    )


def _provider_version_environment(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only OS bootstrap variables; never forward runner or user secrets."""

    source = os.environ if environ is None else environ
    allowed = {"SYSTEMROOT", "WINDIR"}
    return {key: value for key, value in source.items() if key.upper() in allowed}


def _run_bounded_provider_version(
    executable: Path,
    *,
    process_factory: Callable[..., Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> _BoundedProviderVersionOutput:
    """Run the exact image's fixed version command with bounded in-memory output."""

    process_factory = process_factory or subprocess.Popen

    def kill_and_wait(process: Any) -> int | None:
        try:
            process.kill()
        except Exception:
            pass
        try:
            return process.wait(timeout=_PROVIDER_VERSION_TERMINATION_SECONDS)
        except Exception:
            return None

    try:
        process = process_factory(
            (os.fspath(executable), "--version"),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_provider_version_environment(environ),
        )
    except Exception:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from None

    stream = process.stdout
    if stream is None:
        kill_and_wait(process)
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from None

    captured = bytearray()
    state = {"oversized": False, "read_failed": False}

    def drain_output() -> None:
        try:
            while True:
                chunk = stream.read(1024)
                if not chunk:
                    break
                remaining = _PROVIDER_VERSION_MAX_BYTES - len(captured)
                if remaining > 0:
                    captured.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    state["oversized"] = True
        except Exception:
            state["read_failed"] = True

    reader = threading.Thread(target=drain_output, daemon=True)
    reader.start()
    timed_out = False
    returncode: int | None = None
    try:
        returncode = process.wait(timeout=_PROVIDER_VERSION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = kill_and_wait(process)
    except Exception:
        timed_out = True
        returncode = kill_and_wait(process)
    reader.join(timeout=_PROVIDER_VERSION_TERMINATION_SECONDS)
    if reader.is_alive():
        timed_out = True
        try:
            stream.close()
        except Exception:
            pass
        reader.join(timeout=_PROVIDER_VERSION_TERMINATION_SECONDS)
    else:
        try:
            stream.close()
        except Exception:
            pass
    if state["read_failed"]:
        timed_out = True
    return _BoundedProviderVersionOutput(
        returncode=returncode,
        output=bytes(captured),
        timed_out=timed_out,
        oversized=state["oversized"],
    )


def _parse_provider_build_identity(output: bytes) -> ProviderBuildIdentity:
    """Parse only the fixed sanitized llama.cpp first-line identity."""

    if not isinstance(output, bytes) or len(output) > _PROVIDER_VERSION_MAX_BYTES:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    lines = output.splitlines()
    first_line = lines[0] if lines else b""
    match = _PROVIDER_VERSION_LINE_RE.fullmatch(first_line)
    if match is None:
        match = _PROVIDER_VERSION_PAREN_LINE_RE.fullmatch(first_line)
    if match is None:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    try:
        return ProviderBuildIdentity(
            implementation="llama.cpp",
            version=match.group(1).decode("ascii"),
            build=int(match.group(2)),
            commit=match.group(3).decode("ascii"),
        )
    except (UnicodeError, ValueError):
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from None


def _read_provider_build_identity(
    executable: Path,
    *,
    command_runner: Callable[[Path], _BoundedProviderVersionOutput] | None = None,
) -> ProviderBuildIdentity:
    """Derive a sanitized identity without exposing raw version output or paths."""

    command_runner = command_runner or _run_bounded_provider_version
    try:
        result = command_runner(executable)
        if (
            result.timed_out
            or result.oversized
            or type(result.returncode) is not int
            or result.returncode != 0
        ):
            raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
        return _parse_provider_build_identity(result.output)
    except ProviderTimingDiagnosticPreflightError:
        raise
    except Exception:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from None


def _provider_identity_binding_sha256(
    serving_executable_sha256: str,
    identity: ProviderBuildIdentity,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "build": identity.build,
                "commit": identity.commit,
                "implementation": identity.implementation,
                "serving_executable_sha256": serving_executable_sha256,
                "version": identity.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def _prepare_diagnostic_environment(
    args: argparse.Namespace,
    settings: LocalLLMSettings,
    *,
    process_path_resolver: Callable[[int], Path] | None = None,
    file_hasher: Callable[[Path], str] | None = None,
    identity_reader: Callable[[Path], ProviderBuildIdentity] | None = None,
) -> ProviderDiagnosticEnvironment:
    process_path_resolver = process_path_resolver or _resolve_process_executable
    file_hasher = file_hasher or _sha256_file
    identity_reader = identity_reader or _read_provider_build_identity
    pid = getattr(args, "gpu_model_pid", None)
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    expected_executable_hash = _require_sha256_input(
        getattr(args, "provider_serving_executable_sha256", None)
    )
    expected_profile_hash = _require_sha256_input(
        getattr(args, "provider_profile_sha256", None)
    )
    expected_backend_hash = _require_sha256_input(
        getattr(args, "provider_backend_config_sha256", None)
    )
    implementation = getattr(args, "provider_implementation", None)
    version = getattr(args, "provider_version", None)
    build = getattr(args, "provider_build", None)
    commit = getattr(args, "provider_commit", None)
    if (
        not isinstance(implementation, str)
        or _PROVIDER_IDENTITY_TOKEN_RE.fullmatch(implementation) is None
        or not isinstance(version, str)
        or _PROVIDER_IDENTITY_TOKEN_RE.fullmatch(version) is None
        or type(build) is not int
        or build < 0
        or not isinstance(commit, str)
        or _PROVIDER_COMMIT_RE.fullmatch(commit) is None
    ):
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    profile_model = getattr(args, "provider_profile_model", None)
    profile_arguments = getattr(args, "provider_profile_args", None)
    perf_enabled = getattr(args, "provider_profile_perf_enabled", None)
    if (
        not isinstance(profile_model, str)
        or profile_model != settings.model
        or not isinstance(profile_arguments, str)
        or type(perf_enabled) is not bool
    ):
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    actual_perf_enabled = re.search(
        r"(?:^|\s)--perf(?:\s|$)", profile_arguments
    ) is not None
    if actual_perf_enabled is not perf_enabled:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    if _canonical_profile_fingerprint(profile_model, profile_arguments) != expected_profile_hash:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    if settings.backend_config().config_fingerprint != expected_backend_hash:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    try:
        executable = process_path_resolver(pid)
        if not executable.is_file():
            raise OSError("resolved serving executable is not a file")
        actual_executable_hash = file_hasher(executable)
        if actual_executable_hash != expected_executable_hash:
            raise OSError("serving executable fingerprint mismatch")
        derived_identity = identity_reader(executable)
        if file_hasher(executable) != actual_executable_hash:
            raise OSError("serving executable changed during identity read")
    except Exception:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE) from None
    expected_identity = ProviderBuildIdentity(implementation, version, build, commit)
    if derived_identity != expected_identity:
        raise ProviderTimingDiagnosticPreflightError(_DIAGNOSTIC_INCOMPLETE)
    identity_binding_sha256 = _provider_identity_binding_sha256(
        actual_executable_hash, derived_identity
    )
    return ProviderDiagnosticEnvironment(
        serving_pid=pid,
        serving_executable=executable,
        serving_executable_sha256=actual_executable_hash,
        profile_sha256=expected_profile_hash,
        backend_config_sha256=expected_backend_hash,
        identity=derived_identity,
        identity_binding_sha256=identity_binding_sha256,
        provider_profile_perf_enabled=perf_enabled,
    )


def _validate_phase6_timeouts(read: object, request: object) -> tuple[float, float]:
    for value in (read, request):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("Phase 6 requires explicit finite positive read/request timeouts")
    if read > request or request >= _FEATURE_BRAIN_TIMEOUT_SECONDS:
        raise ValueError("Phase 6 requires read <= request < 44 seconds")
    return float(read), float(request)


def _game_broker_config(settings: LocalLLMSettings, *, phase6: bool) -> GenerationBrokerConfig:
    config = GenerationBrokerConfig()
    if not phase6:
        return config
    _, request = _validate_phase6_timeouts(
        settings.read_timeout_seconds, settings.request_timeout_seconds
    )
    drain = max(config.provider_drain_grace_seconds, request)
    return replace(
        config,
        provider_drain_grace_seconds=drain,
        shutdown_grace_seconds=max(
            config.shutdown_grace_seconds, drain + config.cancellation_grace_seconds
        ),
    )


def _prepare_run(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> RunConfig:
    effective = dict(os.environ if environ is None else environ)
    if args.endpoint is not None:
        effective["AIWOLF_LLM_ENDPOINT"] = args.endpoint
    if args.model is not None:
        effective["AIWOLF_LLM_MODEL"] = args.model
    phase6 = bool(getattr(args, "phase6", False))
    settings = replace(
        LocalLLMSettings.from_env(effective),
        generation=GenerationSettings(
            max_output_tokens=PHASE6_MAX_OUTPUT_TOKENS if phase6 else 96
        ),
    )
    read = getattr(args, "phase6_read_timeout_seconds", None)
    request = getattr(args, "phase6_request_timeout_seconds", None)
    if phase6:
        read, request = _validate_phase6_timeouts(read, request)
        settings = replace(
            settings, read_timeout_seconds=read, request_timeout_seconds=request,
            llama_cpp_structured_output=LlamaCppStructuredOutputConfig(),
        )
    elif read is not None or request is not None:
        raise ValueError("Phase 6 timeouts require --phase6")
    if isinstance(args.seed, bool) or not isinstance(args.seed, int):
        raise ValueError("--seed must be an integer")
    if (
        isinstance(args.max_seconds, bool)
        or not isinstance(args.max_seconds, (int, float))
        or not math.isfinite(args.max_seconds)
        or not 0 < args.max_seconds <= _SMOKE_HARD_LIMIT_SECONDS
    ):
        raise ValueError("--max-seconds must be in (0, 1200]")
    if bool(getattr(args, "phase6", False)) and settings.model != "Qwen3.5-9B-Q4_K_M.gguf":
        raise ValueError("Phase 6 requires the exact canonical 9B identity")
    gpu_model_pid = args.gpu_model_pid
    if gpu_model_pid is not None and (
        type(gpu_model_pid) is not int
        or gpu_model_pid <= 0
        or gpu_model_pid == os.getpid()
    ):
        raise ValueError("--gpu-model-pid must be a positive external PID")
    output_dir = Path(args.output_dir).resolve(strict=False)
    if output_dir.exists():
        raise FileExistsError(f"--output-dir must not exist: {output_dir}")
    if output_dir == PROJECT_ROOT or PROJECT_ROOT in output_dir.parents and output_dir.name in {"", "."}:
        raise ValueError("--output-dir must identify a new evidence directory")
    if phase6:
        expected_parent = (
            PROJECT_ROOT / "logs" / "phase6-private-evidence" / "game"
        ).resolve(strict=False)
        leaf_pattern = re.compile(
            r"^[A-Z][A-Z0-9_-]{1,31}-\d{8}T\d{12}Z$"
        )
        if output_dir.parent != expected_parent or leaf_pattern.fullmatch(output_dir.name) is None:
            raise ValueError(
                "Phase 6 --output-dir must be logs/phase6-private-evidence/game/<task>-<utc>"
            )
    diagnostic_mode = bool(getattr(args, "q8_provider_timing_diagnostic", False))
    if bool(args.q8) and diagnostic_mode:
        raise ValueError("--q8 and --q8-provider-timing-diagnostic are mutually exclusive")
    diagnostic_environment = (
        _prepare_diagnostic_environment(args, settings) if diagnostic_mode else None
    )
    return RunConfig(
        settings=settings,
        output_dir=output_dir,
        q8=bool(args.q8),
        q8_provider_timing_diagnostic=diagnostic_mode,
        gpu_model_pid=gpu_model_pid,
        seed=args.seed,
        max_seconds=float(args.max_seconds),
        sanitized_arguments=_sanitized_arguments(args),
        diagnostic_environment=diagnostic_environment,
        phase6=bool(getattr(args, "phase6", False)),
    )


def _phase6_envelopes(content: Any, preset: Any, game: Any) -> dict[str, object]:
    """Project only the exact validated objects owned by the server composition."""
    from ai_client.discussion.context import canonical_json_bytes, canonical_sha256, validate_discussion_bootstrap
    from server.aiwolf_core.models import PlayerRoleState, resolve_effective_win_conditions
    from server.aiwolf_core.targets import effective_attributes, chat_channels_for, passives_for

    if game.content is not content or game.rules is not preset.rules:
        raise ValueError("discussion source objects do not belong to this game")
    material = json.loads(canonical_json_bytes({
        "schema_version": "aiwolf.content-manifest.v1",
        "content_pack": content, "effective_preset": preset,
    }))
    manifest_hash = canonical_sha256(material)
    envelopes = {}
    for player_id, player in game.players.items():
        wins = resolve_effective_win_conditions(
            PlayerRoleState(player_id, player.role, player.modifiers), content.teams
        )
        context = {
            "schema_version": "aiwolf.discussion-context.v1",
            "game_id": game.game_id, "player_id": player_id, "role_id": player.role.id,
            "modifier_ids": sorted(item.definition.id for item in player.modifiers),
            "content_manifest_sha256": manifest_hash,
            **asdict(effective_attributes(player)),
            "win_conditions": sorted((dict(item.data) for item in wins), key=canonical_json_bytes),
            "abilities": [{
                "ability_id": item.id, "timing": item.timing,
                "available_from_night": item.available_from_night, "priority": item.priority,
                "resolution": item.resolution, "target_selector": item.target.selector,
                "target_count": item.target.count, "uses_per_night": item.uses.per_night,
                "uses_per_game": item.uses.per_game, "no_selection": item.no_selection,
                "effect_ids": sorted(effect.id for effect in item.effects),
            } for item in sorted(player.role.abilities, key=lambda item: item.id)],
            "passives": sorted(({
                "type": item.type, "priority": item.priority,
                "effect_ids": sorted(effect.id for effect in item.effects),
            } for item in passives_for(player)), key=canonical_json_bytes),
            "chat_channels": [{"channel_id": channel, "is_public": content.chat_channels[channel].is_public}
                              for channel in sorted(chat_channels_for(player))],
            "knows_teammates": player.role.knowledge.knows_teammates or any(
                item.definition.knowledge.knows_teammates for item in player.modifiers),
            "authorized_known_player_ids": [], "known_players_complete": False,
        }
        envelope = {"schema_version": "aiwolf.discussion-bootstrap.v1",
                    "manifest_material": material, "manifest_sha256": manifest_hash,
                    "context_payload": context, "context_sha256": canonical_sha256(context)}
        pending = validate_discussion_bootstrap(envelope, network_game_id=game.game_id, player_id=player_id)
        pending.discard_manifest()
        envelopes[player_id] = envelope
    return envelopes


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate private JSON key")
        value[key] = item
    return value


def _consume_phase6_relay(path: Path, game_id: str, player_ids: Sequence[str]) -> dict[str, object]:
    """Validate the complete private relay, then remove it before any client launch."""
    from ai_client.discussion.context import validate_discussion_bootstrap
    if len(player_ids) != 9 or len(set(player_ids)) != 9:
        raise ValueError("discussion relay needs exactly nine unique seats")
    with path.open("rb") as source:
        payload = source.read(9 * 80 * 1024 + 8193)
    if len(payload) > 9 * 80 * 1024 + 8192:
        raise ValueError("discussion relay exceeds bound")
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
    if not isinstance(value, dict) or set(value) != set(player_ids):
        raise ValueError("discussion relay seat set mismatch")
    identity = None
    for player_id in player_ids:
        pending = validate_discussion_bootstrap(value[player_id], network_game_id=game_id, player_id=player_id)
        try:
            current = (pending.manifest_sha256, pending.canonical_manifest_bytes())
            if identity is not None and current != identity:
                raise ValueError("discussion relay manifests differ")
            identity = current
        finally:
            pending.discard_manifest()
    path.unlink()
    return value


def nearest_rank(values: Sequence[int], percentile: float) -> int | None:
    """Return the nearest-rank percentile over non-negative integer samples."""

    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    if not values:
        return None
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("percentile samples must be non-negative integers")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _distribution(values: Sequence[int]) -> dict[str, int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "median": nearest_rank(values, 0.50),
        "p95": nearest_rank(values, 0.95),
        "max": max(values) if values else None,
    }


def _metric_aggregates(metrics: Sequence[Mapping[str, object]]) -> dict[str, object]:
    enqueued: dict[str, tuple[int, str, str]] = {}
    offer_wait: list[int] = []
    claim_wait: list[int] = []
    request_latency: list[int] = []
    offer_by_priority: dict[str, list[int]] = defaultdict(list)
    offer_by_client: dict[str, list[int]] = defaultdict(list)
    claim_by_priority: dict[str, list[int]] = defaultdict(list)
    claim_by_client: dict[str, list[int]] = defaultdict(list)
    prompt_token_counts: list[int] = []
    completion_token_counts: list[int] = []
    prompt_rates: list[int] = []
    generation_rates: list[int] = []
    provider_calls = 0
    provider_timing_complete = True
    terminal_counts: Counter[str] = Counter()
    backend_failures = 0
    provider_invocations: set[str] = set()
    active_cancellations: set[str] = set()
    for item in metrics:
        invocation = item.get("invocation_id")
        timestamp = item.get("monotonic_microseconds")
        if item.get("event") == "ENQUEUED" and isinstance(invocation, str) and type(timestamp) is int:
            enqueued.setdefault(
                invocation,
                (timestamp, str(item.get("priority")), str(item.get("client_id"))),
            )
        if item.get("event") == "OFFERED":
            wait = item.get("queue_wait_microseconds")
            if type(wait) is int:
                offer_wait.append(wait)
                offer_by_priority[str(item.get("priority"))].append(wait)
                offer_by_client[str(item.get("client_id"))].append(wait)
        if item.get("event") == "CLAIMED" and isinstance(invocation, str) and type(timestamp) is int:
            if invocation in enqueued:
                started, priority, client_id = enqueued[invocation]
                wait = max(0, timestamp - started)
                claim_wait.append(wait)
                claim_by_priority[priority].append(wait)
                claim_by_client[client_id].append(wait)
        if item.get("event") == "TERMINAL":
            terminal_counts[str(item.get("terminal_status"))] += 1
            if isinstance(invocation, str) and type(timestamp) is int and invocation in enqueued:
                request_latency.append(max(0, timestamp - enqueued[invocation][0]))
            if isinstance(invocation, str) and item.get("terminal_status") == "ABANDONED_DRAINED":
                active_cancellations.add(invocation)
        if item.get("event") == "DRAINING" and isinstance(invocation, str):
            active_cancellations.add(invocation)
        if item.get("event") == "PROVIDER_CALL_TERMINAL":
            provider_calls += 1
            if isinstance(invocation, str):
                provider_invocations.add(invocation)
            if item.get("backend_code") is not None:
                backend_failures += 1
            prompt_tokens = item.get("prompt_tokens")
            completion_tokens = item.get("completion_tokens")
            prompt_us = item.get("provider_prompt_microseconds")
            completion_us = item.get("provider_completion_microseconds")
            complete = all(
                type(value) is int and value >= 0
                for value in (prompt_tokens, completion_tokens, prompt_us, completion_us)
            )
            if not complete:
                provider_timing_complete = False
                continue
            assert isinstance(prompt_tokens, int)
            assert isinstance(completion_tokens, int)
            assert isinstance(prompt_us, int)
            assert isinstance(completion_us, int)
            prompt_token_counts.append(prompt_tokens)
            completion_token_counts.append(completion_tokens)
            if (prompt_tokens and prompt_us == 0) or (completion_tokens and completion_us == 0):
                provider_timing_complete = False
                continue
            prompt_rates.append(0 if prompt_us == 0 else prompt_tokens * 1_000_000 // prompt_us)
            generation_rates.append(
                0 if completion_us == 0 else completion_tokens * 1_000_000 // completion_us
            )
    if provider_calls == 0:
        provider_timing_complete = False
    return {
        "queue_enqueue_to_offer_microseconds": _distribution(offer_wait),
        "queue_enqueue_to_claim_microseconds": _distribution(claim_wait),
        "request_end_to_end_microseconds": _distribution(request_latency),
        "queue_by_priority": {
            key: {
                "enqueue_to_offer_microseconds": _distribution(offer_by_priority.get(key, [])),
                "enqueue_to_claim_microseconds": _distribution(claim_by_priority.get(key, [])),
            }
            for key in sorted(set(offer_by_priority) | set(claim_by_priority))
        },
        "queue_by_opaque_client": {
            key: {
                "enqueue_to_offer_microseconds": _distribution(offer_by_client.get(key, [])),
                "enqueue_to_claim_microseconds": _distribution(claim_by_client.get(key, [])),
            }
            for key in sorted(set(offer_by_client) | set(claim_by_client))
        },
        "provider_prompt_tokens": _distribution(prompt_token_counts),
        "provider_completion_tokens": _distribution(completion_token_counts),
        "provider_prompt_tokens_per_second": _distribution(prompt_rates),
        "provider_generation_tokens_per_second": _distribution(generation_rates),
        "provider_calls": provider_calls,
        "provider_timing_complete": provider_timing_complete,
        "backend_failure_count": backend_failures,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "admission_expiry_count": terminal_counts["EXPIRED"],
        "pre_model_stale_cancellation_count": sum(
            count
            for status, count in terminal_counts.items()
            if status in {"CANCELLED", "REPLACED"}
        )
        - len(
            {
                invocation
                for invocation in provider_invocations
                if any(
                    item.get("event") == "TERMINAL"
                    and item.get("invocation_id") == invocation
                    and item.get("terminal_status") in {"CANCELLED", "REPLACED"}
                    for item in metrics
                )
            }
        ),
        "active_cancellation_count": len(active_cancellations),
        "overload_count": terminal_counts["OVERLOADED"],
        "deadline_miss_count": terminal_counts["EXPIRED"]
        + sum(
            item.get("event") == "PROVIDER_CALL_TERMINAL"
            and item.get("backend_code") == "ADMISSION_EXPIRED"
            for item in metrics
        ),
    }


def _parse_nvidia_smi(output: str, exact_pid: int) -> int | None:
    matches: list[int] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            pid, used = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid == exact_pid and used >= 0:
            matches.append(used)
    return sum(matches) if matches else None


async def _probe_external_listener(
    endpoint: str,
    *,
    connector: Callable[..., Awaitable[tuple[Any, Any]]] = asyncio.open_connection,
    timeout_seconds: float = _EXTERNAL_INVENTORY_TIMEOUT_SECONDS,
) -> bool | None:
    """Connect and immediately close; never send a model request or mutate the listener."""

    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host or parsed.scheme not in {"http", "https"}:
            return None
        _, writer = await asyncio.wait_for(connector(host, port), timeout_seconds)
    except (OSError, TimeoutError, ValueError):
        return False
    try:
        writer.close()
        if hasattr(writer, "wait_closed"):
            await asyncio.wait_for(writer.wait_closed(), timeout_seconds)
    except (OSError, TimeoutError):
        pass
    return True


def _probe_external_pid(exact_pid: int) -> bool | None:
    """Use a read-only process query; never send a signal to the target PID."""

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, exact_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                close_handle(handle)
                return True
            error = ctypes.get_last_error()
            return False if error in {87, 1168} else None
        except (AttributeError, OSError, ValueError):
            return None
    proc = Path("/proc")
    if proc.is_dir():
        return (proc / str(exact_pid)).exists()
    return None


async def _external_model_inventory(
    endpoint: str,
    exact_pid: int | None,
    *,
    listener_probe: Callable[[str], Awaitable[bool | None]] = _probe_external_listener,
    pid_probe: Callable[[int], bool | None] = _probe_external_pid,
) -> dict[str, object]:
    listener = await listener_probe(endpoint)
    pid_available = None if exact_pid is None else pid_probe(exact_pid)

    def availability(value: bool | None, *, omitted: bool = False) -> str:
        if omitted:
            return "NOT_CONFIGURED"
        if value is None:
            return "QUERY_UNAVAILABLE"
        return "AVAILABLE" if value else "UNAVAILABLE"

    return {
        "ownership": "EXTERNAL_UNOWNED",
        "endpoint": endpoint,
        "listener_availability": availability(listener),
        "model_pid": exact_pid,
        "model_pid_availability": availability(
            pid_available, omitted=exact_pid is None
        ),
        "control_actions": [],
    }


def _validate_external_model_evidence(
    before: Mapping[str, object],
    after: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    exact_pid: int | None,
) -> list[str]:
    errors: list[str] = []
    allowed_availability = {"AVAILABLE", "UNAVAILABLE", "QUERY_UNAVAILABLE"}
    for label, inventory in (("before", before), ("after", after)):
        if (
            inventory.get("ownership") != "EXTERNAL_UNOWNED"
            or inventory.get("listener_availability") not in allowed_availability
            or inventory.get("model_pid") != exact_pid
            or inventory.get("control_actions") != []
        ):
            errors.append(f"external model {label} inventory is malformed")
        expected_pid_states = (
            {"NOT_CONFIGURED"}
            if exact_pid is None
            else allowed_availability
        )
        if inventory.get("model_pid_availability") not in expected_pid_states:
            errors.append(f"external model {label} PID inventory is malformed")
    if exact_pid is not None and any(
        item.get("pid") == exact_pid
        for row in rows
        for item in row.get("cleanup", [])
        if isinstance(item, Mapping)
    ):
        errors.append("external model PID entered the owned process collection")
    return errors


async def _sample_gpu_once(exact_pid: int) -> int | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-compute-apps=pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 5.0)
    except TimeoutError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        return None
    if process.returncode != 0:
        return None
    return _parse_nvidia_smi(stdout.decode("utf-8", errors="replace"), exact_pid)


class _GpuSampler:
    def __init__(
        self,
        exact_pid: int | None,
        *,
        sample_once: Callable[[int], Awaitable[int | None]] = _sample_gpu_once,
        interval_seconds: float = _GPU_INTERVAL_SECONDS,
    ) -> None:
        self._pid = exact_pid
        self._sample_once = sample_once
        self._interval = interval_seconds
        self._samples: list[int] = []
        self._unavailable = False
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._pid is not None:
            self._task = asyncio.create_task(self._run(), name="phase5-gpu-sampler")

    async def stop(self) -> dict[str, object]:
        self._stop.set()
        if self._task is not None:
            await self._task
        if self._pid is None:
            return {"status": "NOT_REQUESTED", "pid": None, "interval_ms": 250}
        if self._unavailable or not self._samples:
            return {"status": "UNAVAILABLE", "pid": self._pid, "interval_ms": 250}
        return {
            "status": "AVAILABLE",
            "pid": self._pid,
            "interval_ms": 250,
            "samples": len(self._samples),
            "start_used_mib": self._samples[0],
            "peak_used_mib": max(self._samples),
            "end_used_mib": self._samples[-1],
        }

    async def _run(self) -> None:
        assert self._pid is not None
        while True:
            value = await self._sample_once(self._pid)
            if value is None:
                self._unavailable = True
                return
            self._samples.append(value)
            try:
                await asyncio.wait_for(self._stop.wait(), self._interval)
            except TimeoutError:
                continue
            return


async def _spawn_owned(
    owned: list[OwnedProcess],
    *,
    label: str,
    arguments: Sequence[str],
    output_dir: Path,
    bootstrap: Mapping[str, object],
    environ: Mapping[str, str],
    process_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
) -> OwnedProcess:
    stdout_path = output_dir / f"{label}.stdout.log"
    stderr_path = output_dir / f"{label}.stderr.log"
    with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
        process = await process_factory(
            sys.executable,
            str(_SCRIPT),
            *arguments,
            cwd=str(PROJECT_ROOT),
            env=_child_environment(environ),
            stdin=asyncio.subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        item = OwnedProcess(label, process, stdout_path, stderr_path)
        owned.append(item)
    os.chmod(stdout_path, _PRIVATE_FILE_MODE)
    os.chmod(stderr_path, _PRIVATE_FILE_MODE)
    if process.stdin is None:
        raise RuntimeError("private bootstrap pipe unavailable")
    payload = json.dumps(
        dict(bootstrap),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    process.stdin.write(payload)
    await process.stdin.drain()
    process.stdin.close()
    if hasattr(process.stdin, "wait_closed"):
        await process.stdin.wait_closed()
    return item


async def _wait_for_paths(
    paths: Sequence[Path],
    processes: Sequence[OwnedProcess],
    timeout_seconds: float,
) -> None:
    async def wait() -> None:
        while not all(path.exists() for path in paths):
            failed = {
                item.label: item.process.returncode
                for item in processes
                if item.process.returncode not in {None, 0}
            }
            if failed:
                raise RuntimeError(f"owned child failed before evidence: {failed}")
            await asyncio.sleep(0.025)

    await asyncio.wait_for(wait(), timeout_seconds)


async def _settle_owned(item: OwnedProcess, model_pid: int | None) -> dict[str, object]:
    pid = item.process.pid
    if model_pid is not None and pid == model_pid:
        return {
            "label": item.label,
            "pid": pid,
            "returncode": item.process.returncode,
            "alive": item.process.returncode is None,
            "action": "REFUSED_EXTERNAL_MODEL_PID",
        }
    if item.process.returncode is not None:
        item.touched = "wait"
        await item.process.wait()
    else:
        try:
            item.touched = "wait"
            await asyncio.wait_for(item.process.wait(), _STOP_GRACE_SECONDS)
        except TimeoutError:
            item.touched = "terminate"
            item.process.terminate()
            try:
                await asyncio.wait_for(item.process.wait(), _STOP_GRACE_SECONDS)
            except TimeoutError:
                item.touched = "kill"
                item.process.kill()
                await item.process.wait()
    return {
        "label": item.label,
        "pid": pid,
        "returncode": item.process.returncode,
        "alive": item.process.returncode is None,
        "action": item.touched,
    }


async def _cleanup_owned(
    owned: Sequence[OwnedProcess], model_pid: int | None
) -> list[dict[str, object]]:
    return list(
        await asyncio.gather(*(_settle_owned(item, model_pid) for item in owned))
    )


async def _cleanup_owned_shielded(
    owned: Sequence[OwnedProcess], model_pid: int | None
) -> list[dict[str, object]]:
    """Finish cleanup even if the supervising task is cancelled again."""

    task = asyncio.create_task(_cleanup_owned(owned, model_pid))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        return await task


def _touch_stop(paths: Sequence[Path]) -> None:
    for path in paths:
        if not path.exists():
            path.write_text("stop\n", encoding="utf-8")
            os.chmod(path, _PRIVATE_FILE_MODE)


def _new_private_subdirectory(parent: Path, name: str) -> Path:
    target = parent / name
    _private_directory(target)
    return target


def _process_inventory(owned: Sequence[OwnedProcess]) -> list[dict[str, object]]:
    return [
        {
            "label": item.label,
            "pid": item.process.pid,
            "returncode": item.process.returncode,
            "alive": item.process.returncode is None,
            "action": item.touched,
        }
        for item in owned
    ]


def _strict_short_request(bucket: str, request_id: str) -> StructuredGenerationRequest:
    retained = {"minimum": 0, "median": 16, "maximum": 32}.get(bucket)
    if retained is None:
        raise ValueError("unknown prompt bucket")
    history = [
        {
            "order": index + 1,
            "player_id": f"synthetic-{index % 9}",
            "message": f"synthetic retained chat {index + 1}",
        }
        for index in range(retained)
    ]
    user = json.dumps(
        {"history": history, "request": "write one short synthetic utterance"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    schema = {
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "chat"},
            "message": {"maxLength": 80, "minLength": 1, "type": "string"},
        },
        "required": ["kind", "message"],
        "type": "object",
    }
    request = StructuredGenerationRequest(
        request_id=request_id,
        messages=(
            LLMMessage(
                "system",
                "Return one JSON short utterance targeting 5-30 model tokens; "
                "message must be at most 80 characters and 96 UTF-8 bytes.",
            ),
            LLMMessage("user", user),
        ),
        output_schema=schema,
    )
    encoded = json.dumps(
        {
            "messages": [asdict(item) for item in request.messages],
            "output_schema": schema,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > 32768:
        raise ValueError("synthetic prompt exceeds production prompt bound")
    return request


def _strict_short_response(text: str) -> tuple[int, int]:
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"kind", "message"}:
        raise ValueError("invalid strict short response shape")
    message = value.get("message")
    if value.get("kind") != "chat" or not isinstance(message, str) or not message:
        raise ValueError("invalid strict short response")
    chars = len(message)
    byte_count = len(message.encode("utf-8"))
    if chars > 80 or byte_count > 96:
        raise ValueError("strict short response exceeds text bounds")
    return chars, byte_count


def _strict_q8_response(response: Any) -> tuple[int, int]:
    if response.finish_reason == "length":
        raise ValueError("Q8 response ended at the provider length limit")
    return _strict_short_response(response.text)


def _manifest_entry(path: Path, relative_to: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.relative_to(relative_to)).replace("\\", "/"),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(payload.splitlines()),
        "terminal": True,
    }


def _write_manifest(
    ai_dir: Path,
    statuses: Mapping[str, Mapping[str, object]],
    metrics_path: Path,
    player_to_client: Mapping[str, str],
) -> Path:
    manifest = _manifest_value(ai_dir, statuses, metrics_path, player_to_client)
    target = ai_dir / "manifest.json"
    _write_private_json_atomic(target, manifest)
    return target


def _manifest_value(
    ai_dir: Path,
    statuses: Mapping[str, Mapping[str, object]],
    metrics_path: Path,
    player_to_client: Mapping[str, str],
) -> dict[str, object]:
    shards: dict[str, object] = {}
    for player_id, status in sorted(statuses.items()):
        audit = status.get("audit")
        if not isinstance(audit, Mapping) or not isinstance(audit.get("path"), str):
            raise ValueError("client audit evidence missing")
        path = ai_dir / str(audit["path"])
        shards[player_id] = _manifest_entry(path, ai_dir)
    return {
        "schema": "aiwolf.phase5-private-manifest.v1",
        "player_to_opaque_client_id": dict(sorted(player_to_client.items())),
        "shards": shards,
        "metadata": _manifest_entry(metrics_path, ai_dir),
        "terminal": True,
    }


def _validate_manifest(path: Path, ai_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        value = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return ["manifest missing or malformed"]
    if value.get("terminal") is not True:
        errors.append("manifest is not terminal")
    entries: list[object] = []
    shards = value.get("shards")
    if isinstance(shards, dict):
        entries.extend(shards.values())
    else:
        errors.append("manifest shards missing")
    entries.append(value.get("metadata"))
    if value.get("schema") == "aiwolf.phase6-private-manifest.v1":
        entries.append(value.get("accepted_text"))
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("manifest entry malformed")
            continue
        artifact = ai_dir / str(entry["path"])
        try:
            payload = artifact.read_bytes()
        except OSError:
            errors.append("manifest artifact missing")
            continue
        if entry.get("bytes") != len(payload) or entry.get("sha256") != hashlib.sha256(payload).hexdigest():
            errors.append("manifest hash or byte count mismatch")
        if entry.get("record_count") != len(payload.splitlines()) or entry.get("terminal") is not True:
            errors.append("manifest record count or terminal mismatch")
    return errors


def _validate_private_shard_identity(
    statuses: Mapping[str, Mapping[str, object]],
    manifest_path: Path,
    expected_player_to_client: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    expected_players = {f"player-{index}" for index in range(9)}
    if set(statuses) != expected_players:
        return ["client status player set is not the exact nine-seat set"]
    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return ["manifest player-to-opaque shard mapping mismatch"]
    mapping = manifest.get("player_to_opaque_client_id")
    shards = manifest.get("shards")
    mapping_valid = (
        isinstance(mapping, dict)
        and mapping == dict(expected_player_to_client)
        and set(mapping) == expected_players
        and all(isinstance(value, str) and value for value in mapping.values())
        and len(set(mapping.values())) == 9
        and isinstance(shards, dict)
        and set(shards) == expected_players
    )
    if not mapping_valid:
        errors.append("manifest player-to-opaque shard mapping mismatch")
        mapping = {}
        shards = {}
    seen_request_ids: set[str] = set()
    for player_id in sorted(expected_players):
        status = statuses[player_id]
        if status.get("player_id") != player_id:
            errors.append("client status player identity mismatch")
        audit = status.get("audit")
        if not isinstance(audit, Mapping):
            errors.append("audit shard identity evidence missing")
            continue
        if audit.get("player_ids") != [player_id]:
            errors.append("audit shard player identity mismatch")
        request_ids = audit.get("request_ids")
        if (
            not isinstance(request_ids, list)
            or not request_ids
            or any(not isinstance(value, str) or not value for value in request_ids)
        ):
            errors.append("audit shard request IDs missing or malformed")
        else:
            shard_request_ids = set(request_ids)
            if len(shard_request_ids) != len(request_ids):
                errors.append("audit shard request IDs are not unique")
            elif seen_request_ids & shard_request_ids:
                errors.append("audit request IDs are not globally disjoint")
            else:
                seen_request_ids.update(shard_request_ids)
        if mapping and shards:
            opaque = mapping[player_id]
            expected_path = f"{opaque}/ai.jsonl"
            shard = shards[player_id]
            if (
                audit.get("path") != expected_path
                or not isinstance(shard, Mapping)
                or shard.get("path") != expected_path
            ):
                errors.append("manifest player-to-opaque shard mapping mismatch")
    return errors


def _validate_game_evidence(
    *,
    statuses: Mapping[str, Mapping[str, object]],
    server_result: Mapping[str, object],
    broker_result: Mapping[str, object],
    metrics: Sequence[Mapping[str, object]],
    manifest: Path,
    ai_dir: Path,
    owned: Sequence[OwnedProcess],
    sentinels: Sequence[str],
    evidence_root: Path,
    expected_player_to_client: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    if len(statuses) != 9:
        errors.append("nine client status records are required")
    if server_result.get("success") is not True or server_result.get("game_end") is not True:
        errors.append("server game end missing")
    if server_result.get("listener_closed") is not True:
        errors.append("game listener not closed")
    if broker_result.get("success") is not True or broker_result.get("shutdown_clean") is not True:
        errors.append("broker shutdown not clean")
    if broker_result.get("listener_closed") is not True:
        errors.append("broker listener not closed")
    if broker_result.get("peak_backend_concurrency") != 1:
        errors.append("global peak backend concurrency is not one")
    if broker_result.get("metrics_dropped") != 0:
        errors.append("admission metrics were dropped")
    after_close = broker_result.get("snapshot_after_close")
    if not isinstance(after_close, Mapping) or not (
        after_close.get("pending_total") == 0
        and after_close.get("offered") is None
        and after_close.get("claimed") is None
        and after_close.get("provider_call_active") is False
        and after_close.get("draining") is False
        and after_close.get("poisoned") is False
    ):
        errors.append("broker retained work or listener state after cleanup")
    pids = [status.get("pid") for status in statuses.values()]
    if len(set(pids)) != 9 or any(type(pid) is not int for pid in pids):
        errors.append("client PID evidence is not nine distinct processes")
    all_reported_pids = set(pids) | {
        server_result.get("server_pid"),
        broker_result.get("pid"),
    }
    if len(owned) != 11 or len(all_reported_pids) != 11 or os.getpid() in all_reported_pids:
        errors.append("owned process topology is not one server, one broker, and nine clients")
    for status in statuses.values():
        if not (
            status.get("runtime_success") is True
            and status.get("runtime_exit") == "GAME_ENDED"
            and status.get("world_exit") == "CLIENT_ENDED"
            and status.get("reaction_exit") == "WORLD_ENDED"
            and status.get("vote_ability_exit") == "WORLD_ENDED"
        ):
            errors.append("client terminal evidence incomplete")
        brain = status.get("brain")
        audit = status.get("audit")
        if not isinstance(brain, Mapping) or not isinstance(brain.get("backend_calls"), int) or brain["backend_calls"] < 1:
            errors.append("client made no real structured call")
        if not isinstance(audit, Mapping) or not isinstance(audit.get("record_count"), int) or audit["record_count"] < 1:
            errors.append("client audit shard empty")
        if isinstance(audit, Mapping) and audit.get("membership_valid") is not True:
            errors.append("fabricated option or target evidence")
    enqueued = {
        item.get("invocation_id") for item in metrics if item.get("event") == "ENQUEUED"
    }
    terminal = {
        item.get("invocation_id") for item in metrics if item.get("event") == "TERMINAL"
    }
    if not enqueued or not enqueued <= terminal:
        errors.append("not all broker entries are terminal")
    if any(
        item.get("terminal_status") in {"OVERLOADED", "POISONED"}
        or (item.get("event") == "PROVIDER_CALL_TERMINAL" and item.get("backend_code") is not None)
        for item in metrics
    ):
        errors.append("broker overload, poison, or backend failure")
    day_one_chat = [
        item
        for item in server_result.get("accepted_chats", [])
        if isinstance(item, Mapping) and item.get("day") == 1 and item.get("phase") == "day"
    ]
    if {item.get("player_id") for item in day_one_chat} != set(statuses):
        errors.append("not every Day-1 living seat has accepted short chat")
    if any(
        not (
            isinstance(item.get("message_chars"), int)
            and 1 <= item["message_chars"] <= 80
            and isinstance(item.get("message_utf8_bytes"), int)
            and 1 <= item["message_utf8_bytes"] <= 96
            and item.get("accepted_at", math.inf) < item.get("phase_deadline", -math.inf)
        )
        for item in day_one_chat
    ):
        errors.append("accepted short chat violates bound or deadline")
    expected_items = server_result.get("expected_reservations", [])
    expected_groups: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    if isinstance(expected_items, list):
        for item in expected_items:
            if isinstance(item, Mapping):
                expected_groups[
                    (
                        item.get("player_id"),
                        item.get("day"),
                        item.get("phase"),
                        item.get("action"),
                    )
                ].append(item)
            else:
                errors.append("server-authoritative reservation evidence is malformed")
    else:
        errors.append("server-authoritative reservation evidence is malformed")
    expected = {
        key: items[0]
        for key, items in expected_groups.items()
        if len(items) == 1
    }
    accepted: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    accepted_items = server_result.get("accepted_reservations", [])
    if isinstance(accepted_items, list):
        for item in accepted_items:
            if isinstance(item, Mapping):
                accepted[
                    (
                        item.get("player_id"),
                        item.get("day"),
                        item.get("phase"),
                        item.get("action"),
                    )
                ].append(item)
            else:
                errors.append("accepted reservation evidence is malformed")
    else:
        errors.append("accepted reservation evidence is malformed")
    if not expected:
        errors.append("expected vote/ability reservation evidence missing")
    if (
        any(len(items) != 1 for items in expected_groups.values())
        or set(accepted) != set(expected)
        or any(len(items) != 1 for items in accepted.values())
    ):
        errors.append("accepted vote/ability correlation differs from exact expected set")
    if any(
        not item.get("request_event_id")
        or item.get("accepted_at", math.inf) >= item.get("phase_deadline", -math.inf)
        for items in accepted.values()
        for item in items
    ):
        errors.append("accepted reservation lacks correlation or precedes no deadline")
    for key in set(accepted) & set(expected):
        if len(accepted[key]) != 1:
            continue
        item = accepted[key][0]
        expectation = expected[key]
        action = item.get("action")
        valid = False
        if action == "vote.cast":
            target = item.get("vote_target_player_id")
            valid_targets = expectation.get("valid_targets")
            target_count = expectation.get("target_count")
            targets = [] if target is None else [target]
            valid = (
                item.get("ability_id") is None
                and item.get("ability_target_player_ids") == []
                and type(target_count) is int
                and target_count == 1
                and isinstance(valid_targets, list)
                and len(targets) == target_count
                and all(target not in targets[:index] for index, target in enumerate(targets))
                and all(target in valid_targets for target in targets)
            )
        elif action == "ability.use":
            offered = expectation.get("abilities")
            matching = (
                [
                    ability
                    for ability in offered
                    if isinstance(ability, Mapping)
                    and ability.get("ability_id") == item.get("ability_id")
                ]
                if isinstance(offered, list)
                else []
            )
            targets = item.get("ability_target_player_ids")
            if len(matching) == 1 and isinstance(targets, list):
                ability = matching[0]
                valid_targets = ability.get("valid_targets")
                target_count = ability.get("target_count")
                valid = (
                    item.get("vote_target_player_id") is None
                    and isinstance(item.get("ability_id"), str)
                    and bool(item.get("ability_id"))
                    and type(target_count) is int
                    and isinstance(valid_targets, list)
                    and len(targets) == target_count
                    and all(target not in targets[:index] for index, target in enumerate(targets))
                    and all(target in valid_targets for target in targets)
                )
        if not valid:
            errors.append("accepted reservation violates server-authoritative offer")
    errors.extend(_validate_manifest(manifest, ai_dir))
    errors.extend(
        _validate_private_shard_identity(
            statuses, manifest, expected_player_to_client
        )
    )
    phase_wall = server_result.get("phase_wall_durations")
    total_wall = server_result.get("total_game_wall_microseconds")
    if (
        not isinstance(phase_wall, list)
        or not phase_wall
        or any(
            not isinstance(item, Mapping)
            or type(item.get("wall_microseconds")) is not int
            or item["wall_microseconds"] < 0
            for item in phase_wall
        )
        or type(total_wall) is not int
        or total_wall < 0
    ):
        errors.append("game wall-duration evidence missing or malformed")
    for item in owned:
        if item.process.returncode != 0:
            errors.append(f"owned process failed: {item.label}")
        if item.process.returncode is None:
            errors.append(f"owned process alive after cleanup: {item.label}")
        if _bounded_tail(item.stderr_path):
            errors.append(f"non-empty child stderr: {item.label}")
    evidence_files = [path for path in evidence_root.rglob("*") if path.is_file()]
    for path in evidence_files:
        data = path.read_text(encoding="utf-8", errors="replace")
        if any(secret and secret in data for secret in sentinels):
            errors.append(f"secret retained in evidence: {path.name}")
    return errors


def _validate_phase6_captured_identity(
    record: Mapping[str, Any], projected: Mapping[str, Any],
) -> None:
    """Bind duplicated audited identities to the actual bounded prompt input.

    The projection omits some original state, so this does not reconstruct a
    capture hash or state hash. It verifies every identity counterpart retained
    by the existing projection before the collector trusts its audit buckets.
    """
    from ai_client.discussion.context import canonical_sha256
    from ai_client.discussion.model import (
        DiscussionTrigger, EvidenceRef, EvidenceRecordKind, EvidenceVisibility,
    )

    def require_equal(actual: object, expected: object) -> None:
        # Ordinary equality would accept True as day/world/revision 1.
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError("captured input identity mismatch")

    try:
        require_equal(projected["schema_version"], "aiwolf.discussion-prompt.v1")
        context, capture = projected["context"], projected["capture"]
        state, actions = projected["state"]["identity"], projected["action_context"]
        trigger = capture["trigger"]
        require_equal(context["game_id"], record["game_id"])
        require_equal(context["player_id"], record["player_id"])
        for capture_name, audit_name in (
            ("capture_id", "capture_id"), ("context_sha256", "context_sha256"),
            ("state_sha256", "before_state_sha256"), ("base_revision", "base_revision"),
            ("world_version", "world_version"),
        ):
            require_equal(capture[capture_name], record[audit_name])
        require_equal(canonical_sha256(context), capture["context_sha256"])
        for name in ("day", "phase"):
            require_equal(trigger[name], record[name])
            require_equal(state[name], trigger[name])
        for name in ("epoch", "fact_revision", "world_version", "last_applied_seq"):
            if type(capture[name]) is not int or capture[name] < 0:
                raise ValueError("captured input identity invalid")
            require_equal(state[name], capture[name])
        require_equal(state["revision"], capture["base_revision"])
        require_equal(actions["world_version"], capture["world_version"])
        require_equal(actions["world_last_applied_seq"], capture["last_applied_seq"])
        require_equal(actions["network_last_seq"], capture["last_applied_seq"])
        players = capture["current_player_ids"]
        if (not isinstance(players, list) or not players
                or any(type(player) is not str for player in players)
                or players != sorted(set(players)) or context["player_id"] not in players):
            raise ValueError("captured player identity invalid")
        if type(capture["capture_ordinal"]) is not int or capture["capture_ordinal"] < 1:
            raise ValueError("captured ordinal invalid")
        typed_trigger = dict(trigger)
        if trigger["source"] is not None:
            source = trigger["source"]
            if set(source) != {"record_kind", "order", "visibility"}:
                raise ValueError("captured trigger source invalid")
            typed_trigger["source"] = EvidenceRef(
                EvidenceRecordKind(source["record_kind"]), source["order"],
                EvidenceVisibility(source["visibility"]),
            )
        DiscussionTrigger(**typed_trigger)
        for option in actions["options"]:
            for name in ("day", "phase", "connection_generation", "action_generation"):
                require_equal(option[name], trigger[name])
    except (KeyError, TypeError, AttributeError, UnicodeError):
        # Corrupt/missing fields must not become optional, nor echo private data.
        raise ValueError("captured input identity invalid") from None


class Phase6PopulationExceeded(ValueError):
    reason: Literal["ACCEPTED_TEXT_POPULATION_EXCEEDED"]

    def __init__(self, accepted_text_count: int) -> None:
        self.accepted_text_count = accepted_text_count
        self.reason = "ACCEPTED_TEXT_POPULATION_EXCEEDED"
        super().__init__(self.reason)


def _enforce_phase6_population_limit(accepted_text_count: int) -> None:
    """Fail closed before a 513th accepted text can be published."""
    if type(accepted_text_count) is not int or accepted_text_count < 0:
        raise ValueError("accepted text count invalid")
    if accepted_text_count > 512:
        raise Phase6PopulationExceeded(accepted_text_count)


def _expected_chat_terminal_visibility(
    generation: Mapping[str, Any], terminal: Mapping[str, Any]
) -> str:
    prompt_json = generation.get("prompt_json")
    if not isinstance(prompt_json, str):
        raise ValueError("chat visibility prompt invalid")
    prompt = json.loads(prompt_json, object_pairs_hook=_unique_json_object)
    projected = json.loads(
        prompt["messages"][1]["content"], object_pairs_hook=_unique_json_object
    )
    options = [
        option
        for option in projected["action_context"]["options"]
        if option.get("option_id") == terminal.get("option_id")
        and option.get("action_kind") == "chat"
    ]
    if len(options) != 1 or type(options[0].get("channel")) is not str:
        raise ValueError("chat visibility option invalid")
    descriptors = [
        descriptor
        for descriptor in projected["context"]["chat_channels"]
        if descriptor.get("channel_id") == options[0]["channel"]
    ]
    if len(descriptors) != 1 or type(descriptors[0].get("is_public")) is not bool:
        raise ValueError("chat visibility descriptor invalid")
    return "PUBLIC" if descriptors[0]["is_public"] else "AUTHORIZED_PRIVATE"


def _validate_phase6_generation(record: Mapping[str, Any]) -> None:
    """Restore the versioned audit types without rewriting the hashed raw row."""
    from dataclasses import fields
    from ai_client.discussion.model import AiDiscussionGenerationStatus
    from ai_client.llm.decision import DecisionValidationError, _parse_proposal
    from ai_client.llm.types import (
        AiAuditDecision, AiDiscussionGenerationRecord, AiDiscussionGenerationRecordV2,
        BackendIdentity, DecisionValidationCode, LLMBackendErrorCode, PromptRejectionCode,
    )

    record_type = {
        "aiwolf.ai-discussion-generation.v1": AiDiscussionGenerationRecord,
        "aiwolf.ai-discussion-generation.v2": AiDiscussionGenerationRecordV2,
    }.get(record.get("schema_version"))
    if record_type is None or set(record) != {field.name for field in fields(record_type)}:
        raise ValueError("generation fields invalid")
    typed = dict(record)
    try:
        backend = typed["backend"]
        if not isinstance(backend, dict) or set(backend) != {f.name for f in fields(BackendIdentity)}:
            raise ValueError("generation backend fields invalid")
        typed["backend"] = BackendIdentity(**backend)
        typed["status"] = AiDiscussionGenerationStatus(typed["status"])
        for name, enum in (
            ("backend_error_code", LLMBackendErrorCode),
            ("validation_code", DecisionValidationCode),
            ("prompt_rejection_code", PromptRejectionCode),
        ):
            if typed.get(name) is not None:
                typed[name] = enum(typed[name])
        if typed["decision"] is not None:
            decision = typed["decision"]
            if not isinstance(decision, dict) or set(decision) != {f.name for f in fields(AiAuditDecision)}:
                raise ValueError("generation decision fields invalid")
            if not isinstance(decision["ability_target_player_ids"], list):
                raise ValueError("generation decision targets invalid")
            typed["decision"] = AiAuditDecision(**decision)
        if typed["proposal"] is not None:
            typed["proposal"] = _parse_proposal(typed["proposal"])
        record_type(**typed)
    except (DecisionValidationError, TypeError, ValueError, KeyError, AttributeError):
        raise ValueError("generation typed contract invalid") from None


def _phase6_semantic_population(
    shards: Mapping[str, Sequence[Mapping[str, Any]]],
    accepted_text: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """One machine semantic authority; private linkage contains no duplicate text.

    Shard line numbers are the existing durable audit writer's sequences. The
    future private Reviewer reads text only from that hash-verified generation.
    """
    from ai_client.discussion.context import canonical_sha256
    from ai_client.discussion.projection import token_proxy_units
    from jsonschema import Draft202012Validator
    from dataclasses import fields
    from ai_client.discussion.transaction import AiDiscussionTerminalRecord, _PRE_RESULT_GENERATION_STATUSES
    from ai_client.discussion.model import DiscussionTerminalStatus, DiscussionTerminalReason, EvidenceRef, EvidenceRecordKind, EvidenceVisibility
    generations = {}
    accepted = {}
    counts = Counter()
    status_counts, rejection_counts = Counter(), Counter()
    legacy_rejection_missing = 0
    marker_count = 0
    extended_accounting = False
    generation_schemas = {"aiwolf.ai-discussion-generation.v1", "aiwolf.ai-discussion-generation.v2"}
    success_statuses = {"DECISION", "EXPLICIT_NO_DECISION", "REPAIR_SUCCEEDED"}
    latencies, prompt_bytes, proxies, provider_tokens = [], [], [], []
    pre_votes = 0
    for player, records in shards.items():
        seen_terminals = set()
        for sequence, record in enumerate(records, 1):
            schema = record.get("schema_version")
            if schema in generation_schemas:
                _validate_phase6_generation(record)
                if record.get("player_id") != player:
                    raise ValueError("semantic generation status or identity invalid")
                if type(record["attempt_ordinal"]) is not int or record["attempt_ordinal"] not in {1, 2}:
                    raise ValueError("generation attempt ordinal invalid")
                for name in ("day", "world_version", "prompt_bytes", "latency_microseconds", "base_revision"):
                    if type(record[name]) is not int or record[name] < 0:
                        raise ValueError("generation numeric field invalid")
                successful = record["status"] in success_statuses
                status_counts[record["status"]] += 1
                extended_accounting |= schema.endswith(".v2") or record["status"] not in success_statuses | {"CANCELLED"}
                if record["status"] == "PROMPT_REJECTED":
                    if schema.endswith(".v1"):
                        legacy_rejection_missing += 1
                    else:
                        rejection_counts[record["prompt_rejection_code"]] += 1
                key = (player, record.get("request_id"), record.get("attempt_ordinal"))
                if key in generations or record["capture_id"] in seen_terminals:
                    raise ValueError("duplicate semantic generation")
                if record["status"] in {"REPAIR_FAILED", "REPAIR_SUCCEEDED"} and record["attempt_ordinal"] != 2:
                    raise ValueError("repair generation ordinal invalid")
                if record["attempt_ordinal"] == 2:
                    first = generations.get((player, record["request_id"], 1))
                    if first is None or first[1]["status"] != "OUTPUT_INVALID":
                        raise ValueError("repair initial generation missing")
                    if any(record[name] != first[1][name] for name in (
                        "capture_id", "context_sha256", "before_state_sha256", "base_revision", "day", "phase", "game_id"
                    )):
                        raise ValueError("repair generation identity mismatch")
                prompt = record.get("prompt_json")
                if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != record.get("prompt_sha256") or len(prompt.encode()) != record.get("prompt_bytes"):
                    raise ValueError("semantic prompt digest invalid")
                prompt_value = json.loads(prompt, object_pairs_hook=_unique_json_object)
                projected = json.loads(prompt_value["messages"][1]["content"], object_pairs_hook=_unique_json_object)
                marker = isinstance(projected, dict) and "projection_rejected" in projected
                if marker:
                    if (
                        record["status"] != "PROMPT_REJECTED"
                        or set(projected) != {"projection_rejected", "schema_version"}
                        or projected["schema_version"] != "aiwolf.discussion-prompt-rejected.v1"
                        or projected["projection_rejected"] not in {"PROMPT_INVALID", "PROMPT_TOO_LARGE"}
                        or (schema.endswith(".v2") and projected["projection_rejected"] != record["prompt_rejection_code"])
                        or set(prompt_value) != {"messages", "output_schema"}
                        or len(prompt_value["messages"]) != 2
                        or any(set(message) != {"role", "content"} for message in prompt_value["messages"])
                        or [message["role"] for message in prompt_value["messages"]] != ["system", "user"]
                        or prompt_value["output_schema"] != {"additionalProperties": False, "properties": {}, "required": [], "type": "object"}
                    ):
                        raise ValueError("prompt rejection marker invalid")
                    marker_count += 1
                    capture = None
                else:
                    _validate_phase6_captured_identity(record, projected)
                    capture = projected["capture"]
                    if capture["capture_id"] != record["capture_id"] or record["request_id"] != "phase6:" + record["capture_id"] or canonical_sha256(projected["context"]) != record["context_sha256"]:
                        raise ValueError("semantic capture context identity invalid")
                proposal = record.get("proposal")
                if proposal is not None and canonical_sha256(proposal) != record.get("proposal_sha256"):
                    raise ValueError("semantic proposal digest invalid")
                if successful:
                    response = record["response_text"].encode()
                    if hashlib.sha256(response).hexdigest() != record.get("response_sha256") or len(response) != record.get("response_bytes"):
                        raise ValueError("semantic response digest invalid")
                    response_value = json.loads(record["response_text"], object_pairs_hook=_unique_json_object)
                    if not Draft202012Validator(prompt_value["output_schema"]).is_valid(response_value) or response_value.get("discussion") != proposal:
                        raise ValueError("semantic response schema or audited proposal mismatch")
                    decision = record["decision"]
                    if decision is None or response_value["decision"]["kind"] != decision["kind"] or response_value["decision"].get("option_id") != decision.get("option_id"):
                        raise ValueError("semantic audited decision mismatch")
                    if decision["kind"] in {"chat", "co_declare"} and response_value["decision"].get("message" if decision["kind"] == "chat" else "comment") != decision["text"]:
                        raise ValueError("semantic audited text mismatch")
                    pairs = {"vote": ("target_player_id", "vote_target_player_id"),
                             "ability": ("target_player_ids", "ability_target_player_ids"),
                             "co_declare": ("claimed_role_id", "claimed_role_id")}
                    if decision["kind"] in pairs:
                        response_key, audit_key = pairs[decision["kind"]]
                        if response_value["decision"].get(response_key) != decision.get(audit_key):
                            raise ValueError("semantic audited action mismatch")
                    if proposal["decision_kind"] != decision["kind"] or proposal["option_id"] != decision.get("option_id") or proposal["base_revision"] != record["base_revision"]:
                        raise ValueError("semantic proposal decision identity mismatch")
                generations[key] = (sequence, record, projected)
                if record.get("attempt_ordinal") == 1 and capture is not None and capture["trigger"]["kind"] in {"INITIAL_CHAT", "PEER_CHAT"}:
                    counts[(player, record["day"], record["phase"])] += 1
                latencies.append(record["latency_microseconds"])
                prompt_bytes.append(record["prompt_bytes"])
                proxies.append(token_proxy_units(prompt))
                if record.get("prompt_tokens") is not None:
                    provider_tokens.append(record["prompt_tokens"])
            elif schema == "aiwolf.ai-discussion-terminal.v1":
                if set(record) != {field.name for field in fields(AiDiscussionTerminalRecord)}:
                    raise ValueError("terminal fields invalid")
                typed_terminal = dict(record)
                typed_terminal["status"] = DiscussionTerminalStatus(record["status"])
                typed_terminal["reason"] = DiscussionTerminalReason(record["reason"])
                ref = record["authoritative_evidence"]
                if ref is not None:
                    if not isinstance(ref, dict) or set(ref) != {"record_kind", "order", "visibility"}:
                        raise ValueError("terminal reference fields invalid")
                    typed_terminal["authoritative_evidence"] = EvidenceRef(
                        EvidenceRecordKind(ref["record_kind"]), ref["order"], EvidenceVisibility(ref["visibility"]))
                AiDiscussionTerminalRecord(**typed_terminal)
                capture_id = record.get("capture_id")
                if capture_id in seen_terminals or record.get("player_id") != player:
                    raise ValueError("duplicate or cross-seat terminal")
                seen_terminals.add(capture_id)
                key = (player, record.get("request_id"), record.get("final_attempt_ordinal"))
                if key not in generations:
                    raise ValueError("terminal generation missing")
                generation_sequence, generation, projected = generations[key]
                if record["final_attempt_ordinal"] == 1 and (player, record["request_id"], 2) in generations:
                    raise ValueError("terminal omits final generation attempt")
                if generation_sequence != record.get("generation_audit_sequence") or canonical_sha256(generation) != record.get("generation_record_sha256"):
                    raise ValueError("terminal generation digest or sequence mismatch")
                for name in ("capture_id", "context_sha256", "before_state_sha256", "proposal_sha256", "base_revision", "day", "phase", "game_id"):
                    if record.get(name) != generation.get(name):
                        raise ValueError("terminal generation linkage mismatch")
                expected_statuses = _PRE_RESULT_GENERATION_STATUSES.get(typed_terminal["reason"])
                if expected_statuses is not None:
                    if generation["status"] not in expected_statuses:
                        raise ValueError("terminal generation status mismatch")
                elif generation["status"] not in success_statuses:
                    raise ValueError("terminal references failed generation")
                if record.get("status") == "ACCEPTED":
                    if generation["status"] not in success_statuses or generation["decision"] is None:
                        raise ValueError("accepted terminal references failed generation")
                    proposal = generation["proposal"]
                    decision = generation["decision"]
                    if record.get("reason") != "AUTHORITATIVE_ACCEPTED" or record.get("decision_kind") != decision["kind"] or record.get("option_id") != decision["option_id"]:
                        raise ValueError("accepted terminal decision mismatch")
                    evidence = projected["memory"]["records"]
                    options = projected["action_context"]["options"]
                    offered = [o for o in options if o["option_id"] == decision["option_id"] and o["action_kind"] == decision["kind"]]
                    if len(offered) != 1:
                        raise ValueError("accepted decision not offered")
                    if decision["kind"] == "vote":
                        reassessment = proposal["pre_vote_reassessment"]
                        if reassessment is None or reassessment["option_id"] != decision["option_id"] or reassessment["preferred_target_player_id"] != decision["vote_target_player_id"] or any(t not in offered[0]["valid_targets"] for t in reassessment["ranked_target_player_ids"]):
                            raise ValueError("pre-vote reassessment invalid")
                        if any(not any(e["source"] == ref for e in evidence) for ref in reassessment["evidence"]):
                            raise ValueError("pre-vote reference invalid")
                        if decision["vote_target_player_id"] not in offered[0]["valid_targets"] or decision["vote_target_player_id"] not in reassessment["ranked_target_player_ids"]:
                            raise ValueError("pre-vote selected target invalid")
                        pre_votes += 1
                    if decision["kind"] == "co_declare" and decision["claimed_role_id"] not in offered[0]["claimed_role_ids"]:
                        raise ValueError("CO claim not offered")
                    if decision["kind"] in {"chat", "co_declare"}:
                        if decision["kind"] == "chat":
                            observed_visibility = record.get("authoritative_evidence")
                            if (
                                not isinstance(observed_visibility, Mapping)
                                or observed_visibility.get("record_kind") != "chat"
                                or observed_visibility.get("visibility")
                                != _expected_chat_terminal_visibility(generation, record)
                            ):
                                raise ValueError("chat terminal visibility mismatch")
                        event_key = (player, record["request_event_id"])
                        if event_key in accepted:
                            raise ValueError("ambiguous accepted terminal")
                        responsive = False
                        act = proposal["speech_act"]
                        if decision["kind"] == "chat" and act["kind"] in {"ANSWER", "REBUTTAL"}:
                            source = [e for e in evidence if e["source"] == act["in_reply_to"]]
                            if len(source) != 1 or source[0]["source"]["record_kind"] not in {"chat", "co_declaration"} or source[0]["actor_player_ids"] != [act["addressee_player_id"]] or player == act["addressee_player_id"] or source[0]["source"]["visibility"] not in {"PUBLIC", "AUTHORIZED_PRIVATE"}:
                                raise ValueError("responsive peer reference invalid")
                            if source[0]["source"]["record_kind"] == "chat" and source[0]["channel_id"] != offered[0]["channel"]:
                                raise ValueError("responsive channel authorization mismatch")
                            observed = record.get("authoritative_evidence")
                            if not isinstance(observed, dict) or type(observed.get("order")) is not int or source[0]["source"]["order"] >= observed["order"]:
                                raise ValueError("responsive source is not prior to accepted speech")
                            responsive = True
                        accepted[event_key] = {"capture_id": capture_id,
                            "generation_sequence": generation_sequence, "terminal_sequence": sequence,
                            "generation_sha256": canonical_sha256(generation),
                            "terminal_sha256": canonical_sha256(record),
                            "decision_kind": decision["kind"], "responsive": responsive,
                            "day": generation["day"], "phase": generation["phase"],
                            "source_relevant_applicable": responsive or projected["capture"]["trigger"]["kind"] == "PEER_CHAT",
                            "text_sha256": hashlib.sha256(decision["text"].encode("utf-8")).hexdigest()}
            else:
                raise ValueError("unknown private semantic audit record")
        request_captures = {record["capture_id"] for record in records if record.get("schema_version") in generation_schemas}
        if request_captures != seen_terminals:
            raise ValueError("semantic terminal missing")
    population = []
    seen = set()
    for order, item in enumerate(accepted_text, 1):
        if not isinstance(item, Mapping) or set(item) != {"server_record_order", "player_id", "request_event_id", "decision_kind", "day", "phase", "text_sha256"}:
            raise ValueError("accepted server text fields invalid")
        if type(item["server_record_order"]) is not int or type(item["day"]) is not int:
            raise ValueError("accepted server order or day invalid")
        key = (item["player_id"], item["request_event_id"])
        if item["server_record_order"] != order or key in seen or key not in accepted:
            raise ValueError("accepted server population missing or ambiguous")
        seen.add(key)
        row = accepted[key]
        if any(row[name] != item[name] for name in ("text_sha256", "decision_kind", "day", "phase")):
            raise ValueError("accepted server text mismatch")
        population.append({**dict(item), **row})
        _enforce_phase6_population_limit(len(population))
    if seen != set(accepted):
        raise ValueError("accepted text population incomplete")
    responsive_count = sum(row["responsive"] for row in population)
    cap_ok = bool(counts) and max(counts.values()) <= 2 and marker_count == 0
    aggregate = {"responsive_accepted_count": responsive_count,
        "pre_vote_reassessment_count": pre_votes, "accepted_text_count": len(population),
        "chat_caps_respected": cap_ok, "maximum_chat_starts_per_player_phase": max(counts.values(), default=0),
        "chat_start_count": sum(counts.values()), "generation_count": len(generations),
        "latency_microseconds": _distribution(latencies), "prompt_bytes": _distribution(prompt_bytes),
        "prompt_token_proxy": _distribution(proxies), "provider_prompt_tokens": _distribution(provider_tokens),
        "semantic_requirements_met": responsive_count > 0 and cap_ok}
    # Preserve exact aggregates of previously publishable v1-only manifests.
    if extended_accounting:
        aggregate.update(
            generation_status_counts=dict(sorted(status_counts.items())),
            prompt_rejection_counts=dict(sorted(rejection_counts.items())),
            legacy_prompt_rejection_reason_missing=legacy_rejection_missing,
            prompt_rejection_marker_count=marker_count,
        )
    return population, aggregate


def _write_phase6_evidence(
    ai_dir: Path,
    statuses: Mapping[str, Mapping[str, object]],
    metrics_path: Path,
    player_to_client: Mapping[str, str],
    server_result: Mapping[str, object],
) -> tuple[Path, dict[str, object]]:
    manifest = ai_dir / "manifest.json"
    if manifest.exists():
        raise FileExistsError(f"output already exists: {manifest}")
    value = _manifest_value(ai_dir, statuses, metrics_path, player_to_client)
    shards = {player: _read_jsonl(ai_dir / entry["path"]) for player, entry in value["shards"].items()}
    game_id = ai_dir.parent.name
    if any(record.get("game_id") != game_id for records in shards.values() for record in records):
        raise ValueError("semantic evidence game identity mismatch")
    population, aggregate = _phase6_semantic_population(shards, server_result["accepted_text"])
    target = ai_dir / "accepted-text.jsonl"
    _write_private_jsonl_atomic(target, population)
    value["schema"] = "aiwolf.phase6-private-manifest.v1"
    value["game_id"] = game_id
    value["accepted_text"] = _manifest_entry(target, ai_dir)
    value["semantic_counts"] = aggregate
    _write_private_json_atomic(manifest, value)
    return manifest, aggregate


def _plain_snapshot(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain_snapshot(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_snapshot(child) for child in value]
    return value


class _StartGateClock:
    def __init__(self, start_path: Path, *, integer: bool = False) -> None:
        self._start_path = start_path
        self._origin = time.monotonic()
        self._started: float | None = None
        self._integer = integer

    def __call__(self) -> float | int:
        now = time.monotonic()
        if not self._start_path.exists():
            value = 0.0 if self._integer else self._origin
        else:
            if self._started is None:
                self._started = now
            elapsed = now - self._started
            value = elapsed if self._integer else self._origin + elapsed
        return int(value) if self._integer else value

    async def sleep(self, delay: float) -> None:
        deadline = float(self()) + delay
        while float(self()) < deadline:
            await asyncio.sleep(min(0.02, max(0.001, deadline - float(self()))))


async def _wait_for_start_gate(
    start_path: Path,
    stop_path: Path,
    *,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int | None:
    while not start_path.exists():
        if stop_path.exists():
            return None
        await sleep(0.01)
    return monotonic_ns()


def _elapsed_microseconds(start_ns: int, end_ns: int) -> int:
    if type(start_ns) is not int or type(end_ns) is not int or end_ns < start_ns:
        raise ValueError("wall-clock samples must be monotonic integers")
    return (end_ns - start_ns) // 1000


def _expected_from_state(player_id: str, state: Mapping[str, object]) -> dict[str, object] | None:
    actions = state["actions"]
    assert isinstance(actions, list)
    vote = [item for item in actions if isinstance(item, dict) and item.get("type") == "vote"]
    abilities = [
        item
        for item in actions
        if isinstance(item, dict)
        and item.get("type") == "ability"
        and item.get("uses_remaining") != 0
        and isinstance(item.get("valid_targets"), list)
        and isinstance(item.get("target_count"), int)
        and len(item["valid_targets"]) >= item["target_count"]
    ]
    if vote:
        action = vote[0]
        return {
            "player_id": player_id,
            "day": state["day"],
            "phase": state["phase"],
            "action": "vote.cast",
            "valid_targets": list(action["valid_targets"]),
            "target_count": action["target_count"],
            "abilities": [],
        }
    if abilities:
        return {
            "player_id": player_id,
            "day": state["day"],
            "phase": state["phase"],
            "action": "ability.use",
            "valid_targets": [],
            "target_count": 0,
            "abilities": [dict(item) for item in abilities],
        }
    return None


async def _server_child(args: argparse.Namespace, bootstrap: Mapping[str, object]) -> int:
    from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
    from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer

    phase6 = bootstrap.get("phase6") is True
    plan = PHASE6_GAME_PLAN if phase6 else GAME_PLAN
    entry_tokens = bootstrap.get("entry_tokens")
    if not isinstance(entry_tokens, list) or len(entry_tokens) != 9 or any(not isinstance(item, str) or not item for item in entry_tokens):
        raise ValueError("private entry-token bootstrap is invalid")
    content = load_content(PROJECT_ROOT / "content")
    preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = _apply_game_plan(preset, plan)
    players = tuple(
        PlayerConfig(f"player-{index}", f"Player {index}")
        for index in range(sum(preset.role_counts.values()))
    )
    if len(players) != 9:
        raise ValueError("standard_9 must contain exactly nine seats")
    game_id = str(uuid4())
    clock = _StartGateClock(args.start, integer=True)
    game = GameState.create_from_preset(
        content,
        preset,
        players,
        game_id=game_id,
        event_sink=InMemoryEventSink(),
        rng=Random(args.seed),
        started_at=clock(),
    )
    if phase6:
        relay = bootstrap.get("discussion_relay")
        if not isinstance(relay, str) or not relay:
            raise ValueError("private discussion relay is required")
        _write_private_json_atomic(Path(relay), _phase6_envelopes(content, preset, game))
    token_source = iter(entry_tokens)
    registry = GameRegistry({game_id: game}, entry_token_factory=token_source.__next__)
    sessions = SessionManager(registry, clock=clock)
    ticker = TickDriver(registry, clock=clock)
    server = WebSocketGameServer(registry, sessions=sessions, ticker=ticker, tick_interval_seconds=0.05)
    expected: dict[tuple[str, int, str, str], dict[str, object]] = {}
    accepted: list[dict[str, object]] = []
    chats: list[dict[str, object]] = []
    accepted_text: list[dict[str, object]] = []
    active_request_id: str | None = None
    original_handle = sessions.handle_message
    original_vote = game.submit_vote
    original_action = game.submit_action

    def remember_expected(player_id: str) -> None:
        state = game.get_action_state(player_id)
        item = _expected_from_state(player_id, state)
        if item is not None:
            key = (player_id, int(item["day"]), str(item["phase"]), str(item["action"]))
            expected.setdefault(key, item)

    def handle_spy(message, context=None):
        nonlocal active_request_id
        message_type = message.get("type") if isinstance(message, dict) else None
        request_id = message.get("event_id") if isinstance(message, dict) else None
        previous = active_request_id
        if message_type in {"vote.cast", "ability.use"} and context is not None and context.player_id in game.players:
            remember_expected(context.player_id)
            active_request_id = request_id
        try:
            result = original_handle(message, context)
            if phase6 and message_type == "co.declare" and result.reply is None:
                accepted_text.append({"server_record_order": len(accepted_text) + 1,
                    "player_id": context.player_id, "request_event_id": request_id,
                    "decision_kind": "co_declare", "day": game.day, "phase": game.phase.value,
                    "text_sha256": hashlib.sha256(message["payload"]["comment"].encode("utf-8")).hexdigest()})
            for submission in result.channel_messages:
                if phase6:
                    accepted_text.append({"server_record_order": len(accepted_text) + 1,
                        "player_id": submission.acceptance.player_id, "request_event_id": request_id,
                        "decision_kind": "chat", "day": submission.acceptance.day,
                        "phase": submission.acceptance.phase,
                        "text_sha256": hashlib.sha256(submission.message["message"].encode("utf-8")).hexdigest()})
                acceptance = submission.acceptance
                text = submission.message["message"]
                chats.append(
                    {
                        "player_id": acceptance.player_id,
                        "day": acceptance.day,
                        "phase": acceptance.phase,
                        "channel": submission.channel_id,
                        "accepted_at": acceptance.accepted_at,
                        "phase_deadline": acceptance.phase_deadline,
                        "message_chars": len(text),
                        "message_utf8_bytes": len(text.encode("utf-8")),
                    }
                )
            return result
        finally:
            active_request_id = previous

    def vote_spy(now, player_id, target_player_id):
        acceptance = original_vote(now, player_id, target_player_id)
        accepted.append(
            {
                "player_id": player_id,
                "day": acceptance.day,
                "phase": acceptance.phase,
                "action": "vote.cast",
                "ability_id": None,
                "vote_target_player_id": target_player_id,
                "ability_target_player_ids": [],
                "request_event_id": active_request_id,
                "accepted_at": acceptance.accepted_at,
                "phase_deadline": acceptance.phase_deadline,
            }
        )
        return acceptance

    def action_spy(now, player_id, ability_id, target_player_ids):
        day, phase, deadline = game.day, game.phase.value, game.phase_ends_at
        result = original_action(now, player_id, ability_id, target_player_ids)
        accepted.append(
            {
                "player_id": player_id,
                "day": day,
                "phase": phase,
                "action": "ability.use",
                "ability_id": ability_id,
                "vote_target_player_id": None,
                "ability_target_player_ids": list(target_player_ids),
                "request_event_id": active_request_id,
                "accepted_at": now,
                "phase_deadline": deadline,
            }
        )
        return result

    sessions.handle_message = handle_spy  # type: ignore[method-assign]
    game.submit_vote = vote_spy  # type: ignore[method-assign]
    game.submit_action = action_spy  # type: ignore[method-assign]
    listener = await server.start("127.0.0.1", 0)
    _write_private_json_atomic(
        args.ready,
        {
            "uri": f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}",
            "game_id": game_id,
            "player_ids": list(game.players),
            "server_pid": os.getpid(),
            "profile": asdict(plan),
        },
    )
    failure: str | None = None
    game_started: int | None = None
    game_finished: int | None = None
    phase_started: int | None = None
    current_phase = (game.day, game.phase.value)
    phase_wall: list[dict[str, object]] = []
    try:
        game_started = await asyncio.wait_for(
            _wait_for_start_gate(args.start, args.stop), _READY_SECONDS
        )
        if game_started is None:
            failure = "STOP_REQUESTED"
        else:
            phase_started = game_started
        async with asyncio.timeout(args.max_seconds):
            while game.game_result is None and not args.stop.exists():
                for player_id in game.players:
                    remember_expected(player_id)
                phase = (game.day, game.phase.value)
                if phase != current_phase:
                    now = time.monotonic_ns()
                    assert phase_started is not None
                    phase_wall.append(
                        {
                            "day": current_phase[0],
                            "phase": current_phase[1],
                            "wall_microseconds": _elapsed_microseconds(
                                phase_started, now
                            ),
                        }
                    )
                    current_phase, phase_started = phase, now
                await asyncio.sleep(0.05)
            game_finished = time.monotonic_ns()
            if failure is None and args.stop.exists() and game.game_result is None:
                failure = "STOP_REQUESTED"
            elif failure is None:
                await asyncio.sleep(0.5)
    except TimeoutError:
        game_finished = time.monotonic_ns()
        failure = "GAME_START_TIMEOUT" if game_started is None else "GAME_TIMEOUT"
    finally:
        await server.close()
    if game_started is not None and phase_started is not None:
        if game_finished is None:
            game_finished = time.monotonic_ns()
        phase_wall.append(
            {
                "day": current_phase[0],
                "phase": current_phase[1],
                "wall_microseconds": _elapsed_microseconds(
                    phase_started, game_finished
                ),
            }
        )
    total_game_wall = (
        None
        if game_started is None or game_finished is None
        else _elapsed_microseconds(game_started, game_finished)
    )
    _write_private_json_atomic(
        args.result,
        {
            **({"accepted_text": accepted_text} if phase6 else {}),
            "server_pid": os.getpid(),
            "success": failure is None and game.game_result is not None,
            "failure": failure,
            "game_end": game.game_result is not None,
            "accepted_reservations": accepted,
            "accepted_chats": chats,
            "expected_reservations": list(expected.values()),
            "phase_wall_durations": phase_wall,
            "total_game_wall_microseconds": total_game_wall,
            "listener_closed": True,
        },
    )
    return 0 if failure is None and game.game_result is not None else 1


def _phase5_broker_settings(bootstrap: Mapping[str, object]) -> LocalLLMSettings:
    profile = None
    if "llama_cpp_structured_output" in bootstrap:
        value = bootstrap["llama_cpp_structured_output"]
        if not isinstance(value, Mapping) or set(value) != {"reasoning_format", "enable_thinking"}:
            raise ValueError("invalid llama.cpp structured output profile")
        profile = LlamaCppStructuredOutputConfig(**value)
    elif bootstrap.get("phase6") is True:
        raise ValueError("Phase 6 requires llama.cpp structured output profile")
    phase6_marker = bootstrap.get("phase6", None)
    has_cap = "phase6_max_output_tokens" in bootstrap
    if phase6_marker is None:
        if has_cap:
            raise ValueError("Phase 6 output cap requires Phase 6 marker")
        max_output_tokens = 96
    else:
        cap = bootstrap.get("phase6_max_output_tokens")
        if (
            phase6_marker is not True
            or bootstrap.get("model") != "Qwen3.5-9B-Q4_K_M.gguf"
            or type(cap) is not int
            or cap != PHASE6_MAX_OUTPUT_TOKENS
        ):
            raise ValueError("invalid Phase 6 broker output profile")
        max_output_tokens = PHASE6_MAX_OUTPUT_TOKENS
    return LocalLLMSettings(
        endpoint=str(bootstrap.get("endpoint", "")),
        model=str(bootstrap.get("model", "")),
        api_key=bootstrap.get("api_key")
        if isinstance(bootstrap.get("api_key"), str)
        else None,
        generation=GenerationSettings(
            max_output_tokens=max_output_tokens,
            temperature=float(bootstrap.get("temperature", 0.2)),
        ),
        connect_timeout_seconds=float(bootstrap.get("connect_timeout_seconds", 1.0)),
        read_timeout_seconds=float(bootstrap.get("read_timeout_seconds", 3.5)),
        write_timeout_seconds=float(bootstrap.get("write_timeout_seconds", 1.0)),
        pool_timeout_seconds=float(bootstrap.get("pool_timeout_seconds", 1.0)),
        request_timeout_seconds=float(bootstrap.get("request_timeout_seconds", 4.0)),
        max_request_bytes=int(bootstrap.get("max_request_bytes", 65536)),
        max_response_bytes=int(bootstrap.get("max_response_bytes", 65536)),
        structured_mode=str(bootstrap.get("structured_mode", "json_schema")),  # type: ignore[arg-type]
        llama_cpp_structured_output=profile,
    )


async def _broker_child(args: argparse.Namespace, bootstrap: Mapping[str, object]) -> int:
    registry_value = bootstrap.get("registry")
    if not isinstance(registry_value, dict) or len(registry_value) != 9:
        raise ValueError("private admission registry is invalid")
    registry = {str(key): str(value) for key, value in registry_value.items()}
    settings = _phase5_broker_settings(bootstrap)
    if bootstrap.get("phase6") is True and settings.backend_config().config_fingerprint != bootstrap.get(
        "parent_config_fingerprint"
    ):
        raise ValueError("broker configuration fingerprint mismatch")
    config = _game_broker_config(settings, phase6=bootstrap.get("phase6") is True)
    metrics_resources = ExitStack()
    metrics_wrapper = None
    metrics: AdmissionMetrics | None = None
    backend: object | None = None
    broker: GenerationAdmissionBroker | None = None
    try:
        if bootstrap.get("phase6") is True:
            from scripts.phase6_private_review import _locked_path
            metrics_fd = metrics_resources.enter_context(_locked_path(args.metrics, create=True))
            metrics_wrapper = metrics_resources.enter_context(os.fdopen(metrics_fd, "ab", closefd=False))
            metrics = AdmissionMetrics(config.metrics_queue_capacity, handle=metrics_wrapper)
        else:
            metrics = AdmissionMetrics(config.metrics_queue_capacity, path=args.metrics)
        if bootstrap.get("phase6_fixture") is True:
            from tests.fixtures.phase6_semantic_backend import Phase6SemanticBackend
            backend = Phase6SemanticBackend()
        else:
            backend = OpenAICompatibleBackend(settings.backend_config())
        broker = GenerationAdmissionBroker(
            registry,
            backend,  # type: ignore[arg-type]
            fairness_seed=str(bootstrap.get("fairness_seed", "")),
            config=config,
            metrics=metrics,
            backend_request_timeout_seconds=settings.request_timeout_seconds,
        )
    except BaseException as error:
        await _cleanup_broker_child_after_error(
            error, broker=broker, metrics=metrics, backend=backend,
            metrics_wrapper=metrics_wrapper, metrics_resources=metrics_resources,
        )
        raise AssertionError("unreachable")
    assert broker is not None and metrics is not None and backend is not None
    maximum_pending = 0
    peak_backend = 0
    try:
        ready = await broker.start()
    except BaseException as error:
        await _cleanup_broker_child_after_error(
            error, broker=broker, metrics=metrics, backend=backend,
            metrics_wrapper=metrics_wrapper, metrics_resources=metrics_resources,
        )
        raise AssertionError("unreachable")
    try:
        _write_private_json_atomic(
            args.ready,
            {
            "pid": os.getpid(),
            "host": ready.host,
            "port": ready.port,
            "protocol": ready.protocol,
            "backend_identity": asdict(ready.backend_identity),
            "config_fingerprint": ready.config_fingerprint,
            "config": asdict(config),
            "external_model_ownership": "UNOWNED",
            },
        )
    except BaseException as error:
        await _cleanup_broker_child_after_error(
            error, broker=broker, metrics=metrics, backend=backend,
            metrics_wrapper=metrics_wrapper, metrics_resources=metrics_resources,
        )
        raise AssertionError("unreachable")
    failure: str | None = None
    active_written = False
    try:
        async with asyncio.timeout(args.max_seconds + _READY_SECONDS):
            while not args.stop.exists():
                snapshot = broker.snapshot
                maximum_pending = max(maximum_pending, snapshot.pending_total)
                peak_backend = max(peak_backend, int(snapshot.provider_call_active))
                if snapshot.provider_call_active and not active_written and args.active is not None:
                    _write_private_json_atomic(args.active, {"active": True, "pid": os.getpid()})
                    active_written = True
                if snapshot.poisoned:
                    failure = snapshot.poison_reason or "ADMISSION_POISONED"
                    break
                await asyncio.sleep(0.005)
    except TimeoutError:
        failure = "BROKER_STOP_TIMEOUT"
    finally:
        before_close = broker.snapshot
        maximum_pending = max(maximum_pending, before_close.pending_total)
        peak_backend = max(peak_backend, int(before_close.provider_call_active))
        await _close_broker_metrics_resources(
            broker, metrics_wrapper, metrics_resources
        )
    metric_values = _read_jsonl(args.metrics)
    terminal = {
        str(item["invocation_id"]): item.get("terminal_status")
        for item in metric_values
        if item.get("event") == "TERMINAL" and item.get("invocation_id") is not None
    }
    calls = [item for item in metric_values if item.get("event") == "PROVIDER_CALL_TERMINAL"]
    success = failure is None and broker.shutdown_clean and metrics.dropped_records == 0
    _write_private_json_atomic(
        args.result,
        {
            "pid": os.getpid(),
            "success": success,
            "failure": failure,
            "shutdown_clean": broker.shutdown_clean,
            "listener_closed": broker.shutdown_clean,
            "snapshot_before_close": _plain_snapshot(asdict(before_close)),
            "snapshot_after_close": _plain_snapshot(asdict(broker.snapshot)),
            "maximum_pending": maximum_pending,
            "peak_backend_concurrency": peak_backend,
            "metrics_count": len(metric_values),
            "metrics_dropped": metrics.dropped_records,
            "terminal": terminal,
            "provider_call_terminals": len(calls),
            "backend_identity": asdict(backend.identity),
            "external_model_ownership": "UNOWNED",
        },
    )
    return 0 if success else 1


class _DisposableCredentialStore:
    def __init__(self) -> None:
        self.checkpoint = None
        self.save_count = 0

    async def load(self):
        return self.checkpoint

    async def save(self, checkpoint) -> None:
        self.checkpoint = checkpoint
        self.save_count += 1


def _audit_summary(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    records = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
    records = [record for record in records if record.get("schema_version") != "aiwolf.ai-discussion-terminal.v1"]
    membership_valid = True
    request_ids: list[object] = []
    for record in records:
        request_ids.append(record.get("request_id"))
        decision = record.get("decision")
        if not isinstance(decision, dict) or decision.get("kind") == "none":
            continue
        try:
            prompt = json.loads(record["prompt_json"])
            canonical = json.loads(prompt["messages"][1]["content"])
            options = canonical["action_context"]["options"]
            selected = next((item for item in options if item.get("option_id") == decision.get("option_id")), None)
            targets: list[object] = []
            if decision.get("kind") == "vote" and decision.get("vote_target_player_id") is not None:
                targets = [decision["vote_target_player_id"]]
            elif decision.get("kind") == "ability":
                targets = list(decision.get("ability_target_player_ids") or [])
            if selected is None or any(target not in selected.get("valid_targets", []) for target in targets):
                membership_valid = False
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            membership_valid = False
    return {
        "path": str(Path(path.parent.name) / path.name).replace("\\", "/"),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(records),
        "request_ids": request_ids,
        "player_ids": sorted(
            {item["player_id"] for item in records}
        )
        if all(isinstance(item.get("player_id"), str) for item in records)
        else [item.get("player_id") for item in records],
        "membership_valid": membership_valid,
    }


def _reaction_evidence(snapshot: Any) -> dict[str, object]:
    frequency = snapshot.frequency_state
    return {
        "lifecycle": snapshot.lifecycle.value,
        "chat_brain_invocations": snapshot.chat_brain_invocations,
        "send_count": snapshot.send_count,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "intentional_silence_count": snapshot.intentional_silence_count,
        "frequency_suppressed_count": sum(item.status.value == "frequency_suppressed" for item in snapshot.outcomes),
        "suppression_counts": dict(
            Counter(
                item.suppression.value
                for item in snapshot.outcomes
                if item.suppression is not None
            )
        ),
        "frequency": None if frequency is None else _plain_snapshot(asdict(frequency)),
    }


def _reservation_evidence(snapshot: Any) -> dict[str, object]:
    return {
        "lifecycle": snapshot.lifecycle.value,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "unknown_count": snapshot.unknown_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "unresolved": snapshot.unresolved_reservation is not None,
        "outcomes": [
            {
                "day": item.opportunity_key.reservation.day,
                "phase": item.opportunity_key.reservation.phase,
                "action": item.action,
                "request_event_id": item.request_event_id,
                "status": item.status.value,
            }
            for item in snapshot.outcomes
        ],
    }


async def _client_child(args: argparse.Namespace, bootstrap: dict[str, object]) -> int:
    from ai_client import DiscussionChatConfig, LLMBrainConfig, Phase5ClientRuntime, Phase5ClientRuntimeConfig, ShortChatConfig, SpeakingProfile
    from ai_client.brain import BrainRunConfig
    from ai_client.network import NetworkClientConfig, ReconnectPolicy
    from ai_client.reaction_chat import ReactionChatConfig, ReactionChatLifecycle
    from ai_client.vote_ability import VoteAbilityConfig, VoteAbilityLifecycle
    from ai_client.world import Freshness

    phase6 = bootstrap.get("phase6") is True
    plan = PHASE6_GAME_PLAN if phase6 else GAME_PLAN
    clock = _StartGateClock(args.start)
    player_id = str(bootstrap.get("player_id", ""))
    config = Phase5ClientRuntimeConfig(
        network=NetworkClientConfig(
            str(bootstrap.get("uri", "")),
            str(bootstrap.get("game_id", "")),
            str(bootstrap.get("entry_token", "")),
            shutdown_timeout_seconds=10.0,
        ),
        player_id=player_id,
        admission_host=str(bootstrap.get("admission_host", "")),
        admission_port=int(bootstrap.get("admission_port", 0)),
        admission_credentials=AdmissionCredentials(
            str(bootstrap.get("admission_client_id", "")),
            str(bootstrap.get("admission_token", "")),
        ),
        audit_path=args.audit,
        master_seed=args.seed,
        reconnect=ReconnectPolicy(max_disconnected_seconds=10.0),
        broker=GenerationBrokerConfig(**dict(bootstrap.get("broker_config", {}))),
        llm=LLMBrainConfig(
            short_chat=DiscussionChatConfig() if phase6 else ShortChatConfig()
        ),
        brain=BrainRunConfig(
            max_decision_seconds=_FEATURE_BRAIN_TIMEOUT_SECONDS if phase6 else 5.0,
            cancellation_grace_seconds=0.25,
        ),
        reaction=ReactionChatConfig(
            max_chat_attempts_per_phase=plan.max_chat_attempts_per_phase,
            brain_timeout_seconds=_FEATURE_BRAIN_TIMEOUT_SECONDS,
            deadline_guard_seconds=1.0,
            minimum_start_budget_seconds=0.10,
        ),
        vote_ability=VoteAbilityConfig(
            brain_timeout_seconds=_FEATURE_BRAIN_TIMEOUT_SECONDS,
            deadline_guard_seconds=1.0,
            minimum_start_budget_seconds=0.10,
        ),
        speaking=SpeakingProfile(
            talkativeness=plan.talkativeness,
            ordinary_event_importance=plan.ordinary_event_importance,
            direct_mention_importance=plan.direct_mention_importance,
            initial_event_importance=plan.initial_event_importance,
            cooldown_seconds=plan.cooldown_seconds,
            max_trigger_evaluations_per_phase=plan.max_trigger_evaluations_per_phase,
            repetition_window=plan.repetition_window,
        ),
    )
    store = _DisposableCredentialStore()
    connect = Phase5ClientRuntime.connect
    discussion_kwargs = {}
    if phase6:
        from ai_client.discussion.context import validate_discussion_bootstrap
        pending = validate_discussion_bootstrap(bootstrap.pop("discussion_envelope", None),
            network_game_id=config.network.game_id, player_id=player_id)
        connect = Phase5ClientRuntime.connect_phase6
        discussion_kwargs["pending_discussion_context"] = pending
    runtime = await connect(
        config,
        store,
        clock=clock,
        sleep=clock.sleep,
        request_id_factory=lambda: str(uuid4()),
        **discussion_kwargs,
    )
    await runtime.start()
    while True:
        world = runtime.world.snapshot()
        deadline = runtime.world.transport_observations().current_deadline
        reaction = runtime.reaction.snapshot()
        reservation = runtime.vote_ability.snapshot()
        if (
            world.freshness is Freshness.CURRENT
            and world.is_caught_up
            and world.phase is not None
            and world.phase.day == 0
            and world.phase.phase == "night0"
            and deadline is not None
            and reaction.lifecycle is ReactionChatLifecycle.RUNNING
            and reservation.lifecycle is VoteAbilityLifecycle.RUNNING
        ):
            _write_private_json_atomic(args.ready, {"pid": os.getpid(), "player_id": player_id})
            break
        await asyncio.sleep(0.02)

    async def wait_stop() -> None:
        while not args.stop.exists():
            await asyncio.sleep(0.05)

    runtime_wait = asyncio.create_task(runtime.wait())
    stop_wait = asyncio.create_task(wait_stop())
    try:
        done, _ = await asyncio.wait({runtime_wait, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if stop_wait in done and not runtime_wait.done():
            await runtime.aclose()
        exit_value = await runtime_wait
    finally:
        await runtime.aclose()
        if not stop_wait.done():
            stop_wait.cancel()
        await asyncio.gather(stop_wait, return_exceptions=True)
    reaction = runtime.reaction.snapshot()
    reservation = runtime.vote_ability.snapshot()
    brain = runtime.brain.snapshot()
    status = {
        "pid": os.getpid(),
        "player_id": player_id,
        "brain_mode": "llm",
        "game_end": exit_value.reason.value == "GAME_ENDED",
        "runtime_exit": exit_value.reason.value,
        "runtime_success": exit_value.success,
        "world_exit": None if exit_value.world is None else exit_value.world.reason.value,
        "reaction_exit": None if exit_value.reaction is None else exit_value.reaction.reason.value,
        "vote_ability_exit": None if exit_value.vote_ability is None else exit_value.vote_ability.reason.value,
        "brain": _plain_snapshot(asdict(brain)),
        "reaction": _reaction_evidence(reaction),
        "reservation": _reservation_evidence(reservation),
        "audit": _audit_summary(args.audit),
        "owned_task_names_alive": sorted(
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done() and task.get_name().startswith("aiwolf-")
        ),
        "external_model_ownership": "UNOWNED",
        "composition": {
            "world_count": 1,
            "llm_brain_count": 1,
            "brain_controller_count": 1,
            "arbiter_count": 1,
            "reaction_count": 1,
            "vote_ability_count": 1,
            "shared_arbiter": runtime.reaction.invoker is runtime.arbiter and runtime.vote_ability.invoker is runtime.arbiter,
            "admission_shared": runtime.arbiter._admission is runtime.admission,
            "backend_type": type(runtime.backend).__name__,
            "brain_type": type(runtime.brain).__name__,
            "speaking_profile": asdict(config.speaking),
            "short_chat": asdict(config.llm.short_chat),
        },
    }
    _write_private_json_atomic(args.status, status)
    return 0 if exit_value.success and not status["owned_task_names_alive"] else 1


async def _q8_worker_child(args: argparse.Namespace, bootstrap: Mapping[str, object]) -> int:
    descriptors = bootstrap.get("requests")
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError("Q8 private request plan missing")
    config = GenerationBrokerConfig(**dict(bootstrap.get("broker_config", {})))
    outcomes: list[dict[str, object]] = []
    success = True
    backend: BrokeredStructuredLLMBackend | None = None
    try:
        session = await BrokerAdmissionSession.connect(
            str(bootstrap.get("host", "")),
            int(bootstrap.get("port", 0)),
            AdmissionCredentials(
                str(bootstrap.get("client_id", "")),
                str(bootstrap.get("token", "")),
            ),
            config=config,
        )
        backend = BrokeredStructuredLLMBackend(session)
        _write_private_json_atomic(args.ready, {"pid": os.getpid(), "ready": True})
        if args.start is not None:
            while not args.start.exists():
                if args.stop.exists():
                    raise RuntimeError("Q8 stop requested before start")
                await asyncio.sleep(0.01)
        for ordinal, descriptor in enumerate(descriptors, 1):
            if not isinstance(descriptor, dict):
                raise ValueError("Q8 request descriptor must be an object")
            priority = GenerationPriority(int(descriptor["priority"]))
            invocation_id = str(uuid4())
            admission = await session.acquire(
                AdmissionRequest(
                    invocation_id=invocation_id,
                    priority=priority,
                    phase=str(descriptor["row"]),
                    day=0,
                    action_generation=ordinal,
                    mapping_order=ordinal,
                    not_after_monotonic=time.monotonic() + float(descriptor["limit_seconds"]),
                )
            )
            if admission.status is not AdmissionStatus.OFFERED or admission.lease is None:
                raise RuntimeError(f"admission ended as {admission.status.value}")
            claimed = await admission.lease.claim()
            if claimed is not AdmissionStatus.GRANTED:
                raise RuntimeError(f"claim ended as {claimed.value}")
            request = _strict_short_request(str(descriptor["bucket"]), str(uuid4()))
            with admission.lease.activate():
                response = await backend.generate(request)
            chars, byte_count = _strict_q8_response(response)
            await admission.lease.release()
            outcomes.append(
                {
                    "ordinal": ordinal,
                    "row": descriptor["row"],
                    "bucket": descriptor["bucket"],
                    "cold": bool(descriptor.get("cold")),
                    "priority": int(priority),
                    "queue_wait_microseconds": admission.queue_wait_microseconds,
                    "message_chars": chars,
                    "message_utf8_bytes": byte_count,
                    "provider_timing": response.provider_timing is not None,
                }
            )
    except BaseException:
        success = False
        raise
    finally:
        if backend is not None:
            await asyncio.shield(backend.aclose())
        _write_private_json_atomic(
            args.status,
            {"pid": os.getpid(), "success": success, "outcomes": outcomes, "external_model_ownership": "UNOWNED"},
        )
    return 0


def _broker_bootstrap(config: RunConfig, registry: Mapping[str, str], fairness_seed: str) -> dict[str, object]:
    if config.phase6 and config.settings.generation.max_output_tokens != PHASE6_MAX_OUTPUT_TOKENS:
        raise ValueError("Phase 6 parent output profile is invalid")
    _game_broker_config(config.settings, phase6=config.phase6)
    value = {
        "registry": dict(registry),
        "endpoint": config.settings.endpoint,
        "model": config.settings.model,
        "api_key": config.settings.api_key,
        "temperature": config.settings.generation.temperature,
        "connect_timeout_seconds": config.settings.connect_timeout_seconds,
        "read_timeout_seconds": config.settings.read_timeout_seconds,
        "write_timeout_seconds": config.settings.write_timeout_seconds,
        "pool_timeout_seconds": config.settings.pool_timeout_seconds,
        "request_timeout_seconds": config.settings.request_timeout_seconds,
        "max_request_bytes": config.settings.max_request_bytes,
        "max_response_bytes": config.settings.max_response_bytes,
        "structured_mode": config.settings.structured_mode,
        "fairness_seed": fairness_seed,
    }
    if config.phase6:
        value.update(
            phase6=True,
            phase6_max_output_tokens=PHASE6_MAX_OUTPUT_TOKENS,
            parent_config_fingerprint=config.settings.backend_config().config_fingerprint,
        )
    if config.settings.llama_cpp_structured_output is not None:
        value["llama_cpp_structured_output"] = asdict(config.settings.llama_cpp_structured_output)
    return value


def _random_private_identities(player_ids: Sequence[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    entry_tokens = ["entry_" + secrets.token_urlsafe(32) for _ in player_ids]
    opaque = [secrets.token_hex(32) for _ in player_ids]
    player_to_client = dict(zip(player_ids, opaque, strict=True))
    registry = {client_id: secrets.token_hex(32) for client_id in opaque}
    if len(set(entry_tokens)) != len(player_ids) or len(set(opaque)) != len(player_ids) or len(set(registry.values())) != len(player_ids):
        raise RuntimeError("secure identity generation collision")
    return player_to_client, registry, entry_tokens


async def _cleanup_phase6_wait_failure(
    owned: Sequence[OwnedProcess], stop_paths: Sequence[Path], model_pid: int | None,
    errors: list[str],
) -> tuple[list[dict[str, object]], bool]:
    """Retain the primary wait error while attempting the existing cleanup once."""
    stop_ok = True
    try:
        _touch_stop(stop_paths)
    except BaseException:
        stop_ok = False
    cleanup: list[dict[str, object]] = []
    try:
        result = await _cleanup_owned_shielded(owned, model_pid)
        if not isinstance(result, (list, tuple)) or len(result) != len(owned):
            raise ValueError("cleanup shape")
        for item, row in zip(owned, result, strict=True):
            expected = {
                "label": item.label, "pid": item.process.pid,
                "returncode": item.process.returncode,
                "alive": item.process.returncode is None,
                "action": "REFUSED_EXTERNAL_MODEL_PID"
                if item.process.pid == model_pid else item.touched,
            }
            if (
                not isinstance(row, Mapping) or set(row) != set(expected)
                or any(type(row[key]) is not type(value) or row[key] != value
                       for key, value in expected.items())
            ):
                raise ValueError("cleanup identity")
            cleanup.append(dict(expected))
    except BaseException:
        cleanup = []
        stop_ok = False
    complete = stop_ok and len(cleanup) == len(owned) and all(row["alive"] is False for row in cleanup)
    if not complete:
        errors.append("PHASE6_RECOVERY_CLEANUP_INCOMPLETE")
    return cleanup, complete


def _recover_phase6_wait_failure_evidence(
    *, ai_dir: Path, metrics_path: Path, status_paths: Mapping[str, Path],
    server_result_path: Path, broker_result_path: Path,
    player_to_client: Mapping[str, str], errors: list[str],
) -> dict[str, object]:
    """Read closed originals independently; never synthesize missing raw evidence."""
    recovered: dict[str, object] = {
        "statuses": {}, "server": {}, "broker": {}, "metrics": [],
        "manifest": None, "semantic": {}, "metrics_available": False,
    }
    def read(path: Path, code: str, *, lines: bool = False) -> object | None:
        try:
            return _read_jsonl(path) if lines else _read_json(path)
        except BaseException:
            if code not in errors:
                errors.append(code)
            return None

    statuses = {}
    for player, path in status_paths.items():
        value = read(path, "PHASE6_RECOVERY_CLIENT_STATUS_INCOMPLETE")
        if value is not None:
            statuses[player] = value
    recovered["statuses"] = statuses
    if set(statuses) != set(player_to_client) and "PHASE6_RECOVERY_CLIENT_STATUS_INCOMPLETE" not in errors:
        errors.append("PHASE6_RECOVERY_CLIENT_STATUS_INCOMPLETE")
    server = read(server_result_path, "PHASE6_RECOVERY_SERVER_RESULT_UNAVAILABLE")
    broker = read(broker_result_path, "PHASE6_RECOVERY_BROKER_RESULT_UNAVAILABLE")
    metrics = read(metrics_path, "PHASE6_RECOVERY_METRICS_UNAVAILABLE", lines=True)
    recovered.update(server=server or {}, broker=broker or {}, metrics=metrics or [], metrics_available=metrics is not None)
    try:
        manifest = ai_dir / "manifest.json"
        if manifest.exists():
            recovered["manifest"] = manifest
        elif (
            set(statuses) == set(player_to_client) and len(statuses) == 9
            and metrics is not None and broker is not None
            and isinstance(server, Mapping) and "accepted_text" in server
        ):
            try:
                manifest, semantic = _write_phase6_evidence(
                    ai_dir, statuses, metrics_path, player_to_client, server,
                )
                recovered.update(manifest=manifest, semantic=semantic)
            except BaseException:
                errors.append("PHASE6_RECOVERY_MANIFEST_REJECTED")
    except BaseException:
        errors.append("PHASE6_RECOVERY_FAILED")
    return recovered


def _phase6_wait_failure_row(
    *, root: Path, label: str, errors: list[str], cleanup: list[dict[str, object]],
    recovered: Mapping[str, object],
) -> dict[str, object]:
    """Build a failed row even when secondary diagnostics cannot be calculated."""
    from server.aiwolf_core.models import GamePhase

    server = recovered.get("server", {})
    broker = recovered.get("broker", {})
    server = server if isinstance(server, Mapping) else {}
    broker = broker if isinstance(broker, Mapping) else {}
    metrics = recovered.get("metrics", [])
    statuses = recovered.get("statuses", {})
    def calculate(callback: Callable[[], object], code: str, fallback: object) -> object:
        try:
            return callback()
        except BaseException:
            errors.append(code)
            return fallback

    aggregates = calculate(
        lambda: {key: value for key, value in _metric_aggregates(metrics).items()
                 if key != "queue_by_opaque_client"},
        "PHASE6_RECOVERY_METRIC_AGGREGATE_UNAVAILABLE", {},
    ) if recovered.get("metrics_available") is True else {}
    speaking = calculate(
        lambda: {key: value for key, value in _speaking_aggregates(statuses, server).items()
                 if key != "accepted_by_day_and_seat"},
        "PHASE6_RECOVERY_SPEAKING_AGGREGATE_UNAVAILABLE", {},
    )
    if recovered:
        artifacts = calculate(lambda: _artifact_hashes(root), "PHASE6_RECOVERY_ARTIFACT_HASH_UNAVAILABLE", [])
    else:
        artifacts = []
        errors.append("PHASE6_RECOVERY_ARTIFACT_HASH_UNAVAILABLE")
    def number(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None
    def boolean(value: object) -> bool | None:
        return value if type(value) is bool else None
    def count(value: object) -> int | None:
        return len(value) if isinstance(value, list) else None
    phase_wall = server.get("phase_wall_durations")
    phases = {phase.value for phase in GamePhase}
    if not isinstance(phase_wall, list) or any(
        not isinstance(item, dict) or set(item) != {"phase", "day", "wall_microseconds"}
        or not isinstance(item["phase"], str) or item["phase"] not in phases
        or number(item["day"]) is None or number(item["wall_microseconds"]) is None
        for item in phase_wall
    ):
        phase_wall = None
    total_wall = number(server.get("total_game_wall_microseconds"))
    broker_projection = {
        "maximum_pending": number(broker.get("maximum_pending")),
        "peak_backend_concurrency": number(broker.get("peak_backend_concurrency")),
        "shutdown_clean": boolean(broker.get("shutdown_clean")),
        "metrics_dropped": number(broker.get("metrics_dropped")),
    }
    return {
        "_phase6_wait_failure": True,
        "semantic": recovered.get("semantic", {}), "machine_semantic_pass": False,
        "row": label, "success": False, "errors": list(dict.fromkeys(errors))[:32],
        "process_topology": {"server": 1, "broker": 1, "clients": 9},
        "server": {
            "game_end": boolean(server.get("game_end")),
            "accepted_chat_count": count(server.get("accepted_chats")),
            "accepted_reservation_count": count(server.get("accepted_reservations")),
            "phase_wall_durations": phase_wall, "total_game_wall_microseconds": total_wall,
        },
        "total_game_wall_microseconds": total_wall, "broker": broker_projection,
        "maximum_pending": broker_projection["maximum_pending"],
        "peak_backend_concurrency": broker_projection["peak_backend_concurrency"],
        "metrics_dropped": broker_projection["metrics_dropped"],
        "metrics": aggregates, "speaking": speaking, "artifacts": artifacts,
        "cleanup": cleanup, "raw_metrics": metrics,
    }


async def _run_game(
    config: RunConfig,
    root: Path,
    *,
    label: str,
    environ: Mapping[str, str],
    phase6_fixture: bool = False,
) -> dict[str, object]:
    if phase6_fixture and not config.phase6:
        raise ValueError("semantic fixture requires Phase 6")
    broker_config = _game_broker_config(config.settings, phase6=config.phase6)
    _private_directory(root)
    process_dir = _new_private_subdirectory(root, "process")
    start = root / "clock.start"
    server_stop, broker_stop, clients_stop = root / "server.stop", root / "broker.stop", root / "clients.stop"
    server_ready, server_result_path = root / "server.ready.json", root / "server.result.json"
    broker_ready, broker_result_path = root / "broker.ready.json", root / "broker.result.json"
    broker_active = root / "broker.active.json"
    player_ids = [f"player-{index}" for index in range(9)]
    player_to_client, registry, entry_tokens = _random_private_identities(player_ids)
    sentinels = tuple(entry_tokens) + tuple(registry.values()) + ((config.settings.api_key,) if config.settings.api_key else ())
    relay = root / "discussion.relay.json"
    envelopes: dict[str, object] = {}
    owned: list[OwnedProcess] = []
    stop_paths = (clients_stop, server_stop, broker_stop)
    cleanup: list[dict[str, object]] = []
    errors: list[str] = []
    statuses: dict[str, dict[str, object]] = {}
    server_result: dict[str, object] = {}
    broker_result: dict[str, object] = {}
    metrics: list[dict[str, object]] = []
    semantic: dict[str, object] = {}
    manifest: Path | None = None
    ai_dir: Path | None = None
    completion_wait_pending = False
    recover_wait_failure = False
    cleanup_complete = False
    try:
        server = await _spawn_owned(
            owned,
            label="server",
            arguments=("--_child-mode", "server", "--ready", str(server_ready), "--result", str(server_result_path), "--start", str(start), "--stop", str(server_stop), "--seed", str(config.seed), "--max-seconds", str(config.max_seconds)),
            output_dir=process_dir,
            bootstrap={"entry_tokens": entry_tokens, **({"phase6": True, "discussion_relay": str(relay)} if config.phase6 else {})},
            environ=environ,
        )
        await _wait_for_paths((server_ready,), (server,), _READY_SECONDS)
        ready_server = _read_json(server_ready)
        if ready_server.get("player_ids") != player_ids or "entry_tokens" in ready_server:
            raise RuntimeError("server readiness evidence invalid")
        game_id = str(ready_server["game_id"])
        if config.phase6:
            envelopes = _consume_phase6_relay(relay, game_id, player_ids)
        game_dir = _new_private_subdirectory(root, game_id)
        ai_dir = _new_private_subdirectory(game_dir, "ai")
        metrics_path = ai_dir / "admission.jsonl"
        broker = await _spawn_owned(
            owned,
            label="broker",
            arguments=("--_child-mode", "broker", "--ready", str(broker_ready), "--result", str(broker_result_path), "--active", str(broker_active), "--metrics", str(metrics_path), "--stop", str(broker_stop), "--max-seconds", str(config.max_seconds)),
            output_dir=process_dir,
            bootstrap={**_broker_bootstrap(config, registry, f"{config.seed}:{label}"), **({"phase6_fixture": True} if phase6_fixture else {})},
            environ=environ,
        )
        await _wait_for_paths((broker_ready,), (broker,), _READY_SECONDS)
        ready_broker = _read_json(broker_ready)
        if "registry" in ready_broker:
            raise RuntimeError("broker readiness exposed private registry")
        if config.phase6:
            try:
                actual_broker_config = GenerationBrokerConfig(**ready_broker["config"])
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("broker ready configuration mismatch") from None
            if (
                actual_broker_config.config_fingerprint != broker_config.config_fingerprint
                or ready_broker.get("config_fingerprint") != broker_config.config_fingerprint
            ):
                raise RuntimeError("broker ready configuration mismatch")
        if config.phase6 and not phase6_fixture:
            backend_identity = ready_broker.get("backend_identity")
            if (
                not isinstance(backend_identity, Mapping)
                or backend_identity.get("config_fingerprint")
                != config.settings.backend_config().config_fingerprint
            ):
                raise RuntimeError("broker ready fingerprint mismatch")
        client_ready: list[Path] = []
        status_paths: dict[str, Path] = {}
        for index, player_id in enumerate(player_ids):
            opaque_id = player_to_client[player_id]
            shard = _new_private_subdirectory(ai_dir, opaque_id)
            ready_path = root / f"{player_id}.ready.json"
            status_path = root / f"{player_id}.status.json"
            await _spawn_owned(
                owned,
                label=player_id,
                arguments=("--_child-mode", "client", "--ready", str(ready_path), "--status", str(status_path), "--audit", str(shard / "ai.jsonl"), "--start", str(start), "--stop", str(clients_stop), "--seed", str(config.seed)),
                output_dir=process_dir,
                bootstrap={
                    **({"phase6": True, "discussion_envelope": envelopes.pop(player_id)} if config.phase6 else {}),
                    "uri": ready_server["uri"],
                    "game_id": game_id,
                    "player_id": player_id,
                    "entry_token": entry_tokens[index],
                    "admission_host": ready_broker["host"],
                    "admission_port": ready_broker["port"],
                    "admission_client_id": opaque_id,
                    "admission_token": registry[opaque_id],
                    "broker_config": ready_broker["config"],
                },
                environ=environ,
            )
            client_ready.append(ready_path)
            status_paths[player_id] = status_path
        await _wait_for_paths(tuple(client_ready), tuple(owned[2:]), _READY_SECONDS)
        start.write_text("start\n", encoding="utf-8")
        os.chmod(start, _PRIVATE_FILE_MODE)
        completion_wait_pending = True
        await _wait_for_paths((server_result_path,), tuple(owned), config.max_seconds)
        completion_wait_pending = False
        completion_wait_pending = True
        await _wait_for_paths(tuple(status_paths.values()), tuple(owned[2:]), 60.0)
        completion_wait_pending = False
        broker_stop.write_text("stop\n", encoding="utf-8")
        os.chmod(broker_stop, _PRIVATE_FILE_MODE)
        completion_wait_pending = True
        await _wait_for_paths(
            (broker_result_path,), (broker,),
            broker_config.shutdown_grace_seconds + _READY_SECONDS if config.phase6 else 15.0,
        )
        completion_wait_pending = False
        statuses = {player: _read_json(path) for player, path in status_paths.items()}
        server_result = _read_json(server_result_path)
        broker_result = _read_json(broker_result_path)
        metrics = _read_jsonl(metrics_path)
        if config.phase6:
            manifest, semantic = _write_phase6_evidence(
                ai_dir, statuses, metrics_path, player_to_client, server_result
            )
            if semantic["chat_start_count"] != sum(int(status["reaction"]["chat_brain_invocations"]) for status in statuses.values()):
                raise ValueError("semantic CHAT start accounting mismatch")
            if not semantic["semantic_requirements_met"]:
                errors.append("semantic requirements not met")
        else:
            manifest = _write_manifest(ai_dir, statuses, metrics_path, player_to_client)
    except Phase6PopulationExceeded as error:
        semantic = {
            "accepted_text_count": error.accepted_text_count,
            "failure_reason": error.reason,
        }
        errors.append(error.reason)
    except BaseException as error:
        recover_wait_failure = config.phase6 and completion_wait_pending
        errors.append(type(error).__name__)
        if isinstance(error, asyncio.CancelledError):
            errors.append("CANCELLED")
    finally:
        if recover_wait_failure:
            cleanup, cleanup_complete = await _cleanup_phase6_wait_failure(
                owned, stop_paths, config.gpu_model_pid, errors,
            )
        else:
            _touch_stop(stop_paths)
            cleanup = await _cleanup_owned_shielded(owned, config.gpu_model_pid)
    if recover_wait_failure:
        recovered: dict[str, object] = {}
        if cleanup_complete:
            try:
                recovered = _recover_phase6_wait_failure_evidence(
                    ai_dir=ai_dir, metrics_path=metrics_path, status_paths=status_paths,
                    server_result_path=server_result_path, broker_result_path=broker_result_path,
                    player_to_client=player_to_client, errors=errors,
                )
            except BaseException:
                errors.append("PHASE6_RECOVERY_FAILED")
            if recovered.get("manifest") is not None:
                try:
                    findings = _validate_game_evidence(
                        statuses=recovered["statuses"], server_result=recovered["server"],
                        broker_result=recovered["broker"], metrics=recovered["metrics"],
                        manifest=recovered["manifest"], ai_dir=ai_dir, owned=owned,
                        sentinels=sentinels, evidence_root=root,
                        expected_player_to_client=player_to_client,
                    )
                    if not isinstance(findings, list) or any(not isinstance(value, str) for value in findings):
                        raise ValueError("validation result shape")
                    errors.extend(findings)
                except BaseException:
                    errors.append("PHASE6_RECOVERY_VALIDATION_UNAVAILABLE")
        return _phase6_wait_failure_row(
            root=root, label=label, errors=errors, cleanup=cleanup, recovered=recovered,
        )
    if ai_dir is not None and manifest is not None and not errors:
        errors.extend(
            _validate_game_evidence(
                statuses=statuses,
                server_result=server_result,
                broker_result=broker_result,
                metrics=metrics,
                manifest=manifest,
                ai_dir=ai_dir,
                owned=owned,
                sentinels=sentinels,
                evidence_root=root,
                expected_player_to_client=player_to_client,
            )
        )
    if any(item.get("alive") for item in cleanup):
        errors.append("owned process remains alive")
    return {
        **({"semantic": semantic, "machine_semantic_pass": not errors and semantic.get("semantic_requirements_met") is True} if config.phase6 else {}),
        "row": label,
        "success": not errors,
        "errors": errors[:32],
        "process_topology": {"server": 1, "broker": 1, "clients": 9},
        "server": {
            "game_end": server_result.get("game_end"),
            "accepted_chat_count": len(server_result.get("accepted_chats", [])),
            "accepted_reservation_count": len(server_result.get("accepted_reservations", [])),
            "phase_wall_durations": server_result.get("phase_wall_durations", []),
            "total_game_wall_microseconds": server_result.get(
                "total_game_wall_microseconds"
            ),
        },
        "total_game_wall_microseconds": server_result.get(
            "total_game_wall_microseconds"
        ),
        "broker": {
            "maximum_pending": broker_result.get("maximum_pending"),
            "peak_backend_concurrency": broker_result.get("peak_backend_concurrency"),
            "shutdown_clean": broker_result.get("shutdown_clean"),
            "metrics_dropped": broker_result.get("metrics_dropped"),
        },
        "maximum_pending": broker_result.get("maximum_pending"),
        "peak_backend_concurrency": broker_result.get("peak_backend_concurrency"),
        "metrics": {key: value for key, value in _metric_aggregates(metrics).items()
                    if not config.phase6 or key != "queue_by_opaque_client"},
        "speaking": {key: value for key, value in _speaking_aggregates(statuses, server_result).items()
                     if not config.phase6 or key != "accepted_by_day_and_seat"},
        "artifacts": _artifact_hashes(root),
        "cleanup": cleanup,
        "raw_metrics": metrics,
        "metrics_dropped": broker_result.get("metrics_dropped"),
    }


def _speaking_aggregates(
    statuses: Mapping[str, Mapping[str, object]], server_result: Mapping[str, object]
) -> dict[str, object]:
    chats = [item for item in server_result.get("accepted_chats", []) if isinstance(item, Mapping)]
    by_day_seat: Counter[str] = Counter(
        f"{item.get('day')}:{item.get('player_id')}" for item in chats
    )
    intervals: list[int] = []
    for key in sorted({(item.get("day"), item.get("player_id")) for item in chats}):
        values = sorted(
            float(item["accepted_at"])
            for item in chats
            if (item.get("day"), item.get("player_id")) == key
        )
        intervals.extend(max(0, int((right - left) * 1_000_000)) for left, right in zip(values, values[1:]))
    suppressions: Counter[str] = Counter()
    chat_calls = 0
    deadline_misses = 0
    for status in statuses.values():
        reaction = status.get("reaction")
        if isinstance(reaction, Mapping):
            if isinstance(reaction.get("chat_brain_invocations"), int):
                chat_calls += int(reaction["chat_brain_invocations"])
            if isinstance(reaction.get("deadline_suppressed_count"), int):
                deadline_misses += int(reaction["deadline_suppressed_count"])
            if isinstance(reaction.get("suppression_counts"), Mapping):
                suppressions.update({str(k): int(v) for k, v in reaction["suppression_counts"].items()})
        reservation = status.get("reservation")
        if isinstance(reservation, Mapping) and isinstance(
            reservation.get("deadline_suppressed_count"), int
        ):
            deadline_misses += int(reservation["deadline_suppressed_count"])
    return {
        "accepted_by_day_and_seat": dict(sorted(by_day_seat.items())),
        "message_chars": _distribution([int(item["message_chars"]) for item in chats]),
        "message_utf8_bytes": _distribution([int(item["message_utf8_bytes"]) for item in chats]),
        "inter_accepted_microseconds": _distribution(intervals),
        "calls_per_accepted_speech": None if not chats else chat_calls / len(chats),
        "frequency_suppression_counts": dict(sorted(suppressions.items())),
        "deadline_miss_count": deadline_misses,
    }


def _artifact_hashes(root: Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".json", ".jsonl"}:
            values.append(
                {
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    return values


async def _run_q8_broker_row(
    config: RunConfig,
    root: Path,
    plan: Q8RowPlan,
    *,
    environ: Mapping[str, str],
) -> dict[str, object]:
    _private_directory(root)
    process_dir = _new_private_subdirectory(root, "process")
    metrics_path = root / "admission.jsonl"
    broker_ready, broker_result_path = root / "broker.ready.json", root / "broker.result.json"
    broker_active, broker_stop = root / "broker.active.json", root / "broker.stop"
    start = root / "workers.start"
    first_start = root / "first-worker.start"
    client_ids = [secrets.token_hex(32) for _ in range(9)]
    registry = {client_id: secrets.token_hex(32) for client_id in client_ids}
    sentinels = tuple(registry.values()) + ((config.settings.api_key,) if config.settings.api_key else ())
    owned: list[OwnedProcess] = []
    status_paths: list[Path] = []
    cleanup: list[dict[str, object]] = []
    errors: list[str] = []
    metrics: list[dict[str, object]] = []
    broker_result: dict[str, object] = {}
    worker_statuses: list[dict[str, object]] = []
    try:
        broker = await _spawn_owned(
            owned,
            label="broker",
            arguments=("--_child-mode", "broker", "--ready", str(broker_ready), "--result", str(broker_result_path), "--active", str(broker_active), "--metrics", str(metrics_path), "--stop", str(broker_stop), "--max-seconds", str(plan.limit_seconds)),
            output_dir=process_dir,
            bootstrap=_broker_bootstrap(config, registry, f"{config.seed}:{plan.row}"),
            environ=environ,
        )
        await _wait_for_paths((broker_ready,), (broker,), _READY_SECONDS)
        ready = _read_json(broker_ready)

        async def spawn_worker(index: int, descriptors: list[dict[str, object]], gate: Path | None) -> None:
            ready_path = root / f"worker-{index}.ready.json"
            status_path = root / f"worker-{index}.status.json"
            await _spawn_owned(
                owned,
                label=f"worker-{index}",
                arguments=("--_child-mode", "q8-worker", "--ready", str(ready_path), "--status", str(status_path), "--stop", str(broker_stop)) + (() if gate is None else ("--start", str(gate))),
                output_dir=process_dir,
                bootstrap={
                    "host": ready["host"],
                    "port": ready["port"],
                    "client_id": client_ids[index],
                    "token": registry[client_ids[index]],
                    "broker_config": ready["config"],
                    "requests": descriptors,
                },
                environ=environ,
            )
            status_paths.append(status_path)

        if plan.row == "Q8-A":
            descriptors = [
                {
                    "row": plan.row,
                    "bucket": bucket,
                    "cold": ordinal == 0,
                    "priority": int(GenerationPriority.REACTION),
                    "limit_seconds": plan.limit_seconds,
                }
                for ordinal, bucket in enumerate(("minimum", "minimum", "minimum", "median", "median", "median", "maximum", "maximum", "maximum"))
            ]
            await spawn_worker(0, descriptors, start)
            await _wait_for_paths((root / "worker-0.ready.json",), tuple(owned[1:]), _READY_SECONDS)
            start.write_text("start\n", encoding="utf-8")
        elif plan.row == "Q8-B":
            for index in range(9):
                await spawn_worker(
                    index,
                    [{"row": plan.row, "bucket": "median", "cold": False, "priority": int(GenerationPriority.REACTION), "limit_seconds": plan.limit_seconds}],
                    start,
                )
            await _wait_for_paths(tuple(root / f"worker-{index}.ready.json" for index in range(9)), tuple(owned[1:]), _READY_SECONDS)
            start.write_text("start\n", encoding="utf-8")
        elif plan.row == "Q8-C":
            await spawn_worker(
                0,
                [{"row": plan.row, "bucket": "median", "cold": False, "priority": int(GenerationPriority.REACTION), "limit_seconds": plan.limit_seconds}],
                first_start,
            )
            for index, priority in enumerate(plan.priorities[1:], 1):
                await spawn_worker(
                    index,
                    [{"row": plan.row, "bucket": "median", "cold": False, "priority": int(priority), "limit_seconds": plan.limit_seconds}],
                    start,
                )
            await _wait_for_paths(
                tuple(root / f"worker-{index}.ready.json" for index in range(9)),
                tuple(owned[1:]),
                _READY_SECONDS,
            )
            first_start.write_text("start first reaction\n", encoding="utf-8")
            os.chmod(first_start, _PRIVATE_FILE_MODE)
            await _wait_for_paths((broker_active,), tuple(owned), _READY_SECONDS)
            start.write_text("start\n", encoding="utf-8")
        else:
            raise ValueError("unsupported Q8 broker row")
        os.chmod(start, _PRIVATE_FILE_MODE)
        await _wait_for_paths(tuple(status_paths), tuple(owned[1:]), plan.limit_seconds)
        await asyncio.gather(*(asyncio.wait_for(item.process.wait(), 10.0) for item in owned[1:]))
        broker_stop.write_text("stop\n", encoding="utf-8")
        os.chmod(broker_stop, _PRIVATE_FILE_MODE)
        await _wait_for_paths((broker_result_path,), (broker,), 15.0)
        worker_statuses = [_read_json(path) for path in status_paths]
        broker_result = _read_json(broker_result_path)
        metrics = _read_jsonl(metrics_path)
    except BaseException as error:
        errors.append(type(error).__name__)
        if isinstance(error, asyncio.CancelledError):
            errors.append("CANCELLED")
    finally:
        _touch_stop((broker_stop,))
        cleanup = await _cleanup_owned_shielded(owned, config.gpu_model_pid)
    if len(worker_statuses) != (1 if plan.row == "Q8-A" else 9):
        errors.append("Q8 worker evidence count mismatch")
    if sum(len(item.get("outcomes", [])) for item in worker_statuses) != plan.requests:
        errors.append("Q8 request count mismatch")
    if any(item.get("success") is not True for item in worker_statuses):
        errors.append("Q8 worker failed")
    if broker_result.get("success") is not True or broker_result.get("peak_backend_concurrency") != 1:
        errors.append("Q8 broker failed or concurrency was not one")
    aggregates = _metric_aggregates(metrics)
    if not aggregates["provider_timing_complete"]:
        errors.append("PROVIDER_TIMING_UNAVAILABLE")
    if aggregates["backend_failure_count"] != 0:
        errors.append("Q8 backend failure")
    if any(item.get("terminal_status") in {"OVERLOADED", "POISONED"} for item in metrics):
        errors.append("Q8 overload or poison")
    enqueued_ids = {
        item.get("invocation_id") for item in metrics if item.get("event") == "ENQUEUED"
    }
    terminal_ids = {
        item.get("invocation_id") for item in metrics if item.get("event") == "TERMINAL"
    }
    offered = [item for item in metrics if item.get("event") == "OFFERED"]
    if len(enqueued_ids) != plan.requests or enqueued_ids != terminal_ids or len(offered) != plan.requests:
        errors.append("Q8 admission lifecycle is incomplete")
    if plan.row == "Q8-B":
        if any(item.get("priority") != int(GenerationPriority.REACTION) for item in offered):
            errors.append("Q8-B contained a non-reaction grant")
        if len({item.get("client_id") for item in offered}) != 9:
            errors.append("Q8-B did not use nine authenticated clients")
    if plan.row == "Q8-C":
        priorities = [item.get("priority") for item in offered]
        expected_priorities = [
            int(GenerationPriority.REACTION),
            *(int(GenerationPriority.RESERVATION) for _ in range(4)),
            *(int(GenerationPriority.REACTION) for _ in range(4)),
        ]
        if priorities != expected_priorities or not broker_active.exists():
            errors.append("Q8-C active/non-preemptive reservation order is not exact")
    for item in owned:
        if item.process.returncode != 0 or _bounded_tail(item.stderr_path):
            errors.append(f"Q8 owned child failure: {item.label}")
    for path in (artifact for artifact in root.rglob("*") if artifact.is_file()):
        data = path.read_text(encoding="utf-8", errors="replace")
        if any(secret and secret in data for secret in sentinels):
            errors.append(f"secret retained in Q8 evidence: {path.name}")
    if any(item.get("alive") for item in cleanup):
        errors.append("Q8 owned process remains alive")
    return {
        "row": plan.row,
        "workload": plan.workload,
        "requests": plan.requests,
        "limit_seconds": plan.limit_seconds,
        "success": not errors,
        "errors": errors[:32],
        "metrics": aggregates,
        "maximum_pending": broker_result.get("maximum_pending"),
        "peak_backend_concurrency": broker_result.get("peak_backend_concurrency"),
        "metrics_dropped": broker_result.get("metrics_dropped"),
        "artifacts": _artifact_hashes(root),
        "cleanup": cleanup,
        "raw_metrics": metrics,
    }


def _strip_raw(result: Mapping[str, object]) -> dict[str, object]:
    excluded = {"raw_metrics", "_phase6_wait_failure"}
    if result.get("_phase6_wait_failure") is True:
        # Keep the original inventory in the private result, never in its summary.
        excluded.add("artifacts")
    return {key: value for key, value in result.items() if key not in excluded}


def _raw_records(row: str, metrics: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [{"row": row, **dict(metric)} for metric in metrics]


def _run_metadata(config: RunConfig) -> dict[str, object]:
    backend = config.settings.backend_config()
    return {
        "schema": "aiwolf.phase6-run-summary.v1" if config.phase6 else "aiwolf.phase5-run-summary.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "phase6" if config.phase6 else ("q8" if config.q8 else "smoke"),
        "model_identity": {
            "endpoint": config.settings.endpoint,
            "model": config.settings.model,
            "config_fingerprint": backend.config_fingerprint,
            "ownership": "EXTERNAL_UNOWNED",
        },
        "python": platform.python_version(),
        "os": platform.platform(),
        "seed": config.seed,
        "arguments": list(config.sanitized_arguments),
        "game_plan": asdict(PHASE6_GAME_PLAN if config.phase6 else GAME_PLAN),
        "frequency_profile": {
            "talkativeness": GAME_PLAN.talkativeness,
            "initial_event_importance": GAME_PLAN.initial_event_importance,
            "direct_mention_importance": GAME_PLAN.direct_mention_importance,
            "ordinary_event_importance": GAME_PLAN.ordinary_event_importance,
            "cooldown_seconds": GAME_PLAN.cooldown_seconds,
            "max_trigger_evaluations_per_phase": GAME_PLAN.max_trigger_evaluations_per_phase,
            "repetition_window": GAME_PLAN.repetition_window,
        },
    }


_DIAGNOSTIC_STATUS_FIELD = "provider_timing_diagnostic_status"
_DIAGNOSTIC_STATE_FIELDS = (
    "provider_timing_prompt_n_state",
    "provider_timing_prompt_ms_state",
    "provider_timing_predicted_n_state",
    "provider_timing_predicted_ms_state",
)


def _diagnostic_shape_summary(
    calls: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], bool]:
    status_counts = {status.value: 0 for status in ProviderTimingShapeStatus}
    field_counts = {
        field: {state.value: 0 for state in ProviderTimingFieldState}
        for field in _DIAGNOSTIC_STATE_FIELDS
    }
    extra_counts: list[int] = []
    well_formed = True
    for call in calls:
        status = call.get(_DIAGNOSTIC_STATUS_FIELD)
        states = [call.get(field) for field in _DIAGNOSTIC_STATE_FIELDS]
        extra_count = call.get("provider_timing_extra_key_count")
        try:
            parsed_status = ProviderTimingShapeStatus(status)
            parsed_states = [ProviderTimingFieldState(state) for state in states]
            ProviderTimingDiagnostic(
                status=parsed_status,
                prompt_n=parsed_states[0],
                prompt_ms=parsed_states[1],
                predicted_n=parsed_states[2],
                predicted_ms=parsed_states[3],
                extra_key_count=extra_count,
            )
        except (TypeError, ValueError):
            well_formed = False
            continue
        assert isinstance(status, str)
        assert isinstance(extra_count, int)
        status_counts[status] += 1
        for field, state in zip(_DIAGNOSTIC_STATE_FIELDS, states):
            assert isinstance(state, str)
            field_counts[field][state] += 1
        extra_counts.append(extra_count)
    return (
        {
            "status_counts": status_counts,
            "field_state_counts": field_counts,
            "extra_key_count": {
                "count": len(extra_counts),
                "min": min(extra_counts) if extra_counts else None,
                "max": max(extra_counts) if extra_counts else None,
            },
        },
        well_formed,
    )


def _diagnostic_cleanup_clean(
    cleanup: Sequence[Mapping[str, object]],
) -> bool:
    if len(cleanup) != 2 or any(not isinstance(item, Mapping) for item in cleanup):
        return False
    labels = [item.get("label") for item in cleanup]
    pids = [item.get("pid") for item in cleanup]
    return (
        all(type(label) is str for label in labels)
        and labels.count("broker") == 1
        and labels.count("worker-0") == 1
        and all(type(pid) is int and pid > 0 for pid in pids)
        and len(set(pids)) == 2
        and all(
            type(item.get("returncode")) is int
            and item.get("returncode") == 0
            and item.get("alive") is False
            and type(item.get("action")) is str
            and item.get("action") == "wait"
            for item in cleanup
        )
    )


def _diagnostic_route(
    metrics: Sequence[Mapping[str, object]],
    *,
    row_errors: Sequence[object],
    metrics_dropped: object,
    cleanup: Sequence[Mapping[str, object]],
    end_identity_matches: bool,
    external_evidence_clean: bool,
) -> dict[str, object]:
    calls = [item for item in metrics if item.get("event") == "PROVIDER_CALL_TERMINAL"]
    shape, diagnostics_well_formed = _diagnostic_shape_summary(calls)
    call_ids = [item.get("invocation_id") for item in calls]
    terminal = [item for item in metrics if item.get("event") == "TERMINAL"]
    terminal_ids = [item.get("invocation_id") for item in terminal]
    structural_errors = [error for error in row_errors if error != _TIMING_UNAVAILABLE]
    cleanup_clean = _diagnostic_cleanup_clean(cleanup)
    complete = (
        not structural_errors
        and type(metrics_dropped) is int
        and metrics_dropped == 0
        and len(calls) == 9
        and all(isinstance(value, str) and value for value in call_ids)
        and len(call_ids) == len(set(call_ids))
        and all(item.get("backend_code") is None for item in calls)
        and diagnostics_well_formed
        and len(terminal) == 9
        and all(isinstance(value, str) and value for value in terminal_ids)
        and len(terminal_ids) == len(set(terminal_ids))
        and set(terminal_ids) == set(call_ids)
        and all(item.get("terminal_status") == "RELEASED" for item in terminal)
        and cleanup_clean
        and end_identity_matches is True
        and external_evidence_clean is True
    )
    result: dict[str, object] = {
        **shape,
        "complete": complete,
        "successful_requests": sum(item.get("backend_code") is None for item in calls),
        "terminal_diagnostics": len(calls),
        "metrics_dropped": metrics_dropped,
        "end_identity_matches": end_identity_matches,
        "cleanup_clean": cleanup_clean,
        "result": None,
        "route": None,
    }
    if not complete:
        result["result"] = _DIAGNOSTIC_INCOMPLETE
        return result

    statuses = [str(item[_DIAGNOSTIC_STATUS_FIELD]) for item in calls]
    timing_complete = _metric_aggregates(metrics)["provider_timing_complete"] is True
    timings_shape_valid = all(
        status
        in {
            ProviderTimingShapeStatus.VALID_EXACT.value,
            ProviderTimingShapeStatus.VALID_WITH_EXTRA.value,
        }
        for status in statuses
    )
    if timings_shape_valid and timing_complete:
        result["result"] = "PASS"
        result["route"] = "RUN_STANDARD_Q8"
        return result
    if all(status == ProviderTimingShapeStatus.VALID_EXACT.value for status in statuses):
        result["result"] = _TIMING_UNAVAILABLE
        result["route"] = "REPAIR_PROVIDER_TIMING_PROPAGATION"
        return result
    if (
        timings_shape_valid
        and ProviderTimingShapeStatus.VALID_WITH_EXTRA.value in statuses
    ):
        result["result"] = _TIMING_UNAVAILABLE
        result["route"] = "REPAIR_ADAPTER_REQUIRED_SUBSET"
        return result
    if all(status == ProviderTimingShapeStatus.ABSENT.value for status in statuses):
        result["result"] = _TIMING_UNAVAILABLE
        result["route"] = "REVIEW_HOST_PROFILE_PERF"
        return result
    result["result"] = _TIMING_UNAVAILABLE
    result["route"] = "REVIEW_PROVIDER_CAPABILITY_OR_UPGRADE"
    return result


def _diagnostic_end_identity_matches(
    environment: ProviderDiagnosticEnvironment,
    *,
    process_path_resolver: Callable[[int], Path] | None = None,
    file_hasher: Callable[[Path], str] | None = None,
    identity_reader: Callable[[Path], ProviderBuildIdentity] | None = None,
) -> bool:
    process_path_resolver = process_path_resolver or _resolve_process_executable
    file_hasher = file_hasher or _sha256_file
    identity_reader = identity_reader or _read_provider_build_identity
    try:
        executable = process_path_resolver(environment.serving_pid)
        if executable != environment.serving_executable or not executable.is_file():
            return False
        executable_hash = file_hasher(executable)
        if executable_hash != environment.serving_executable_sha256:
            return False
        identity = identity_reader(executable)
        if file_hasher(executable) != executable_hash:
            return False
        return (
            identity == environment.identity
            and _provider_identity_binding_sha256(executable_hash, identity)
            == environment.identity_binding_sha256
        )
    except Exception:
        return False


async def _execute_provider_timing_diagnostic(
    config: RunConfig, environ: Mapping[str, str]
) -> int:
    environment = config.diagnostic_environment
    assert environment is not None
    _private_directory(config.output_dir)
    external_before = await _external_model_inventory(
        config.settings.endpoint, environment.serving_pid
    )
    sampler = _GpuSampler(environment.serving_pid)
    sampler.start()
    result = await _run_q8_broker_row(
        config,
        config.output_dir / "q8-a",
        Q8_PLAN[0],
        environ=environ,
    )
    gpu = await asyncio.shield(sampler.stop())
    external_after = await _external_model_inventory(
        config.settings.endpoint, environment.serving_pid
    )
    external_errors = _validate_external_model_evidence(
        external_before,
        external_after,
        (result,),
        environment.serving_pid,
    )
    end_identity_matches = _diagnostic_end_identity_matches(environment)
    raw = _raw_records("Q8-A", result["raw_metrics"])
    if len(raw) > _MAX_RAW_RECORDS:
        raw = raw[:_MAX_RAW_RECORDS]
        result = {
            **result,
            "errors": [*result.get("errors", ()), "RAW_EVIDENCE_BOUND_EXCEEDED"],
        }
    raw_path = config.output_dir / "raw.jsonl"
    _write_private_jsonl_atomic(raw_path, raw)
    diagnostic = _diagnostic_route(
        raw,
        row_errors=result.get("errors", ()),
        metrics_dropped=result.get("metrics_dropped"),
        cleanup=result.get("cleanup", ()),
        end_identity_matches=end_identity_matches,
        external_evidence_clean=not external_errors,
    )
    summary = {
        "schema": "aiwolf.phase5-provider-timing-diagnostic.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "q8-provider-timing-diagnostic",
        "serving_executable_sha256": environment.serving_executable_sha256,
        "profile_sha256": environment.profile_sha256,
        "profile_fingerprint_method": "sha256-canonical-json-exact-model-and-args",
        "serving_executable_fingerprint_method": "sha256-exact-serving-process-image-bytes",
        "backend_config_sha256": environment.backend_config_sha256,
        "provider_identity": asdict(environment.identity),
        "provider_identity_binding_sha256": environment.identity_binding_sha256,
        "provider_profile_perf_enabled": environment.provider_profile_perf_enabled,
        "diagnostic": diagnostic,
        "q8_a": {
            "requests": Q8_PLAN[0].requests,
            "limit_seconds": Q8_PLAN[0].limit_seconds,
            "metrics": result.get("metrics"),
            "maximum_pending": result.get("maximum_pending"),
            "peak_backend_concurrency": result.get("peak_backend_concurrency"),
            "cleanup": result.get("cleanup"),
        },
        "gpu": gpu,
        "external_model_availability": {
            "before_listener": external_before.get("listener_availability"),
            "before_pid": external_before.get("model_pid_availability"),
            "after_listener": external_after.get("listener_availability"),
            "after_pid": external_after.get("model_pid_availability"),
        },
        "evidence_files": [
            {
                "path": "raw.jsonl",
                "bytes": raw_path.stat().st_size,
                "sha256": _sha256_file(raw_path),
            },
            *(
                {
                    **dict(artifact),
                    "path": f"q8-a/{artifact['path']}",
                }
                for artifact in result.get("artifacts", ())
                if isinstance(artifact, Mapping)
                and isinstance(artifact.get("path"), str)
            ),
        ],
        "success": diagnostic["result"] == "PASS",
        "failure": None if diagnostic["result"] == "PASS" else diagnostic["result"],
        "external_model_control_actions": [],
    }
    _write_private_json_atomic(config.output_dir / "summary.json", summary)
    if summary["success"]:
        print(f"Phase 5 provider timing diagnostic PASS; evidence={config.output_dir}")
        return 0
    print(
        f"Phase 5 provider timing diagnostic FAILED; code={summary['failure']}; evidence={config.output_dir}",
        file=sys.stderr,
    )
    return 1


async def _execute(config: RunConfig, environ: Mapping[str, str]) -> int:
    if config.q8_provider_timing_diagnostic:
        return await _execute_provider_timing_diagnostic(config, environ)
    _private_directory(config.output_dir)
    external_before = await _external_model_inventory(
        config.settings.endpoint, config.gpu_model_pid
    )
    sampler = _GpuSampler(config.gpu_model_pid)
    sampler.start()
    rows: list[dict[str, object]] = []
    raw: list[dict[str, object]] = []
    failure: str | None = None
    try:
        if config.q8:
            for plan in Q8_PLAN[:3]:
                result = await _run_q8_broker_row(
                    config,
                    config.output_dir / plan.row.lower(),
                    plan,
                    environ=environ,
                )
                rows.append(_strip_raw(result))
                raw.extend(_raw_records(plan.row, result["raw_metrics"]))
                if not result["success"]:
                    if "CANCELLED" in result["errors"]:
                        failure = "CANCELLED"
                    else:
                        failure = "PROVIDER_TIMING_UNAVAILABLE" if "PROVIDER_TIMING_UNAVAILABLE" in result["errors"] else "Q8_ROW_FAILED"
                    break
            if failure is None:
                result = await _run_game(
                    config,
                    config.output_dir / "q8-d",
                    label="Q8-D",
                    environ=environ,
                )
                rows.append(_strip_raw(result))
                raw.extend(_raw_records("Q8-D", result["raw_metrics"]))
                if not result["success"]:
                    failure = "CANCELLED" if "CANCELLED" in result["errors"] else "Q8_D_FAILED"
                elif not result["metrics"]["provider_timing_complete"]:
                    failure = "PROVIDER_TIMING_UNAVAILABLE"
        else:
            result = await _run_game(
                config,
                config.output_dir / "smoke",
                label="SMOKE",
                environ=environ,
            )
            rows.append(_strip_raw(result))
            raw.extend(_raw_records("SMOKE", result["raw_metrics"]))
            if not result["success"]:
                failure = "CANCELLED" if "CANCELLED" in result["errors"] else "SMOKE_FAILED"
    except asyncio.CancelledError:
        failure = "CANCELLED"
    except BaseException as error:
        failure = type(error).__name__
    gpu = await asyncio.shield(sampler.stop())
    external_after = await _external_model_inventory(
        config.settings.endpoint, config.gpu_model_pid
    )
    external_errors = _validate_external_model_evidence(
        external_before, external_after, rows, config.gpu_model_pid
    )
    if external_errors:
        failure = failure or "EXTERNAL_MODEL_INVENTORY_FAILED"
    if len(raw) > _MAX_RAW_RECORDS:
        failure = failure or "RAW_EVIDENCE_BOUND_EXCEEDED"
        raw = raw[:_MAX_RAW_RECORDS]
    if config.phase6 and failure is not None:
        for row in rows:
            row["machine_semantic_pass"] = False
    _write_private_jsonl_atomic(config.output_dir / "raw.jsonl", raw)
    overall_metrics = _metric_aggregates(raw)
    if config.phase6:
        overall_metrics.pop("queue_by_opaque_client", None)
    summary = {
        **_run_metadata(config),
        "success": failure is None and all(row.get("success") is True for row in rows),
        "failure": failure,
        "rows": rows,
        "overall_metrics": overall_metrics,
        "overall_queue_high_water": max(
            (
                int(row["maximum_pending"])
                for row in rows
                if isinstance(row.get("maximum_pending"), int)
            ),
            default=None,
        ),
        "overall_peak_backend_concurrency": max(
            (
                int(row["peak_backend_concurrency"])
                for row in rows
                if isinstance(row.get("peak_backend_concurrency"), int)
            ),
            default=None,
        ),
        "overall_speaking": next(
            (row.get("speaking") for row in rows if row.get("row") in {"SMOKE", "Q8-D"}),
            None,
        ),
        "gpu": gpu,
        "external_model_inventory": {
            "before": external_before,
            "after": external_after,
            "model_pid_owned": any(
                item.get("pid") == config.gpu_model_pid
                for row in rows
                for item in row.get("cleanup", [])
                if isinstance(item, Mapping)
            )
            if config.gpu_model_pid is not None
            else False,
            "errors": external_errors,
        },
        "external_model_control_actions": [],
        "cleanup": [item for row in rows for item in row.get("cleanup", [])],
    }
    _write_private_json_atomic(config.output_dir / "summary.json", summary)
    if summary["success"]:
        print(f"Phase 5 {'Q8' if config.q8 else 'smoke'} PASS; evidence={config.output_dir}")
        return 0
    print(
        f"Phase 5 {'Q8' if config.q8 else 'smoke'} FAILED; code={failure}; evidence={config.output_dir}",
        file=sys.stderr,
    )
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--q8", action="store_true")
    mode.add_argument("--phase6", action="store_true")
    parser.add_argument("--phase6-read-timeout-seconds", type=float)
    parser.add_argument("--phase6-request-timeout-seconds", type=float)
    mode.add_argument("--q8-provider-timing-diagnostic", action="store_true")
    parser.add_argument("--gpu-model-pid", type=int)
    parser.add_argument("--provider-serving-executable-sha256")
    parser.add_argument("--provider-profile-sha256")
    parser.add_argument("--provider-profile-model")
    parser.add_argument("--provider-profile-args")
    parser.add_argument("--provider-backend-config-sha256")
    parser.add_argument("--provider-implementation")
    parser.add_argument("--provider-version")
    parser.add_argument("--provider-build", type=int)
    parser.add_argument("--provider-commit")
    parser.add_argument(
        "--provider-profile-perf-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--seed", type=int, default=8625)
    parser.add_argument("--max-seconds", type=float, default=_SMOKE_HARD_LIMIT_SECONDS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "logs" / f"phase5-smoke-{int(time.time())}-{uuid4().hex[:8]}",
    )
    parser.add_argument("--_child-mode", choices=("server", "broker", "client", "q8-worker"), help=argparse.SUPPRESS)
    parser.add_argument("--ready", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--status", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--metrics", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--audit", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--active", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--start", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--stop", type=Path, help=argparse.SUPPRESS)
    return parser


async def _run_child(args: argparse.Namespace) -> int:
    raw = sys.stdin.buffer.readline()
    if not raw:
        raise ValueError("private bootstrap pipe is required")
    bootstrap = json.loads(raw, object_pairs_hook=_unique_json_object)
    del raw
    if not isinstance(bootstrap, dict):
        raise ValueError("private bootstrap must be a JSON object")
    required: dict[str, tuple[str, ...]] = {
        "server": ("ready", "result", "start", "stop"),
        "broker": ("ready", "result", "metrics", "stop"),
        "client": ("ready", "status", "audit", "start", "stop"),
        "q8-worker": ("ready", "status", "stop"),
    }
    assert args._child_mode is not None
    if any(getattr(args, name) is None for name in required[args._child_mode]):
        raise ValueError("child evidence paths are required")
    if args._child_mode == "server":
        return await _server_child(args, bootstrap)
    if args._child_mode == "broker":
        return await _broker_child(args, bootstrap)
    if args._child_mode == "client":
        return await _client_child(args, bootstrap)
    return await _q8_worker_child(args, bootstrap)


def main() -> None:
    args = _parser().parse_args()
    try:
        if args._child_mode is not None:
            code = asyncio.run(_run_child(args))
        else:
            config = _prepare_run(args)
            code = asyncio.run(_execute(config, dict(os.environ)))
    except ProviderTimingDiagnosticPreflightError:
        print(_DIAGNOSTIC_INCOMPLETE, file=sys.stderr)
        code = 1
    except (FileExistsError, TypeError, ValueError) as error:
        if bool(getattr(args, "q8_provider_timing_diagnostic", False)):
            print(_DIAGNOSTIC_INCOMPLETE, file=sys.stderr)
            code = 1
        elif args._child_mode is not None:
            print(f"Runner child configuration invalid: {type(error).__name__}", file=sys.stderr)
            code = 2
        else:
            print(f"Phase 5 runner configuration error: {type(error).__name__}: {error}", file=sys.stderr)
            code = 2
    except BaseException as error:
        if bool(getattr(args, "q8_provider_timing_diagnostic", False)):
            print(_DIAGNOSTIC_INCOMPLETE, file=sys.stderr)
        else:
            print(f"Phase 5 runner failed: {type(error).__name__}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
