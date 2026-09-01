"""Immutable snapshot state and action handles for the Network Client."""

from __future__ import annotations

from typing import Any, Mapping

from .types import (
    AbilityAction,
    Action,
    ChatAction,
    ClientLifecycle,
    ClientSnapshot,
    CoDeclareAction,
    CoReportAction,
    VoteAction,
    immutable_mapping,
    immutable_value,
)


class ClientState:
    """Receiver-owned mutable state which exposes only immutable snapshots."""

    def __init__(self) -> None:
        self._lifecycle = ClientLifecycle.NEW
        self._player_id: str | None = None
        self._last_seq = 0
        self._snapshot_generation = 0
        self._connection_generation = 0
        self._action_generation = 0
        self._players: tuple[Mapping[str, Any], ...] = ()
        self._deaths: tuple[Mapping[str, Any], ...] = ()
        self._action_state: Mapping[str, Any] | None = None
        self._self_info: Mapping[str, Any] | None = None
        self._revealed_roles: tuple[Mapping[str, Any], ...] = ()
        self._history: tuple[Mapping[str, Any], ...] = ()
        self._state_sync: Mapping[str, Any] | None = None
        self._player_list: Mapping[str, Any] | None = None
        self._player_deaths: Mapping[str, Any] | None = None
        self._actions: tuple[Action, ...] = ()

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def connection_generation(self) -> int:
        return self._connection_generation

    @property
    def action_generation(self) -> int:
        return self._action_generation

    def set_last_seq(self, sequence: int) -> None:
        self._last_seq = sequence

    def set_lifecycle(self, lifecycle: ClientLifecycle) -> None:
        self._lifecycle = lifecycle

    def begin_connection(self, generation: int) -> None:
        self._connection_generation = generation
        self.invalidate_actions()

    def invalidate_actions(self) -> None:
        self._action_generation += 1
        self._actions = ()

    def apply_server_event(self, message: Mapping[str, Any]) -> None:
        message_type = message["type"]
        payload = message["payload"]
        if message_type in {
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
        }:
            self._snapshot_generation += 1
        if message_type == "session.joined":
            self._player_id = payload["player_id"]
        elif message_type == "session.resumed":
            self._player_id = payload["player_id"]
        elif message_type == "game.state_sync":
            self._apply_state_sync(payload)
        elif message_type == "player.list":
            self._player_list = immutable_mapping(payload)
            self._players = tuple(immutable_value(item) for item in payload["players"])
        elif message_type == "player.deaths":
            self._player_deaths = immutable_mapping(payload)
            self._deaths = tuple(immutable_value(item) for item in payload["deaths"])
        elif message_type == "player.action_state":
            self._set_action_state(payload)

    def _apply_state_sync(self, payload: Mapping[str, Any]) -> None:
        self._state_sync = immutable_mapping(payload)
        self._players = tuple(immutable_value(item) for item in payload["players"])
        self._deaths = tuple(immutable_value(item) for item in payload["deaths"])
        self._player_list = immutable_mapping({"players": payload["players"]})
        self._player_deaths = immutable_mapping({"deaths": payload["deaths"]})
        self._self_info = immutable_mapping(payload["self"])
        self._revealed_roles = tuple(immutable_value(item) for item in payload["revealed_roles"])
        self._history = tuple(immutable_value(item) for item in payload["history"])
        self._set_action_state(payload["action_state"])

    def _set_action_state(self, payload: Mapping[str, Any]) -> None:
        self._action_state = immutable_mapping(payload)
        self._action_generation += 1
        phase = payload["phase"]
        day = payload["day"]
        base = {
            "connection_generation": self._connection_generation,
            "action_generation": self._action_generation,
            "phase": phase,
            "day": day,
        }
        actions: list[Action] = []
        for raw_action in payload["actions"]:
            action_type = raw_action["type"]
            if action_type == "ability":
                actions.append(
                    AbilityAction(
                        **base,
                        type=action_type,
                        ability_id=raw_action["ability_id"],
                        description=raw_action["description"],
                        valid_targets=tuple(raw_action["valid_targets"]),
                        target_count=raw_action["target_count"],
                        uses_remaining=raw_action["uses_remaining"],
                    )
                )
            elif action_type == "chat":
                actions.append(ChatAction(**base, type=action_type, channel=raw_action["channel"]))
            elif action_type == "vote":
                actions.append(
                    VoteAction(
                        **base,
                        type=action_type,
                        valid_targets=tuple(raw_action["valid_targets"]),
                        target_count=raw_action["target_count"],
                        allows_abstain=raw_action["allows_abstain"],
                    )
                )
            elif action_type == "co_declare":
                actions.append(
                    CoDeclareAction(
                        **base,
                        type=action_type,
                        claimed_role_ids=tuple(raw_action["claimed_role_ids"]),
                    )
                )
            elif action_type == "co_report":
                actions.append(CoReportAction(**base, type=action_type))
        self._actions = tuple(actions)

    def snapshot(self) -> ClientSnapshot:
        return ClientSnapshot(
            lifecycle=self._lifecycle,
            player_id=self._player_id,
            last_seq=self._last_seq,
            players=self._players,
            deaths=self._deaths,
            action_state=self._action_state,
            self_info=self._self_info,
            revealed_roles=self._revealed_roles,
            history=self._history,
            actions=self._actions,
            generation=self._snapshot_generation,
            connection_generation=self._connection_generation,
            action_generation=self._action_generation,
            state_sync=self._state_sync,
            player_list=self._player_list,
            player_deaths=self._player_deaths,
        )
