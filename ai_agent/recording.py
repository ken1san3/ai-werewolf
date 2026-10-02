"""Minimal public transcript and completion checks for agent development runs."""
from collections import Counter
import json
from pathlib import Path
import time

from .repetition import normalize, sentences
from .state import PRIVATE_RESULTS


class Recorder:
    def __init__(self, public_channels):
        self.public_channels = set(public_channels)
        self.started = time.monotonic()
        self.rows, self.rejections = [], []
        self.private_messages, self.private_results, self.tokens = [], [], set()
        self._viewer_counts, self._recorded_counts = Counter(), Counter()

    def core_event(self, event):
        self.rows.append({"t": round(time.monotonic() - self.started, 2),
                          "kind": event.type, "payload": dict(event.payload)})

    def observe(self, viewer, message):
        kind, payload = message["type"], message["payload"]
        if kind == "session.joined":
            self.tokens.add(payload["connection_token"])
        elif kind == "action.rejected":
            self.rejections.append({"player_id": viewer, **payload})
        elif kind == "game.state_sync":
            for entry in payload["history"]:
                self.visible(viewer, entry["type"], entry["payload"])
        else:
            self.visible(viewer, kind, payload)

    def visible(self, viewer, kind, payload):
        if kind not in {"chat.message", "game.event"}:
            return
        if kind == "game.event":
            if payload["event_type"] in PRIVATE_RESULTS:
                self.private_results.append({"player_id": viewer, **payload})
            return
        # Per-viewer occurrence counts preserve real repeated messages while
        # merging copies delivered to multiple recipients.
        signature = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        key = viewer, signature
        self._viewer_counts[key] += 1
        if self._viewer_counts[key] <= self._recorded_counts[signature]:
            return
        self._recorded_counts[signature] = self._viewer_counts[key]
        row = {"t": round(time.monotonic() - self.started, 2), "kind": "chat", **payload}
        if payload["channel"] in self.public_channels:
            self.rows.append(row)
        else:
            self.private_messages.append(row)

    def checks(self, game, agents, errors, calls):
        counts, previous, immediate = Counter(), {}, []
        for row in self.rows:
            if row["kind"] == "chat":
                player, text = row["message"]["player_id"], row["message"]["message"]
            elif row["kind"] == "CO_DECLARED":
                player, text = row["payload"]["player_id"], row["payload"]["comment"]
            else:
                continue
            normalized = normalize(text)
            if previous.get(player) == normalized:
                immediate.append({"player_id": player, "text": text})
            previous[player] = normalized
            counts.update(sentences(text))
        repeated = [{"text": text, "count": count} for text, count in counts.items() if count >= 3]
        return {
            "completed": game.game_result is not None and all(a.state.done for a in agents),
            "winner": game.game_result.winner_team if game.game_result else None,
            "players": len(agents), "finished_agents": sum(a.state.done for a in agents),
            "server_rejections": len(self.rejections), "rejections": self.rejections,
            "crashes": len(errors), "errors": errors,
            "immediate_repetitions": immediate, "sentences_repeated_three_times": repeated,
            "stale_generations_suppressed": sum(a.stale_suppressed for a in agents),
            "llm_calls": calls, "wall_sec": round(time.monotonic() - self.started, 2),
        }

    def save(self, directory, checks):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        lines = ["# Game transcript", "", "Only public server facts and public speech are included.", ""]
        for row in self.rows:
            if row["kind"] == "chat":
                text = f"{row['message']['player_id']}: {row['message']['message']}"
            else:
                text = f"{row['kind']}: {json.dumps(row['payload'], ensure_ascii=False)}"
            lines.append(f"[{row['t']:.2f}s] {text}")
        (directory / "transcript.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        (directory / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
