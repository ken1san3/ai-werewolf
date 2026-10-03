"""Only information delivered to this player's authenticated connection."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time


@dataclass
class PlayerState:
    player_id: str
    role_id: str | None = None
    teammates: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    alive: set[str] = field(default_factory=set)
    private: list[dict] = field(default_factory=list)
    own_actions: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    chats: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    phase: str | None = None
    day: int = 0
    phase_ends_at: int | None = None
    server_offset: float = 0
    done: bool = False

    @property
    def phase_key(self):
        return self.day, self.phase, self.phase_ends_at

    def seconds_left(self):
        if self.phase_ends_at is None:
            return 0
        return self.phase_ends_at - (time.monotonic() + self.server_offset)

    def action(self, kind):
        return next((a for a in self.actions if a["type"] == kind), None)

    def receive(self, message):
        if isinstance(message.get("timestamp"), int):
            # Server timestamps are integer seconds. Use the upper bound so
            # quantization cannot make a nearly expired action look fresh.
            self.server_offset = message["timestamp"] + 1 - time.monotonic()
        kind, payload = message["type"], message["payload"]
        if kind == "game.state_sync":
            if payload["self"]["player_id"] != self.player_id:
                raise ValueError("snapshot belongs to another player")
            self.role_id = payload["self"]["role_id"]
            self.players = [p["player_id"] for p in payload["players"]]
            self.alive = set(self.players)
            self.teammates, self.private, self.facts, self.chats = [], [], [], []
            for entry in payload["history"]:
                self._visible(entry["type"], entry["payload"])
            self.alive -= {d["player_id"] for d in payload["deaths"]}
            self._actions(payload["action_state"])
        elif kind == "player.action_state":
            self._actions(payload)
        elif kind == "player.list":
            self.players = [p["player_id"] for p in payload["players"]]
        elif kind == "player.deaths":
            self.alive -= {d["player_id"] for d in payload["deaths"]}
        else:
            self._visible(kind, payload)

    def _actions(self, payload):
        self.actions = payload["actions"]
        self.phase, self.day = payload["phase"], payload["day"]
        self.phase_ends_at = payload["phase_ends_at"]
        if self.phase == "game_end":
            self.done = True

    def _visible(self, kind, payload):
        if kind == "chat.message":
            self.chats.append({"day": self.day, "channel": payload["channel"], **payload["message"]})
        elif kind == "game.event":
            event, data = payload["event_type"], payload["event_payload"]
            if event == "ROLE_ASSIGNED":
                if data["player_id"] != self.player_id:
                    raise ValueError("role assignment belongs to another player")
                self.role_id = data["role_id"]
                self.teammates = list(data.get("teammate_player_ids", []))
            elif payload.get("visibility") != "public":
                self.private.append({"type": event, "received_day": self.day, "received_phase": self.phase, **data})
            else:
                self.facts.append({"type": event, **data})
                if event == "PHASE_STARTED":
                    self.actions = []
                    self.phase, self.day = data["phase"], data["day"]
                    self.phase_ends_at = data.get("phase_ends_at")
                elif event == "PLAYER_DIED":
                    self.alive.discard(data["player_id"])
                elif event == "GAME_ENDED":
                    self.done = True

    def private_text(self):
        return json.dumps(self.private, ensure_ascii=False)
