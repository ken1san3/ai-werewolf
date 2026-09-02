from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from server.aiwolf_core import GameState, InMemoryEventSink, PlayerConfig, load_content, load_preset
from server.network import GameRegistry, SessionManager, WebSocketGameServer, monotonic_seconds
from ai_client.network import ClientLifecycle, ClientSnapshot, ServerEvent
from ai_client.network.types import immutable_mapping
from ai_client.world import WorldState
from tests.fixtures.phase3_2_world_client_process import semantic_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT = PROJECT_ROOT / "tests" / "fixtures" / "phase3_2_world_client_process.py"
GAME_ID = "123e4567-e89b-12d3-a456-426614174262"


class _AuthoritativeSyncSource:
    def __init__(self) -> None:
        self._snapshot = ClientSnapshot(
            lifecycle=ClientLifecycle.ENDED,
            last_seq=1,
        )

    def snapshot(self) -> ClientSnapshot:
        return self._snapshot

    async def events(self):
        if False:
            yield None


def _semantic_world_from_sync(payload: dict[str, object]) -> dict[str, object]:
    world = WorldState(_AuthoritativeSyncSource())
    world._consume(
        ServerEvent(
            type="game.state_sync",
            protocol_version="1.0",
            event_id="authoritative-sync",
            game_id=GAME_ID,
            seq=1,
            timestamp=1,
            payload=immutable_mapping(payload),
        )
    )
    return semantic_snapshot(world.snapshot(), world.history())


def _client_visible_sync(payload: dict[str, object]) -> dict[str, object]:
    """Match the wire-visible terminal stream, which omits game_end phase start."""

    visible = deepcopy(payload)
    visible["history"] = [
        entry
        for entry in visible["history"]
        if not (
            entry.get("type") == "game.event"
            and entry.get("payload", {}).get("event_type") == "PHASE_STARTED"
            and entry.get("payload", {}).get("event_payload", {}).get("phase") == "game_end"
        )
    ]
    return visible


def _client_visible_terminal_phase(expected: dict[str, object]) -> dict[str, object]:
    """Derive the last phase that the terminal wire stream exposes."""

    for record in reversed(expected["history"]):
        if record.get("record_type") == "PhaseTransitionRecord":
            return {
                "phase": record["phase"],
                "day": record["day"],
                "phase_ends_at": record["phase_ends_at"],
                "record_type": "PhaseView",
            }
    raise AssertionError("authoritative semantic history has no visible phase transition")


def _assert_semantic_world_matches(
    testcase: unittest.TestCase,
    actual: dict[str, object],
    expected: dict[str, object],
    *,
    ignored_fields: frozenset[str] = frozenset(),
) -> None:
    """Compare client and authoritative semantic facts without weakening expected data."""

    compared_actual = {
        key: value for key, value in actual.items() if key not in ignored_fields
    }
    compared_expected = {
        key: value for key, value in expected.items() if key not in ignored_fields
    }
    testcase.assertEqual(compared_actual, compared_expected)


class PhaseThreeTwoCompletionTests(unittest.IsolatedAsyncioTestCase):
    def test_semantic_completion_guard_rejects_dropped_history_record(self) -> None:
        expected = {
            "history": [{"record_type": "ChatRecord", "order": 1}],
            "history_retention": {"retained_count": 1},
        }
        mutated = deepcopy(expected)
        mutated["history"].pop()

        with self.assertRaises(AssertionError):
            _assert_semantic_world_matches(self, mutated, expected)

    async def test_nine_world_clients_recover_from_in_retention_replay(self) -> None:
        await self._complete_after_restart(replay_history_limit=128, restart_delay=0.0)

    async def test_nine_world_clients_recover_from_out_of_retention_sync(self) -> None:
        await self._complete_after_restart(replay_history_limit=1, restart_delay=1.0)

    async def _complete_after_restart(
        self, *, replay_history_limit: int, restart_delay: float
    ) -> None:
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
        server = WebSocketGameServer(registry, sessions=sessions, tick_interval_seconds=0.02)
        listener = await server.start("127.0.0.1", 0)
        uri = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        processes: dict[str, asyncio.subprocess.Process] = {}
        observed_pids: set[int] = set()

        with TemporaryDirectory() as directory:
            root = Path(directory)

            async def start_client(player_id: str) -> asyncio.subprocess.Process:
                process = await asyncio.create_subprocess_exec(
                    os.sys.executable,
                    str(CLIENT),
                    "--uri", uri,
                    "--game-id", GAME_ID,
                    "--entry-token", registry.entry_tokens_for(GAME_ID)[player_id],
                    "--credentials", str(root / f"{player_id}.credentials.json"),
                    "--status", str(root / f"{player_id}.status.json"),
                    "--ready", str(root / f"{player_id}.ready"),
                    cwd=str(PROJECT_ROOT),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                observed_pids.add(process.pid)
                return process

            try:
                for player_id in game.players:
                    processes[player_id] = await start_client(player_id)
                await self._wait_for(
                    lambda: all(
                        (root / f"{player_id}.credentials.json").exists()
                        for player_id in game.players
                    ),
                    10,
                    "all clients to store their connection tokens",
                )
                await self._wait_for(
                    lambda: all((root / f"{player_id}.ready").exists() for player_id in game.players),
                    15,
                    "all clients to consume their initial state sync",
                )

                # Keep the restarted seat alive long enough to observe both
                # replay and the authoritative sync after the interruption.
                restarted_player = tuple(game.players)[-1]
                session = sessions.session_for(GAME_ID)
                checkpoint_seq = self._checkpoint_seq(root, restarted_player)
                checkpoint_seq = await self._queue_recovery_event_until_unread(
                    server,
                    session,
                    root,
                    restarted_player,
                    checkpoint_seq,
                    replay_history_limit,
                )
                first_process = processes[restarted_player]
                first_pid = first_process.pid
                first_process.kill()
                await first_process.wait()
                if restart_delay:
                    await asyncio.sleep(restart_delay)
                if replay_history_limit == 128:
                    await self._wait_for(
                        lambda: any(
                            reply.seq > checkpoint_seq
                            for reply in session._history_by_player.get(restarted_player, ())
                        ),
                        10,
                        "an in-retention replay event",
                    )
                else:
                    await self._wait_for(
                        lambda: session._replay_floor_by_player.get(restarted_player, 0) > checkpoint_seq,
                        10,
                        "the replay floor to pass the checkpoint",
                    )
                replay_floor_at_restart = session._replay_floor_by_player.get(restarted_player, 0)
                history_sequences_at_restart = [
                    reply.seq
                    for reply in session._history_by_player.get(restarted_player, ())
                ]
                if replay_history_limit == 128:
                    self.assertGreaterEqual(checkpoint_seq, replay_floor_at_restart)
                    self.assertTrue(
                        any(seq > checkpoint_seq for seq in history_sequences_at_restart),
                        (checkpoint_seq, replay_floor_at_restart, history_sequences_at_restart),
                    )
                else:
                    self.assertLess(checkpoint_seq, replay_floor_at_restart)
                processes[restarted_player] = await start_client(restarted_player)
                self.assertNotEqual(first_pid, processes[restarted_player].pid)

                await self._wait_for(lambda: game.game_result is not None, 45, "game end")
                await self._wait_for(
                    lambda: all((root / f"{player_id}.status.json").exists() for player_id in game.players),
                    15,
                    "all world client statuses",
                )
                statuses = {
                    player_id: json.loads(
                        (root / f"{player_id}.status.json").read_text(encoding="utf-8")
                    )
                    for player_id in game.players
                }
                self.assertTrue(all(status["game_end"] for status in statuses.values()), statuses)
                self.assertTrue(all(status["freshness"] == "ENDED" for status in statuses.values()), statuses)
                self.assertTrue(all(status["players"] == 9 for status in statuses.values()), statuses)
                self.assertTrue(all(not status["send_errors"] for status in statuses.values()), statuses)
                self.assertTrue(all(status["unknown_event_count"] == 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["malformed_event_count"] == 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["history_records"] > 0 for status in statuses.values()), statuses)
                self.assertTrue(all(status["production_import_guard"] for status in statuses.values()), statuses)
                self.assertEqual(statuses[restarted_player]["recovery"]["session_resumed_count"], 1, statuses)
                recovery = statuses[restarted_player]["recovery"]
                self.assertEqual(recovery["resume_checkpoints"], [checkpoint_seq], statuses)
                self.assertGreater(
                    recovery["resume_event_sequences"][0],
                    recovery["resume_checkpoints"][0],
                    statuses,
                )
                self.assertEqual(len(recovery["state_sync_sequences"]), 1, statuses)
                self.assertGreater(
                    recovery["state_sync_sequences"][0],
                    recovery["resume_event_sequences"][0],
                    statuses,
                )
                if replay_history_limit == 128:
                    self.assertTrue(recovery["replay_event_sequences"], statuses)
                    self.assertEqual(recovery["gaps_detected"], [], statuses)
                    self.assertEqual(recovery["gaps_recovered"], [], statuses)
                else:
                    self.assertEqual(recovery["replay_event_sequences"], [], statuses)
                    self.assertEqual(len(recovery["gaps_detected"]), 1, statuses)
                    self.assertEqual(len(recovery["gaps_recovered"]), 1, statuses)
                    self.assertEqual(
                        recovery["gaps_recovered"][0]["recovered_seq"],
                        recovery["state_sync_sequences"][-1],
                        statuses,
                    )
                for player_id in game.players:
                    expected_semantic = _semantic_world_from_sync(
                        _client_visible_sync(game.get_state_sync(player_id))
                    )
                    # GAME_ENDED terminates the transport without a final
                    # state-sync phase update. Derive the expected phase from
                    # the last client-visible transition, never from `actual`.
                    expected_wire_semantic = {
                        **expected_semantic,
                        "phase": _client_visible_terminal_phase(expected_semantic),
                    }
                    _assert_semantic_world_matches(
                        self,
                        statuses[player_id]["semantic_world"],
                        expected_wire_semantic,
                    )
                self.assertEqual(len(observed_pids), len(game.players) + 1)
            finally:
                for process in processes.values():
                    if process.returncode is None:
                        process.terminate()
                await asyncio.gather(
                    *(self._finish_process(process) for process in processes.values()),
                    return_exceptions=True,
                )
                await server.close()

    async def _wait_for(self, condition, timeout: float, description: str) -> None:
        async def poll() -> None:
            while not condition():
                await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(poll(), timeout)
        except TimeoutError as error:
            raise AssertionError(f"timed out waiting for {description}") from error

    def _checkpoint_seq(self, root: Path, player_id: str) -> int:
        return int(
            json.loads(
                (root / f"{player_id}.credentials.json").read_text(encoding="utf-8")
            )["last_seq"]
        )

    async def _queue_recovery_event_until_unread(
        self,
        server: WebSocketGameServer,
        session,
        root: Path,
        player_id: str,
        checkpoint_seq: int,
        replay_history_limit: int,
    ) -> int:
        """Put a deterministic event after the checkpoint before interruption."""

        for index in range(6):
            await server.publish_channel_message(
                GAME_ID,
                "public",
                {
                    "player_id": player_id,
                    "display_name": f"Player {player_id.rsplit('-', 1)[-1]}",
                    "message": f"recovery-fixture-{index}",
                },
            )
            latest_checkpoint = self._checkpoint_seq(root, player_id)
            if latest_checkpoint != checkpoint_seq:
                checkpoint_seq = latest_checkpoint
            history = session._history_by_player.get(player_id, ())
            if replay_history_limit == 1:
                ready = session._replay_floor_by_player.get(player_id, 0) > checkpoint_seq
            else:
                ready = any(reply.seq > checkpoint_seq for reply in history)
            if ready:
                return checkpoint_seq
        raise AssertionError("recovery fixture events were consumed before interruption")

    async def _finish_process(self, process: asyncio.subprocess.Process) -> None:
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
