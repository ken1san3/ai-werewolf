"""One production-composition Phase 5 client subprocess."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client import (
    AdmissionCredentials,
    GenerationBrokerConfig,
    LLMBrainConfig,
    Phase5ClientRuntime,
    Phase5ClientRuntimeConfig,
    Phase5RuntimeExitReason,
    ShortChatConfig,
    SpeakingProfile,
)
from ai_client.brain import BrainRunConfig
from ai_client.network import NetworkClientConfig, ReconnectPolicy, SessionCheckpoint
from ai_client.reaction_chat import ReactionChatConfig, ReactionChatLifecycle
from ai_client.vote_ability import VoteAbilityConfig, VoteAbilityLifecycle
from ai_client.world import Freshness


from tests.fixtures.completion_clock import CompletionClock as _BarrierClock


class _DisposableCredentialStore:
    """Process-private CredentialStore for the disposable completion client."""

    def __init__(self) -> None:
        self.checkpoint: SessionCheckpoint | None = None
        self.save_count = 0

    async def load(self) -> SessionCheckpoint | None:
        return self.checkpoint

    async def save(self, checkpoint: SessionCheckpoint) -> None:
        if not isinstance(checkpoint, SessionCheckpoint):
            raise TypeError("checkpoint must be a SessionCheckpoint")
        self.checkpoint = checkpoint
        self.save_count += 1


def _private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _reaction_evidence(snapshot) -> dict[str, object]:
    frequency = snapshot.frequency_state
    return {
        "lifecycle": snapshot.lifecycle.value,
        "chat_brain_invocations": snapshot.chat_brain_invocations,
        "send_count": snapshot.send_count,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "intentional_silence_count": snapshot.intentional_silence_count,
        "frequency_suppressed_count": sum(
            item.status.value == "frequency_suppressed" for item in snapshot.outcomes
        ),
        "frequency": None
        if frequency is None
        else {
            "phase_key": None
            if frequency.phase_key is None
            else asdict(frequency.phase_key),
            "evaluation_count": frequency.evaluation_count,
            "committed_brain_invocations": frequency.committed_brain_invocations,
            "last_accepted_chat_at": frequency.last_accepted_chat_at,
            "recent_source_fingerprints": list(frequency.recent_source_fingerprints),
        },
        "outcomes": [
            {
                "day": item.trigger.phase_key.day,
                "phase": item.trigger.phase_key.phase,
                "trigger": item.trigger.kind.value,
                "status": item.status.value,
                "suppression": (
                    None if item.suppression is None else item.suppression.value
                ),
                "frequency_evaluation_ordinal": item.frequency_evaluation_ordinal,
                "source_fingerprint": item.source_fingerprint,
            }
            for item in snapshot.outcomes
        ],
    }


def _reservation_evidence(snapshot) -> dict[str, object]:
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
                "ability_id": item.ability_id,
                "vote_target_player_id": item.vote_target_player_id,
                "ability_target_player_ids": list(item.ability_target_player_ids),
                "request_event_id": item.request_event_id,
                "send_connection_generation": item.send_connection_generation,
                "observation_connection_generation": item.observation_connection_generation,
                "status": item.status.value,
                "attempts": item.attempts,
            }
            for item in snapshot.outcomes
        ],
    }


def _audit_summary(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    records = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
    return {
        "path": str(Path(path.parent.name) / path.name),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(records),
        "request_ids": [item["request_id"] for item in records],
        "player_ids": sorted({item["player_id"] for item in records}),
        "decisions": [
            {
                "request_id": item["request_id"],
                "kind": None if item["decision"] is None else item["decision"]["kind"],
                "option_id": None
                if item["decision"] is None
                else item["decision"]["option_id"],
                "text": None if item["decision"] is None else item["decision"]["text"],
                "vote_target_player_id": None
                if item["decision"] is None
                else item["decision"]["vote_target_player_id"],
                "ability_target_player_ids": []
                if item["decision"] is None
                else item["decision"]["ability_target_player_ids"],
            }
            for item in records
        ],
    }


async def run_client(arguments: argparse.Namespace, bootstrap: dict[str, object]) -> int:
    clock = _BarrierClock(arguments.clock_start, arguments.day_one_release)
    player_id = str(bootstrap["player_id"])
    request_ordinal = 0

    def next_request_id() -> str:
        nonlocal request_ordinal
        request_ordinal += 1
        return f"phase5-{player_id}-{request_ordinal}"

    config = Phase5ClientRuntimeConfig(
        network=NetworkClientConfig(
            str(bootstrap["uri"]),
            str(bootstrap["game_id"]),
            str(bootstrap["entry_token"]),
            shutdown_timeout_seconds=10.0,
        ),
        player_id=player_id,
        admission_host=str(bootstrap["admission_host"]),
        admission_port=int(bootstrap["admission_port"]),
        admission_credentials=AdmissionCredentials(
            str(bootstrap["admission_client_id"]),
            str(bootstrap["admission_token"]),
        ),
        audit_path=arguments.audit,
        master_seed=arguments.seed,
        reconnect=ReconnectPolicy(max_disconnected_seconds=10.0),
        broker=GenerationBrokerConfig(**bootstrap["broker_config"]),
        llm=LLMBrainConfig(short_chat=ShortChatConfig()),
        brain=BrainRunConfig(max_decision_seconds=1.0),
        reaction=ReactionChatConfig(
            max_chat_attempts_per_phase=2,
            brain_timeout_seconds=1.0,
        ),
        vote_ability=VoteAbilityConfig(brain_timeout_seconds=1.0),
        speaking=SpeakingProfile(
            talkativeness=1.0,
            ordinary_event_importance=1.0,
            direct_mention_importance=1.0,
            initial_event_importance=1.0,
            cooldown_seconds=0.20,
            max_trigger_evaluations_per_phase=32,
            repetition_window=8,
        ),
    )
    credential_store = _DisposableCredentialStore()
    runtime = await Phase5ClientRuntime.connect(
        config,
        credential_store,
        clock=clock,
        sleep=clock.sleep,
        request_id_factory=next_request_id,
    )

    async def marker(path: Path, day: int, phase: str) -> None:
        while True:
            world = runtime.world.snapshot()
            deadline = runtime.world.transport_observations().current_deadline
            reaction = runtime.reaction.snapshot()
            reservation = runtime.vote_ability.snapshot()
            if (
                world.freshness is Freshness.CURRENT
                and world.is_caught_up
                and world.phase is not None
                and (world.phase.day, world.phase.phase) == (day, phase)
                and deadline is not None
                and (deadline.day, deadline.phase) == (day, phase)
                and reaction.lifecycle is ReactionChatLifecycle.RUNNING
                and reservation.lifecycle is VoteAbilityLifecycle.RUNNING
                and reaction.transport_cursor >= deadline.mapping_order
                and reservation.transport_cursor >= deadline.mapping_order
            ):
                _private_json(
                    path,
                    {
                        "pid": os.getpid(),
                        "player_id": player_id,
                        "day": day,
                        "phase": phase,
                        "brain_mode": "llm",
                        "runtime_lifecycle": runtime.lifecycle.value,
                    },
                )
                return
            await asyncio.sleep(0.01)

    await runtime.start()
    marker_tasks = (
        asyncio.create_task(marker(arguments.ready, 0, "night0")),
        asyncio.create_task(marker(arguments.day_one_ready, 1, "day")),
    )
    async def wait_for_stop() -> None:
        while not arguments.stop.exists():
            await asyncio.sleep(0.02)

    runtime_wait = asyncio.create_task(runtime.wait())
    stop_wait = asyncio.create_task(wait_for_stop())
    exit_value = None
    try:
        done, _ = await asyncio.wait(
            {runtime_wait, stop_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_wait in done and not runtime_wait.done():
            await runtime.aclose()
        exit_value = await runtime_wait
    finally:
        await runtime.aclose()
        if not stop_wait.done():
            stop_wait.cancel()
        await asyncio.gather(stop_wait, return_exceptions=True)
        for task in marker_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*marker_tasks, return_exceptions=True)

    reaction = runtime.reaction.snapshot()
    reservation = runtime.vote_ability.snapshot()
    brain = runtime.brain.snapshot()
    audit = _audit_summary(arguments.audit)
    owned_alive = sorted(
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith("aiwolf-")
    )
    status = {
        "pid": os.getpid(),
        "player_id": player_id,
        "brain_mode": "llm",
        "game_end": exit_value.reason is Phase5RuntimeExitReason.GAME_ENDED,
        "runtime_exit": exit_value.reason.value,
        "runtime_success": exit_value.success,
        "runtime_lifecycle": runtime.lifecycle.value,
        "network_exit": None
        if exit_value.network is None
        else exit_value.network.reason.value,
        "world_exit": None if exit_value.world is None else exit_value.world.reason.value,
        "reaction_exit": None
        if exit_value.reaction is None
        else exit_value.reaction.reason.value,
        "vote_ability_exit": None
        if exit_value.vote_ability is None
        else exit_value.vote_ability.reason.value,
        "brain": {
            key: value.value if hasattr(value, "value") else value
            for key, value in asdict(brain).items()
        },
        "reaction": _reaction_evidence(reaction),
        "reservation": _reservation_evidence(reservation),
        "audit": audit,
        "owned_task_names_alive": owned_alive,
        "model_pid": None,
        "credential_store": {
            "kind": "disposable_process_private",
            "save_count": credential_store.save_count,
            "has_checkpoint": credential_store.checkpoint is not None,
            "last_seq": None
            if credential_store.checkpoint is None
            else credential_store.checkpoint.last_seq,
            "protocol_version": None
            if credential_store.checkpoint is None
            else credential_store.checkpoint.protocol_version,
        },
        "composition": {
            "world_count": 1,
            "llm_brain_count": 1,
            "brain_controller_count": 1,
            "arbiter_count": 1,
            "reaction_count": 1,
            "vote_ability_count": 1,
            "shared_arbiter": (
                runtime.reaction.invoker is runtime.arbiter
                and runtime.vote_ability.invoker is runtime.arbiter
            ),
            "admission_shared": runtime.arbiter._admission is runtime.admission,
            "backend_type": type(runtime.backend).__name__,
            "brain_type": type(runtime.brain).__name__,
            "frequency_policy_type": type(runtime.reaction.frequency_policy).__name__,
            "speaking_profile": asdict(config.speaking),
            "short_chat": asdict(config.llm.short_chat),
        },
        "production_import_guard": not any(
            name == "server.aiwolf_core"
            or name.startswith("server.aiwolf_core.")
            or name == "server.network"
            or name.startswith("server.network.")
            for name in sys.modules
        ),
    }
    _private_json(arguments.status, status)
    return 0 if exit_value.success and not owned_alive else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--day-one-ready", type=Path, required=True)
    parser.add_argument("--clock-start", type=Path, required=True)
    parser.add_argument("--day-one-release", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    raw_bootstrap = sys.stdin.readline()
    if not raw_bootstrap:
        parser.error("private bootstrap pipe is required")
    bootstrap = json.loads(raw_bootstrap)
    try:
        code = asyncio.run(run_client(arguments, bootstrap))
    except BaseException as error:
        _private_json(
            arguments.status,
            {
                "pid": os.getpid(),
                "startup_failure": type(error).__name__,
                "runtime_success": False,
                "model_pid": None,
            },
        )
        sys.stderr.write(f"phase5 client failed: {type(error).__name__}\n")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
