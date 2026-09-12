"""One-LLM/eight-rule-based Phase 4 client process composition."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_client.brain import (
    BrainController,
    BrainInvocationArbiter,
    BrainRunConfig,
    FeatureControllerExitReason,
)
from ai_client.llm import (
    JsonlAiAuditSink,
    LLMBrain,
    LLMClientIdentity,
    LocalLLMSettings,
    OpenAICompatibleBackend,
)
from ai_client.network import (
    ClientExitReason,
    FileCredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
)
from ai_client.reaction_chat import (
    ReactionChatConfig,
    ReactionChatController,
    ReactionChatLifecycle,
)
from ai_client.vote_ability import (
    VoteAbilityConfig,
    VoteAbilityController,
    VoteAbilityLifecycle,
)
from ai_client.world import ActionRejectionObservation, Freshness, WorldState
from tests.fixtures.phase3_4_reaction_brain import CompletionReactionMode
from tests.fixtures.phase3_5_brain import CompletionPhase35Brain
from tests.fixtures.phase4_fake_backend import Phase4FakeBackend


class _BarrierClock:
    """Mirror the existing Phase 3.5 completion barriers."""

    def __init__(self, clock_start_path: Path, day_one_release_path: Path) -> None:
        self._clock_start_path = clock_start_path
        self._day_one_release_path = day_one_release_path
        self._origin = time.monotonic()
        self._started_at: float | None = None
        self._day_one_released_at: float | None = None

    def __call__(self) -> float:
        now = time.monotonic()
        if not self._clock_start_path.exists():
            return self._origin
        if self._started_at is None:
            self._started_at = now
        if not self._day_one_release_path.exists():
            return self._origin + min(now - self._started_at, 1.0)
        if self._day_one_released_at is None:
            self._day_one_released_at = now
        return self._origin + 1.0 + (now - self._day_one_released_at)

    async def sleep(self, delay: float) -> None:
        deadline = self() + delay
        while self() < deadline:
            await asyncio.sleep(min(0.02, deadline - self()))


def _reaction_evidence(snapshot, observations) -> dict[str, object]:
    return {
        "lifecycle": snapshot.lifecycle.value,
        "chat_brain_invocations": snapshot.chat_brain_invocations,
        "send_count": snapshot.send_count,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "intentional_silence_count": snapshot.intentional_silence_count,
        "rejections": [
            {
                "action": item.action,
                "reason": item.reason,
                "seq": item.seq,
                "connection_generation": item.connection_generation,
            }
            for item in observations
            if isinstance(item, ActionRejectionObservation)
            and item.action not in {"vote.cast", "ability.use"}
        ],
        "outcomes": [
            {
                "trigger": item.trigger.kind.value,
                "day": item.trigger.phase_key.day,
                "phase": item.trigger.phase_key.phase,
                "status": item.status.value,
            }
            for item in snapshot.outcomes
        ],
    }


def _reservation_evidence(snapshot, observations) -> dict[str, object]:
    return {
        "lifecycle": snapshot.lifecycle.value,
        "accepted_count": snapshot.accepted_count,
        "rejected_count": snapshot.rejected_count,
        "unknown_count": snapshot.unknown_count,
        "deadline_suppressed_count": snapshot.deadline_suppressed_count,
        "unresolved": snapshot.unresolved_reservation is not None,
        "rejections": [
            {
                "action": item.action,
                "reason": item.reason,
                "seq": item.seq,
                "connection_generation": item.connection_generation,
                "request_event_id": item.request_event_id,
            }
            for item in observations
            if isinstance(item, ActionRejectionObservation)
            and item.action in {"vote.cast", "ability.use"}
        ],
        "outcomes": [
            {
                "day": item.opportunity_key.reservation.day,
                "phase": item.opportunity_key.reservation.phase,
                "family": item.opportunity_key.reservation.family,
                "action": item.action,
                "ability_id": item.ability_id,
                "vote_target_player_id": item.vote_target_player_id,
                "ability_target_player_ids": list(item.ability_target_player_ids),
                "request_event_id": item.request_event_id,
                "send_connection_generation": item.send_connection_generation,
                "observation_connection_generation": item.observation_connection_generation,
                "status": item.status.value,
                "rejection_reason": item.rejection_reason,
                "attempts": item.attempts,
            }
            for item in snapshot.outcomes
        ],
    }


def _deadline_evidence(deadline) -> dict[str, object] | None:
    if deadline is None:
        return None
    return {
        "mapping_order": deadline.mapping_order,
        "phase": deadline.phase,
        "day": deadline.day,
        "connection_generation": deadline.connection_generation,
        "action_generation": deadline.action_generation,
        "local_deadline_monotonic": deadline.local_deadline_monotonic,
    }


def _audit_summary(path: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            decision = value.get("decision") or {}
            records.append(
                {
                    "request_id": value.get("request_id"),
                    "phase": value.get("phase"),
                    "day": value.get("day"),
                    "status": value.get("status"),
                    "attempt_ordinal": value.get("attempt_ordinal"),
                    "kind": decision.get("kind"),
                    "option_id": decision.get("option_id"),
                    "vote_target_player_id": decision.get("vote_target_player_id"),
                    "ability_id": decision.get("ability_id"),
                    "ability_target_player_ids": decision.get(
                        "ability_target_player_ids", []
                    ),
                    "prompt_bytes": value.get("prompt_bytes"),
                    "response_bytes": value.get("response_bytes"),
                }
            )
    return {"record_count": len(records), "records": records[-32:]}


async def _close_shielded(resource: object) -> None:
    close_task = asyncio.create_task(resource.aclose())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        await close_task
        raise


async def _start_audit_or_close_resources(audit: object, backend: object) -> None:
    """Start audit before clients, closing both owned resources on failure."""

    try:
        await audit.start()
    except BaseException:
        for resource in (audit, backend):
            try:
                await resource.aclose()
            except BaseException:
                pass
        raise


async def run_driver(
    uri: str,
    game_id: str,
    player_id: str,
    entry_token: str,
    credentials_path: Path,
    status_path: Path,
    seed: int,
    mode: CompletionReactionMode,
    ready_path: Path,
    day_one_ready_path: Path,
    clock_start_path: Path,
    day_one_release_path: Path,
    *,
    llm_player_id: str,
    backend_mode: str,
    endpoint: str | None,
    model: str | None,
    audit_path: Path,
    evidence_dir: Path | None,
) -> int:
    shared_clock = _BarrierClock(clock_start_path, day_one_release_path)
    client = NetworkClient(
        NetworkClientConfig(uri, game_id, entry_token),
        FileCredentialStore(credentials_path),
        reconnect_policy=ReconnectPolicy(max_disconnected_seconds=10.0),
        clock=shared_clock,
        sleep=shared_clock.sleep,
    )
    world = WorldState(client)
    is_llm = player_id == llm_player_id
    backend = None
    audit = None
    request_ordinal = 0

    if is_llm:
        if backend_mode == "fake":
            backend = Phase4FakeBackend()

            def next_request_id() -> str:
                nonlocal request_ordinal
                request_ordinal += 1
                return f"phase4-{player_id}-{request_ordinal}"

            brain_run_config = BrainRunConfig(max_decision_seconds=0.25)
            reaction_config = ReactionChatConfig()
            reservation_config = VoteAbilityConfig()
        else:
            settings = LocalLLMSettings(
                endpoint=endpoint or "",
                model=model or "",
                api_key=os.environ.get("AIWOLF_LLM_API_KEY"),
            )
            backend = OpenAICompatibleBackend(settings.backend_config())
            next_request_id = None
            brain_run_config = BrainRunConfig(
                max_decision_seconds=5.0,
                cancellation_grace_seconds=0.25,
            )
            reaction_config = ReactionChatConfig(
                brain_timeout_seconds=4.0,
                deadline_guard_seconds=1.0,
                minimum_start_budget_seconds=0.10,
            )
            reservation_config = VoteAbilityConfig(
                brain_timeout_seconds=4.0,
                deadline_guard_seconds=1.0,
                minimum_start_budget_seconds=0.10,
            )
        audit = JsonlAiAuditSink(audit_path)
        await _start_audit_or_close_resources(audit, backend)
        if backend_mode == "fake":
            brain = LLMBrain(
                backend=backend,
                audit=audit,
                identity=LLMClientIdentity(game_id, player_id),
                request_id_factory=next_request_id,
                clock=shared_clock,
            )
        else:
            brain = LLMBrain(
                backend=backend,
                audit=audit,
                identity=LLMClientIdentity(game_id, player_id),
                config=settings.brain,
                clock=shared_clock,
            )
    else:
        brain = CompletionPhase35Brain(player_id=player_id, seed=seed, mode=mode)
        brain_run_config = BrainRunConfig(max_decision_seconds=0.25)
        reaction_config = ReactionChatConfig()
        reservation_config = VoteAbilityConfig()

    brain_controller = BrainController(
        world=world,
        sender=client,
        brain=brain,
        config=brain_run_config,
        clock=shared_clock,
    )
    arbiter = BrainInvocationArbiter(controller=brain_controller, clock=shared_clock)
    reaction = ReactionChatController(
        world=world,
        invoker=arbiter,
        master_seed=seed,
        config=reaction_config,
        clock=shared_clock,
    )
    reservation = VoteAbilityController(
        world=world,
        invoker=arbiter,
        config=reservation_config,
        clock=shared_clock,
    )

    async def write_marker(path: Path, *, day: int, phase: str) -> None:
        while True:
            snapshot = world.snapshot()
            transport = world.transport_observations()
            deadline = transport.current_deadline
            reaction_snapshot = reaction.snapshot()
            reservation_snapshot = reservation.snapshot()
            if (
                snapshot.freshness is Freshness.CURRENT
                and snapshot.is_caught_up
                and snapshot.phase is not None
                and (snapshot.phase.day, snapshot.phase.phase) == (day, phase)
                and deadline is not None
                and (deadline.day, deadline.phase) == (day, phase)
                and reaction_snapshot.lifecycle is ReactionChatLifecycle.RUNNING
                and reservation_snapshot.lifecycle is VoteAbilityLifecycle.RUNNING
                and reaction_snapshot.transport_cursor >= deadline.mapping_order
                and reservation_snapshot.transport_cursor >= deadline.mapping_order
            ):
                path.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "player_id": player_id,
                            "brain_mode": "llm" if is_llm else "rule_based",
                            "day": day,
                            "phase": phase,
                            "world_version": snapshot.version,
                            "mapping_order": deadline.mapping_order,
                            "deadline_mapping": _deadline_evidence(deadline),
                            "reaction": _reaction_evidence(
                                reaction_snapshot, transport.observations
                            ),
                            "reservation": _reservation_evidence(
                                reservation_snapshot, transport.observations
                            ),
                        }
                    ),
                    encoding="utf-8",
                )
                return
            await asyncio.sleep(0.01)

    client_task = asyncio.create_task(client.run())
    world_task = asyncio.create_task(world.run())
    reaction.start()
    reservation.start()
    reaction_wait = asyncio.create_task(reaction.wait())
    reservation_wait = asyncio.create_task(reservation.wait())
    marker_tasks = (
        asyncio.create_task(write_marker(ready_path, day=0, phase="night0")),
        asyncio.create_task(write_marker(day_one_ready_path, day=1, phase="day")),
    )
    result: dict[str, object] | None = None
    try:
        watched = {client_task, world_task, reaction_wait, reservation_wait}
        while not client_task.done():
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            for feature_name, feature_task in (
                ("reaction", reaction_wait),
                ("reservation", reservation_wait),
            ):
                if feature_task in done:
                    exit_value = feature_task.result()
                    if exit_value.reason in {
                        FeatureControllerExitReason.FAILED,
                        FeatureControllerExitReason.WORLD_FAILED,
                    }:
                        raise RuntimeError(f"{feature_name} controller failed: {exit_value}")
            if world_task in done and not world_task.result().success:
                raise RuntimeError(f"World failed: {world_task.result()}")
            if not client_task.done():
                await asyncio.sleep(0)
        client_exit = client_task.result()
        world_exit = await asyncio.wait_for(world_task, 10.0)
        reaction_exit, reservation_exit = await asyncio.wait_for(
            asyncio.gather(reaction_wait, reservation_wait), 10.0
        )
        if reaction_exit.reason in {
            FeatureControllerExitReason.FAILED,
            FeatureControllerExitReason.WORLD_FAILED,
        } or reservation_exit.reason in {
            FeatureControllerExitReason.FAILED,
            FeatureControllerExitReason.WORLD_FAILED,
        }:
            raise RuntimeError(
                f"feature controller failed: {reaction_exit!r}, {reservation_exit!r}"
            )
        transport = world.transport_observations()
        llm_snapshot = brain.snapshot() if is_llm else None
        result = {
            "pid": os.getpid(),
            "player_id": player_id,
            "mode": mode.value,
            "brain_mode": "llm" if is_llm else "rule_based",
            "backend_mode": backend_mode if is_llm else None,
            "game_end": world.snapshot().freshness is Freshness.ENDED
            and client_exit.reason is ClientExitReason.GAME_ENDED,
            "freshness": world.snapshot().freshness.value,
            "client_exit": client_exit.reason.value,
            "world_exit": world_exit.reason.value,
            "brain_call_count": (
                llm_snapshot.calls if is_llm else brain.call_count
            ),
            "brain_decisions": (
                llm_snapshot.decisions if is_llm else brain.decisions
            ),
            "llm_snapshot": None
            if llm_snapshot is None
            else {
                key: (value.value if hasattr(value, "value") else value)
                for key, value in asdict(llm_snapshot).items()
            },
            "backend": None
            if not isinstance(backend, Phase4FakeBackend)
            else {
                "call_count": len(backend.calls),
                "max_active": backend.max_active,
                "calls": backend.calls[-32:],
            },
            "reaction": _reaction_evidence(reaction.snapshot(), transport.observations),
            "reservation": _reservation_evidence(
                reservation.snapshot(), transport.observations
            ),
            "deadline_mapping": _deadline_evidence(transport.current_deadline),
            "production_import_guard": not any(
                name == "server.aiwolf_core"
                or name.startswith("server.aiwolf_core.")
                or name == "server.network"
                or name.startswith("server.network.")
                for name in sys.modules
            ),
        }
        return_code = 0 if client_exit.reason is ClientExitReason.GAME_ENDED else 1
    finally:
        cleanup_errors: list[BaseException] = []

        async def cleanup(awaitable) -> None:
            try:
                await awaitable
            except BaseException as error:
                cleanup_errors.append(error)

        await cleanup(reservation.stop())
        await cleanup(reaction.stop())
        await cleanup(arbiter.stop())
        if audit is not None:
            await cleanup(_close_shielded(audit))
        if backend is not None:
            await cleanup(backend.aclose())
        await cleanup(world.stop())
        await cleanup(client.stop())
        for task in (*marker_tasks, reaction_wait, reservation_wait, world_task, client_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            *marker_tasks,
            reaction_wait,
            reservation_wait,
            world_task,
            client_task,
            return_exceptions=True,
        )
        if cleanup_errors:
            raise RuntimeError(
                "phase4 client cleanup failed: "
                + ",".join(type(error).__name__ for error in cleanup_errors)
            )

    assert result is not None
    if is_llm:
        result["audit"] = _audit_summary(audit_path)
        result["audit_path"] = str(audit_path)
        if audit_path.exists():
            os.chmod(audit_path, stat.S_IRUSR | stat.S_IWUSR)
    encoded = json.dumps(result, ensure_ascii=False)
    status_path.write_text(encoded, encoding="utf-8")
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / f"{player_id}.json").write_text(encoded, encoding="utf-8")
    return return_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--entry-token")
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--mode", required=True, choices=[item.value for item in CompletionReactionMode])
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--day-one-ready", required=True, type=Path)
    parser.add_argument("--clock-start", required=True, type=Path)
    parser.add_argument("--day-one-release", required=True, type=Path)
    parser.add_argument("--llm-player", default=os.environ.get("AIWOLF_PHASE4_LLM_PLAYER", "player-0"))
    parser.add_argument("--backend", choices=("fake", "openai"), default=os.environ.get("AIWOLF_PHASE4_BACKEND", "fake"))
    parser.add_argument("--endpoint", default=os.environ.get("AIWOLF_LLM_ENDPOINT"))
    parser.add_argument("--model", default=os.environ.get("AIWOLF_LLM_MODEL"))
    parser.add_argument("--audit-log", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    entry_token = args.entry_token or os.environ.get("AIWOLF_ENTRY_TOKEN")
    if not entry_token:
        parser.error("--entry-token or AIWOLF_ENTRY_TOKEN is required")
    evidence_dir = args.evidence_dir
    if evidence_dir is None and os.environ.get("AIWOLF_PHASE4_EVIDENCE_DIR"):
        evidence_dir = Path(os.environ["AIWOLF_PHASE4_EVIDENCE_DIR"])
    audit_path = args.audit_log or (
        (evidence_dir or args.status.parent) / f"{args.player_id}.ai.jsonl"
    )
    raise SystemExit(
        asyncio.run(
            run_driver(
                args.uri,
                args.game_id,
                args.player_id,
                entry_token,
                args.credentials,
                args.status,
                args.seed,
                CompletionReactionMode(args.mode),
                args.ready,
                args.day_one_ready,
                args.clock_start,
                args.day_one_release,
                llm_player_id=args.llm_player,
                backend_mode=args.backend,
                endpoint=args.endpoint,
                model=args.model,
                audit_path=audit_path,
                evidence_dir=evidence_dir,
            )
        )
    )


if __name__ == "__main__":
    main()
