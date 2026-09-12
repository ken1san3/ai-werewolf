"""Production Phase 5 composition for one AI client process.

The runtime owns one complete client composition.  The generation broker and
the external model service are deliberately outside this ownership boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import stat
import time
from uuid import uuid4

from .brain import (
    BrainController,
    BrainInvocationArbiter,
    BrainRunConfig,
    FeatureControllerExit,
    FeatureControllerExitReason,
)
from .llm import (
    AdmissionCredentials,
    AiAuditWriterConfig,
    BrokerAdmissionSession,
    BrokeredStructuredLLMBackend,
    GenerationBrokerConfig,
    JsonlAiAuditSink,
    LLMBrain,
    LLMBrainConfig,
    LLMClientIdentity,
    ShortChatConfig,
)
from .network import (
    ClientExit,
    ClientExitReason,
    CredentialStore,
    NetworkClient,
    NetworkClientConfig,
    ReconnectPolicy,
)
from .reaction_chat import (
    DeterministicSpeakingFrequencyPolicy,
    ReactionChatConfig,
    ReactionChatController,
    SpeakingProfile,
)
from .vote_ability import VoteAbilityConfig, VoteAbilityController
from .world import WorldState, WorldStateConfig, WorldStateExit, WorldStateExitReason


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
RequestIdFactory = Callable[[], str]


class Phase5RuntimeLifecycle(str, Enum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ENDED = "ENDED"
    FAILED = "FAILED"


class Phase5RuntimeExitReason(str, Enum):
    GAME_ENDED = "GAME_ENDED"
    STOPPED = "STOPPED"
    NETWORK_FAILED = "NETWORK_FAILED"
    WORLD_FAILED = "WORLD_FAILED"
    CONTROLLER_FAILED = "CONTROLLER_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


@dataclass(frozen=True)
class Phase5RuntimeExit:
    reason: Phase5RuntimeExitReason
    success: bool
    network: ClientExit | None
    world: WorldStateExit | None
    reaction: FeatureControllerExit | None
    vote_ability: FeatureControllerExit | None
    detail: str | None = None


def _short_llm_config() -> LLMBrainConfig:
    return LLMBrainConfig(short_chat=ShortChatConfig())


@dataclass(frozen=True, repr=False)
class Phase5ClientRuntimeConfig:
    """Complete, model-neutral construction inputs for one Phase 5 client.

    ``repr`` is intentionally redacted because the nested Network and
    admission configurations contain game-entry and admission credentials.
    """

    network: NetworkClientConfig
    player_id: str
    admission_host: str
    admission_port: int
    admission_credentials: AdmissionCredentials
    audit_path: Path
    master_seed: int
    reconnect: ReconnectPolicy = field(default_factory=ReconnectPolicy)
    world: WorldStateConfig = field(default_factory=WorldStateConfig)
    broker: GenerationBrokerConfig = field(default_factory=GenerationBrokerConfig)
    audit: AiAuditWriterConfig = field(default_factory=AiAuditWriterConfig)
    llm: LLMBrainConfig = field(default_factory=_short_llm_config)
    brain: BrainRunConfig = field(default_factory=BrainRunConfig)
    reaction: ReactionChatConfig = field(default_factory=ReactionChatConfig)
    vote_ability: VoteAbilityConfig = field(default_factory=VoteAbilityConfig)
    speaking: SpeakingProfile = field(default_factory=SpeakingProfile)

    def __post_init__(self) -> None:
        if not isinstance(self.network, NetworkClientConfig):
            raise TypeError("network must be NetworkClientConfig")
        if not isinstance(self.player_id, str) or not self.player_id:
            raise ValueError("player_id must be a non-empty string")
        if self.admission_host not in {"127.0.0.1", "::1"}:
            raise ValueError("admission_host must be numeric loopback")
        if type(self.admission_port) is not int or not 1 <= self.admission_port <= 65535:
            raise ValueError("admission_port must be an int in [1, 65535]")
        if not isinstance(self.admission_credentials, AdmissionCredentials):
            raise TypeError("admission_credentials must be AdmissionCredentials")
        if not isinstance(self.audit_path, Path):
            raise TypeError("audit_path must be pathlib.Path")
        if not self.audit_path.parent.is_dir():
            raise ValueError("audit_path parent directory must already exist")
        if isinstance(self.master_seed, bool) or not isinstance(self.master_seed, int):
            raise ValueError("master_seed must be an integer")
        for name, expected in (
            ("reconnect", ReconnectPolicy),
            ("world", WorldStateConfig),
            ("broker", GenerationBrokerConfig),
            ("audit", AiAuditWriterConfig),
            ("llm", LLMBrainConfig),
            ("brain", BrainRunConfig),
            ("reaction", ReactionChatConfig),
            ("vote_ability", VoteAbilityConfig),
            ("speaking", SpeakingProfile),
        ):
            if not isinstance(getattr(self, name), expected):
                raise TypeError(f"{name} must be {expected.__name__}")
        if self.llm.short_chat is None:
            raise ValueError("Phase 5 runtime requires the explicit short-chat profile")
        if self.speaking.cooldown_seconds < self.reaction.minimum_accepted_chat_interval_seconds:
            raise ValueError(
                "speaking cooldown must cover the reaction minimum accepted-chat interval"
            )

    def __repr__(self) -> str:
        return (
            "Phase5ClientRuntimeConfig("
            f"game_id={self.network.game_id!r}, player_id={self.player_id!r}, "
            f"admission_host={self.admission_host!r}, "
            f"admission_port={self.admission_port}, audit_path={self.audit_path!r}, "
            f"master_seed={self.master_seed})"
        )


class Phase5ClientRuntime:
    """Own and supervise one production LLM client composition.

    Construct with :meth:`connect`, then use ``start``/``wait`` or ``run``.
    ``aclose`` is idempotent and drains every client-owned task and writer.
    """

    def __init__(
        self,
        *,
        config: Phase5ClientRuntimeConfig,
        network: NetworkClient,
        world: WorldState,
        admission: BrokerAdmissionSession,
        backend: BrokeredStructuredLLMBackend,
        audit: JsonlAiAuditSink,
        brain: LLMBrain,
        brain_controller: BrainController,
        arbiter: BrainInvocationArbiter,
        reaction: ReactionChatController,
        vote_ability: VoteAbilityController,
    ) -> None:
        self.config = config
        self.network = network
        self.world = world
        self.admission = admission
        self.backend = backend
        self.audit = audit
        self.brain = brain
        self.brain_controller = brain_controller
        self.arbiter = arbiter
        self.reaction = reaction
        self.vote_ability = vote_ability

        self._lifecycle = Phase5RuntimeLifecycle.NEW
        self._exit: Phase5RuntimeExit | None = None
        self._stop_requested = False
        self._network_task: asyncio.Task[ClientExit] | None = None
        self._world_task: asyncio.Task[WorldStateExit] | None = None
        self._reaction_task: asyncio.Task[FeatureControllerExit] | None = None
        self._vote_task: asyncio.Task[FeatureControllerExit] | None = None
        self._monitor_task: asyncio.Task[Phase5RuntimeExit] | None = None
        self._cleanup_task: asyncio.Task[tuple[str, ...]] | None = None

    @property
    def lifecycle(self) -> Phase5RuntimeLifecycle:
        return self._lifecycle

    @property
    def exit(self) -> Phase5RuntimeExit | None:
        return self._exit

    @classmethod
    async def connect(
        cls,
        config: Phase5ClientRuntimeConfig,
        credential_store: CredentialStore,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        request_id_factory: RequestIdFactory | None = None,
    ) -> "Phase5ClientRuntime":
        """Authenticate admission, start the private audit, and compose one client."""

        if not isinstance(config, Phase5ClientRuntimeConfig):
            raise TypeError("config must be Phase5ClientRuntimeConfig")
        if not callable(clock) or not callable(sleep):
            raise TypeError("clock and sleep must be callable")
        if request_id_factory is not None and not callable(request_id_factory):
            raise TypeError("request_id_factory must be callable when supplied")

        session: BrokerAdmissionSession | None = None
        backend: BrokeredStructuredLLMBackend | None = None
        audit: JsonlAiAuditSink | None = None
        try:
            os.chmod(config.audit_path.parent, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            session = await BrokerAdmissionSession.connect(
                config.admission_host,
                config.admission_port,
                config.admission_credentials,
                config=config.broker,
            )
            backend = BrokeredStructuredLLMBackend(session)
            audit = JsonlAiAuditSink(config.audit_path, config=config.audit)
            await audit.start()
            os.chmod(config.audit_path, stat.S_IRUSR | stat.S_IWUSR)

            network = NetworkClient(
                config.network,
                credential_store,
                reconnect_policy=config.reconnect,
                clock=clock,
                sleep=sleep,
            )
            world = WorldState(network, config=config.world)
            brain_arguments: dict[str, object] = {
                "backend": backend,
                "audit": audit,
                "identity": LLMClientIdentity(config.network.game_id, config.player_id),
                "config": config.llm,
                "clock": clock,
            }
            if request_id_factory is not None:
                brain_arguments["request_id_factory"] = request_id_factory
            brain = LLMBrain(**brain_arguments)  # type: ignore[arg-type]
            brain_controller = BrainController(
                world=world,
                sender=network,
                brain=brain,
                config=config.brain,
                clock=clock,
            )
            arbiter = BrainInvocationArbiter(
                controller=brain_controller,
                admission=session,
                clock=clock,
            )
            frequency = DeterministicSpeakingFrequencyPolicy(
                profile=config.speaking,
                master_seed=config.master_seed,
            )
            reaction = ReactionChatController(
                world=world,
                invoker=arbiter,
                master_seed=config.master_seed,
                config=config.reaction,
                frequency_policy=frequency,
                clock=clock,
            )
            vote_ability = VoteAbilityController(
                world=world,
                invoker=arbiter,
                config=config.vote_ability,
                clock=clock,
            )
            return cls(
                config=config,
                network=network,
                world=world,
                admission=session,
                backend=backend,
                audit=audit,
                brain=brain,
                brain_controller=brain_controller,
                arbiter=arbiter,
                reaction=reaction,
                vote_ability=vote_ability,
            )
        except BaseException:
            close_errors: list[BaseException] = []
            for resource in (audit, backend if backend is not None else session):
                if resource is None:
                    continue
                try:
                    await asyncio.shield(resource.aclose())
                except BaseException as error:
                    close_errors.append(error)
            if close_errors:
                raise RuntimeError(
                    "Phase 5 runtime construction cleanup failed: "
                    + ",".join(type(error).__name__ for error in close_errors)
                )
            raise

    async def start(self) -> None:
        if self._lifecycle is not Phase5RuntimeLifecycle.NEW:
            raise RuntimeError("Phase5ClientRuntime.start() may only be called once")
        self._lifecycle = Phase5RuntimeLifecycle.RUNNING
        try:
            self._network_task = asyncio.create_task(
                self.network.run(), name=f"aiwolf-network-{self.config.player_id}"
            )
            self._world_task = asyncio.create_task(
                self.world.run(), name=f"aiwolf-world-{self.config.player_id}"
            )
            self.reaction.start()
            self.vote_ability.start()
            self._reaction_task = asyncio.create_task(
                self.reaction.wait(), name=f"aiwolf-reaction-wait-{self.config.player_id}"
            )
            self._vote_task = asyncio.create_task(
                self.vote_ability.wait(), name=f"aiwolf-vote-wait-{self.config.player_id}"
            )
            self._monitor_task = asyncio.create_task(
                self._monitor(), name=f"aiwolf-runtime-{self.config.player_id}"
            )
        except BaseException:
            self._stop_requested = True
            self._lifecycle = Phase5RuntimeLifecycle.STOPPING
            await asyncio.shield(self._ensure_cleanup())
            self._lifecycle = Phase5RuntimeLifecycle.FAILED
            raise

    async def wait(self) -> Phase5RuntimeExit:
        if self._monitor_task is None:
            raise RuntimeError("Phase5ClientRuntime.wait() requires start()")
        return await asyncio.shield(self._monitor_task)

    async def run(self) -> Phase5RuntimeExit:
        await self.start()
        try:
            return await self.wait()
        except asyncio.CancelledError:
            await self._close_after_cancellation()
            raise

    async def aclose(self) -> None:
        if self._lifecycle in {
            Phase5RuntimeLifecycle.ENDED,
            Phase5RuntimeLifecycle.FAILED,
        }:
            await self._ensure_cleanup()
            return
        self._stop_requested = True
        if self._lifecycle is Phase5RuntimeLifecycle.NEW:
            self._lifecycle = Phase5RuntimeLifecycle.STOPPING
            errors = await self._ensure_cleanup()
            reason = (
                Phase5RuntimeExitReason.CLEANUP_FAILED
                if errors
                else Phase5RuntimeExitReason.STOPPED
            )
            self._exit = Phase5RuntimeExit(
                reason=reason,
                success=not errors,
                network=None,
                world=None,
                reaction=None,
                vote_ability=None,
                detail=",".join(errors) or None,
            )
            self._lifecycle = (
                Phase5RuntimeLifecycle.FAILED
                if errors
                else Phase5RuntimeLifecycle.ENDED
            )
            return
        self._lifecycle = Phase5RuntimeLifecycle.STOPPING
        await self._ensure_cleanup()
        monitor = self._monitor_task
        if monitor is not None and monitor is not asyncio.current_task():
            await asyncio.shield(monitor)

    async def _close_after_cancellation(self) -> None:
        close_task = asyncio.create_task(self.aclose())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await close_task

    async def _monitor(self) -> Phase5RuntimeExit:
        reason = Phase5RuntimeExitReason.STOPPED
        detail: str | None = None
        network_exit: ClientExit | None = None
        world_exit: WorldStateExit | None = None
        reaction_exit: FeatureControllerExit | None = None
        vote_exit: FeatureControllerExit | None = None
        assert self._network_task is not None
        assert self._world_task is not None
        assert self._reaction_task is not None
        assert self._vote_task is not None
        watched: set[asyncio.Task[object]] = {
            self._network_task,  # type: ignore[arg-type]
            self._world_task,  # type: ignore[arg-type]
            self._reaction_task,  # type: ignore[arg-type]
            self._vote_task,  # type: ignore[arg-type]
        }
        try:
            while watched:
                done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                watched.difference_update(done)
                for task in done:
                    if task.cancelled():
                        if not self._stop_requested:
                            reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                            detail = "owned task cancelled unexpectedly"
                            watched.clear()
                            break
                        continue
                    error = task.exception()
                    if error is not None:
                        reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                        detail = type(error).__name__
                        watched.clear()
                        break
                    value = task.result()
                    if task is self._network_task:
                        network_exit = value  # type: ignore[assignment]
                        assert isinstance(network_exit, ClientExit)
                        if network_exit.reason is ClientExitReason.GAME_ENDED:
                            reason = Phase5RuntimeExitReason.GAME_ENDED
                            try:
                                world_exit, reaction_exit, vote_exit = await asyncio.wait_for(
                                    asyncio.gather(
                                        asyncio.shield(self._world_task),
                                        asyncio.shield(self._reaction_task),
                                        asyncio.shield(self._vote_task),
                                    ),
                                    self.config.network.shutdown_timeout_seconds,
                                )
                            except TimeoutError:
                                reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                                detail = "natural game-end drain timed out"
                            except BaseException as error:
                                reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                                detail = type(error).__name__
                            else:
                                if world_exit.reason is not WorldStateExitReason.CLIENT_ENDED:
                                    reason = Phase5RuntimeExitReason.WORLD_FAILED
                                    detail = world_exit.reason.value
                                elif any(
                                    item.reason is not FeatureControllerExitReason.WORLD_ENDED
                                    for item in (reaction_exit, vote_exit)
                                ):
                                    reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                                    detail = "missing natural WORLD_ENDED"
                            watched.clear()
                        elif self._stop_requested and network_exit.reason is ClientExitReason.STOPPED:
                            reason = Phase5RuntimeExitReason.STOPPED
                            watched.clear()
                        else:
                            reason = Phase5RuntimeExitReason.NETWORK_FAILED
                            detail = network_exit.reason.value
                            watched.clear()
                        break
                    if task is self._world_task:
                        world_exit = value  # type: ignore[assignment]
                        assert isinstance(world_exit, WorldStateExit)
                        if not world_exit.success:
                            reason = Phase5RuntimeExitReason.WORLD_FAILED
                            detail = world_exit.reason.value
                            watched.clear()
                            break
                    else:
                        controller_exit = value
                        assert isinstance(controller_exit, FeatureControllerExit)
                        if task is self._reaction_task:
                            reaction_exit = controller_exit
                        else:
                            vote_exit = controller_exit
                        if controller_exit.reason in {
                            FeatureControllerExitReason.FAILED,
                            FeatureControllerExitReason.WORLD_FAILED,
                        } or (
                            controller_exit.reason is FeatureControllerExitReason.STOP_REQUESTED
                            and not self._stop_requested
                        ):
                            reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                            detail = (
                                controller_exit.error_type
                                or controller_exit.reason.value
                            )
                            watched.clear()
                            break
        except asyncio.CancelledError:
            self._stop_requested = True
            reason = Phase5RuntimeExitReason.STOPPED
        finally:
            self._lifecycle = Phase5RuntimeLifecycle.STOPPING
            cleanup_errors = await asyncio.shield(self._ensure_cleanup())

        network_exit = network_exit or self._completed(self._network_task, ClientExit)
        world_exit = world_exit or self._completed(self._world_task, WorldStateExit)
        reaction_exit = reaction_exit or self._completed(
            self._reaction_task, FeatureControllerExit
        )
        vote_exit = vote_exit or self._completed(self._vote_task, FeatureControllerExit)
        if cleanup_errors:
            reason = Phase5RuntimeExitReason.CLEANUP_FAILED
            detail = ",".join(cleanup_errors)
        success = reason in {
            Phase5RuntimeExitReason.GAME_ENDED,
            Phase5RuntimeExitReason.STOPPED,
        }
        self._exit = Phase5RuntimeExit(
            reason=reason,
            success=success,
            network=network_exit,
            world=world_exit,
            reaction=reaction_exit,
            vote_ability=vote_exit,
            detail=detail,
        )
        self._lifecycle = (
            Phase5RuntimeLifecycle.ENDED if success else Phase5RuntimeLifecycle.FAILED
        )
        return self._exit

    async def _ensure_cleanup(self) -> tuple[str, ...]:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(
                self._cleanup_owned(), name=f"aiwolf-runtime-cleanup-{self.config.player_id}"
            )
        return await asyncio.shield(self._cleanup_task)

    async def _cleanup_owned(self) -> tuple[str, ...]:
        errors: list[str] = []

        async def close(name: str, awaitable: Awaitable[object]) -> None:
            try:
                await awaitable
            except BaseException as error:
                errors.append(f"{name}:{type(error).__name__}")

        await close("vote_ability", self.vote_ability.stop())
        await close("reaction", self.reaction.stop())
        await close("arbiter", self.arbiter.stop())
        await close("audit", self.audit.aclose())
        await close("admission", self.backend.aclose())
        await close("world", self.world.stop())
        await close("network", self.network.stop())

        owned_tasks = tuple(
            task
            for task in (
                self._network_task,
                self._world_task,
                self._reaction_task,
                self._vote_task,
            )
            if task is not None
        )
        if owned_tasks:
            results = await asyncio.gather(*owned_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    errors.append(f"task:{type(result).__name__}")
        return tuple(errors)

    @staticmethod
    def _completed(task: asyncio.Task[object] | None, expected: type):
        if task is None or not task.done() or task.cancelled():
            return None
        try:
            value = task.result()
        except BaseException:
            return None
        return value if isinstance(value, expected) else None


def phase5_request_id_factory(player_id: str) -> RequestIdFactory:
    """Return a collision-resistant request-ID source scoped by opaque player ID."""

    if not isinstance(player_id, str) or not player_id:
        raise ValueError("player_id must be a non-empty string")
    return lambda: f"phase5-{player_id}-{uuid4()}"
