"""Regression for a valid opportunity lost before admission counter commit."""

import asyncio
import unittest

from ai_client.brain import BrainDispatchResult, DecisionOutcome, DecisionStatus
from ai_client.network import PhaseTimingMapped
from ai_client.reaction_chat.frequency import SpeakingProfile
from tests.test_phase5_speaking_frequency import (
    _FakeClock, _emit_chat, _event, _make_stack, _sync_payload, _wait_until,
)


def stale():
    return BrainDispatchResult(DecisionOutcome(DecisionStatus.STALE), None)


class UnstartedStaleTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_and_reaction_reuse_exact_frequency_approval(self):
        for peer in (False, True):
            with self.subTest(peer=peer):
                reaction, world, source, brain, sender = _make_stack(SpeakingProfile(
                    talkativeness=1, cooldown_seconds=0,
                    initial_event_importance=0 if peer else 1,
                ))
                original = reaction.invoker.invoke
                calls = []

                async def invoke(*, on_brain_start=None, **kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1:
                        self.assertEqual(reaction._phase_chat_invocations, 0)
                        return stale()
                    return await original(on_brain_start=on_brain_start, **kwargs)

                reaction.invoker.invoke = invoke
                reaction.start()
                try:
                    if peer:
                        await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
                        _emit_chat(world, source, player_id="p7", message="A fresh question?")
                    await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
                    snapshot = reaction.snapshot()
                    before, after = snapshot.outcomes[-2:]
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(len(brain.requests), 1)
                    self.assertEqual(len(sender.calls), 1)
                    self.assertEqual(snapshot.frequency_state.committed_brain_invocations, 1)
                    self.assertEqual(snapshot.frequency_state.evaluation_count, 2 if peer else 1)
                    self.assertEqual(before.frequency_evaluation_ordinal, after.frequency_evaluation_ordinal)
                    self.assertEqual(before.frequency_draw, after.frequency_draw)
                    self.assertEqual(before.frequency_source_fingerprint, after.frequency_source_fingerprint)
                    self.assertIsNone(after.frequency_suppression)
                    self.assertFalse(reaction._chat_deadline_closed)
                    if peer:
                        self.assertEqual(snapshot.frequency_state.recent_source_fingerprints,
                                         (after.frequency_source_fingerprint,))
                finally:
                    await reaction.stop()
                    await reaction.invoker.stop()

    async def test_same_version_stale_waits_without_hot_loop_or_attempt_charge(self):
        reaction, world, source, brain, sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0))
        calls = []

        async def invoke(*, on_brain_start=None, **kwargs):
            calls.append(world.snapshot().version)
            return stale()

        reaction.invoker.invoke = invoke
        reaction.start()
        try:
            await _wait_until(lambda: len(calls) == 2)
            await asyncio.sleep(0.03)
            self.assertEqual(len(calls), 2)
            self.assertEqual(reaction._phase_chat_invocations, 0)
            self.assertEqual(reaction.snapshot().frequency_state.evaluation_count, 1)
            self.assertFalse(reaction._chat_deadline_closed)
            _emit_chat(world, source, player_id="p7", message="New context")
            await _wait_until(lambda: len(calls) > 2)
            await asyncio.sleep(0.03)
            # Each opportunity is bounded within the same observed version.
            self.assertLessEqual(len(calls), 5)
            self.assertEqual(brain.requests, [])
            self.assertEqual(sender.calls, [])
        finally:
            await reaction.stop()
            await reaction.invoker.stop()
        self.assertIsNone(reaction._pending_initial)
        self.assertIsNone(reaction._pending_reaction)

    async def test_transient_uncaught_up_world_preserves_initial_until_consumer_drains(self):
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0))
        original = reaction.invoker.invoke
        calls = 0

        async def invoke(*, on_brain_start=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                source.advance(2)  # Receiver is ahead; World has not consumed it.
                return stale()
            return await original(on_brain_start=on_brain_start, **kwargs)

        reaction.invoker.invoke = invoke
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            self.assertEqual(calls, 1)
            self.assertFalse(reaction._chat_deadline_closed)
            world._consume(_event("chat.message", 2, {
                "channel": "public", "message": {
                    "player_id": "p7", "display_name": "P7", "message": "Update",
                },
            }))
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(reaction.snapshot().outcomes[0].frequency_draw,
                             reaction.snapshot().outcomes[1].frequency_draw)
        finally:
            await reaction.stop()
            await reaction.invoker.stop()

    async def test_committed_stale_deadline_and_cancel_are_not_rearmed(self):
        for kind in ("committed", "deadline", "cutoff", "cancel"):
            with self.subTest(kind=kind):
                clock = _FakeClock(10)
                reaction, _world, _source, brain, sender = _make_stack(
                    SpeakingProfile(talkativeness=1, cooldown_seconds=0), clock=clock)
                calls = 0

                async def invoke(*, on_brain_start=None, **kwargs):
                    nonlocal calls
                    calls += 1
                    if kind == "committed":
                        on_brain_start()
                    if kind == "cutoff":
                        clock.value = 20
                    if kind == "cancel":
                        raise asyncio.CancelledError
                    return BrainDispatchResult(DecisionOutcome(
                        DecisionStatus.DEADLINE_SUPPRESSED if kind == "deadline"
                        else DecisionStatus.STALE), None)

                reaction.invoker.invoke = invoke
                reaction.start()
                try:
                    await _wait_until(lambda: reaction._task.done() if kind == "cancel"
                                      else bool(reaction.snapshot().outcomes))
                    await asyncio.sleep(0.01)
                    self.assertEqual(calls, 1)
                    self.assertEqual(reaction._phase_chat_invocations,
                                     1 if kind == "committed" else 0)
                    self.assertEqual(brain.requests, [])
                    self.assertEqual(sender.calls, [])
                    self.assertIsNone(reaction._pending_initial)
                    if kind != "cancel":
                        self.assertTrue(reaction._chat_deadline_closed)
                finally:
                    await reaction.stop()
                    await reaction.invoker.stop()

    async def test_mapping_replacement_discards_old_policy_approval(self):
        clock = _FakeClock(10)
        reaction, world, source, brain, _sender = _make_stack(
            SpeakingProfile(talkativeness=1, cooldown_seconds=0), clock=clock)
        original = reaction.invoker.invoke
        mappings = []

        async def invoke(*, on_brain_start=None, **kwargs):
            mappings.append(kwargs["dispatch_deadline"].mapping_order)
            if len(mappings) == 1:
                world._consume(PhaseTimingMapped(
                    "day", 1, source.snapshot().last_seq, 1, 3, 101, 112, 10, 21))
                return stale()
            return await original(on_brain_start=on_brain_start, **kwargs)

        reaction.invoker.invoke = invoke
        reaction.start()
        try:
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            self.assertEqual(len(mappings), 2)
            self.assertNotEqual(mappings[0], mappings[1])
            self.assertEqual(len(brain.requests), 1)
            self.assertEqual(reaction.snapshot().frequency_state.evaluation_count, 2)
        finally:
            await reaction.stop()
            await reaction.invoker.stop()

    async def test_action_removal_and_phase_change_discard_stale_work(self):
        for day in (1, 2):
            with self.subTest(day=day):
                reaction, world, source, brain, sender = _make_stack(
                    SpeakingProfile(talkativeness=1, cooldown_seconds=0))
                calls = 0

                async def invoke(*, on_brain_start=None, **kwargs):
                    nonlocal calls
                    calls += 1
                    source.replace_phase(seq=2, day=day, action_generation=4, chat=False)
                    world._consume(_event("game.state_sync", 2,
                                          _sync_payload(day=day, chat=False), day=day))
                    return stale()

                reaction.invoker.invoke = invoke
                reaction.start()
                try:
                    await _wait_until(lambda: bool(reaction.snapshot().outcomes))
                    await asyncio.sleep(0.01)
                    self.assertEqual(calls, 1)
                    self.assertIsNone(reaction._pending_initial)
                    self.assertIsNone(reaction._pending_reaction)
                    self.assertEqual(brain.requests, [])
                    self.assertEqual(sender.calls, [])
                finally:
                    await reaction.stop()
                    await reaction.invoker.stop()

    async def test_new_peer_trigger_replaces_old_stale_reaction_approval(self):
        reaction, world, source, brain, _sender = _make_stack(SpeakingProfile(
            talkativeness=1, cooldown_seconds=0, initial_event_importance=0,
            ordinary_event_importance=1,
        ))
        original = reaction.invoker.invoke
        calls = 0

        async def invoke(*, on_brain_start=None, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                _emit_chat(world, source, player_id="p7", message="A newer question")
                return stale()
            return await original(on_brain_start=on_brain_start, **kwargs)

        reaction.invoker.invoke = invoke
        reaction.start()
        try:
            await _wait_until(lambda: len(reaction.snapshot().outcomes) == 1)
            _emit_chat(world, source, player_id="p7", message="An old question")
            await _wait_until(lambda: reaction.snapshot().accepted_count == 1)
            before, after = reaction.snapshot().outcomes[-2:]
            self.assertEqual(calls, 2)
            self.assertEqual(len(brain.requests), 1)
            self.assertNotEqual(before.trigger.source_order, after.trigger.source_order)
            self.assertNotEqual(before.frequency_source_fingerprint, after.frequency_source_fingerprint)
            self.assertEqual(reaction.snapshot().frequency_state.evaluation_count, 3)
        finally:
            await reaction.stop()
            await reaction.invoker.stop()
