"""Independent randomized speech timing and phase/deadline guards."""
import asyncio


async def speech_pause(mentioned, rng, first, scale=1):
    delay = rng.uniform(1, 12) if first else rng.uniform(4, 14)
    mentioned.clear()
    try:
        await asyncio.wait_for(mentioned.wait(), delay * scale)
        await asyncio.sleep(rng.uniform(0.5, 2) * scale)
    except asyncio.TimeoutError:
        pass


def fresh(state, key, guard=0.25):
    return not state.done and state.player_id in state.alive and state.phase_key == key and state.seconds_left() > guard
