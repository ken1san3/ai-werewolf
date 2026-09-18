"""Bounded, synchronous observations for the Phase 3.5 fixture only."""
from collections import deque
from functools import wraps

from ai_client.brain import DecisionStatus
from ai_client.network import AbilityAction, VoteAction
from ai_client.world import Freshness


class ReservationProbe:
    def __init__(self, brain, reservation, *, limit=32):
        self.records = deque(maxlen=limit)
        self.frames = []
        world = brain.world
        for name in ("snapshot", "current_actions"):
            self._observe_read(world, name)
        self._observe_check(brain, "capture_input", "CAPTURE_NONE")
        self._observe_check(brain, "_request_is_current", "BEFORE_BRAIN")
        self._observe_check(brain, "_stale_before_send", "BEFORE_SEND")
        original = reservation._handle_non_sent

        @wraps(original)
        def non_sent(candidate, outcome):
            # The returned coroutine is the original object; no extra await,
            # task, retry, or scheduling boundary is introduced here.
            self._append_outcome(outcome)
            return original(candidate, outcome)

        reservation._handle_non_sent = non_sent

    def snapshot(self):
        return [dict(record) for record in self.records]

    def _observe_read(self, world, name):
        original = getattr(world, name)

        @wraps(original)
        def read(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.frames:
                self.frames[-1][name] = result
            return result

        setattr(world, name, read)

    @staticmethod
    def _handles(name, args, kwargs):
        if name == "capture_input":
            return kwargs.get("allowed_handles", ())
        request = args[0] if args else kwargs.get("request")
        return tuple(option.handle for option in request.action_context.options)

    def _observe_check(self, brain, name, site):
        original = getattr(brain, name)

        @wraps(original)
        def check(*args, **kwargs):
            try:
                handles = self._handles(name, args, kwargs)
                enabled = bool(handles) and all(
                    isinstance(handle, (VoteAction, AbilityAction)) for handle in handles
                )
            except Exception:
                enabled = False
            if not enabled:
                return original(*args, **kwargs)
            frame = {"handles": handles}
            self.frames.append(frame)
            try:
                result = original(*args, **kwargs)
            except BaseException:
                self._append_check(site, "ERROR", frame)
                raise
            else:
                failed = (result is None if name == "capture_input" else
                          result is True if name == "_stale_before_send" else result is False)
                if failed:
                    self._append_check(site, "STALE", frame)
                return result
            finally:
                self.frames.pop()

        setattr(brain, name, check)

    def _append_check(self, site, status, frame):
        try:
            snapshot = frame.get("snapshot")
            actions = frame.get("current_actions")
            record = {"site": site, "status": status,
                      "caught_up": "UNKNOWN", "actions_current": "UNKNOWN",
                      "handles_current": "UNKNOWN", "phase_matches": "UNKNOWN",
                      "freshness_current": "UNKNOWN", "world_complete": "UNKNOWN"}
            if snapshot is not None:
                record["caught_up"] = bool(snapshot.is_caught_up)
                record["freshness_current"] = snapshot.freshness is Freshness.CURRENT
                record["world_complete"] = bool(snapshot.complete)
                phase = snapshot.phase
                record["phase_matches"] = bool(phase is not None and all(
                    (handle.day, handle.phase) == (phase.day, phase.phase)
                    for handle in frame["handles"]
                ))
            if actions is not None:
                record["handles_current"] = all(
                    handle in actions.actions for handle in frame["handles"]
                )
                if snapshot is not None:
                    record["actions_current"] = bool(
                        actions.is_caught_up and actions.world_version == snapshot.version
                        and actions.world_last_applied_seq == snapshot.last_applied_seq
                        and actions.network_last_seq == snapshot.last_applied_seq
                    )
            self.records.append(record)
        except Exception:
            self.records.append({"site": site, "status": "UNKNOWN"})

    def _append_outcome(self, outcome):
        try:
            status = outcome.status
            started = outcome.invocation_started
            self.records.append({
                "site": "OUTCOME",
                "status": status.value if isinstance(status, DecisionStatus) else "UNKNOWN",
                "invocation_started": started if type(started) is bool else "UNKNOWN",
            })
        except Exception:
            self.records.append({"site": "OUTCOME", "status": "UNKNOWN"})
