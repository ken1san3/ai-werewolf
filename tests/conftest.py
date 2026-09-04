"""Collection markers for the deliberately small set of heavy completion tests."""

from __future__ import annotations

import pytest


_COMPLETION_TESTS = {
    (
        "tests/test_phase3_1_completion.py",
        "test_nine_protocol_clients_complete_and_one_resumes",
    ),
    (
        "tests/test_phase3_1_completion.py",
        "test_separate_process_action_stop_resumes_outside_retention_and_recovers_gap",
    ),
    (
        "tests/test_phase3_1_completion.py",
        "test_separate_process_rejection_and_production_import_guard",
    ),
    (
        "tests/test_phase3_1_completion.py",
        "test_driver_failure_writes_diagnostics",
    ),
    (
        "tests/test_phase2_completion.py",
        "test_separate_process_clients_complete_game_using_only_protocol_actions_and_server_ticks",
    ),
    (
        "tests/test_phase3_2_completion.py",
        "test_nine_world_clients_recover_from_in_retention_replay",
    ),
    (
        "tests/test_phase3_2_completion.py",
        "test_nine_world_clients_recover_from_out_of_retention_sync",
    ),
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        module, _, qualified_name = item.nodeid.partition("::")
        test_name = qualified_name.rsplit("::", 1)[-1]
        if (module, test_name.split("[", 1)[0]) in _COMPLETION_TESTS:
            item.add_marker(pytest.mark.completion)
