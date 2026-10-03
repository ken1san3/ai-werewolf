"""Public protocol transcript, private comparisons and mechanical checks."""
from collections import Counter
import json
from pathlib import Path
import time

from .checks import redact, text_checks, timing_summary
from .metrics import measure, mechanical_conditions, night_private_activity


class Recorder:
    def __init__(self, public_channels, public_viewer="player-0", progress=False):
        self.public_channels = set(public_channels)
        self.started = time.monotonic()
        self.public_viewer, self.progress = public_viewer, progress
        self.rows, self.rejections = [], []
        self.private_messages, self.private_results, self.tokens = [], [], set()
        self._viewer_counts, self._recorded_counts = Counter(), Counter()

    def core_event(self, event):
        # GAME_CREATED precedes player assignment and is absent from client
        # histories. All subsequent public rows follow the observer's seq order.
        if event.type == "GAME_CREATED":
            self.rows.append({"t": 0, "kind": event.type, "payload": dict(event.payload)})

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
            if payload.get("visibility") == "private" and payload["event_type"] != "ROLE_ASSIGNED":
                self.private_results.append({"t": round(time.monotonic() - self.started, 2),
                                             "player_id": viewer, **payload})
            elif payload.get("visibility") == "public" and viewer == self.public_viewer:
                self.rows.append({"t": round(time.monotonic() - self.started, 2),
                                  "kind": payload["event_type"], "payload": payload["event_payload"]})
            return
        if payload["channel"] in self.public_channels:
            if viewer == self.public_viewer:
                self.rows.append({"t": round(time.monotonic() - self.started, 2), "kind": "chat", **payload})
                if self.progress:
                    print(redact(f"CHAT {payload['message']['player_id']}: {payload['message']['message']}", self.tokens), flush=True)
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
        self.private_messages.append(row)

    def checks(self, game, agents, errors, calls):
        roles = {p.player_id: p.role for p in game.players.values()}
        text_result = text_checks(self.rows, self.private_messages, self.private_results, self.tokens,
                                  roles)
        # Timings contain no prompts or model-private information, even when a
        # test LLM keeps richer debug records in its own calls collection.
        safe_calls = [{key: call[key] for key in ("player_id", "purpose", "total_sec", "generation_sec",
                      "wait_sec", "prompt_tokens", "completion_tokens", "http_status", "http_error", "completed") if key in call} for call in calls]
        discards = Counter()
        for agent in agents:
            discards.update(agent.speech_discards)
        for reason in ("japanese_check", "own_previous_sentence", "third_sentence", "similarity", "phase_expired", "unjustified_self_disclosure", "invalid_decision_json", "self_id_confusion", "own_result_conflict", "private_body_copy", "private_chat_budget", "meta_refusal", "self_fact_confusion", "dead_player_address", "empty_agreement"):
            discards.setdefault(reason, 0)
        measurements = measure(self.rows, self.private_results, roles, game.content.roles, game.rules, safe_calls,
                               generated=sum(a.speech_generations for a in agents), discards=dict(discards))
        self.server_record = redact({"rows": self.rows, "roles": {p: r.id for p, r in roles.items()},
                                     "private_results": self.private_results, "private_messages": self.private_messages,
                                     "accepted_abilities": [{"player_id": a.state.player_id, **action} for a in agents for action in a.state.own_actions]}, self.tokens)
        self.decisions = redact([{**{k: v for k, v in d.items() if k != "at_monotonic"},
                                   "t": round(d["at_monotonic"] - self.started, 2)} for a in agents for d in a.decisions], self.tokens)
        checks = {
            "completed": game.game_result is not None and all(a.state.done for a in agents),
            "winner": game.game_result.winner_team if game.game_result else None,
            "outcome": game.game_result.outcome if game.game_result else None,
            "players": len(agents), "finished_agents": sum(a.state.done for a in agents),
            "server_rejections": len(self.rejections), "rejections": self.rejections,
            "crashes": len(errors), "errors": errors,
            **text_result,
            **redact(measurements, self.tokens),
            "strategic_disclosure_review": {"status": "pending", "leaks": None,
                                            "uncertain_candidates": [],
                                            "note": "Codex classifies provisionally without stopping; uncertain cases are collected for CP3."},
            "public_messages": sum(row["kind"] == "chat" for row in self.rows),
            "private_messages_compared": len(self.private_messages),
            "private_results_compared": len(self.private_results),
            "night_private_activity": night_private_activity(self.rows, self.private_messages, roles),
            "authentication_tokens_compared": len(self.tokens),
            "stale_generations_suppressed": sum(a.stale_suppressed for a in agents),
            "llm_calls": safe_calls,
            "llm_http_errors": sum(bool(c.get("http_error")) or (c.get("http_status") is not None and c["http_status"] >= 400) for c in safe_calls),
            "decision_records": len(self.decisions), "decision_record_path": "decisions.json",
            "generation_sec": timing_summary([c for c in safe_calls if c.get("completed", True)], "generation_sec"),
            "wait_sec": timing_summary(safe_calls, "wait_sec"),
            "wall_sec": round(time.monotonic() - self.started, 2),
        }
        checks["mechanical_conditions"] = mechanical_conditions(checks)
        return checks

    def save(self, directory, checks):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        lines = ["# ゲームの書き起こし", "公開されたサーバの事実と発言だけを記録しています。"]
        for row in self.rows:
            if row["kind"] == "chat":
                text = f"{row['message']['player_id']}: {row['message']['message']}"
            else:
                text = f"{row['kind']}: {json.dumps(row['payload'], ensure_ascii=False)}"
            lines.append(redact(f"[{row['t']:.2f}s] {text}", self.tokens))
        (directory / "transcript.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        (directory / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if hasattr(self, "server_record"):
            (directory / "server_record.json").write_text(json.dumps(self.server_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (directory / "decisions.json").write_text(json.dumps(self.decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
