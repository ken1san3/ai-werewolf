"""Collection markers for the deliberately small set of heavy completion tests."""

from __future__ import annotations

import pytest


_COMPLETION_TESTS = {
    (
        "tests/test_phase2_completion.py",
        "test_separate_process_clients_complete_game_using_only_protocol_actions_and_server_ticks",
    ),
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        module, _, qualified_name = item.nodeid.partition("::")
        test_name = qualified_name.rsplit("::", 1)[-1]
        if (module, test_name.split("[", 1)[0]) in _COMPLETION_TESTS:
            item.add_marker(pytest.mark.completion)
