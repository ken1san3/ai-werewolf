"""Phase-scoped invocation policy for the Phase 3.3 Brain boundary."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from ai_client.world import Freshness, WorldState

from .controller import BrainController
from .model import (
    CoordinatorExit,
    CoordinatorExitReason,
    CoordinatorState,
    PhaseKey,
)


class PhaseBrainCoordinator:
    """Invoke a controller at most once for each observed day/phase key."""

    def __init__(self, *, world: WorldState, controller: BrainController) -> None:
        self.world = world
        self.controller = controller
        self.state = CoordinatorState.CREATED
        self._run_started = False
        self._stop_requested = False
        self._run_task: asyncio.Task[CoordinatorExit] | None = None
        self._attempted: list[PhaseKey] = []

    @property
    def lifecycle(self) -> CoordinatorState:
        return self.state

    @property
    def attempted_phases(self) -> tuple[PhaseKey, ...]:
        return tuple(self._attempted)

    async def run(self) -> CoordinatorExit:
        if self._run_started:
            raise RuntimeError("PhaseBrainCoordinator.run() may only be called once")
        self._run_started = True
        self._run_task = asyncio.current_task()
        self.state = CoordinatorState.RUNNING
        after_version = -1
        try:
            while not self._stop_requested:
                snapshot = await self.world.wait_for_update(after_version)
                after_version = max(after_version, snapshot.version)
                if snapshot.freshness is Freshness.ENDED:
                    self.state = CoordinatorState.STOPPED
                    await self.controller.stop()
                    return self._exit(CoordinatorExitReason.WORLD_ENDED)
                if snapshot.freshness is Freshness.FAILED:
                    self.state = CoordinatorState.FAILED
                    await self.controller.stop()
                    return self._exit(CoordinatorExitReason.WORLD_FAILED)
                if snapshot.phase is None:
                    continue
                key = PhaseKey(snapshot.phase.day, snapshot.phase.phase)
                if key in self._attempted:
                    continue
                request = self.controller.capture_input()
                if request is None:
                    continue
                outcome = await self.controller.decide_and_send(request)
                after_version = max(after_version, self.world.snapshot().version)
                if outcome.invocation_started:
                    self._attempted.append(key)
            self.state = CoordinatorState.STOPPED
            return self._exit(CoordinatorExitReason.STOPPED)
        except asyncio.CancelledError:
            self.state = CoordinatorState.STOPPED
            await self.controller.stop()
            if self._stop_requested:
                return self._exit(CoordinatorExitReason.STOPPED)
            return self._exit(CoordinatorExitReason.CANCELLED)
        except Exception:
            self.state = CoordinatorState.FAILED
            await self.controller.stop()
            return self._exit(CoordinatorExitReason.WORLD_FAILED)
        finally:
            self._run_task = None

    async def stop(self) -> None:
        if not self._run_started:
            self._run_started = True
        self._stop_requested = True
        if self.state in {CoordinatorState.CREATED, CoordinatorState.RUNNING}:
            self.state = CoordinatorState.STOPPING
        await self.controller.stop()
        task = self._run_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.state = CoordinatorState.STOPPED

    def _exit(self, reason: CoordinatorExitReason) -> CoordinatorExit:
        return CoordinatorExit(reason=reason, attempted_phases=tuple(self._attempted))
