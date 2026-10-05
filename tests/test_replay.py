"""Offline replay tests: public information, partial files and regeneration."""
import json
import os
from html.parser import HTMLParser
from pathlib import Path

import pytest

from ai_agent.replay import build_replays, discover_games, replace_player_ids
from ai_agent.replay_data import load_replay
from ai_agent.replay_ui import render_replay


SECRETS = (
    "PRIVATE_CHAT_SENTINEL",
    "PRIVATE_RESULT_SENTINEL",
    "PRIVATE_EVENT_SENTINEL",
    "PRIVATE_ABILITY_SENTINEL",
    "PRIVATE_DECISION_SENTINEL",
    "PRIVATE_CHECK_SENTINEL",
    "PRIVATE_UNKNOWN_FIELD_SENTINEL",
)


@pytest.fixture(autouse=True)
def replay_names(tmp_path):
    path = tmp_path / "content" / "replay_names.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("player-0: アオイ\nplayer-1: ユウ\nplayer-10: レン\n", encoding="utf-8")


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _rows(*, completed=True, message="player-1の意見を聞きたいです。"):
    rows = [
        {"t": 0, "kind": "GAME_CREATED", "payload": {
            "game_id": "replay-fixture", "day": 0, "phase": "setup",
            "players": [{"player_id": f"player-{n}", "display_name": f"player-{n}"}
                        for n in (0, 1, 10)],
        }},
        {"t": 1, "kind": "PHASE_STARTED", "payload": {
            "day": 1, "phase": "day", "phase_ends_at": 100,
        }},
        {"t": 2, "kind": "chat", "channel": "public", "message": {
            "player_id": "player-0", "display_name": "player-0", "message": message,
        }},
        {"t": 3, "kind": "CO_DECLARED", "payload": {
            "player_id": "player-1", "claimed_role_id": "seer", "comment": "占い師です。",
        }},
        {"t": 4, "kind": "VOTE_RESOLVED", "payload": {
            "day": 1, "phase": "execution", "result": "lynch", "tallies": {"player-1": 2, "player-10": 1},
            "lynched_player_id": "player-1",
        }},
        {"t": 5, "kind": "PLAYER_DIED", "payload": {
            "day": 1, "player_id": "player-1", "public_cause": "lynched",
        }},
    ]
    if completed:
        rows.append({"t": 6, "kind": "GAME_ENDED", "payload": {
            "winner_team": "village", "outcome": "team_victory",
            "player_results": {"player-0": "won", "player-1": "lost", "player-10": "won"},
        }})
    return rows


def _record(directory, *, completed=True, message="player-1の意見を聞きたいです。", secrets=False):
    rows = _rows(completed=completed, message=message)
    record = {
        "rows": rows,
        "roles": {"player-0": "seer", "player-1": "werewolf", "player-10": "villager"},
    }
    if secrets:
        # These are deliberately outside the public recording boundary.
        record.update({
            "private_messages": [{"kind": "chat", "channel": "wolf", "message": {
                "player_id": "player-1", "message": SECRETS[0],
            }}],
            "private_results": [{"event_type": "INSPECT_RESULT", "visibility": "private",
                                 "event_payload": {"result": SECRETS[1]}}],
            "accepted_abilities": [{"player_id": "player-0", "secret": SECRETS[3]}],
        })
        rows.extend([
            {"t": 2.1, "kind": "chat", "channel": "wolf", "message": {
                "player_id": "player-1", "message": SECRETS[0],
            }},
            {"t": 2.2, "kind": "INSPECT_RESULT", "visibility": "private",
             "payload": {"result": SECRETS[2]}},
            {"t": 2.3, "kind": "PLAYER_DIED", "visibility": "server", "payload": {
                "player_id": "player-0", "day": 1, "public_cause": SECRETS[2],
            }},
        ])
        rows[1]["payload"]["unknown_secret"] = SECRETS[6]
        rows[2]["message"]["private_reason"] = SECRETS[6]
        _write_json(directory / "decisions.json", [{"reason": SECRETS[4]}])
    _write_json(directory / "server_record.json", record)
    _write_json(directory / "checks.json", {
        "completed": completed, "crashes": 0, "wall_sec": 6,
        "settings": {"seed": 1, "day_seconds": 90, "clock_rate": 1},
        "private_copy_candidates": [{"body": SECRETS[5]}],
    })
    return directory


class _ReplayJSON(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.active = tag == "script" and dict(attrs).get("id") == "replay-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)


def _embedded_data(html):
    parser = _ReplayJSON()
    parser.feed(html)
    assert parser.parts, "The page must contain the replay-data JSON script."
    return json.loads("".join(parser.parts))


def _game_html(summary):
    assert len(summary["matches"]) == 1
    return Path(summary["matches"][0]["html_path"])


def test_private_fields_and_events_never_enter_replay_or_html(tmp_path):
    directory = _record(tmp_path / "games" / "private_boundary", secrets=True)
    data = load_replay(directory)
    assert data["mode"] == "server_record"
    assert data["public_messages"] == 1
    assert {p["id"] for p in data["players"]} == {"player-0", "player-1", "player-10"}
    assert all(set(p) == {"id"} for p in data["players"])
    assert not any(e["kind"] == "INSPECT_RESULT" for e in data["events"])
    html = render_replay(data)
    summary = build_replays(tmp_path, output=tmp_path / "html")
    for text in (json.dumps(data), html, _game_html(summary).read_text(encoding="utf-8"),
                 summary["index"].read_text(encoding="utf-8")):
        assert all(secret not in text for secret in SECRETS)


def test_true_roles_exist_only_in_game_ended_payload(tmp_path):
    data = load_replay(_record(tmp_path / "finished"))
    ended = [e for e in data["events"] if e["kind"] == "GAME_ENDED"]
    assert len(ended) == 1
    assert "roles" not in data
    assert all("roles" not in e["payload"] for e in data["events"] if e["kind"] != "GAME_ENDED")
    assert all(set(p) == {"id"} for p in data["players"])
    assert ended[0]["payload"]["roles"]["player-0"] == {"role": "占い師", "result": "won"}
    assert ended[0]["payload"]["roles"]["player-1"] == {"role": "人狼", "result": "lost"}
    embedded = _embedded_data(render_replay(data))
    assert embedded["events"] == data["events"]


def test_incomplete_record_never_reveals_true_roles(tmp_path):
    data = load_replay(_record(tmp_path / "interrupted", completed=False))
    assert not data["completed"]
    assert data["status"] == "途中"
    assert not any(e["kind"] == "GAME_ENDED" for e in data["events"])
    assert "roles" not in json.dumps(data)
    assert "人狼" not in json.dumps(data, ensure_ascii=False)
    assert all("roles" not in e["payload"] for e in _embedded_data(render_replay(data))["events"])


def test_crashed_agents_are_failed_even_when_server_published_game_end(tmp_path):
    directory = _record(tmp_path / "failed")
    _write_json(directory / "checks.json", {"completed": False, "crashes": 3, "wall_sec": 6})
    data = load_replay(directory)
    assert data["status"] == "失敗"
    assert "3件" in data["reason"]
    assert data["completed"]  # The public end screen remains available.
    assert any(event["kind"] == "GAME_ENDED" for event in data["events"])


def test_transcript_fallback_reads_public_timed_events(tmp_path):
    directory = tmp_path / "transcript_only"
    directory.mkdir()
    (directory / "server_record.json").write_text("{broken", encoding="utf-8")
    text = "# ゲームの書き起こし\n\n"
    for row in _rows():
        body = (f"{row['message']['player_id']}: {row['message']['message']}"
                if row["kind"] == "chat"
                else f"{row['kind']}: {json.dumps(row['payload'], ensure_ascii=False)}")
        text += f"[{row['t']:.2f}s] {body}\n\n"
    text += f"[2.50s] INSPECT_RESULT: {{\"result\": \"{SECRETS[2]}\"}}\n"
    (directory / "transcript.md").write_text(text, encoding="utf-8")
    data = load_replay(directory)
    assert data["mode"] == "transcript"
    assert data["completed"]
    assert data["winner"] == "village"
    assert data["duration"] == 6
    assert data["public_messages"] == 1
    assert data["players"]
    assert SECRETS[2] not in render_replay(data)


@pytest.mark.parametrize("layout", ("direct", "nested"))
def test_log_only_sibling_is_partial_and_does_not_load_request(tmp_path, layout):
    games = tmp_path / "games" if layout == "direct" else tmp_path / "runs" / "experiment" / "games"
    games.mkdir(parents=True)
    directory = games / "B_learn_0007"
    directory.with_suffix(".log").write_text(
        "PHASE_STARTED {'phase': 'day', 'day': 1, 'phase_ends_at': 100}\n"
        "CHAT player-0: player-1さん、理由を教えてください。\n"
        "CHAT player-1: 公開発言への回答です。\n"
        "PHASE_STARTED {'phase': 'vote', 'day': 1, 'phase_ends_at': 130}\n"
        f"INSPECT_RESULT {{'result': '{SECRETS[2]}'}}\n",
        encoding="utf-8",
    )
    _write_json(games / "B_learn_0007_request.json", {"notes": SECRETS[4]})
    assert directory in discover_games(tmp_path)
    data = load_replay(directory)
    assert data["mode"] == "log"
    assert data["status"] == "途中"
    assert not data["completed"]
    assert data["public_messages"] == 2
    assert "roles" not in json.dumps(data)
    assert all(secret not in render_replay(data) for secret in SECRETS)


def test_discovery_finds_both_game_tree_layouts_without_duplicates(tmp_path):
    direct = _record(tmp_path / "games" / "direct")
    nested = _record(tmp_path / "runs" / "experiment" / "games" / "nested")
    # A sibling log for an existing directory must not create a second match.
    direct.with_suffix(".log").write_text("CHAT player-0: 公開です。\n", encoding="utf-8")
    _record(tmp_path / "unrelated" / "not_a_game")
    assert set(discover_games(tmp_path)) == {direct, nested}


def test_unchanged_sources_preserve_game_html_mtime(tmp_path):
    _record(tmp_path / "games" / "stable")
    output = tmp_path / "html"
    first = build_replays(tmp_path, output=output)
    assert first["generated"] == 1
    html = _game_html(first)
    os.utime(html, ns=(1_000_000_000, 1_000_000_000))
    before = html.stat().st_mtime_ns
    second = build_replays(tmp_path, output=output)
    assert second["generated"] == 0
    assert second["unchanged"] == 1
    assert html.stat().st_mtime_ns == before


def test_resumed_source_change_regenerates_html(tmp_path):
    directory = _record(tmp_path / "games" / "resumed", completed=False)
    output = tmp_path / "html"
    first = build_replays(tmp_path, output=output)
    html = _game_html(first)
    before = html.read_text(encoding="utf-8")
    os.utime(html, ns=(1_000_000_000, 1_000_000_000))
    _record(directory, completed=True, message="再開後に確定した公開発言です。")
    second = build_replays(tmp_path, output=output)
    assert second["generated"] == 1
    assert second["unchanged"] == 0
    assert html.stat().st_mtime_ns != 1_000_000_000
    assert html.read_text(encoding="utf-8") != before
    assert "再開後に確定した公開発言です。" in html.read_text(encoding="utf-8")
    assert second["playable"] == 1


def test_name_setting_change_regenerates_html(tmp_path):
    _record(tmp_path / "games" / "renamed")
    output = tmp_path / "html"
    first = build_replays(tmp_path, output=output)
    assert _embedded_data(_game_html(first).read_text(encoding="utf-8"))["players"][0]["name"] == "アオイ"
    (tmp_path / "content" / "replay_names.yaml").write_text("player-0: ミナト\n", encoding="utf-8")
    second = build_replays(tmp_path, output=output)
    assert second["generated"] == 1
    assert second["unchanged"] == 0
    players = _embedded_data(_game_html(second).read_text(encoding="utf-8"))["players"]
    assert next(player for player in players if player["id"] == "player-0")["name"] == "ミナト"


def test_script_and_html_injection_is_escaped_without_losing_public_text(tmp_path):
    message = '</script><script>window.REPLAY_SENTINEL=1</script><img src=x onerror="alert(1)">&\u2028\u2029'
    data = load_replay(_record(tmp_path / "injection", message=message))
    html = render_replay(data, index_href='index.html"><img src=x onerror="alert(2)">')
    assert "<script>window.REPLAY_SENTINEL" not in html
    assert "<img src=x" not in html
    assert "\u2028" not in html
    assert "\u2029" not in html
    chats = [e for e in _embedded_data(html)["events"] if e["kind"] == "chat"]
    assert chats[0]["payload"]["message"] == message


def test_player_id_replacement_uses_complete_id_boundaries():
    names = {"player-1": "一郎", "player-10": "十郎"}
    text = "player-1 player-10 player-100 xplayer-1 player-1x player-1さん"
    assert replace_player_ids(text, names) == "一郎 十郎 player-100 xplayer-1 player-1x 一郎さん"


def test_missing_public_artifacts_are_reported_unavailable(tmp_path):
    directory = tmp_path / "games" / "empty"
    directory.mkdir(parents=True)
    _write_json(directory / "decisions.json", [{"reason": SECRETS[4]}])
    data = load_replay(directory)
    assert data["mode"] == "none"
    assert data["events"] == []
    assert data["reason"]
    summary = build_replays(tmp_path, output=tmp_path / "html")
    assert summary["total"] == 1
    assert summary["unavailable"] == 1
    assert summary["playable"] == 0
    assert SECRETS[4] not in summary["index"].read_text(encoding="utf-8")


@pytest.fixture
def existing_recorded_game():
    directory = (Path(__file__).resolve().parents[1] / "runs" / "stage3_20261004_predeparture"
                 / "games" / "A_qwen35-9b_off")
    if not (directory / "server_record.json").is_file():
        pytest.skip("An existing recorded game is optional; no game or model is started.")
    return directory


def test_existing_game_renders_html_without_running_model(existing_recorded_game, tmp_path):
    data = load_replay(existing_recorded_game)
    assert data["completed"]
    assert len(data["players"]) == 9
    assert data["public_messages"] > 0
    html = render_replay(data)
    destination = tmp_path / "existing_game.html"
    destination.write_text(html, encoding="utf-8")
    embedded = _embedded_data(destination.read_text(encoding="utf-8"))
    assert embedded["events"] == data["events"]
    assert any(event["kind"] == "GAME_ENDED" for event in embedded["events"])
    assert "<html" in html.lower()
