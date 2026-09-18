"""Finite opt-in one-real-LLM Phase 4 game smoke.

The script owns only the game/client subprocesses it creates.  It never starts,
stops, probes, or otherwise manages the separately operated model server.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from random import Random
import stat
import sys
import tempfile
import time
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client._compat import await_with_timeout
from ai_client.llm import LocalLLMSettings
from server.aiwolf_core import (
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import (
    GameRegistry,
    SessionManager,
    SessionResult,
    WebSocketGameServer,
    monotonic_seconds,
)
from tests.fixtures.phase3_4_reaction_brain import CompletionReactionMode


CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase4_client_process.py"
_HARD_LIMIT_SECONDS = 300.0
_TAIL_BYTES = 4096


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _write_private_server_launch(
    path: Path,
    *,
    uri: str,
    game_id: str,
    entry_tokens: Mapping[str, str],
    player_ids: list[str],
    server_pid: int,
) -> None:
    _write_private_json(
        path,
        {
            "uri": uri,
            "game_id": game_id,
            "entry_tokens": dict(entry_tokens),
            "player_ids": player_ids,
            "server_pid": server_pid,
        },
    )


def _accepted_action_evidence(result: SessionResult) -> list[dict[str, object]]:
    """Extract accepted actions from each production SessionResult evidence surface."""

    evidence: list[dict[str, object]] = []
    if result.reply is not None and result.reply.type == "action.accepted":
        evidence.append(
            {
                "player_id": None if result.context is None else result.context.player_id,
                "action": result.reply.payload.get("action"),
                "request_event_id": result.reply.payload.get("request_event_id"),
                "reply_seq": result.reply.seq,
            }
        )
    for submission in result.channel_messages:
        evidence.append(
            {
                "player_id": submission.acceptance.player_id,
                "action": submission.acceptance.action,
                "request_event_id": None,
                "reply_seq": None,
            }
        )
    return evidence


async def _server_child(state_path: Path, result_path: Path, seed: int) -> int:
    content = load_content(PROJECT_ROOT / "content")
    preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
    preset = replace(
        preset,
        rules=replace(
            preset.rules,
            day_seconds=12,
            vote_seconds=12,
            night_seconds=12,
            silence_after_dawn_seconds=0,
        ),
    )
    players = tuple(
        PlayerConfig(f"player-{index}", f"Player {index}")
        for index in range(sum(preset.role_counts.values()))
    )
    game_id = f"123e4567-e89b-12d3-a456-{seed:012d}"
    game = GameState.create_from_preset(
        content,
        preset,
        players,
        game_id=game_id,
        event_sink=InMemoryEventSink(),
        rng=Random(seed),
        started_at=monotonic_seconds(),
    )
    registry = GameRegistry({game_id: game})
    sessions = SessionManager(registry)
    accepted: list[dict[str, object]] = []
    original_handle = sessions.handle_message

    def handle_spy(message, context=None):
        result = original_handle(message, context)
        accepted.extend(_accepted_action_evidence(result))
        return result

    sessions.handle_message = handle_spy  # type: ignore[method-assign]
    server = WebSocketGameServer(registry, sessions=sessions, tick_interval_seconds=0.05)
    listener = await server.start("127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    _write_private_server_launch(
        state_path,
        uri=f"ws://127.0.0.1:{port}",
        game_id=game_id,
        entry_tokens=registry.entry_tokens_for(game_id),
        player_ids=list(game.players),
        server_pid=os.getpid(),
    )
    try:
        async def _wait_game():
            while game.game_result is None:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
        await await_with_timeout(_HARD_LIMIT_SECONDS, _wait_game)
        _write_private_json(
            result_path,
            {
                "game_end": game.game_result is not None,
                "accepted": accepted,
                "server_pid": os.getpid(),
            },
        )
        return 0
    except TimeoutError:
        _write_private_json(
            result_path,
            {"game_end": False, "accepted": accepted, "server_pid": os.getpid()},
        )
        return 2
    finally:
        await server.close()


def _bounded_tail(path: Path) -> str:
    try:
        return path.read_bytes()[-_TAIL_BYTES:].decode(errors="replace")
    except OSError:
        return ""


async def _wait_for_file(path: Path, processes: list[asyncio.subprocess.Process], timeout: float) -> None:
    async def _wait_ready():
        while not path.exists():
            if any(process.returncode is not None for process in processes):
                raise RuntimeError("child exited before readiness evidence")
            await asyncio.sleep(0.05)
    await await_with_timeout(timeout, _wait_ready)


async def _stop_owned(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5.0)
        except TimeoutError:
            process.kill()
            await process.wait()
    else:
        await process.wait()


def _validate_result(
    *,
    output_dir: Path,
    llm_player: str,
    server_result: dict[str, object],
    statuses: list[dict[str, object]],
    processes: list[asyncio.subprocess.Process],
) -> list[str]:
    errors: list[str] = []
    if not server_result.get("game_end"):
        errors.append("missing game end")
    if len(statuses) != 9 or not all(item.get("game_end") for item in statuses):
        errors.append("not all nine clients recorded game end")
    if len({item.get("pid") for item in statuses}) != 9:
        errors.append("client PID evidence is not nine distinct processes")
    llm_status = next(
        (item for item in statuses if item.get("player_id") == llm_player), None
    )
    if llm_status is None or llm_status.get("brain_mode") != "llm":
        errors.append("exactly one LLM client evidence is missing")
        return errors
    if sum(item.get("brain_mode") == "llm" for item in statuses) != 1:
        errors.append("LLM client count is not exactly one")
    snapshot = llm_status.get("llm_snapshot") or {}
    audit = llm_status.get("audit") or {}
    records = audit.get("records") or []
    if snapshot.get("decisions", 0) < 1:
        errors.append("model produced no valid decision")
    accepted = server_result.get("accepted") or []
    llm_accepted = [item for item in accepted if item.get("player_id") == llm_player]
    for required in ("chat.send", "vote.cast"):
        if not any(item.get("action") == required for item in llm_accepted):
            errors.append(f"missing accepted LLM action: {required}")
    if not any(item.get("kind") == "chat" for item in records):
        errors.append("ai.jsonl has no valid chat decision")
    if not any(item.get("kind") == "vote" for item in records):
        errors.append("ai.jsonl has no valid vote decision")
    ability_outcomes = [
        item
        for item in (llm_status.get("reservation") or {}).get("outcomes", [])
        if item.get("action") == "ability.use"
    ]
    if ability_outcomes and not all(item.get("status") == "ACCEPTED" for item in ability_outcomes):
        errors.append("offered LLM ability did not produce only accepted selection evidence")
    if any(process.returncode is None for process in processes):
        errors.append("owned child remained alive after cleanup")
    if any(process.returncode != 0 for process in processes):
        errors.append("owned child exited non-zero")
    audit_path = output_dir / "ai.jsonl"
    if not audit_path.is_file() or audit_path.stat().st_size == 0:
        errors.append("ai.jsonl is missing or empty")
    return errors


def _resolve_settings(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None = None,
) -> LocalLLMSettings:
    effective = dict(os.environ if environ is None else environ)
    if args.endpoint is not None:
        effective["AIWOLF_LLM_ENDPOINT"] = args.endpoint
    if args.model is not None:
        effective["AIWOLF_LLM_MODEL"] = args.model
    return LocalLLMSettings.from_env(effective)


async def _run_smoke(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
    process_factory=None,
) -> int:
    settings = _resolve_settings(args, environ)
    spawn = process_factory or asyncio.create_subprocess_exec
    if args.max_seconds <= 0 or args.max_seconds > _HARD_LIMIT_SECONDS:
        raise ValueError("--max-seconds must be in (0, 300]")
    deadline = asyncio.get_running_loop().time() + args.max_seconds
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(output_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    server_stdout = output_dir / "server.stdout.log"
    server_stderr = output_dir / "server.stderr.log"
    server_result_path = output_dir / "server-result.json"
    children: list[asyncio.subprocess.Process] = []
    log_paths: dict[int, tuple[Path, Path]] = {}
    with tempfile.TemporaryDirectory(prefix="aiwolf-phase4-smoke-") as temporary:
        private_root = Path(temporary)
        launch_path = private_root / "server-launch.json"
        (private_root / "clock-start").write_text("real clock\n", encoding="utf-8")
        (private_root / "day-one-release").write_text("real clock\n", encoding="utf-8")
        with server_stdout.open("wb") as stdout_file, server_stderr.open("wb") as stderr_file:
            server = await spawn(
                sys.executable,
                str(Path(__file__).resolve()),
                "--server-child",
                "--state-path",
                str(launch_path),
                "--result-path",
                str(server_result_path),
                "--seed",
                str(args.seed),
                cwd=str(PROJECT_ROOT),
                stdout=stdout_file,
                stderr=stderr_file,
            )
        children.append(server)
        log_paths[id(server)] = (server_stdout, server_stderr)
        try:
            await _wait_for_file(
                launch_path,
                children,
                min(10.0, max(0.001, deadline - asyncio.get_running_loop().time())),
            )
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
            player_ids = launch["player_ids"]
            llm_player = player_ids[0]
            for player_id in player_ids:
                stdout_path = output_dir / f"{player_id}.stdout.log"
                stderr_path = output_dir / f"{player_id}.stderr.log"
                status_path = output_dir / f"{player_id}.json"
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    child_environment = dict(os.environ if environ is None else environ)
                    child_environment["AIWOLF_ENTRY_TOKEN"] = launch["entry_tokens"][player_id]
                    process = await spawn(
                        sys.executable,
                        str(CLIENT),
                        "--uri",
                        launch["uri"],
                        "--game-id",
                        launch["game_id"],
                        "--player-id",
                        player_id,
                        "--credentials",
                        str(private_root / f"{player_id}.credentials.json"),
                        "--status",
                        str(status_path),
                        "--ready",
                        str(private_root / f"{player_id}.ready.json"),
                        "--day-one-ready",
                        str(private_root / f"{player_id}.day-ready.json"),
                        "--clock-start",
                        str(private_root / "clock-start"),
                        "--day-one-release",
                        str(private_root / "day-one-release"),
                        "--seed",
                        str(args.seed),
                        "--mode",
                        CompletionReactionMode.SPEAK.value,
                        "--llm-player",
                        llm_player,
                        "--backend",
                        "openai",
                        "--endpoint",
                        settings.endpoint,
                        "--model",
                        settings.model,
                        "--audit-log",
                        str(output_dir / "ai.jsonl"),
                        cwd=str(PROJECT_ROOT),
                        env=child_environment,
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                children.append(process)
                log_paths[id(process)] = (stdout_path, stderr_path)
            async def _wait_children():
                await asyncio.gather(*(process.wait() for process in children))
            await await_with_timeout(max(0.001, deadline - asyncio.get_running_loop().time()), _wait_children)
        except TimeoutError:
            print(f"Phase 4 smoke timed out; log={output_dir}", file=sys.stderr)
            return_code = 2
        except Exception as error:
            print(
                f"Phase 4 smoke failed before completion: {type(error).__name__}; log={output_dir}",
                file=sys.stderr,
            )
            return_code = 2
        else:
            return_code = 0
        finally:
            await asyncio.gather(*(_stop_owned(process) for process in children))

        statuses: list[dict[str, object]] = []
        for player_id in range(9):
            path = output_dir / f"player-{player_id}.json"
            if path.exists():
                try:
                    statuses.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    pass
        try:
            server_result = json.loads(server_result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            server_result = {}
        errors = _validate_result(
            output_dir=output_dir,
            llm_player="player-0",
            server_result=server_result,
            statuses=statuses,
            processes=children,
        )
        if return_code or errors:
            exits = [process.returncode for process in children]
            stderr_nonempty = sum(bool(_bounded_tail(log_paths[id(p)][1])) for p in children)
            print(
                f"Phase 4 smoke FAILED; errors={errors[:8]!r}; exits={exits}; "
                f"stderr_children={stderr_nonempty}; log={output_dir}",
                file=sys.stderr,
            )
            return 1
        print(
            f"Phase 4 smoke PASS; clients=9; llm=1; accepted_chat=1+; "
            f"accepted_vote=1+; log={output_dir}"
        )
        return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=8425)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "logs" / f"phase4-smoke-{int(time.time())}",
    )
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--state-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.server_child:
            if args.state_path is None or args.result_path is None:
                raise ValueError("server child paths are required")
            code = asyncio.run(_server_child(args.state_path, args.result_path, args.seed))
        else:
            code = asyncio.run(_run_smoke(args))
    except (TypeError, ValueError) as error:
        print(f"Phase 4 smoke configuration error: {error}", file=sys.stderr)
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
