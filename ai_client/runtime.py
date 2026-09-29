"""Production Phase 5 composition for one AI client process.

The runtime owns one complete client composition.  The generation broker and
the external model service are deliberately outside this ownership boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
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
from .discussion import (
    AuthorizedDiscussionContext,
    BoundDiscussionContext,
    DiscussionContextError,
    DiscussionStateStore,
    PendingDiscussionContext,
    canonical_sha256,
    validate_bound_context_snapshot,
)
from .discussion.context import _revalidate_authority_pending_v2
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
    ClientEvent,
    ClientExit,
    ClientExitReason,
    ClientSnapshot,
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
from .world import (
    Freshness,
    WorldState,
    WorldStateConfig,
    WorldStateExit,
    WorldStateExitReason,
)
from .world.inbound_authority_v2 import _create_runtime as _create_inbound_authority_runtime


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
RequestIdFactory = Callable[[], str]


class _Phase6NetworkEventSource:
    """Serialize Phase 6 context checks with World commits without buffering.

    ``WorldState`` synchronously consumes a yielded event before requesting the
    next one.  That request is the only public seam at which the exact committed
    snapshot can be inspected before another queued Network event is pulled.
    """

    def __init__(
        self,
        network: NetworkClient,
        pending: PendingDiscussionContext,
        *,
        enable_inbound_authority_v2: bool = False,
    ) -> None:
        self._network = network
        acquire_authority = (getattr(network, "acquire_inbound_authority_capability", None)
                             if enable_inbound_authority_v2 else None)
        self._authority_capability = None if acquire_authority is None else acquire_authority()
        self._pending: PendingDiscussionContext | None = pending
        self._world: WorldState | None = None
        self._iteration_started = False
        self._retired = False
        self._last_observed_version = 0
        self._bound: BoundDiscussionContext | None = None
        self._context_owner_receipt_v2 = None
        self._authority_owner_registration_v2 = None
        loop = asyncio.get_running_loop()
        self._bound_outcome: asyncio.Future[BoundDiscussionContext] = (
            loop.create_future()
        )
        self._context_outcome: asyncio.Future[None] = loop.create_future()
        self._composition_gate = asyncio.Event()

    def claim_committed_inbound(self, event_object: object):
        if self._authority_capability is None: return None
        return self._network.claim_committed_inbound(event_object, self._authority_capability)

    def claim_lifecycle_observation(self, event_object: object):
        if self._authority_capability is None: return None
        return self._network.claim_lifecycle_observation(event_object, self._authority_capability)

    def snapshot(self) -> ClientSnapshot:
        return self._network.snapshot()

    def attach_world(self, world: WorldState) -> None:
        if self._world is not None:
            raise RuntimeError("Phase 6 event source already has a World")
        self._world = world

    @property
    def bound_outcome(self) -> asyncio.Future[BoundDiscussionContext]:
        return self._bound_outcome

    @property
    def context_owner_receipt_v2(self):
        return self._context_owner_receipt_v2

    async def wait_context_outcome(self) -> None:
        await self._context_outcome

    def context_failure_detail(self) -> str | None:
        """Read the recorded fact without depending on its waiter being scheduled."""
        outcome = self._context_outcome
        if not outcome.done() or outcome.cancelled():
            return None
        error = outcome.exception()
        if error is None:
            return None
        return (
            error.code if isinstance(error, DiscussionContextError)
            else type(error).__name__
        )

    def release_composition(self) -> None:
        if self._bound is None or not self._bound_outcome.done():
            raise RuntimeError("Phase 6 composition cannot release before context bind")
        if self._composition_gate.is_set():
            raise RuntimeError("Phase 6 composition gate was already released")
        self._composition_gate.set()

    def close(self) -> None:
        # Revoke snapshot inspection before Pending is disposed. World may still
        # resume its existing iterator while earlier owners are being closed.
        self._retired = True
        if self._authority_owner_registration_v2 is not None:
            from .discussion.authority_capture_bridge_v2 import _retire_authority_registration_v2
            _retire_authority_registration_v2(self)
        self._pending = None
        for outcome in (self._bound_outcome, self._context_outcome):
            if not outcome.done():
                outcome.cancel()
            elif not outcome.cancelled():
                # Cleanup is the final owner.  Retrieve any already-recorded
                # exception so cancellation before its waiter resumes cannot
                # leave an unobserved Future exception.
                outcome.exception()

    async def events(self) -> AsyncIterator[ClientEvent]:
        if self._world is None:
            raise RuntimeError("Phase 6 event source requires its exact World")
        if self._iteration_started:
            raise RuntimeError("Phase 6 event source may only be iterated once")
        self._iteration_started = True
        iterator = self._network.events()
        completed_cleanly = False
        try:
            async for event in iterator:
                yield event
                first_bind = self._inspect_committed_snapshot()
                if first_bind:
                    await self._composition_gate.wait()
            completed_cleanly = True
        finally:
            close_iterator = getattr(iterator, "aclose", None)
            if close_iterator is not None:
                await close_iterator()
            if completed_cleanly:
                self._finish_clean_source()
            elif self._bound is not None and not self._context_outcome.done():
                # Non-context source/reducer termination remains World-owned.
                self._context_outcome.set_result(None)

    def _inspect_committed_snapshot(self) -> bool:
        if self._retired:
            return False
        world = self._world
        assert world is not None
        snapshot = world.snapshot()
        if snapshot.version <= self._last_observed_version:
            return False
        self._last_observed_version = snapshot.version
        if snapshot.freshness in {Freshness.ENDED, Freshness.FAILED}:
            if self._bound is None and not self._bound_outcome.done():
                self._bound_outcome.set_exception(
                    RuntimeError("World terminated before discussion context bind")
                )
            elif not self._context_outcome.done():
                self._context_outcome.set_result(None)
            return False
        if snapshot.freshness is not Freshness.CURRENT:
            return False
        if self._bound is None:
            pending = self._pending
            if pending is None:
                error = DiscussionContextError("pending discussion context is required")
                self._bound_outcome.set_exception(error)
                raise error
            try:
                if self._authority_capability is not None:
                    proof = _revalidate_authority_pending_v2(
                        pending, snapshot, self._authority_owner_registration_v2)
                    from .discussion.authority_capture_bridge_v2 import (
                        _consume_authority_pending_proof_v2,
                    )
                    bound, self._context_owner_receipt_v2 = (
                        _consume_authority_pending_proof_v2(proof, self, world))
                else:
                    bound = pending.bind(snapshot)
            except DiscussionContextError as error:
                self._bound_outcome.set_exception(error)
                raise
            except BaseException as error:
                self._bound_outcome.set_exception(error)
                raise
            self._pending = None
            self._bound = bound
            self._bound_outcome.set_result(bound)
            return True
        try:
            validate_bound_context_snapshot(self._bound, snapshot)
        except DiscussionContextError as error:
            if not self._context_outcome.done():
                self._context_outcome.set_exception(error)
            raise
        return False

    def _finish_clean_source(self) -> None:
        if self._bound is None:
            if not self._bound_outcome.done():
                self._bound_outcome.set_exception(
                    RuntimeError("World terminated before discussion context bind")
                )
            return
        if not self._context_outcome.done():
            self._context_outcome.set_result(None)


def _create_phase6_v2_inbound_world(
    network: NetworkClient,
    pending: PendingDiscussionContext,
    *,
    config: WorldStateConfig = WorldStateConfig(),
) -> tuple[_Phase6NetworkEventSource, WorldState]:
    """Explicit offline v2 opt-in; normal Phase 6 runtime remains sink-null."""
    source = _Phase6NetworkEventSource(
        network, pending, enable_inbound_authority_v2=True
    )
    authority = _create_inbound_authority_runtime(source._authority_capability)
    from .world.service import _REGISTERED_WORLD_CONSTRUCTION_V2
    world = WorldState(source, config=config, inbound_authority=authority,
                       _authority_registration_token=_REGISTERED_WORLD_CONSTRUCTION_V2)
    source.attach_world(world)
    from .discussion.authority_capture_bridge_v2 import _register_authority_owners_v2
    _register_authority_owners_v2(network, source._authority_capability, source, authority, world)
    return source, world


def _create_phase6_v2_authority_capture_bridge(
    source: _Phase6NetworkEventSource,
    world: WorldState,
    store: DiscussionStateStore,
):
    """Compose the private UNLEASED bridge from exact already-bound owners."""
    receipt = source.context_owner_receipt_v2
    if receipt is None:
        raise DiscussionContextError("validated authority context owner is required")
    from .discussion.authority_capture_bridge_v2 import (
        _attach_discussion_store_v2, _create_authority_capture_bridge_v2,
    )
    registration = source._authority_owner_registration_v2
    if registration.exact_discussion_store is None:
        _attach_discussion_store_v2(registration, receipt, store)
    return _create_authority_capture_bridge_v2(
        world._create_authority_read_port_v2(receipt),
        store._create_capture_read_port_v2(receipt),
        receipt,
    )


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
        brain: LLMBrain | None,
        brain_controller: BrainController | None,
        arbiter: BrainInvocationArbiter | None,
        reaction: ReactionChatController | None,
        vote_ability: VoteAbilityController | None,
        pending_discussion_context: PendingDiscussionContext | None = None,
        phase6_event_source: _Phase6NetworkEventSource | None = None,
        clock: Clock = time.monotonic,
        request_id_factory: RequestIdFactory | None = None,
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

        self.discussion_context: BoundDiscussionContext | None = None
        self.discussion_store: DiscussionStateStore | None = None
        self._pending_discussion_context = pending_discussion_context
        self._phase6_event_source = phase6_event_source
        self._phase6 = phase6_event_source is not None
        self._clock = clock
        self._request_id_factory = request_id_factory

        self._lifecycle = Phase5RuntimeLifecycle.NEW
        self._exit: Phase5RuntimeExit | None = None
        self._stop_requested = False
        self._startup_failure_detail: str | None = None
        self._start_completed = False
        self._network_task: asyncio.Task[ClientExit] | None = None
        self._world_task: asyncio.Task[WorldStateExit] | None = None
        self._reaction_task: asyncio.Task[FeatureControllerExit] | None = None
        self._vote_task: asyncio.Task[FeatureControllerExit] | None = None
        self._context_task: asyncio.Task[None] | None = None
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
                clock=clock,
                request_id_factory=request_id_factory,
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

    @classmethod
    async def connect_phase6(
        cls,
        config: Phase5ClientRuntimeConfig,
        credential_store: CredentialStore,
        *,
        pending_discussion_context: PendingDiscussionContext | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        request_id_factory: RequestIdFactory | None = None,
    ) -> "Phase5ClientRuntime":
        """Compose the mandatory Phase 6 resources without starting features.

        The caller supplies the already validated, single-use result of
        ``validate_discussion_bootstrap``.  It is rechecked and accepted before
        any filesystem permission or resource action.  The complete feature
        graph is constructed only after ``start`` binds the first CURRENT
        snapshot.
        """

        if not isinstance(config, Phase5ClientRuntimeConfig):
            cls._discard_pending_value(pending_discussion_context)
            raise TypeError("config must be Phase5ClientRuntimeConfig")
        if not callable(clock) or not callable(sleep):
            cls._discard_pending_value(pending_discussion_context)
            raise TypeError("clock and sleep must be callable")
        if request_id_factory is not None and not callable(request_id_factory):
            cls._discard_pending_value(pending_discussion_context)
            raise TypeError("request_id_factory must be callable when supplied")

        pending = cls._validate_phase6_pending(config, pending_discussion_context)
        session: BrokerAdmissionSession | None = None
        backend: BrokeredStructuredLLMBackend | None = None
        audit: JsonlAiAuditSink | None = None
        network: NetworkClient | None = None
        world: WorldState | None = None
        event_source: _Phase6NetworkEventSource | None = None
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
            event_source = _Phase6NetworkEventSource(network, pending)
            authority_runtime = (None if event_source._authority_capability is None else
                                 _create_inbound_authority_runtime(event_source._authority_capability))
            world = WorldState(event_source, config=config.world,
                               inbound_authority=authority_runtime)
            event_source.attach_world(world)
            return cls(
                config=config,
                network=network,
                world=world,
                admission=session,
                backend=backend,
                audit=audit,
                brain=None,
                brain_controller=None,
                arbiter=None,
                reaction=None,
                vote_ability=None,
                pending_discussion_context=pending,
                phase6_event_source=event_source,
                clock=clock,
                request_id_factory=request_id_factory,
            )
        except BaseException:
            pending.discard_manifest()
            close_errors: list[BaseException] = []

            async def close_resource(resource: object | None, method: str) -> None:
                if resource is None:
                    return
                try:
                    await asyncio.shield(getattr(resource, method)())
                except BaseException as error:
                    close_errors.append(error)

            await close_resource(audit, "aclose")
            await close_resource(backend if backend is not None else session, "aclose")
            await close_resource(world, "stop")
            await close_resource(network, "stop")
            if close_errors:
                raise RuntimeError(
                    "Phase 6 runtime construction cleanup failed: "
                    + ",".join(type(error).__name__ for error in close_errors)
                )
            raise

    @staticmethod
    def _discard_pending_value(value: object) -> None:
        if isinstance(value, PendingDiscussionContext):
            value.discard_manifest()

    @classmethod
    def _validate_phase6_pending(
        cls,
        config: Phase5ClientRuntimeConfig,
        value: object,
    ) -> PendingDiscussionContext:
        if not isinstance(value, PendingDiscussionContext):
            raise DiscussionContextError("pending discussion context is required")
        try:
            if not value.manifest_is_retained:
                raise DiscussionContextError("pending discussion context has been disposed")
            manifest_bytes = value.canonical_manifest_bytes()
            if type(manifest_bytes) is not bytes:
                raise DiscussionContextError("pending discussion context is invalid")
            if not isinstance(value.manifest_sha256, str) or not isinstance(
                value.context_sha256, str
            ):
                raise DiscussionContextError("pending discussion context hash is invalid")
            if any(
                len(digest) != 64
                or digest != digest.lower()
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in (value.manifest_sha256, value.context_sha256)
            ):
                raise DiscussionContextError("pending discussion context hash is invalid")
            if sha256(manifest_bytes).hexdigest() != value.manifest_sha256:
                raise DiscussionContextError("pending discussion context hash mismatch")
            if not isinstance(value.context, AuthorizedDiscussionContext):
                raise DiscussionContextError("pending discussion context is invalid")
            if value.context.content_manifest_sha256 != value.manifest_sha256:
                raise DiscussionContextError("pending discussion context hash mismatch")
            if canonical_sha256(value.context) != value.context_sha256:
                raise DiscussionContextError("pending discussion context hash mismatch")
            if (
                value.context.game_id != config.network.game_id
                or value.context.player_id != config.player_id
            ):
                raise DiscussionContextError(
                    "runtime game/player identity does not match discussion context"
                )
        except DiscussionContextError:
            value.discard_manifest()
            raise
        except BaseException:
            value.discard_manifest()
            raise DiscussionContextError("pending discussion context is invalid") from None
        return value

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
            if self._phase6:
                if not await self._compose_phase6_after_first_current():
                    errors = await self._ensure_cleanup()
                    self._record_pre_monitor_close(errors)
                    self._start_completed = True
                    return
            if self.reaction is None or self.vote_ability is None:
                raise RuntimeError("runtime feature graph is incomplete")
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
            self._start_completed = True
        except BaseException as error:
            self._capture_first_bind_failure()
            if (
                self._phase6
                and self._stop_requested
                and isinstance(error, asyncio.CancelledError)
            ):
                # A concurrent close already owns shutdown. Caller cancellation
                # must join its result, not overwrite it with startup failure.
                errors = await asyncio.shield(self._ensure_cleanup())
                self._record_pre_monitor_close(errors)
                raise
            self._stop_requested = True
            self._lifecycle = Phase5RuntimeLifecycle.STOPPING
            if self._phase6 and self._startup_failure_detail is None:
                self._startup_failure_detail = (
                    error.code if isinstance(error, DiscussionContextError)
                    else type(error).__name__
                )
            errors = await asyncio.shield(self._ensure_cleanup())
            if self._phase6:
                self._record_pre_monitor_close(errors)
            self._lifecycle = Phase5RuntimeLifecycle.FAILED
            raise

    async def _compose_phase6_after_first_current(self) -> bool:
        pending = self._pending_discussion_context
        if pending is None:
            raise DiscussionContextError("pending discussion context is required")
        source = self._phase6_event_source
        if source is None:
            raise DiscussionContextError("Phase 6 event source is required")
        bound = await self._first_bound_context(source)
        # This is the sole suspension before graph construction and feature
        # starts. Close sets stop synchronously before starting cached cleanup;
        # all remaining composition is synchronous, so either it owns the whole
        # graph or cleanup sees no graph and none can subsequently appear.
        if bound is None or self._stop_requested:
            return False
        self._pending_discussion_context = None
        self.discussion_context = bound

        store = DiscussionStateStore(bound)
        if source.context_owner_receipt_v2 is not None:
            from .discussion.authority_capture_bridge_v2 import _attach_discussion_store_v2
            _attach_discussion_store_v2(
                source._authority_owner_registration_v2,
                source.context_owner_receipt_v2,
                store,
            )
        self.discussion_store = store
        brain_arguments: dict[str, object] = {
            "backend": self.backend,
            "audit": self.audit,
            "identity": LLMClientIdentity(self.config.network.game_id, self.config.player_id),
            "config": self.config.llm,
            "clock": self._clock,
        }
        if self._request_id_factory is not None:
            brain_arguments["request_id_factory"] = self._request_id_factory
        brain = LLMBrain(**brain_arguments)  # type: ignore[arg-type]
        self.brain = brain
        brain_controller = BrainController(
            world=self.world,
            sender=self.network,
            brain=brain,
            config=self.config.brain,
            clock=self._clock,
            discussion_state=store,
            discussion_audit=self.audit,
        )
        self.brain_controller = brain_controller
        arbiter = BrainInvocationArbiter(
            controller=brain_controller,
            admission=self.admission,
            clock=self._clock,
        )
        self.arbiter = arbiter
        frequency = DeterministicSpeakingFrequencyPolicy(
            profile=self.config.speaking,
            master_seed=self.config.master_seed,
        )
        reaction = ReactionChatController(
            world=self.world,
            invoker=arbiter,
            master_seed=self.config.master_seed,
            config=self.config.reaction,
            frequency_policy=frequency,
            clock=self._clock,
            discussion_context=bound,
        )
        self.reaction = reaction
        vote_ability = VoteAbilityController(
            world=self.world,
            invoker=arbiter,
            discussion_context=bound,
            config=self.config.vote_ability,
            clock=self._clock,
        )
        self.vote_ability = vote_ability
        self._context_task = asyncio.create_task(
            source.wait_context_outcome(),
            name=f"aiwolf-discussion-context-{self.config.player_id}",
        )
        source.release_composition()
        return True

    async def _first_bound_context(
        self,
        source: _Phase6NetworkEventSource,
    ) -> BoundDiscussionContext | None:
        assert self._world_task is not None
        await asyncio.wait(
            {source.bound_outcome, self._world_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if source.bound_outcome.done() and not source.bound_outcome.cancelled():
            return source.bound_outcome.result()
        if self._stop_requested:
            return None
        raise RuntimeError("World terminated before discussion context bind")

    async def wait(self) -> Phase5RuntimeExit:
        if self._monitor_task is None:
            if self._start_completed and self._exit is not None:
                return self._exit
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
            self._record_pre_monitor_close(errors)
            return
        self._lifecycle = Phase5RuntimeLifecycle.STOPPING
        errors = await self._ensure_cleanup()
        monitor = self._monitor_task
        if monitor is None:
            self._record_pre_monitor_close(errors)
            return
        if monitor is not None and monitor is not asyncio.current_task():
            await asyncio.shield(monitor)

    def _capture_first_bind_failure(self) -> None:
        source = self._phase6_event_source
        if source is None:
            return
        outcome = source.bound_outcome
        if outcome.done() and not outcome.cancelled():
            error = outcome.exception()
            if error is not None:
                # The ready Future owns the first-bind fact even if start was
                # cancelled before consuming it. Caller cancellation is separate.
                self._startup_failure_detail = (
                    error.code if isinstance(error, DiscussionContextError)
                    else type(error).__name__
                )

    def _record_pre_monitor_close(self, errors: tuple[str, ...]) -> None:
        if self._exit is not None and not (
            self._startup_failure_detail is not None
            and self._exit.reason is Phase5RuntimeExitReason.STOPPED
        ):
            return
        reason = (
            Phase5RuntimeExitReason.CLEANUP_FAILED
            if errors
            else Phase5RuntimeExitReason.CONTROLLER_FAILED
            if self._startup_failure_detail is not None
            else Phase5RuntimeExitReason.STOPPED
        )
        success = reason is Phase5RuntimeExitReason.STOPPED
        self._exit = Phase5RuntimeExit(
            reason=reason,
            success=success,
            network=None,
            world=None,
            reaction=None,
            vote_ability=None,
            detail=",".join(errors) or self._startup_failure_detail,
        )
        self._lifecycle = (
            Phase5RuntimeLifecycle.FAILED
            if not success
            else Phase5RuntimeLifecycle.ENDED
        )

    async def _close_after_cancellation(self) -> None:
        close_task = asyncio.create_task(self.aclose())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await close_task

    async def _monitor(self) -> Phase5RuntimeExit:
        reason = Phase5RuntimeExitReason.STOPPED
        detail: str | None = None
        source = self._phase6_event_source
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
        if self._context_task is not None:
            watched.add(self._context_task)  # type: ignore[arg-type]
        try:
            while watched:
                done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                watched.difference_update(done)
                context_detail = source.context_failure_detail() if source is not None else None
                if context_detail is not None:
                    reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                    detail = context_detail
                    break
                context_task = self._context_task
                if context_task is not None and context_task in done:
                    if context_task.cancelled():
                        if not self._stop_requested:
                            reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                            detail = "owned context task cancelled unexpectedly"
                            watched.clear()
                            break
                    else:
                        context_error = context_task.exception()
                        if context_error is not None:
                            reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                            detail = (
                                context_error.code
                                if isinstance(context_error, DiscussionContextError)
                                else type(context_error).__name__
                            )
                            watched.clear()
                            break
                    done.remove(context_task)  # type: ignore[arg-type]
                ordered_done = (
                    tuple(
                        task
                        for task in (
                            self._network_task,
                            self._world_task,
                            self._reaction_task,
                            self._vote_task,
                        )
                        if task in done
                    )
                    if self._phase6
                    else tuple(done)
                )
                for task in ordered_done:
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
                                natural_tasks: list[Awaitable[object]] = [
                                    asyncio.shield(self._world_task),
                                    asyncio.shield(self._reaction_task),
                                    asyncio.shield(self._vote_task),
                                ]
                                if self._context_task is not None:
                                    natural_tasks.append(asyncio.shield(self._context_task))
                                natural_results = await asyncio.wait_for(
                                    asyncio.gather(*natural_tasks),
                                    self.config.network.shutdown_timeout_seconds,
                                )
                                world_exit = natural_results[0]  # type: ignore[assignment]
                                reaction_exit = natural_results[1]  # type: ignore[assignment]
                                vote_exit = natural_results[2]  # type: ignore[assignment]
                            except TimeoutError:
                                reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                                detail = "natural game-end drain timed out"
                            except DiscussionContextError as error:
                                reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
                                detail = error.code
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
        # Cleanup preserves completed source outcomes while retiring pending work.
        # Reconcile again after every shutdown suspension, including cancellation
        # and natural drain, before publishing the terminal result.
        context_detail = source.context_failure_detail() if source is not None else None
        if context_detail is not None:
            reason = Phase5RuntimeExitReason.CONTROLLER_FAILED
            detail = context_detail
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

        self._capture_first_bind_failure()
        if self._phase6_event_source is not None:
            self._phase6_event_source.close()
        pending = self._pending_discussion_context
        self._pending_discussion_context = None
        if pending is not None:
            try:
                pending.discard_manifest()
            except BaseException as error:
                errors.append(f"discussion_context:{type(error).__name__}")

        if self.vote_ability is not None:
            await close("vote_ability", self.vote_ability.stop())
        if self.reaction is not None:
            await close("reaction", self.reaction.stop())
        if self.arbiter is not None:
            await close("arbiter", self.arbiter.stop())
        elif self.brain_controller is not None:
            await close("brain_controller", self.brain_controller.stop())
        if self.discussion_store is not None:
            try:
                self.discussion_store.close()
            except BaseException as error:
                errors.append(f"discussion_store:{type(error).__name__}")
        await close("audit", self.audit.aclose())
        if self.backend is not None:
            await close("admission", self.backend.aclose())
        else:
            await close("admission", self.admission.aclose())
        await close("world", self.world.stop())
        await close("network", self.network.stop())
        if self._context_task is not None and not self._context_task.done():
            self._context_task.cancel()

        owned_tasks = tuple(
            task
            for task in (
                self._network_task,
                self._world_task,
                self._reaction_task,
                self._vote_task,
                self._context_task,
            )
            if task is not None
        )
        if owned_tasks:
            results = await asyncio.gather(*owned_tasks, return_exceptions=True)
            for task, result in zip(owned_tasks, results, strict=True):
                if task is self._context_task:
                    # The context validator reports its primary runtime failure
                    # through _monitor; gathering it here is task drainage, not
                    # a second cleanup operation or failure classification.
                    continue
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    errors.append(f"task:{type(result).__name__}")
        if self._phase6 and self._monitor_task is None:
            # The cached owner publishes the terminal result even when every
            # start/close caller was cancelled while cleanup was in progress.
            self._record_pre_monitor_close(tuple(errors))
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
