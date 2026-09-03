from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server.aiwolf_core import (
    GamePhase,
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, WebSocketGameServer, monotonic_seconds
from server.aiwolf_core.rejections import PLAYER_ACTION_REJECTION_REASONS
from tests.fixtures.completion_evidence import (
    ALL_REJECTION_REASONS,
    BOUNDARY_REJECTION_REASONS,
    DEFECT_REJECTION_REASONS,
    EvidenceValidationError,
    STATUS_SCHEMA_VERSION,
    acceptance_quorum,
    assert_acceptance_quorum,
    derive_interrupted_rounds,
    validate_interruption_bounds,
    validate_rejection_vocabulary,
    validate_status,
    validate_stop_marker,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_1_network_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174261"


def _co_acceptance_keys(events: list[object]) -> set[tuple[int, str]]:
    """Recover CO_DECLARED's day from the preceding PHASE_STARTED context."""

    current_day: int | None = None
    accepted: set[tuple[int, str]] = set()
    for event in events:
        if getattr(event, "type", None) == "PHASE_STARTED":
            day = event.payload.get("day")
            if isinstance(day, int) and not isinstance(day, bool):
                current_day = day
        elif getattr(event, "type", None) == "CO_DECLARED":
            if current_day is None:
                raise AssertionError("CO_DECLARED has no PHASE_STARTED day context")
            player_id = event.payload.get("player_id")
            if not isinstance(player_id, str) or not player_id:
                raise AssertionError("CO_DECLARED player_id is missing")
            accepted.add((current_day, player_id))
    return accepted


class PhaseThreeOneCompletionTests(unittest.IsolatedAsyncioTestCase):
    def test_co_acceptance_keys_keep_each_phase_day(self) -> None:
        events = [
            SimpleNamespace(type="PHASE_STARTED", payload={"day": 1}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p0"}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p1"}),
            SimpleNamespace(type="PHASE_STARTED", payload={"day": 2}),
            SimpleNamespace(type="CO_DECLARED", payload={"player_id": "p0"}),
        ]

        self.assertEqual(
            _co_acceptance_keys(events),
            {(1, "p0"), (1, "p1"), (2, "p0")},
        )

    def test_completion_evidence_contract_is_fail_closed(self) -> None:
        self.assertEqual(
            ALL_REJECTION_REASONS,
            PLAYER_ACTION_REJECTION_REASONS
            | {"invalid_action", "game_mismatch", "unsupported_protocol_version"},
        )
        self.assertEqual(len(BOUNDARY_REJECTION_REASONS), 5)
        self.assertEqual(
            BOUNDARY_REJECTION_REASONS | DEFECT_REJECTION_REASONS,
            ALL_REJECTION_REASONS,
        )
        validate_rejection_vocabulary(["action_deadline_passed", "action_unavailable"])
        with self.assertRaises(EvidenceValidationError):
            validate_rejection_vocabulary(["co_limit_reached"])
        with self.assertRaises(EvidenceValidationError):
            validate_rejection_vocabulary(["future_reason"])
        status = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "pid": 123,
            "player_id": "p0",
            "resumed": False,
            "resume_events": [],
            "state_sync_events": [],
            "gap_events": [],
            "action_evidence": [
                {
                    "key": "p0|1|vote|vote",
                    "kind": "vote",
                    "day": 1,
                    "phase": "vote",
                    "action_generation": 1,
                    "after_resume": False,
                    "outcome": "sent",
                }
            ],
            "action_rejections": [],
            "chat_messages_received": [],
            "game_end": True,
            "last_seq": 4,
            "events_received": 1,
            "send_errors": [],
            "server_imports": [],
            "production_import_guard": True,
            "client_exit_reason": "GAME_ENDED",
            "exception_type": None,
            "exception_message": None,
        }
        validate_status(status, expected_player_id="p0")
        invalid_outcome = {**status, "action_evidence": [
            {**status["action_evidence"][0], "outcome": "ignored"}
        ]}
        with self.assertRaises(EvidenceValidationError):
            validate_status(invalid_outcome)
        duplicate_rejection = {**status, "action_rejections": [
            {"action": "co.declare", "reason": "action_unavailable", "seq": 2, "after_resume": False},
            {"action": "co.declare", "reason": "action_unavailable", "seq": 2, "after_resume": False},
        ]}
        with self.assertRaises(EvidenceValidationError):
            validate_status(duplicate_rejection)

    def test_completion_evidence_quorum_and_interruption_boundaries(self) -> None:
        self.assertEqual(acceptance_quorum(0), 0)
        self.assertEqual(acceptance_quorum(1), 1)
        self.assertEqual(acceptance_quorum(9), 5)
        records = [
            {"voter_player_id": "p0"},
            {"voter_player_id": "p0"},
            {"voter_player_id": "p1"},
        ]
        with self.assertRaises(EvidenceValidationError):
            assert_acceptance_quorum(5, records, field="voter_player_id")
        self.assertEqual(
            assert_acceptance_quorum(
                3,
                records,
                field="voter_player_id",
            ),
            {"p0", "p1"},
        )
        rounds = [
            {"day": 1, "phase": "vote", "round_start_seq": 10},
            {"day": 1, "phase": "runoff", "round_start_seq": 20},
            {"day": 2, "phase": "vote", "round_start_seq": 30},
        ]
        self.assertEqual(
            derive_interrupted_rounds(rounds, stop_seq=10, resume_sync_seq=20),
            {(1, "runoff")},
        )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                {(1, "vote"), (1, "runoff"), (2, "vote")},
                stop_seq=10,
                resume_sync_seq=20,
            )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                set(),
                stop_seq=10,
                resume_sync_seq=20,
            )
        with self.assertRaises(EvidenceValidationError):
            validate_interruption_bounds(
                rounds,
                {(1, "night")},
                stop_seq=10,
                resume_sync_seq=20,
            )
        self.assertEqual(
            validate_interruption_bounds(
                rounds,
                {(1, "runoff")},
                stop_seq=10,
                resume_sync_seq=20,
            ),
            {(1, "runoff")},
        )

    async def test_nine_protocol_clients_complete_and_one_resumes(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=128,
            stop_after="action",
            expected_gap_counts=(0, 0),
        )

    async def _start_completion_server(
        self, *, replay_history_limit: int = 128
    ) -> tuple[GameState, GameRegistry, WebSocketGameServer, str]:
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content)
        preset = replace(
            preset,
            rules=replace(
                preset.rules,
                night_seconds=2,
                silence_after_dawn_seconds=0,
                day_seconds=2,
                vote_seconds=2,
            ),
        )
        players = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}")
            for index in range(sum(preset.role_counts.values()))
        )
        game = GameState.create_from_preset(
            content,
            preset,
            players,
            game_id=GAME_ID,
            event_sink=InMemoryEventSink(),
            rng=Random(0),
            started_at=monotonic_seconds(),
        )
        registry = GameRegistry({GAME_ID: game})
        sessions = SessionManager(registry, replay_history_limit=replay_history_limit)
        server = WebSocketGameServer(
            registry,
            sessions=sessions,
            tick_interval_seconds=0.02,
        )
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        return game, registry, server, uri

    async def _start_client_process(
        self,
        *,
        player_id: str,
        registry: GameRegistry,
        uri: str,
        root: Path,
        stop_after: str | None = None,
        inject_rejection: bool = False,
        inject_driver_error: bool = False,
    ) -> asyncio.subprocess.Process:
        marker = root / f"{player_id}.stop.json"
        arguments = [
            os.sys.executable,
            str(CLIENT),
            "--uri", uri,
            "--game-id", GAME_ID,
            "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
            "--credentials", str(root / f"{player_id}.credentials.json"),
            "--status", str(root / f"{player_id}.status.json"),
        ]
        if stop_after is not None:
            arguments.extend(["--stop-after", stop_after, "--stop-marker", str(marker)])
        if inject_rejection:
            arguments.append("--inject-rejection")
        if inject_driver_error:
            arguments.append("--inject-driver-error")
        return await asyncio.create_subprocess_exec(
            *arguments,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _run_restarted_process_scenario(
        self,
        *,
        replay_history_limit: int,
        stop_after: str,
        expected_gap_counts: tuple[int, int],
    ) -> None:
        game, registry, server, uri = await self._start_completion_server(
            replay_history_limit=replay_history_limit
        )
        processes: dict[str, asyncio.subprocess.Process] = {}
        # With the deterministic first-target fixture, the first seat is the
        # day-one execution target.  Keep the last seat so the restart spans
        # at least three completed vote rounds as required by the contract.
        stopped_player = tuple(game.players)[-1]
        session = server.sessions.session_for(GAME_ID)
        round_replies: list[dict[str, object]] = []
        original_reply = session._reply

        def record_reply(
            player_id: str, message_type: str, payload: object
        ) -> object:
            reply = original_reply(player_id, message_type, payload)
            if player_id == stopped_player and message_type in {
                "game.state_sync",
                "player.action_state",
            }:
                action_state = (
                    payload.get("action_state")
                    if message_type == "game.state_sync"
                    and isinstance(payload, dict)
                    else payload
                )
                if isinstance(action_state, dict) and any(
                    isinstance(action, dict) and action.get("type") == "vote"
                    for action in action_state.get("actions", ())
                ):
                    round_replies.append(
                        {
                            "day": action_state.get("day"),
                            "phase": action_state.get("phase"),
                            "round_start_seq": getattr(reply, "seq"),
                        }
                    )
            return reply

        session._reply = record_reply
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                try:
                    for player_id in game.players:
                        processes[player_id] = await self._start_client_process(
                            player_id=player_id,
                            registry=registry,
                            uri=uri,
                            root=root,
                            stop_after=stop_after if player_id == stopped_player else None,
                        )
                    marker_path = root / f"{stopped_player}.stop.json"
                    await self._wait_for(
                        lambda: marker_path.exists(),
                        timeout=15,
                        description=f"{stop_after} stop marker",
                    )
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    validate_stop_marker(
                        marker,
                        expected_player_id=stopped_player,
                        expected_kind=stop_after,
                    )
                    checkpoint_seq = marker["last_seq"]
                    self.assertIsInstance(checkpoint_seq, int)
                    first_pid = processes[stopped_player].pid

                    # The receiver remains alive while the controller is
                    # paused.  Require a later vote round and a client-written
                    # checkpoint before killing the process, so the marker
                    # bounds a real interruption interval.
                    try:
                        await self._wait_for(
                            lambda: any(
                                isinstance(reply["round_start_seq"], int)
                                and reply["round_start_seq"] > checkpoint_seq
                                for reply in round_replies
                            ),
                            timeout=15,
                            description="the next vote round reply",
                        )
                        next_round_seq = min(
                            reply["round_start_seq"]
                            for reply in round_replies
                            if isinstance(reply["round_start_seq"], int)
                            and reply["round_start_seq"] > checkpoint_seq
                        )
                        # Let the receiver finish the bounded sequence of
                        # checkpoint writes before reading the file.  Polling
                        # a Windows file while FileCredentialStore replaces
                        # it can itself make the atomic replace fail.
                        await asyncio.sleep(0.2)
                    except AssertionError as error:
                        raise AssertionError(
                            f"{error}; phase={game.phase.value} day={game.day} "
                            f"round_replies={round_replies!r} "
                            f"checkpoint_path={root / f'{stopped_player}.credentials.json'} "
                            f"replies={list(session._history_by_player.get(stopped_player, ()))!r}"
                        ) from error
                    checkpoint_before_rewind = json.loads(
                        (root / f"{stopped_player}.credentials.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertGreaterEqual(
                        checkpoint_before_rewind["last_seq"],
                        next_round_seq,
                    )
                    self.assertGreater(checkpoint_before_rewind["last_seq"], checkpoint_seq)
                    connection_token = checkpoint_before_rewind["connection_token"]
                    if replay_history_limit == 1:
                        await self._wait_for(
                            lambda: session._replay_floor_by_player.get(stopped_player, 0) > checkpoint_seq,
                            timeout=10,
                            description="replay history to leave the retention window",
                        )
                    else:
                        await self._wait_for(
                            lambda: any(
                                reply.seq > checkpoint_seq
                                for reply in session._history_by_player.get(stopped_player, ())
                            ),
                            timeout=10,
                            description="replay history to retain a missed event",
                        )
                    processes[stopped_player].kill()
                    await processes[stopped_player].wait()

                    # The driver pauses after writing the marker, but the client's
                    # receiver continues checkpointing messages until the process is
                    # killed. Rewind the persisted checkpoint to the exact stop point
                    # so the restart models the outage represented by the marker.
                    credentials_path = root / f"{stopped_player}.credentials.json"
                    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
                    credentials["last_seq"] = checkpoint_seq
                    credentials_path.write_text(
                        json.dumps(credentials, ensure_ascii=False), encoding="utf-8"
                    )
                    rewound = json.loads(credentials_path.read_text(encoding="utf-8"))
                    self.assertEqual(rewound["connection_token"], connection_token)
                    self.assertEqual(rewound["last_seq"], checkpoint_seq)

                    processes[stopped_player] = await self._start_client_process(
                        player_id=stopped_player,
                        registry=registry,
                        uri=uri,
                        root=root,
                    )
                    self.assertNotEqual(first_pid, processes[stopped_player].pid)
                    await self._wait_for(
                        lambda: game.game_result is not None,
                        timeout=45,
                        description="the server ticker to complete the game",
                    )
                    await self._wait_for(
                        lambda: all(
                            (root / f"{player_id}.status.json").exists()
                            for player_id in game.players
                        ),
                        timeout=15,
                        description="all client statuses",
                    )
                    statuses = {
                        player_id: json.loads(
                            (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                        )
                        for player_id in game.players
                    }
                    for player_id, status in statuses.items():
                        validate_status(status, expected_player_id=player_id)
                    self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                    self.assertTrue(statuses[stopped_player]["resumed"], statuses)
                    self.assertEqual(
                        (
                            statuses[stopped_player]["gap_detected"],
                            statuses[stopped_player]["gap_recovered"],
                        ),
                        expected_gap_counts,
                        statuses,
                    )
                    resume_syncs = [
                        event
                        for event in statuses[stopped_player]["state_sync_events"]
                        if event["after_resume"]
                    ]
                    self.assertEqual(len(resume_syncs), 1, statuses)
                    resume_sync_seq = resume_syncs[0]["seq"]
                    completed_rounds = [
                        {
                            "day": event.payload["day"],
                            "phase": event.payload["phase"],
                            "round_start_seq": min(
                                (
                                    reply["round_start_seq"]
                                    for reply in round_replies
                                    if reply["day"] == event.payload["day"]
                                    and reply["phase"] == event.payload["phase"]
                                ),
                                default=None,
                            ),
                        }
                        for event in game.event_bus.events
                        if event.type == "VOTE_RESOLVED"
                    ]
                    self.assertTrue(
                        completed_rounds
                        and all(item["round_start_seq"] is not None for item in completed_rounds),
                        (completed_rounds, round_replies),
                    )
                    self.assertGreaterEqual(len(completed_rounds), 3)
                    interrupted = derive_interrupted_rounds(
                        completed_rounds,
                        stop_seq=checkpoint_seq,
                        resume_sync_seq=resume_sync_seq,
                    )
                    validate_interruption_bounds(
                        completed_rounds,
                        interrupted,
                        stop_seq=checkpoint_seq,
                        resume_sync_seq=resume_sync_seq,
                    )
                    self.assertTrue(
                        any(
                            evidence["kind"] == "vote"
                            and evidence["outcome"] == "sent"
                            and evidence["phase"] == "vote"
                            and evidence["day"] == completed_rounds[0]["day"]
                            for evidence in marker["action_evidence"]
                        ),
                        marker,
                    )
                    final_evidence = {
                        item["key"]: item
                        for item in marker["action_evidence"] + statuses[stopped_player]["action_evidence"]
                    }
                    aggregate_statuses = dict(statuses)
                    aggregate_statuses[stopped_player] = dict(statuses[stopped_player])
                    aggregate_statuses[stopped_player]["action_evidence"] = list(
                        final_evidence.values()
                    )
                    for event in game.event_bus.events:
                        if event.type != "VOTE_RESOLVED":
                            continue
                        round_key = (event.payload["day"], event.payload["phase"])
                        expected_voters = set(event.payload["tallies"])
                        accepted = [
                            event_record.payload
                            for event_record in game.event_bus.events
                            if event_record.type == "VOTE_SUBMITTED"
                            and (
                                event_record.payload["day"],
                                event_record.payload["phase"],
                            )
                            == round_key
                        ]
                        assert_acceptance_quorum(
                            len(expected_voters),
                            accepted,
                            field="voter_player_id",
                        )
                        sent_voters = {
                            evidence["key"].split("|", 1)[0]
                            for evidence in aggregate_statuses[stopped_player]["action_evidence"]
                            if evidence["kind"] == "vote"
                            and evidence["outcome"] == "sent"
                            and (evidence["day"], evidence["phase"]) == round_key
                        }
                        sent_voters.update(
                            evidence["key"].split("|", 1)[0]
                            for player_id, status in aggregate_statuses.items()
                            if player_id != stopped_player
                            for evidence in status["action_evidence"]
                            if evidence["kind"] == "vote"
                            and evidence["outcome"] == "sent"
                            and (evidence["day"], evidence["phase"]) == round_key
                        )
                        required_voters = expected_voters - (
                            {stopped_player} if round_key in interrupted else set()
                        )
                        # Evidence must cover every required living voter and
                        # contain no voter outside the authoritative tally. The
                        # restarted seat may already have sent before the stop
                        # marker, even when this round is exempted.
                        self.assertTrue(
                            required_voters <= sent_voters <= expected_voters,
                            {
                                "round": round_key,
                                "expected_voters": sorted(expected_voters),
                                "required_voters": sorted(required_voters),
                                "sent_voters": sorted(sent_voters),
                                "interrupted": sorted(interrupted),
                            },
                        )
                    evidence = [
                        item
                        for status in aggregate_statuses.values()
                        for item in status["action_evidence"]
                    ]
                    evidence_by_key = {item["key"]: item for item in evidence}
                    for kind in ("ability", "co_declare", "chat"):
                        eligible = {
                            item["key"]
                            for item in evidence_by_key.values()
                            if item["kind"] == kind
                            and not (
                                kind == "ability"
                                and item["outcome"] == "no_legal_target"
                            )
                            and not (
                                kind == "co_declare"
                                and item["outcome"] == "no_legal_target"
                            )
                        }
                        sent = {
                            item["key"]
                            for item in evidence_by_key.values()
                            if item["kind"] == kind and item["outcome"] == "sent"
                        }
                        self.assertTrue(eligible, f"fixture exposed no {kind} opportunity")
                        self.assertEqual(sent, eligible, (kind, evidence_by_key))
                    ability_keys = {
                        "|".join(
                            (
                                payload["actor_player_id"],
                                str(payload["day"]),
                                payload["phase"],
                                "ability",
                                payload["ability_id"],
                            )
                        )
                        for event in game.event_bus.events
                        if event.type == "ACTION_SUBMITTED"
                        for payload in (event.payload,)
                    }
                    sent_ability_keys = {
                        item["key"]
                        for item in evidence_by_key.values()
                        if item["kind"] == "ability" and item["outcome"] == "sent"
                    }
                    self.assertGreaterEqual(len(sent_ability_keys), 2, evidence_by_key)
                    self.assertGreaterEqual(
                        len(ability_keys),
                        max(2, acceptance_quorum(len(sent_ability_keys))),
                    )
                    self.assertGreaterEqual(
                        len(ability_keys & sent_ability_keys),
                        max(2, acceptance_quorum(len(sent_ability_keys))),
                    )
                    co_eligible = {
                        item["key"]
                        for item in evidence_by_key.values()
                        if item["kind"] == "co_declare"
                        and item["outcome"] != "no_legal_target"
                    }
                    co_eligible_by_day: dict[int, set[str]] = {}
                    for key in co_eligible:
                        player_id, day, action_kind = key.split("|")
                        self.assertEqual(action_kind, "co_declare", key)
                        co_eligible_by_day.setdefault(int(day), set()).add(player_id)
                    co_accepted = _co_acceptance_keys(game.event_bus.events)
                    eligible_pairs = {
                        (day, player_id)
                        for day, player_ids in co_eligible_by_day.items()
                        for player_id in player_ids
                    }
                    self.assertTrue(co_accepted <= eligible_pairs, (co_accepted, eligible_pairs))
                    for day, eligible_players in co_eligible_by_day.items():
                        accepted_players = {
                            player_id for accepted_day, player_id in co_accepted if accepted_day == day
                        }
                        self.assertGreaterEqual(
                            len(accepted_players),
                            acceptance_quorum(len(eligible_players)),
                            (day, eligible_players, accepted_players),
                        )
                    self.assertGreaterEqual(
                        len(co_accepted),
                        2,
                    )
                    chat_sent_players = {
                        item["key"].split("|", 1)[0]
                        for item in evidence_by_key.values()
                        if item["kind"] == "chat" and item["outcome"] == "sent"
                    }
                    chat_received_players = {
                        message["sender_player_id"]
                        for status in statuses.values()
                        for message in status["chat_messages_received"]
                    }
                    self.assertGreaterEqual(
                        len(chat_received_players),
                        max(2, acceptance_quorum(len(chat_sent_players))),
                    )
                    self.assertTrue(
                        any(
                            evidence["after_resume"]
                            and evidence["outcome"] == "sent"
                            for evidence in statuses[stopped_player]["action_evidence"]
                        ),
                        statuses[stopped_player],
                    )
                    self.assertTrue(
                        all(status["production_import_guard"] for status in statuses.values()),
                        statuses,
                    )
                    self.assertTrue(
                        all(not status["server_imports"] for status in statuses.values()),
                        statuses,
                    )
                    self.assertEqual(
                        sum(len(status["send_errors"]) for status in statuses.values()),
                        0,
                        statuses,
                    )
                finally:
                    for process in processes.values():
                        if process.returncode is None:
                            process.terminate()
                    cleanup_results = await asyncio.gather(
                        *(
                            self._finish_process(
                                process,
                                status_path=root / f"{player_id}.status.json",
                            )
                            for player_id, process in processes.items()
                        ),
                        return_exceptions=True,
                    )
                    failures = [
                        result for result in cleanup_results if isinstance(result, Exception)
                    ]
                    if failures:
                        raise AssertionError(
                            "client cleanup failures: " + " | ".join(map(str, failures))
                        )
        finally:
            session._reply = original_reply
            await server.close()

    async def test_separate_process_action_stop_resumes_outside_retention_and_recovers_gap(self) -> None:
        await self._run_restarted_process_scenario(
            replay_history_limit=1,
            stop_after="action",
            expected_gap_counts=(1, 1),
        )

    async def test_separate_process_rejection_and_production_import_guard(self) -> None:
        game, registry, server, uri = await self._start_completion_server()
        processes: dict[str, asyncio.subprocess.Process] = {}
        injected_player = next(iter(game.players))
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                try:
                    for player_id in game.players:
                        processes[player_id] = await self._start_client_process(
                            player_id=player_id,
                            registry=registry,
                            uri=uri,
                            root=root,
                            inject_rejection=player_id == injected_player,
                        )
                    await self._wait_for(
                        lambda: game.game_result is not None,
                        timeout=45,
                        description="the server ticker to complete the game",
                    )
                    await self._wait_for(
                        lambda: all(
                            (root / f"{player_id}.status.json").exists()
                            for player_id in game.players
                        ),
                        timeout=15,
                        description="all client statuses",
                    )
                    statuses = {
                        player_id: json.loads(
                            (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                        )
                        for player_id in game.players
                    }
                    for player_id, status in statuses.items():
                        validate_status(
                            status,
                            expected_player_id=player_id,
                            allow_defect_rejections=True,
                        )
                    rejection_reasons = [
                        rejection["reason"]
                        for status in statuses.values()
                        for rejection in status["action_rejections"]
                    ]
                    self.assertGreater(
                        len(rejection_reasons),
                        0,
                        statuses,
                    )
                    self.assertIn("co_limit_reached", rejection_reasons, statuses)
                    self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                    self.assertTrue(
                        all(status["production_import_guard"] for status in statuses.values()),
                        statuses,
                    )
                    self.assertTrue(
                        all(not status["server_imports"] for status in statuses.values()),
                        statuses,
                    )
                    self.assertEqual(
                        sum(len(status["send_errors"]) for status in statuses.values()),
                        0,
                        statuses,
                    )
                    self.assertTrue(all(status["pid"] != os.getpid() for status in statuses.values()))
                finally:
                    for process in processes.values():
                        if process.returncode is None:
                            process.terminate()
                    cleanup_results = await asyncio.gather(
                        *(
                            self._finish_process(
                                process,
                                status_path=root / f"{player_id}.status.json",
                            )
                            for player_id, process in processes.items()
                        ),
                        return_exceptions=True,
                    )
                    failures = [
                        result for result in cleanup_results if isinstance(result, Exception)
                    ]
                    if failures:
                        raise AssertionError(
                            "client cleanup failures: " + " | ".join(map(str, failures))
                        )
        finally:
            await server.close()

    async def test_driver_failure_writes_diagnostics(self) -> None:
        game, registry, server, uri = await self._start_completion_server()
        process: asyncio.subprocess.Process | None = None
        try:
            with TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                player_id = next(iter(game.players))
                stdout_path = root / "driver.stdout"
                stderr_path = root / "driver.stderr"
                arguments = [
                    os.sys.executable,
                    str(CLIENT),
                    "--uri", uri,
                    "--game-id", GAME_ID,
                    "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                    "--credentials", str(root / f"{player_id}.credentials.json"),
                    "--status", str(root / f"{player_id}.status.json"),
                    "--inject-driver-error",
                ]
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    process = await asyncio.create_subprocess_exec(
                        *arguments,
                        cwd=str(PROJECT_ROOT),
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                    await process.wait()
                stdout = stdout_path.read_bytes()
                stderr = stderr_path.read_bytes()
                self.assertNotEqual(process.returncode, 0)
                status = json.loads(
                    (root / f"{player_id}.status.json").read_text(
                        encoding="utf-8"
                    )
                )
                validate_status(status)
                self.assertEqual(status["exception_type"], "RuntimeError")
                self.assertEqual(
                    status["exception_message"], "injected Phase 3.1 driver failure"
                )
                self.assertEqual(status["events_received"], 0)
                self.assertEqual(status["actions_sent"], 0)
                self.assertIn("RuntimeError", stderr.decode(errors="replace"))
                self.assertEqual(stdout, b"")
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            await server.close()

    async def _wait_for(self, condition, *, timeout: float, description: str) -> None:
        async def wait() -> None:
            while not condition():
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError as error:
            raise AssertionError(f"timed out waiting for {description}") from error

    def _checkpoint_seq(self, root: Path, player_id: str) -> int:
        return int(
            json.loads(
                (root / f"{player_id}.credentials.json").read_text(encoding="utf-8")
            )["last_seq"]
        )

    async def _finish_process(
        self, process: asyncio.subprocess.Process, *, status_path: Path | None = None
    ) -> None:
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
        if process.returncode not in {0, -15}:
            diagnostics = [
                f"stdout={stdout.decode(errors='replace')!r}",
                f"stderr={stderr.decode(errors='replace')!r}",
            ]
            if status_path is not None:
                try:
                    status = status_path.read_text(encoding="utf-8")
                except OSError as error:
                    diagnostics.append(
                        f"status_read_error={type(error).__name__}: {error}"
                    )
                else:
                    diagnostics.append(f"status={status}")
            self.fail(
                f"Phase 3.1 client {process.pid} exited {process.returncode}: "
                + " ".join(diagnostics)
            )


if __name__ == "__main__":
    unittest.main()
