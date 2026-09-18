from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import pytest

from ai_client.brain import (
    BrainActionContext,
    BrainActionOption,
    BrainInput,
    ChatDecision,
    NoDecision,
)
from ai_client.network import ChatAction
from ai_client.world import (
    AbilityResultView,
    CoView,
    Freshness,
    HistoryView,
    PhaseView,
    WorldSnapshot,
)
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
from tests.fixtures.phase3_4_reaction_brain import (
    CompletionReactionBrain,
    CompletionReactionMode,
)
from tests.fixtures.reaction_chat_evidence import (
    AcceptedChatEvidence,
    accepted_histogram,
    first_speaker_by_phase,
    validate_rejections,
)
from tests.fixtures.reaction_frontier import (
    FirstSpeakerFrontier,
    ReactionFrontierClock,
    publish,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_4_reaction_client_process.py"


class _DayOneBarrierClock:
    """Expose the shared fixture clock as server integer game time."""

    def __init__(self) -> None:
        self.shared = None

    def bind(self, root: Path) -> None:
        self.shared = ReactionFrontierClock(root / "clock-start", root / "day-one-release")

    def __call__(self) -> int:
        return 0 if self.shared is None else int(self.shared())

    def release_initial_night(self) -> None:
        assert self.shared is not None
        if self.shared.start.exists():
            raise RuntimeError("completion initial night was released twice")
        publish(self.shared.start, {"started_ns": time.monotonic_ns()})


def _brain_input(*, include_chat: bool = True) -> BrainInput:
    phase = PhaseView("day", 1, 10)
    snapshot = WorldSnapshot(
        version=1,
        freshness=Freshness.CURRENT,
        is_caught_up=True,
        last_applied_seq=1,
        phase=phase,
    )
    options = ()
    if include_chat:
        handle = ChatAction(1, 1, "day", 1, "chat", "public")
        options = (BrainActionOption("action:0", handle),)
    retention = snapshot.history_retention
    return BrainInput(
        snapshot=snapshot,
        action_context=BrainActionContext(1, 1, 1, True, options),
        history=HistoryView((), True, retention),
        co=CoView((), (), True, retention),
        ability_results=AbilityResultView((), True, retention),
    )


class CompletionReactionBrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_speaking_and_silent_brains_follow_the_fixed_message_contract(self) -> None:
        request = _brain_input()
        first = CompletionReactionBrain(
            player_id="p0", seed=73, mode=CompletionReactionMode.SPEAK
        )
        repeated = CompletionReactionBrain(
            player_id="p0", seed=73, mode=CompletionReactionMode.SPEAK
        )
        first_decisions = [await first.decide(request), await first.decide(request)]
        repeated_decisions = [await repeated.decide(request), await repeated.decide(request)]
        self.assertEqual(first_decisions, repeated_decisions)
        self.assertEqual(
            first_decisions,
            [
                ChatDecision("action:0", "phase3.4/73/p0/1/day/1"),
                ChatDecision("action:0", "phase3.4/73/p0/1/day/2"),
            ],
        )
        self.assertIsInstance(await first.decide(_brain_input(include_chat=False)), NoDecision)
        silent = CompletionReactionBrain(
            player_id="p0", seed=73, mode=CompletionReactionMode.SILENT
        )
        self.assertIsInstance(await silent.decide(request), NoDecision)

    def test_completion_rules_override_is_local_and_has_positive_budget(self) -> None:
        content = load_content(PROJECT_ROOT / "content")
        original = load_preset(
            PROJECT_ROOT / "content" / "presets" / "standard_9.yaml", content
        )
        overridden = replace(
            original,
            rules=replace(
                original.rules,
                night_seconds=1,
                silence_after_dawn_seconds=0,
                day_seconds=2,
                vote_seconds=1,
            ),
        )
        self.assertEqual(overridden.rules.day_seconds, 2)
        self.assertNotEqual(original.rules, overridden.rules)
        self.assertGreater(2 - 0.25 - 0.20 - 0.20 - 0.15 - 0.05, 0)
        self.assertGreater(2 - 0.25 - 0.20 - 0.20 - 0.15 - 0.05 - 0.25 - 0.25, 0)


@pytest.mark.completion
class PhaseThreeFourCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_seat_and_one_silent_reaction_scenarios_complete(self) -> None:
        started = time.monotonic()
        results = {}
        for name, seed, silent_player in (
            ("all-seat", 7341, None),
            ("one-silent", 7342, "player-0"),
        ):
            with self.subTest(name=name):
                results[name] = await self._run_scenario(name, seed, silent_player)
        self.assertLess(time.monotonic() - started, 100.0)
        if set(results) == {"all-seat", "one-silent"}:
            self.assertNotEqual(
                results["all-seat"]["first_speaker"].get((1, "day")),
                results["one-silent"]["first_speaker"].get((1, "day")),
            )

    async def _run_scenario(
        self, name: str, seed: int, silent_player: str | None
    ) -> dict[str, object]:
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
        accepted: list[AcceptedChatEvidence] = []
        expected: set[tuple[str, int, str]] = set()
        original_queue = server._delivery_router.queue_channel_message  # noqa: SLF001
        original_drain = server._delivery_router.drain  # noqa: SLF001
        speaking_players = set(game.players) - ({silent_player} if silent_player else set())
        deferred_peer_deliveries: list[OutboundDelivery] = []
        first_wave_flushed = False
        second_wave_flushed = False
        day_one_budget_started: float | None = None
        day_one_completed_seconds: float | None = None

        def queue_spy(spy_game_id: str, channel: str, message, *, acceptance=None) -> None:
            self.assertEqual(spy_game_id, game_id)
            self.assertIsNotNone(acceptance)
            assert acceptance is not None
            self.assertEqual(acceptance.action, "chat.send")
            self.assertEqual(message["player_id"], acceptance.player_id)
            self.assertEqual(
                set(message), {"player_id", "display_name", "message"}
            )
            accepted.append(
                AcceptedChatEvidence(
                    player_id=message["player_id"],
                    day=acceptance.day,
                    phase=acceptance.phase,
                    channel=channel,
                    accepted_at=acceptance.accepted_at,
                    phase_deadline=acceptance.phase_deadline,
                    message=message["message"],
                )
            )
            original_queue(spy_game_id, channel, message, acceptance=acceptance)

        def drain_spy():
            nonlocal first_wave_flushed, second_wave_flushed, day_one_completed_seconds
            deliveries = original_drain()
            immediate: list[OutboundDelivery] = []
            for delivery in deliveries:
                if delivery.message_type == "player.action_state":
                    if any(action.get("type") == "chat" for action in delivery.payload["actions"]):
                        expected.update(
                            (player_id, delivery.payload["day"], delivery.payload["phase"])
                            for player_id in delivery.recipient_player_ids
                        )
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

            histogram = accepted_histogram(accepted)
            if not first_wave_flushed and all(
                histogram.get((player_id, 1, "day"), 0) >= 1
                for player_id in speaking_players
            ):
                immediate.extend(deferred_peer_deliveries)
                deferred_peer_deliveries.clear()
                first_wave_flushed = True
            elif first_wave_flushed and not second_wave_flushed and all(
                histogram.get((player_id, 1, "day"), 0) >= 2
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
        day_one_ready_paths: dict[str, Path] = {}

        with TemporaryDirectory() as temporary_directory, patch.object(
            server._delivery_router, "queue_channel_message", side_effect=queue_spy  # noqa: SLF001
        ), patch.object(server._delivery_router, "drain", side_effect=drain_spy):  # noqa: SLF001
            root = Path(temporary_directory)
            clock.bind(root)

            async def start_client(player_id: str) -> asyncio.subprocess.Process:
                status_path = root / f"{player_id}.status.json"
                ready_path = root / f"{player_id}.ready.json"
                day_one_ready_path = root / f"{player_id}.day-one-ready.json"
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
                        "--day-one-ready", str(day_one_ready_path),
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
                day_one_ready_paths[player_id] = day_one_ready_path
                return process

            try:
                launched = await asyncio.gather(*(start_client(player_id) for player_id in game.players))
                processes = dict(zip(game.players, launched))
                ready_markers: dict[str, dict[str, object]] = {}
                day_one_ready_markers: dict[str, dict[str, object]] = {}

                def load_reaction_markers(
                    paths: dict[str, Path],
                    destination: dict[str, dict[str, object]],
                    *,
                    expected_day: int,
                    expected_phase: str,
                ) -> bool:
                    observed: dict[str, dict[str, object]] = {}
                    try:
                        for player_id, path in paths.items():
                            marker = json.loads(path.read_text(encoding="utf-8"))
                            if marker["player_id"] != player_id:
                                return False
                            world = marker["world"]
                            reaction = marker["reaction"]
                            deadline = marker["deadline"]
                            phase_key = reaction["phase_key"]
                            if (
                                world["day"] != expected_day
                                or world["phase"] != expected_phase
                                or deadline["day"] != expected_day
                                or deadline["phase"] != expected_phase
                                or reaction["lifecycle"] != "running"
                                or reaction["transport_cursor"] < deadline["mapping_order"]
                                or (
                                    phase_key is not None
                                    and (
                                        phase_key["connection_generation"]
                                        != deadline["connection_generation"]
                                        or phase_key["action_generation"]
                                        != deadline["action_generation"]
                                        or phase_key["day"] != deadline["day"]
                                        or phase_key["phase"] != deadline["phase"]
                                    )
                                )
                                or (phase_key is None and expected_day != 0)
                            ):
                                return False
                            observed[player_id] = marker
                    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
                        return False
                    destination.clear()
                    destination.update(observed)
                    return set(observed) == set(game.players)

                await self._wait_for(
                    lambda: load_reaction_markers(
                        ready_paths,
                        ready_markers,
                        expected_day=0,
                        expected_phase="night0",
                    ),
                    timeout=15,
                    description=f"{name}: all Reaction controllers consumed night0 mapping",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                clock.release_initial_night()
                await self._wait_for(
                    lambda: load_reaction_markers(
                        day_one_ready_paths,
                        day_one_ready_markers,
                        expected_day=1,
                        expected_phase="day",
                    ),
                    timeout=15,
                    description=f"{name}: all Reaction controllers consumed Day 1 opportunity",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )

                day_one_budget_started = time.monotonic()
                frontier = FirstSpeakerFrontier(root / "day-one-release", tuple(game.players))

                def first_speaker_resolved() -> bool:
                    reports = {}
                    for player_id, status_path in status_paths.items():
                        try:
                            reports[player_id] = json.loads(
                                status_path.with_suffix(".frontier.json").read_text(encoding="utf-8")
                            )
                        except (FileNotFoundError, json.JSONDecodeError):
                            return False
                    return frontier.step(reports, accepted=any(
                        record.day == 1 and record.phase == "day" for record in accepted
                    ))

                await self._wait_for(
                    first_speaker_resolved,
                    timeout=2.0,
                    description=f"{name}: CO-aware first-speaker frontier",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                await self._wait_for(
                    lambda: game.game_result is not None,
                    timeout=45,
                    description=f"{name}: game end",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                await self._wait_for(
                    lambda: all(path.exists() for path in status_paths.values()),
                    timeout=15,
                    description=f"{name}: client status",
                    processes=processes,
                    outputs=outputs,
                    output_paths=output_paths,
                    status_paths=status_paths,
                )
                statuses = {
                    player_id: json.loads(path.read_text(encoding="utf-8"))
                    for player_id, path in status_paths.items()
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(
                    all(status["production_import_guard"] for status in statuses.values()),
                    statuses,
                )
                validate_rejections(statuses)
                histogram = accepted_histogram(accepted)
                target = (1, "day")
                expected_target = {
                    player_id for player_id, day, phase in expected if (day, phase) == target
                }
                self.assertEqual(expected_target, set(game.players))
                self.assertTrue(first_wave_flushed, (name, histogram))
                self.assertTrue(second_wave_flushed, (name, histogram))
                self.assertEqual(deferred_peer_deliveries, [])
                self.assertIsNotNone(day_one_completed_seconds)
                assert day_one_completed_seconds is not None
                self.assertLess(day_one_completed_seconds, 2.0)
                for player_id in game.players:
                    count = histogram.get((player_id, *target), 0)
                    self.assertLessEqual(count, 2, (name, player_id, histogram, statuses[player_id]))
                    if player_id == silent_player:
                        self.assertEqual(count, 0, (name, player_id, histogram))
                    elif silent_player is None:
                        self.assertEqual(count, 2, (name, player_id, histogram, statuses[player_id]))
                    else:
                        self.assertGreaterEqual(count, 1, (name, player_id, histogram, statuses[player_id]))
                self.assertTrue(
                    all(record.accepted_at < record.phase_deadline for record in accepted),
                    accepted,
                )
                if silent_player is not None:
                    self.assertEqual(
                        statuses[silent_player]["reaction"]["accepted_count"], 0
                    )
                    self.assertGreater(
                        statuses[silent_player]["reaction"]["intentional_silence_count"], 0
                    )
                self.assertEqual(
                    len([event for event in game.event_bus.events if event.type == "GAME_ENDED"]),
                    1,
                )
                return {
                    "histogram": histogram,
                    "first_speaker": first_speaker_by_phase(accepted),
                    "ready_markers": ready_markers,
                    "day_one_ready_markers": day_one_ready_markers,
                    "day_one_completed_seconds": day_one_completed_seconds,
                    "statuses": statuses,
                }
            finally:
                cleanup_results = await asyncio.gather(
                    *(
                        finish_process(
                            process,
                            output=outputs.get(id(process)),
                            label=f"{name} reaction client {player_id}",
                            status_path=status_paths.get(player_id),
                            output_paths=output_paths.get(id(process)),
                        )
                        for player_id, process in processes.items()
                    ),
                    return_exceptions=True,
                )
                await server.close()
                failures = [result for result in cleanup_results if isinstance(result, Exception)]
                if failures:
                    raise AssertionError("client cleanup failures: " + " | ".join(map(str, failures)))

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
    ) -> None:
        async def diagnostics() -> str:
            for process in processes.values():
                if process.returncode is not None and id(process) in outputs:
                    load_process_output(outputs[id(process)], output_paths.get(id(process)))
            return repr(
                {
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
            )

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


if __name__ == "__main__":
    unittest.main()
