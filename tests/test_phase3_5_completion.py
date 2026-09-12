from __future__ import annotations

import asyncio
from collections import Counter, deque
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import time
from typing import Callable
import unittest
from unittest.mock import patch

import pytest

from server.aiwolf_core import (
    GameState,
    InMemoryEventSink,
    PlayerConfig,
    load_content,
    load_preset,
)
from server.network import GameRegistry, SessionManager, TickDriver, WebSocketGameServer
from server.network.delivery import OutboundDelivery
from tests.fixtures.completion_process import (
    _ProcessOutput,
    finish_process,
    load_process_output,
)
from tests.fixtures.phase3_4_reaction_brain import CompletionReactionMode
from tests.fixtures.phase3_5_evidence import (
    AcceptedReservation,
    ExpectedReservation,
    decision_sequence,
    reservation_histogram,
)
from tests.fixtures.reaction_chat_evidence import (
    AcceptedChatEvidence,
    accepted_histogram,
    validate_rejections,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_5_client_process.py"


class _DayOneBarrierClock:
    def __init__(self) -> None:
        self._started_at_ns: int | None = None
        self._day_one_released_at_ns: int | None = None

    def __call__(self) -> int:
        if self._started_at_ns is None:
            return 0
        if self._day_one_released_at_ns is None:
            elapsed = (time.monotonic_ns() - self._started_at_ns) // 1_000_000_000
            return min(elapsed, 1)
        return 1 + (
            time.monotonic_ns() - self._day_one_released_at_ns
        ) // 1_000_000_000

    def release_initial_night(self) -> None:
        if self._started_at_ns is not None:
            raise RuntimeError("initial night released twice")
        self._started_at_ns = time.monotonic_ns()

    def release_day_one(self) -> None:
        if self._started_at_ns is None or self._day_one_released_at_ns is not None:
            raise RuntimeError("invalid Day 1 release")
        self._day_one_released_at_ns = time.monotonic_ns()


def _expected_from_state(player_id: str, payload) -> ExpectedReservation | None:
    phase = payload["phase"]
    day = payload["day"]
    vote_actions = [action for action in payload["actions"] if action["type"] == "vote"]
    ability_actions = [
        action
        for action in payload["actions"]
        if action["type"] == "ability"
        and action["uses_remaining"] != 0
        and len(action["valid_targets"]) >= action["target_count"]
    ]
    if vote_actions:
        if len(vote_actions) != 1:
            raise AssertionError(f"duplicate vote action for {player_id}: {payload}")
        action = vote_actions[0]
        return ExpectedReservation(
            player_id,
            day,
            phase,
            "vote.cast",
            None,
            tuple(action["valid_targets"]),
            action["target_count"],
            None,
        )
    if ability_actions:
        # The final reservation is per actor/phase.  Individual allowed ability
        # handles are validated separately against the captured payload.
        return ExpectedReservation(
            player_id,
            day,
            phase,
            "ability.use",
            None,
            (),
            0,
            None,
        )
    return None


@pytest.mark.completion
class PhaseThreeFiveCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nine_process_cumulative_reservations_are_exact_and_reproducible(self) -> None:
        started = time.monotonic()
        first = await self._run_scenario("same-a", 8351, None)
        second = await self._run_scenario("same-b", 8351, None)
        different = await self._run_scenario("different-silent", 8352, "player-0")
        self.assertEqual(first["decision_sequence"], second["decision_sequence"])
        self.assertEqual(first["selection_map"], second["selection_map"])
        common = set(first["multi_candidate"]) & set(different["multi_candidate"])
        self.assertTrue(common, (first["multi_candidate"], different["multi_candidate"]))
        self.assertTrue(
            any(first["selection_map"][key] != different["selection_map"][key] for key in common),
            (first["selection_map"], different["selection_map"]),
        )
        self.assertLess(time.monotonic() - started, 120.0)

    async def _run_scenario(self, name: str, seed: int, silent_player: str | None):
        content = load_content(PROJECT_ROOT / "content")
        preset = load_preset(
            PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content
        )
        preset = replace(
            preset,
            rules=replace(
                preset.rules,
                night_seconds=1,
                silence_after_dawn_seconds=0,
                day_seconds=2,
                vote_seconds=1,
            ),
        )
        players = tuple(
            PlayerConfig(f"player-{index}", f"Player {index}")
            for index in range(sum(preset.role_counts.values()))
        )
        game_id = f"123e4567-e89b-12d3-a456-{seed:012d}"
        clock = _DayOneBarrierClock()
        game = GameState.create_from_preset(
            content,
            preset,
            players,
            game_id=game_id,
            event_sink=InMemoryEventSink(),
            rng=Random(seed),
            started_at=clock(),
        )
        registry = GameRegistry({game_id: game})
        sessions = SessionManager(registry, clock=clock)
        ticker = TickDriver(registry, clock=clock)
        server = WebSocketGameServer(
            registry,
            sessions=sessions,
            ticker=ticker,
            tick_interval_seconds=0.02,
        )
        expected: dict[tuple[str, int, str, str], ExpectedReservation] = {}
        expected_abilities: dict[tuple[str, int, str, str], tuple[dict, ...]] = {}
        accepted: list[AcceptedReservation] = []
        chats: list[AcceptedChatEvidence] = []
        last_replies: deque[dict[str, object]] = deque(maxlen=64)
        speaking_players = set(game.players) - (
            {silent_player} if silent_player is not None else set()
        )
        deferred_peer_deliveries: list[OutboundDelivery] = []
        first_wave_flushed = False
        second_wave_flushed = False
        day_one_budget_started: float | None = None
        day_one_completed_seconds: float | None = None
        active_request_id: str | None = None
        original_handle = sessions.handle_message
        original_vote = game.submit_vote
        original_action = game.submit_action
        original_queue = server._delivery_router.queue_channel_message  # noqa: SLF001
        original_drain = server._delivery_router.drain  # noqa: SLF001

        def remember_expected(player_id: str, payload) -> None:
            item = _expected_from_state(player_id, payload)
            if item is None:
                return
            key = (item.player_id, item.day, item.phase, item.action)
            expected.setdefault(key, item)
            if item.action == "ability.use":
                abilities = tuple(
                    dict(action)
                    for action in payload["actions"]
                    if action["type"] == "ability"
                    and action["uses_remaining"] != 0
                    and len(action["valid_targets"]) >= action["target_count"]
                )
                previous = expected_abilities.setdefault(key, abilities)
                self.assertEqual(previous, abilities)

        def handle_spy(message, context=None):
            nonlocal active_request_id
            message_type = message.get("type") if isinstance(message, dict) else None
            request_id = message.get("event_id") if isinstance(message, dict) else None
            previous = active_request_id
            if message_type in {"vote.cast", "ability.use"}:
                active_request_id = request_id
            try:
                result = original_handle(message, context)
                if result.reply is not None:
                    payload = dict(result.reply.payload)
                    last_replies.append(
                        {
                            "request_type": message_type,
                            "request_event_id": request_id,
                            "player_id": (
                                None
                                if result.context is None
                                else result.context.player_id
                            ),
                            "reply_type": result.reply.type,
                            "reply_seq": result.reply.seq,
                            "reply_payload": {
                                key: payload[key]
                                for key in (
                                    "action",
                                    "reason",
                                    "request_event_id",
                                    "player_id",
                                    "ready",
                                    "last_seq",
                                )
                                if key in payload
                            },
                            "replay_tail": tuple(
                                (reply.type, reply.seq) for reply in result.replay[-8:]
                            ),
                        }
                    )
                return result
            finally:
                active_request_id = previous

        def vote_spy(now, player_id, target_player_id):
            acceptance = original_vote(now, player_id, target_player_id)
            self.assertIsNotNone(active_request_id)
            accepted.append(
                AcceptedReservation(
                    player_id,
                    acceptance.day,
                    acceptance.phase,
                    "vote.cast",
                    None,
                    target_player_id,
                    (),
                    active_request_id or "",
                    acceptance.accepted_at,
                    acceptance.phase_deadline,
                )
            )
            return acceptance

        def action_spy(now, player_id, ability_id, target_player_ids):
            day = game.day
            phase = game.phase.value
            deadline = game.phase_ends_at
            original_action(now, player_id, ability_id, target_player_ids)
            self.assertIsNotNone(active_request_id)
            self.assertIsNotNone(deadline)
            accepted.append(
                AcceptedReservation(
                    player_id,
                    day,
                    phase,
                    "ability.use",
                    ability_id,
                    None,
                    tuple(target_player_ids),
                    active_request_id or "",
                    now,
                    deadline or 0,
                )
            )

        def queue_spy(spy_game_id, channel, message, *, acceptance=None):
            if acceptance is not None:
                chats.append(
                    AcceptedChatEvidence(
                        message["player_id"],
                        acceptance.day,
                        acceptance.phase,
                        channel,
                        acceptance.accepted_at,
                        acceptance.phase_deadline,
                        message["message"],
                    )
                )
            original_queue(spy_game_id, channel, message, acceptance=acceptance)

        def drain_spy():
            nonlocal first_wave_flushed, second_wave_flushed, day_one_completed_seconds
            deliveries = original_drain()
            immediate: list[OutboundDelivery] = []
            for delivery in deliveries:
                if delivery.message_type == "player.action_state":
                    self.assertEqual(len(delivery.recipient_player_ids), 1)
                    remember_expected(delivery.recipient_player_ids[0], delivery.payload)
                    immediate.append(delivery)
                    continue
                if not (
                    delivery.message_type == "chat.message"
                    and game.day == 1
                    and game.phase.value == "day"
                ):
                    immediate.append(delivery)
                    continue
                author = delivery.payload["message"]["player_id"]
                author_recipients = tuple(
                    player_id
                    for player_id in delivery.recipient_player_ids
                    if player_id == author
                )
                peer_recipients = tuple(
                    player_id
                    for player_id in delivery.recipient_player_ids
                    if player_id != author
                )
                if author_recipients:
                    immediate.append(
                        OutboundDelivery(
                            delivery.game_id,
                            author_recipients,
                            delivery.message_type,
                            delivery.payload,
                            delivery.acceptance,
                        )
                    )
                if peer_recipients:
                    deferred_peer_deliveries.append(
                        OutboundDelivery(
                            delivery.game_id,
                            peer_recipients,
                            delivery.message_type,
                            delivery.payload,
                            delivery.acceptance,
                        )
                    )

            chat_histogram = accepted_histogram(chats)
            if not first_wave_flushed and all(
                chat_histogram.get((player_id, 1, "day"), 0) >= 1
                for player_id in speaking_players
            ):
                immediate.extend(deferred_peer_deliveries)
                deferred_peer_deliveries.clear()
                first_wave_flushed = True
            elif first_wave_flushed and not second_wave_flushed and all(
                chat_histogram.get((player_id, 1, "day"), 0) >= 2
                for player_id in speaking_players
            ):
                immediate.extend(deferred_peer_deliveries)
                deferred_peer_deliveries.clear()
                second_wave_flushed = True
                if day_one_budget_started is not None:
                    day_one_completed_seconds = time.monotonic() - day_one_budget_started
            return tuple(immediate)

        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        processes: dict[str, asyncio.subprocess.Process] = {}
        outputs: dict[int, _ProcessOutput] = {}
        output_paths: dict[int, tuple[Path, Path]] = {}
        status_paths: dict[str, Path] = {}
        ready_paths: dict[str, Path] = {}
        day_ready_paths: dict[str, Path] = {}

        with TemporaryDirectory() as temporary_directory, patch.object(
            sessions, "handle_message", side_effect=handle_spy
        ), patch.object(game, "submit_vote", side_effect=vote_spy), patch.object(
            game, "submit_action", side_effect=action_spy
        ), patch.object(
            server._delivery_router, "queue_channel_message", side_effect=queue_spy  # noqa: SLF001
        ), patch.object(
            server._delivery_router, "drain", side_effect=drain_spy  # noqa: SLF001
        ):
            root = Path(temporary_directory)

            def read_evidence(path: Path | None) -> dict[str, object] | None:
                if path is None or not path.exists():
                    return None
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    return {"read_error": f"{type(error).__name__}: {error}"}
                return value if isinstance(value, dict) else {"invalid": value}

            def latest_client_evidence(player_id: str) -> dict[str, object] | None:
                for path in (
                    status_paths.get(player_id),
                    day_ready_paths.get(player_id),
                    ready_paths.get(player_id),
                ):
                    if (value := read_evidence(path)) is not None:
                        return value
                return None

            def latest_deadline_mapping(player_id: str) -> object:
                for path in (
                    status_paths.get(player_id),
                    day_ready_paths.get(player_id),
                    ready_paths.get(player_id),
                ):
                    value = read_evidence(path)
                    if (
                        isinstance(value, dict)
                        and value.get("deadline_mapping") is not None
                    ):
                        return value["deadline_mapping"]
                return None

            def diagnostic_state() -> dict[str, object]:
                day_one_counts = {
                    player_id: accepted_histogram(chats).get(
                        (player_id, 1, "day"), 0
                    )
                    for player_id in game.players
                }
                failing_players = {
                    player_id: {
                        "expected": (
                            "0"
                            if player_id == silent_player
                            else "2" if silent_player is None else ">=1"
                        ),
                        "actual": count,
                    }
                    for player_id, count in day_one_counts.items()
                    if (
                        (player_id == silent_player and count != 0)
                        or (
                            player_id != silent_player
                            and silent_player is None
                            and count != 2
                        )
                        or (
                            player_id != silent_player
                            and silent_player is not None
                            and count < 1
                        )
                    )
                }
                accepted_by_key: dict[
                    tuple[str, int, str, str], list[AcceptedReservation]
                ] = {}
                for item in accepted:
                    key = (item.player_id, item.day, item.phase, item.action)
                    accepted_by_key.setdefault(key, []).append(item)
                reservation_keys = sorted(set(expected) | set(accepted_by_key))
                reservation_matrix = [
                    {
                        "key": key,
                        "expected": (
                            None if key not in expected else asdict(expected[key])
                        ),
                        "accepted": [
                            asdict(item) for item in accepted_by_key.get(key, ())
                        ],
                    }
                    for key in reservation_keys
                ]
                client_evidence = {
                    player_id: latest_client_evidence(player_id)
                    for player_id in processes
                }

                def outcomes(family: str, evidence: object) -> list[object]:
                    if not isinstance(evidence, dict):
                        return []
                    controller = evidence.get(family)
                    if not isinstance(controller, dict):
                        return []
                    values = controller.get("outcomes")
                    return list(values[-16:]) if isinstance(values, list) else []

                def rejections(family: str, evidence: object) -> list[object]:
                    if not isinstance(evidence, dict):
                        return []
                    controller = evidence.get(family)
                    if not isinstance(controller, dict):
                        return []
                    values = controller.get("rejections")
                    return list(values[-16:]) if isinstance(values, list) else []

                return {
                    "day1_chat_counts": day_one_counts,
                    "failing_players": failing_players,
                    "chat_waves": {
                        "first_flushed": first_wave_flushed,
                        "second_flushed": second_wave_flushed,
                        "deferred_count": len(deferred_peer_deliveries),
                        "completed_seconds": day_one_completed_seconds,
                    },
                    "last_replies": list(last_replies),
                    "phase_histogram": dict(
                        Counter(
                            (event.payload.get("day"), event.payload.get("phase"))
                            for event in game.event_bus.events
                            if event.type == "PHASE_STARTED"
                        )
                    ),
                    "expected_accepted_matrix": reservation_matrix,
                    "rejections": {
                        "server": [
                            reply
                            for reply in last_replies
                            if reply["reply_type"] == "action.rejected"
                        ],
                        "clients": {
                            player_id: {
                                "reaction": rejections("reaction", evidence),
                                "reservation": rejections("reservation", evidence),
                            }
                            for player_id, evidence in client_evidence.items()
                        },
                    },
                    "controller_outcomes": {
                        player_id: {
                            "reaction": outcomes("reaction", evidence),
                            "reservation": outcomes("reservation", evidence),
                        }
                        for player_id, evidence in client_evidence.items()
                    },
                    "deadline_mapping": {
                        player_id: latest_deadline_mapping(player_id)
                        for player_id in client_evidence
                    },
                }

            async def start_client(player_id: str):
                status_path = root / f"{player_id}.status.json"
                ready_path = root / f"{player_id}.ready.json"
                day_ready_path = root / f"{player_id}.day-ready.json"
                stdout_path = root / f"{player_id}.stdout.log"
                stderr_path = root / f"{player_id}.stderr.log"
                mode = (
                    CompletionReactionMode.SILENT
                    if player_id == silent_player
                    else CompletionReactionMode.SPEAK
                )
                with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                    process = await asyncio.create_subprocess_exec(
                        os.sys.executable,
                        str(CLIENT),
                        "--uri", uri,
                        "--game-id", game_id,
                        "--player-id", player_id,
                        "--entry-token", registry.entry_tokens_for(game_id)[player_id],
                        "--credentials", str(root / f"{player_id}.credentials.json"),
                        "--status", str(status_path),
                        "--ready", str(ready_path),
                        "--day-one-ready", str(day_ready_path),
                        "--clock-start", str(root / "clock-start"),
                        "--day-one-release", str(root / "day-one-release"),
                        "--seed", str(seed),
                        "--mode", mode.value,
                        cwd=str(PROJECT_ROOT),
                        stdout=stdout_file,
                        stderr=stderr_file,
                    )
                output = _ProcessOutput(bytearray(), bytearray(), bytearray(), [])
                outputs[id(process)] = output
                output_paths[id(process)] = (stdout_path, stderr_path)
                status_paths[player_id] = status_path
                ready_paths[player_id] = ready_path
                day_ready_paths[player_id] = day_ready_path
                return process

            try:
                launched = await asyncio.gather(
                    *(start_client(player_id) for player_id in game.players)
                )
                processes = dict(zip(game.players, launched))
                self.assertEqual(len(processes), 9, processes)
                child_pids = {process.pid for process in processes.values()}
                self.assertEqual(len(child_pids), 9, child_pids)
                self.assertNotIn(os.getpid(), child_pids)
                await self._wait_for(
                    lambda: all(path.exists() for path in ready_paths.values()),
                    timeout=15,
                    description=f"{name}: all cumulative controllers ready at night0",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                    diagnostic_state=diagnostic_state,
                )
                for player_id in game.players:
                    remember_expected(player_id, game.get_action_state(player_id))
                (root / "clock-start").write_text("release night0\n", encoding="utf-8")
                clock.release_initial_night()
                await self._wait_for(
                    lambda: all(path.exists() for path in day_ready_paths.values()),
                    timeout=15,
                    description=f"{name}: all cumulative controllers ready at Day 1",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                    diagnostic_state=diagnostic_state,
                )
                (root / "day-one-release").write_text("release Day 1\n", encoding="utf-8")
                day_one_budget_started = time.monotonic()
                clock.release_day_one()
                await self._wait_for(
                    lambda: game.game_result is not None,
                    timeout=55,
                    description=f"{name}: game end",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                    diagnostic_state=diagnostic_state,
                )
                await self._wait_for(
                    lambda: all(path.exists() for path in status_paths.values()),
                    timeout=15,
                    description=f"{name}: client status",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                    diagnostic_state=diagnostic_state,
                )
                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in status_paths.items()
                }
                self.assertTrue(all(item["game_end"] for item in statuses.values()), statuses)
                self.assertTrue(
                    all(item["production_import_guard"] for item in statuses.values()), statuses
                )
                self.assertEqual(
                    {status["pid"] for status in statuses.values()}, child_pids, statuses
                )
                validate_rejections(statuses)
                for player_id, status in statuses.items():
                    reservation = status["reservation"]
                    self.assertEqual(reservation["rejected_count"], 0, (player_id, status))
                    self.assertEqual(reservation["unknown_count"], 0, (player_id, status))
                    self.assertFalse(reservation["unresolved"], (player_id, status))
                    self.assertEqual(reservation["rejections"], [], (player_id, status))

                histogram = reservation_histogram(accepted)
                self.assertEqual(set(histogram), set(expected), (expected, accepted, statuses))
                self.assertTrue(all(count == 1 for count in histogram.values()), histogram)
                self.assertTrue(
                    all(item.accepted_at < item.phase_deadline for item in accepted), accepted
                )
                for item in accepted:
                    key = (item.player_id, item.day, item.phase, item.action)
                    expectation = expected[key]
                    if item.action == "vote.cast":
                        self.assertEqual(expectation.target_count, 1)
                        if item.vote_target_player_id is None:
                            state = game.get_action_state(item.player_id)
                            self.assertIsNone(item.vote_target_player_id, state)
                        else:
                            self.assertIn(item.vote_target_player_id, expectation.valid_targets)
                    else:
                        abilities = expected_abilities[key]
                        ability = next(
                            action for action in abilities if action["ability_id"] == item.ability_id
                        )
                        self.assertEqual(
                            len(item.ability_target_player_ids), ability["target_count"]
                        )
                        self.assertEqual(
                            len(set(item.ability_target_player_ids)),
                            len(item.ability_target_player_ids),
                        )
                        self.assertTrue(
                            all(
                                target in ability["valid_targets"]
                                for target in item.ability_target_player_ids
                            )
                        )
                        self.assertNotEqual(ability["uses_remaining"], 0)

                chat_counts = accepted_histogram(chats)
                day_one_counts = {
                    player_id: chat_counts.get((player_id, 1, "day"), 0)
                    for player_id in game.players
                }
                failing_players = diagnostic_state()["failing_players"]
                if failing_players:
                    raise AssertionError(
                        f"day1_chat_counts={day_one_counts}; "
                        f"failing_players={failing_players}"
                    )
                self.assertTrue(
                    first_wave_flushed,
                    f"day1_chat_counts={day_one_counts}; first wave not flushed",
                )
                self.assertTrue(
                    second_wave_flushed,
                    f"day1_chat_counts={day_one_counts}; second wave not flushed",
                )
                self.assertEqual(
                    deferred_peer_deliveries,
                    [],
                    f"day1_chat_counts={day_one_counts}; deferred peer delivery remained",
                )
                self.assertIsNotNone(
                    day_one_completed_seconds,
                    f"day1_chat_counts={day_one_counts}; missing wave completion timing",
                )
                assert day_one_completed_seconds is not None
                self.assertLess(
                    day_one_completed_seconds,
                    2.0,
                    f"day1_chat_counts={day_one_counts}; waves exceeded Day-1 budget",
                )
                if silent_player is not None:
                    self.assertGreater(
                        statuses[silent_player]["reaction"]["intentional_silence_count"], 0
                    )

                ordered = decision_sequence(
                    sorted(
                        accepted,
                        key=lambda item: (
                            item.day,
                            {"night0": 0, "day": 1, "vote": 2, "runoff": 3, "night": 4}.get(item.phase, 9),
                            item.player_id,
                            item.action,
                        ),
                    )
                )
                selection_map = {
                    (item.player_id, item.day, item.phase, item.action): (
                        item.ability_id,
                        item.vote_target_player_id,
                        item.ability_target_player_ids,
                    )
                    for item in accepted
                }
                multi_candidate = {
                    key
                    for key, expectation in expected.items()
                    if (
                        expectation.action == "vote.cast"
                        and len(expectation.valid_targets) > 1
                    )
                    or (
                        expectation.action == "ability.use"
                        and any(
                            len(action["valid_targets"]) > action["target_count"]
                            or len(expected_abilities[key]) > 1
                            for action in expected_abilities[key]
                        )
                    )
                }
                return {
                    "decision_sequence": ordered,
                    "selection_map": selection_map,
                    "multi_candidate": multi_candidate,
                }
            except Exception as error:
                raise AssertionError(
                    f"{name} scenario failed: {error}; "
                    f"diagnostics={diagnostic_state()!r}"
                ) from error
            finally:
                cleanup = await asyncio.gather(
                    *(
                        finish_process(
                            process,
                            output=outputs.get(id(process)),
                            label=f"{name} Phase 3.5 client {player_id}",
                            status_path=status_paths.get(player_id),
                            output_paths=output_paths.get(id(process)),
                        )
                        for player_id, process in processes.items()
                    ),
                    return_exceptions=True,
                )
                await server.close()
                failures = [item for item in cleanup if isinstance(item, Exception)]
                if failures:
                    raise AssertionError(
                        "client cleanup failures: "
                        + " | ".join(map(str, failures))
                        + f"; diagnostics={diagnostic_state()!r}"
                    )

    async def _wait_for(
        self,
        condition,
        *,
        timeout: float,
        description: str,
        processes: dict[str, asyncio.subprocess.Process],
        outputs: dict[int, _ProcessOutput],
        output_paths: dict[int, tuple[Path, Path]],
        status_paths: dict[str, Path],
        diagnostic_state: Callable[[], dict[str, object]],
    ) -> None:
        async def diagnostics() -> str:
            for process in processes.values():
                if process.returncode is not None and id(process) in outputs:
                    load_process_output(outputs[id(process)], output_paths.get(id(process)))
            state = {
                "wait": description,
                "processes": {
                    player_id: {
                        "pid": process.pid,
                        "returncode": process.returncode,
                        "stdout": bytes(outputs[id(process)].stdout_tail).decode(errors="replace"),
                        "stderr": bytes(outputs[id(process)].stderr_tail).decode(errors="replace"),
                        "status_exists": status_paths[player_id].exists(),
                    }
                    for player_id, process in processes.items()
                },
            }
            state.update(diagnostic_state())
            return repr(state)

        async def wait() -> None:
            while not condition():
                for player_id, process in processes.items():
                    if process.returncode is not None and not status_paths[player_id].exists():
                        raise AssertionError(
                            f"child exited while waiting for {description}: {await diagnostics()}"
                        )
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(wait(), timeout)
        except TimeoutError as error:
            raise AssertionError(
                f"timed out waiting for {description}: {await diagnostics()}"
            ) from error
