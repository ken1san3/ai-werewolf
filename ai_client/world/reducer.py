"""Canonical event-to-world reducer.

This module only understands the player-visible event vocabulary.  It never
imports game-core classes and never exposes the mapping that arrived on the
wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_client.network import ServerEvent

from .memory import HistoryStore
from .model import (
    AbilityResultRecord,
    ChatRecord,
    CoDeclarationRecord,
    CoReportRecord,
    DeathRecord,
    DeathView,
    GameLifecycleRecord,
    HistoryRecord,
    KnownUnmodeledEventRecord,
    MalformedEventRecord,
    PhaseTimingChangedRecord,
    PhaseTransitionRecord,
    PhaseView,
    PlayerView,
    PublicNotifyRecord,
    RevealedRoleView,
    SelfView,
    TieResolvedRandomRecord,
    UnknownEventRecord,
    VoteEntry,
    VoteRevealRecord,
    VoteResultRecord,
    WorldStateConfig,
)


TOP_LEVEL_TYPES = frozenset(
    {
        "session.joined",
        "session.resumed",
        "session.ready",
        "game.state_sync",
        "player.list",
        "player.deaths",
        "player.action_state",
        "game.event",
        "chat.message",
        "action.rejected",
    }
)

KNOWN_CORE_TYPES = frozenset(
    {
        "ACTION_NO_SELECTION_RANDOM_TARGETS_SELECTED",
        "ACTION_RESOLVED",
        "ACTION_SUBMITTED",
        "CO_DECLARED",
        "CO_REPORTED",
        "DAY_EXTENDED",
        "DAY_SHORTENED",
        "FIRST_NIGHT_INSPECT_TARGET_SELECTED",
        "GAME_CREATED",
        "GAME_ENDED",
        "GUARD_SUCCEEDED",
        "INSPECT_DEAD_ROLE_RESULT",
        "INSPECT_RESULT",
        "MEDIUM_RESULT",
        "PASSIVE_TARGET_SELECTED",
        "PHASE_STARTED",
        "PLAYER_DIED",
        "PUBLIC_NOTIFY",
        "ROLE_ASSIGNED",
        "ROLE_MISSING_APPLIED",
        "TIE_RESOLVED_RANDOM",
        "VOTE_RESOLVED",
        "VOTE_REVEALED_LIVE",
        "VOTE_SUBMITTED",
        "VOTES_REVEALED_AFTER",
        "WOLF_ATTACK_TARGET_RESOLVED",
        "WOLF_ATTACK_TARGET_SELECTED_RANDOM",
        "WOLF_ATTACK_TIE_RESOLVED_RANDOM",
    }
)

TYPED_CORE_TYPES = frozenset(
    {
        "CO_DECLARED",
        "CO_REPORTED",
        "DAY_EXTENDED",
        "DAY_SHORTENED",
        "GAME_CREATED",
        "GAME_ENDED",
        "GUARD_SUCCEEDED",
        "INSPECT_DEAD_ROLE_RESULT",
        "INSPECT_RESULT",
        "MEDIUM_RESULT",
        "PHASE_STARTED",
        "PLAYER_DIED",
        "PUBLIC_NOTIFY",
        "TIE_RESOLVED_RANDOM",
        "VOTE_RESOLVED",
        "VOTE_REVEALED_LIVE",
        "VOTES_REVEALED_AFTER",
    }
)


@dataclass(frozen=True)
class ReductionResult:
    state_sync: bool = False
    changed: bool = True


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _required_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None | object:
    if key not in payload:
        return None
    value = payload[key]
    if value is None or (isinstance(value, str) and value):
        return value
    return _INVALID


def _optional_int(payload: Mapping[str, Any], key: str) -> int | None | object:
    if key not in payload:
        return None
    value = payload[key]
    if value is None or _is_int(value):
        return value
    return _INVALID


_INVALID = object()


def _required_nullable_string(payload: Mapping[str, Any], key: str) -> str | None | object:
    if key not in payload:
        return _INVALID
    return _optional_string(payload, key)


def _required_nullable_int(payload: Mapping[str, Any], key: str) -> int | None | object:
    if key not in payload:
        return _INVALID
    return _optional_int(payload, key)


def _required_day_phase(payload: Mapping[str, Any]) -> tuple[int, str] | None:
    day = payload.get("day")
    phase = payload.get("phase")
    if not _is_int(day) or not isinstance(phase, str) or not phase:
        return None
    return day, phase


class WorldReducer:
    """Mutable single-writer state used by :class:`WorldState`."""

    def __init__(self, config: WorldStateConfig) -> None:
        self.memory = HistoryStore(config.max_history_records, config.max_history_bytes)
        self.players: dict[str, PlayerView] = {}
        self.deaths: dict[str, DeathView] = {}
        self.phase: PhaseView | None = None
        self.self_view: SelfView | None = None
        self.revealed_roles: tuple[RevealedRoleView, ...] = ()
        self.last_applied_seq = 0
        self.unknown_event_count = 0
        self.known_unmodeled_event_count = 0
        self.malformed_event_count = 0
        self._next_order = 1

    def apply_server_event(self, event: ServerEvent) -> ReductionResult:
        self.last_applied_seq = max(self.last_applied_seq, event.seq)
        if event.type == "game.state_sync":
            if not self._valid_state_sync_shape(event.payload):
                self._append_malformed("game.state_sync")
                return ReductionResult()
            if not self._apply_state_sync(event.payload):
                return ReductionResult()
            return ReductionResult(state_sync=True)
        if event.type == "game.event":
            return ReductionResult(changed=self._apply_game_event_container(event.payload))
        if event.type == "chat.message":
            return ReductionResult(changed=self._apply_chat(event.payload))
        if event.type == "player.list":
            self._apply_player_list(event.payload)
            return ReductionResult()
        if event.type == "player.deaths":
            self._apply_deaths(event.payload)
            return ReductionResult()
        if event.type == "player.action_state":
            self._apply_action_state(event.payload)
            return ReductionResult()
        if event.type in TOP_LEVEL_TYPES:
            # Session and rejection envelopes are facts for transport, not
            # world-history records.  Their sequence is nevertheless drained.
            return ReductionResult()
        self._append_unknown(event.type)
        return ReductionResult()

    def apply_unknown_notice(self) -> None:
        self.unknown_event_count += 1

    def reset_for_sync(self) -> None:
        self.players.clear()
        self.deaths.clear()
        self.phase = None
        self.self_view = None
        self.revealed_roles = ()
        self.memory.reset()
        self.unknown_event_count = 0
        self.known_unmodeled_event_count = 0
        self.malformed_event_count = 0
        self._next_order = 1

    def _apply_state_sync(self, payload: Mapping[str, Any]) -> bool:
        players = payload.get("players")
        revealed_roles = payload.get("revealed_roles")
        parsed_revealed_roles = self._parse_revealed_roles(players, revealed_roles)
        if parsed_revealed_roles is None:
            self._append_malformed("game.state_sync")
            return False

        self.reset_for_sync()
        deaths = payload.get("deaths")
        action_state = payload.get("action_state")
        own = payload.get("self")
        history = payload.get("history")
        if not isinstance(players, (list, tuple)):
            return False
        if not isinstance(deaths, (list, tuple)):
            return False
        if not isinstance(action_state, Mapping):
            return False
        if not isinstance(own, Mapping):
            return False
        self._apply_player_list({"players": players})
        self._apply_deaths({"deaths": deaths})
        self._apply_action_state(action_state)
        self.revealed_roles = parsed_revealed_roles
        authoritative_players = dict(self.players)
        authoritative_deaths = dict(self.deaths)
        # ``action_state`` is the final current phase, not the context of the
        # first item in the historical stream.  Keep it aside while history is
        # replayed so dayless records use the preceding PHASE_STARTED fact.
        sync_phase = self.phase
        self.phase = None
        player_id = _required_string(own, "player_id")
        role_id = _required_string(own, "role_id")
        modifier_ids = own.get("modifier_ids")
        if player_id is not None and role_id is not None and isinstance(modifier_ids, (list, tuple)):
            if all(isinstance(item, str) and item for item in modifier_ids):
                self.self_view = SelfView(player_id, role_id, tuple(modifier_ids))
        if isinstance(history, (list, tuple)):
            for entry in history:
                if isinstance(entry, Mapping):
                    self._apply_history_entry(entry)
        # Historical GAME_CREATED / PLAYER_DIED facts are retained, but the
        # explicit state-sync collections remain authoritative for current
        # view materialization.
        self.players = authoritative_players
        self.deaths = authoritative_deaths
        self._rebuild_players()
        self.phase = sync_phase
        return True

    @staticmethod
    def _parse_revealed_roles(
        players: Any, revealed_roles: Any
    ) -> tuple[RevealedRoleView, ...] | None:
        if not isinstance(players, (list, tuple)) or not isinstance(
            revealed_roles, (list, tuple)
        ):
            return None

        player_ids: list[str] = []
        for raw_player in players:
            if not isinstance(raw_player, Mapping):
                return None
            player_id = _required_string(raw_player, "player_id")
            if player_id is None or player_id in player_ids:
                return None
            player_ids.append(player_id)

        parsed: dict[str, RevealedRoleView] = {}
        for raw_revealed in revealed_roles:
            if not isinstance(raw_revealed, Mapping):
                return None
            player_id = _required_string(raw_revealed, "player_id")
            role_id = _required_string(raw_revealed, "role_id")
            if (
                player_id is None
                or role_id is None
                or player_id not in player_ids
                or player_id in parsed
            ):
                return None
            parsed[player_id] = RevealedRoleView(player_id, role_id)

        return tuple(parsed[player_id] for player_id in player_ids if player_id in parsed)

    @staticmethod
    def _valid_state_sync_shape(payload: Mapping[str, Any]) -> bool:
        required = {"players", "deaths", "action_state", "self", "revealed_roles", "history"}
        if not required.issubset(payload):
            return False
        if not all(isinstance(payload[key], (list, tuple)) for key in ("players", "deaths", "revealed_roles", "history")):
            return False
        action_state = payload["action_state"]
        own = payload["self"]
        if not isinstance(action_state, Mapping) or not isinstance(own, Mapping):
            return False
        if not isinstance(action_state.get("phase"), str) or not _is_int(action_state.get("day")):
            return False
        if not isinstance(action_state.get("actions"), (list, tuple)):
            return False
        return all(
            isinstance(item, Mapping)
            and isinstance(item.get("player_id"), str)
            and isinstance(item.get("display_name"), str)
            for item in payload["players"]
        )

    def _apply_history_entry(self, entry: Mapping[str, Any]) -> None:
        message_type = entry.get("type")
        payload = entry.get("payload")
        if not isinstance(message_type, str) or not isinstance(payload, Mapping):
            self._append_malformed(str(message_type or "history"))
            return
        if message_type == "game.event":
            self._apply_game_event_container(payload)
        elif message_type == "chat.message":
            self._apply_chat(payload)
        else:
            # A state-sync history is authorized by the network layer, but an
            # unrecognized entry still remains observable without raw data.
            self._append_unknown(message_type)

    def _apply_player_list(self, payload: Mapping[str, Any]) -> None:
        raw_players = payload.get("players")
        if not isinstance(raw_players, (list, tuple)):
            self._append_malformed("player.list")
            return
        parsed: dict[str, PlayerView] = {}
        for raw in raw_players:
            if not isinstance(raw, Mapping):
                self._append_malformed("player.list")
                return
            player_id = _required_string(raw, "player_id")
            display_name = raw.get("display_name")
            if player_id is None or not isinstance(display_name, str) or player_id in parsed:
                self._append_malformed("player.list")
                return
            parsed[player_id] = PlayerView(player_id, display_name)
        self.players = parsed
        self._rebuild_players()

    def _apply_deaths(self, payload: Mapping[str, Any]) -> None:
        raw_deaths = payload.get("deaths")
        if not isinstance(raw_deaths, (list, tuple)):
            self._append_malformed("player.deaths")
            return
        parsed: dict[str, DeathView] = {}
        for raw in raw_deaths:
            if not isinstance(raw, Mapping):
                self._append_malformed("player.deaths")
                return
            player_id = _required_string(raw, "player_id")
            day = _optional_int(raw, "day")
            cause = _optional_string(raw, "public_cause")
            if player_id is None or day is _INVALID or cause is _INVALID:
                self._append_malformed("player.deaths")
                return
            parsed[player_id] = DeathView(player_id, day, cause if isinstance(cause, (str, type(None))) else None)
        self.deaths = parsed
        self._rebuild_players()

    def _apply_action_state(self, payload: Mapping[str, Any]) -> None:
        phase = _required_string(payload, "phase")
        day = payload.get("day")
        phase_ends_at = _optional_int(payload, "phase_ends_at")
        actions = payload.get("actions")
        if phase is None or not _is_int(day) or phase_ends_at is _INVALID or not isinstance(actions, (list, tuple)):
            self._append_malformed("player.action_state")
            return
        self.phase = PhaseView(phase, day, phase_ends_at if isinstance(phase_ends_at, (int, type(None))) else None)

    def _apply_game_event_container(self, payload: Mapping[str, Any]) -> bool:
        event_type = payload.get("event_type")
        event_payload = payload.get("event_payload")
        if not isinstance(event_type, str) or not event_type or not isinstance(event_payload, Mapping):
            self._append_malformed("game.event")
            return True
        return self._apply_core_event(event_type, event_payload)

    def _apply_chat(self, payload: Mapping[str, Any]) -> bool:
        channel = _required_string(payload, "channel")
        message = payload.get("message")
        if channel is None or not isinstance(message, Mapping):
            self._append_malformed("chat.message")
            return True
        text = message.get("message")
        if not isinstance(text, str):
            text = message.get("text")
        player_id = message.get("player_id")
        display_name = message.get("display_name")
        if not isinstance(text, str):
            self._append_malformed("chat.message")
            return True
        if player_id is not None and (not isinstance(player_id, str) or not player_id):
            self._append_malformed("chat.message")
            return True
        if display_name is not None and not isinstance(display_name, str):
            self._append_malformed("chat.message")
            return True
        self._append(
            ChatRecord(
                order=self._take_order(),
                day=self.phase.day if self.phase else None,
                phase=self.phase.phase if self.phase else None,
                channel=channel,
                player_id=player_id,
                display_name=display_name,
                message=text,
            )
        )
        return True

    def _apply_core_event(self, event_type: str, payload: Mapping[str, Any]) -> bool:
        if event_type not in KNOWN_CORE_TYPES:
            self._append_unknown(event_type)
            return True
        if event_type not in TYPED_CORE_TYPES:
            self.known_unmodeled_event_count += 1
            self._append(KnownUnmodeledEventRecord(self._take_order(), event_type))
            return True
        day = self._context_day(payload)
        phase = self._context_phase(payload)
        if event_type == "CO_DECLARED":
            player_id = _required_string(payload, "player_id")
            claimed = _required_string(payload, "claimed_role_id")
            comment = payload.get("comment")
            if player_id is None or claimed is None or not isinstance(comment, str):
                return self._malformed_core(event_type)
            self._append(CoDeclarationRecord(self._take_order(), day, phase, player_id, claimed, comment))
        elif event_type == "CO_REPORTED":
            player_id = _required_string(payload, "player_id")
            kind = _required_string(payload, "kind")
            target = _required_string(payload, "target_player_id")
            result = _required_string(payload, "claimed_result")
            if None in (player_id, kind, target, result):
                return self._malformed_core(event_type)
            self._append(CoReportRecord(self._take_order(), day, phase, player_id, kind, target, result))
        elif event_type == "VOTE_RESOLVED":
            result = _required_string(payload, "result")
            tallies = payload.get("tallies")
            context = _required_day_phase(payload)
            lynched = _required_nullable_string(payload, "lynched_player_id")
            runoff = payload.get("runoff_candidate_player_ids")
            if result is None or context is None or not isinstance(tallies, Mapping) or lynched is _INVALID or not isinstance(runoff, (list, tuple)):
                return self._malformed_core(event_type)
            if not all(isinstance(key, str) and _is_int(value) for key, value in tallies.items()):
                return self._malformed_core(event_type)
            if not all(isinstance(item, str) for item in runoff):
                return self._malformed_core(event_type)
            self._append(VoteResultRecord(self._take_order(), context[0], context[1], result, dict(tallies), lynched, tuple(runoff)))
        elif event_type == "VOTE_REVEALED_LIVE":
            voter = _required_string(payload, "voter_player_id")
            target = _required_nullable_string(payload, "target_player_id")
            context = _required_day_phase(payload)
            if voter is None or target is _INVALID or context is None:
                return self._malformed_core(event_type)
            self._append(VoteRevealRecord(self._take_order(), context[0], context[1], voter, target, ()))
        elif event_type == "VOTES_REVEALED_AFTER":
            context = _required_day_phase(payload)
            raw_votes = payload.get("votes")
            if context is None or not isinstance(raw_votes, (list, tuple)):
                return self._malformed_core(event_type)
            votes: list[VoteEntry] = []
            for raw_vote in raw_votes:
                if not isinstance(raw_vote, Mapping):
                    return self._malformed_core(event_type)
                voter = _required_string(raw_vote, "voter_player_id")
                target = _required_nullable_string(raw_vote, "target_player_id")
                if voter is None or target is _INVALID:
                    return self._malformed_core(event_type)
                votes.append(VoteEntry(voter, target))
            self._append(VoteRevealRecord(self._take_order(), context[0], context[1], final_votes=tuple(votes)))
        elif event_type == "PLAYER_DIED":
            player_id = _required_string(payload, "player_id")
            public_cause = _optional_string(payload, "public_cause")
            if player_id is None or public_cause is _INVALID or not _is_int(payload.get("day")):
                return self._malformed_core(event_type)
            day = payload["day"]
            death = DeathView(player_id, day, public_cause)
            self.deaths[player_id] = death
            self._rebuild_players()
            self._append(DeathRecord(self._take_order(), day, phase, player_id, public_cause))
        elif event_type == "PHASE_STARTED":
            event_phase = _required_string(payload, "phase")
            event_day = payload.get("day")
            ends = _optional_int(payload, "phase_ends_at")
            if event_phase is None or not _is_int(event_day) or ends is _INVALID:
                return self._malformed_core(event_type)
            self.phase = PhaseView(event_phase, event_day, ends)
            self._append(PhaseTransitionRecord(self._take_order(), event_day, event_phase, ends))
        elif event_type in {"DAY_EXTENDED", "DAY_SHORTENED"}:
            ends = _required_nullable_int(payload, "phase_ends_at")
            event_day = payload.get("day")
            event_phase = _required_string(payload, "phase")
            used = _optional_int(payload, "extensions_used")
            if ends is _INVALID or not _is_int(event_day) or event_phase is None or used is _INVALID:
                return self._malformed_core(event_type)
            if self.phase is not None and self.phase.day == event_day and self.phase.phase == event_phase:
                self.phase = PhaseView(event_phase, event_day, ends)
            self._append(PhaseTimingChangedRecord(self._take_order(), event_day, event_phase, event_type, ends, used))
        elif event_type == "GAME_CREATED":
            game_id = _required_string(payload, "game_id")
            event_day = payload.get("day")
            event_phase = _required_string(payload, "phase")
            raw_players = payload.get("players")
            if game_id is None or not _is_int(event_day) or event_phase is None or not isinstance(raw_players, (list, tuple)):
                return self._malformed_core(event_type)
            players: list[PlayerView] = []
            for raw in raw_players:
                if not isinstance(raw, Mapping):
                    return self._malformed_core(event_type)
                player_id = _required_string(raw, "player_id")
                display_name = raw.get("display_name")
                if player_id is None or not isinstance(display_name, str):
                    return self._malformed_core(event_type)
                players.append(PlayerView(player_id, display_name))
            self.players = {player.player_id: player for player in players}
            self._rebuild_players()
            self._append(GameLifecycleRecord(self._take_order(), event_day, event_phase, event_type, game_id=game_id, players=tuple(self.players.values())))
        elif event_type == "GAME_ENDED":
            winner = _optional_string(payload, "winner_team")
            outcome = _required_string(payload, "outcome")
            results = payload.get("player_results")
            if winner is _INVALID or outcome is None or not isinstance(results, Mapping):
                return self._malformed_core(event_type)
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in results.items()):
                return self._malformed_core(event_type)
            self._append(GameLifecycleRecord(self._take_order(), day, phase, event_type, winner_team_id=winner, outcome_id=outcome, player_results=dict(results)))
        elif event_type in {"INSPECT_RESULT", "MEDIUM_RESULT", "INSPECT_DEAD_ROLE_RESULT", "GUARD_SUCCEEDED"}:
            target = _required_string(payload, "target_player_id")
            if target is None:
                return self._malformed_core(event_type)
            result = _required_nullable_string(payload, "result")
            role = _required_nullable_string(payload, "role_id")
            if event_type in {"INSPECT_RESULT", "MEDIUM_RESULT"} and result is _INVALID:
                return self._malformed_core(event_type)
            if event_type == "INSPECT_DEAD_ROLE_RESULT" and (role is _INVALID or role is None):
                return self._malformed_core(event_type)
            self._append(AbilityResultRecord(self._take_order(), day, phase, event_type, target, result if isinstance(result, (str, type(None))) else None, role if isinstance(role, (str, type(None))) else None))
        elif event_type == "PUBLIC_NOTIFY":
            notify_id = _required_string(payload, "notify_id")
            if notify_id is None:
                return self._malformed_core(event_type)
            self._append(PublicNotifyRecord(self._take_order(), day, phase, notify_id))
        elif event_type == "TIE_RESOLVED_RANDOM":
            candidates = payload.get("candidate_player_ids")
            selected = _required_string(payload, "selected_player_id")
            if not isinstance(candidates, (list, tuple)) or selected is None or not all(isinstance(item, str) for item in candidates):
                return self._malformed_core(event_type)
            self._append(TieResolvedRandomRecord(self._take_order(), day, phase, tuple(candidates), selected))
        self._update_context_from_payload(event_type, payload)
        return True

    def _update_context_from_payload(self, event_type: str, payload: Mapping[str, Any]) -> None:
        # Do not allow a stale timing event to roll back current phase context.
        if event_type == "PHASE_STARTED":
            return
        day = payload.get("day")
        phase = payload.get("phase")
        if self.phase is None and _is_int(day) and isinstance(phase, str) and phase:
            self.phase = PhaseView(phase, day, None)

    def _context_day(self, payload: Mapping[str, Any]) -> int | None:
        value = payload.get("day")
        if _is_int(value):
            return value
        return self.phase.day if self.phase else None

    def _context_phase(self, payload: Mapping[str, Any]) -> str | None:
        value = payload.get("phase")
        if isinstance(value, str) and value:
            return value
        return self.phase.phase if self.phase else None

    def _malformed_core(self, event_type: str) -> bool:
        self._append_malformed(event_type)
        return True

    def _append_unknown(self, event_type: str) -> None:
        self.unknown_event_count += 1
        self._append(UnknownEventRecord(self._take_order(), event_type))

    def _append_malformed(self, event_type: str) -> None:
        self.malformed_event_count += 1
        self._append(MalformedEventRecord(self._take_order(), event_type))

    def _append(self, record: HistoryRecord) -> None:
        self.memory.append(record)

    def _take_order(self) -> int:
        order = self._next_order
        self._next_order += 1
        return order

    def _rebuild_players(self) -> None:
        self.players = {
            player_id: PlayerView(
                player_id=player.player_id,
                display_name=player.display_name,
                alive=player_id not in self.deaths,
                death=self.deaths.get(player_id),
            )
            for player_id, player in self.players.items()
        }
