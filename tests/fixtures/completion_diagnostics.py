"""Fixed-field, text-free progress for the disposable completion fixture."""
from collections import Counter
import json
import os
from pathlib import Path
import time

_ENUMS = frozenset({
    "night0", "day", "vote", "night", "ended", "new", "running", "stopping", "stopped", "failed",
    "ABSENT", "DUE", "FUTURE", "EXPIRED", "VALID", "HOLD", "RUNNING", "UNKNOWN",
    "no_decision", "invalid", "brain_failed", "deadline_suppressed", "timed_out", "accepted",
    "rejected", "transport_gap", "history_gap", "frequency_suppressed",
    "NO_DECISION", "SENT", "INVALID_DECISION", "TIMED_OUT", "BRAIN_FAILED", "STALE", "CANCELLED",
    "SEND_NOT_DELIVERED", "SEND_DELIVERY_UNKNOWN", "DEADLINE_SUPPRESSED",
})
_FIELDS = frozenset({
    "day", "phase", "lifecycle", "phase_attempts", "accepted", "sent", "clock_stage", "day1_accepted",
    "initial", "reaction", "co", "deadline", "deadline_closed", "rejected_closed", "history_closed",
    "transport_closed", "outcomes", "observed_day1", "brain_outcomes", "caught_up", "actions_current", "diagnostic",
})


def safe_progress(value):
    if not isinstance(value, dict):
        return {"status": "UNKNOWN"}
    result = {}
    for key, item in value.items():
        if key not in _FIELDS:
            continue
        if key in {"outcomes", "brain_outcomes"}:
            result[key] = {name: count for name, count in item.items()
                           if name in _ENUMS and type(count) is int and 0 <= count <= 100000} if isinstance(item, dict) else {}
        elif type(item) is bool or type(item) is int and 0 <= item <= 100000:
            result[key] = item
        elif isinstance(item, str) and item in _ENUMS:
            result[key] = item
        else:
            result[key] = "UNKNOWN"
    return result


def write_progress(path: Path, value: dict, previous: dict | None):
    value = safe_progress(value)
    if value != previous:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="ascii")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    return value


class ProgressSampler:
    """Bound diagnostic overhead; failure cannot stop the fixture being measured."""
    def __init__(self, path: Path):
        self.path = path
        self.previous = None
        self.next_sample = 0.0
        self.enabled = True

    def sample(self, snapshot):
        if not self.enabled or time.monotonic() < self.next_sample:
            return
        self.next_sample = time.monotonic() + 0.25
        try:
            self.previous = write_progress(self.path, snapshot(), self.previous)
        except Exception:
            self.enabled = False
            try:
                write_progress(self.path, {"diagnostic": "UNKNOWN"}, None)
            except Exception:
                pass


def client_progress(runtime, clock, day_one_ready: bool):
    controller = runtime.reaction
    state = controller.snapshot()
    world = runtime.world.snapshot()
    actions = runtime.world.current_actions()
    now = clock()
    deadline = runtime.world.transport_observations().current_deadline
    boundary = None if deadline is None else deadline.local_deadline_monotonic
    value = {
        "day": None if world.phase is None else world.phase.day,
        "phase": None if world.phase is None else world.phase.phase,
        "lifecycle": state.lifecycle.value,
        "phase_attempts": controller._phase_chat_invocations,
        "accepted": state.accepted_count, "sent": state.send_count,
        "observed_day1": day_one_ready,
        "caught_up": world.is_caught_up,
        "actions_current": (actions.is_caught_up and actions.world_version == world.version
                            and actions.world_last_applied_seq == world.last_applied_seq
                            and actions.network_last_seq == world.last_applied_seq),
        "deadline": "ABSENT" if boundary is None else "EXPIRED" if now >= boundary else "VALID",
        "deadline_closed": controller._chat_deadline_closed,
        "rejected_closed": controller._chat_rejected_closed,
        "history_closed": controller._history_gap_closed,
        "transport_closed": controller._transport_gap_closed,
        "outcomes": dict(Counter(item.status.value for item in state.outcomes)),
        "brain_outcomes": dict(Counter(item.brain_outcome for item in state.outcomes
                                        if item.brain_outcome is not None)),
    }
    for name in ("initial", "reaction", "co"):
        pending = getattr(controller, "_pending_" + name)
        value[name] = "ABSENT" if pending is None else "DUE" if pending.due <= now else "FUTURE"
    return value


def timeout_summary(root: Path):
    result = {}
    for label in ("server.result", *(f"player-{index}.status" for index in range(9))):
        try:
            with (root / (label + ".progress.json")).open("rb") as handle:
                payload = handle.read(16385)
            if len(payload) > 16384:
                raise ValueError
            value = json.loads(payload)
            result[label] = safe_progress(value)
        except (OSError, ValueError):
            result[label] = {"status": "UNKNOWN"}
    return result
