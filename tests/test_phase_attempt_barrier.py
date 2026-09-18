from tests.fixtures.phase_attempt_barrier import (
    acknowledge_attempt, attempt_path, phase_attempts_complete,
)


def test_tick_permission_requires_every_current_phase_attempt(tmp_path):
    paths = [tmp_path / f"player-{index}.ready.json" for index in range(9)]
    complete = lambda: phase_attempts_complete(paths, 5, "night", count=9)
    assert not complete()
    for path in paths[:-1]:
        acknowledge_attempt(path, 5, "night")
    assert not complete()
    acknowledge_attempt(paths[-1], 5, "execution")
    assert not complete()
    acknowledge_attempt(paths[-1], 4, "night")
    assert not complete()
    acknowledge_attempt(paths[-1], 5, "night")
    assert complete()
    assert not phase_attempts_complete(paths, 6, "dawn", count=9)


def test_missing_duplicate_or_partial_acknowledgements_fail_closed(tmp_path):
    path = tmp_path / "player.ready.json"
    acknowledge_attempt(path, 0, "night0")
    assert not phase_attempts_complete([], 0, "night0", count=0)
    assert not phase_attempts_complete([path], 0, "night0", count=9)
    assert not phase_attempts_complete([path, path], 0, "night0", count=2)
    attempt_path(path).write_text('{"day":', encoding="utf-8")
    assert not phase_attempts_complete([path], 0, "night0", count=1)
    acknowledge_attempt(path, 0, "night0")
    assert phase_attempts_complete([path], 0, "night0", count=1)
