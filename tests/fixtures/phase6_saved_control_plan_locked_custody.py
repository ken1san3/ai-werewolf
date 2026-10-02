"""Main-only physical reconstruction for the T575 saved-control pin."""
from __future__ import annotations

from pathlib import Path

from tests.fixtures import phase6_saved_control_plan_locked as helper


def reconstruct_saved_control_authority(owned_root, prepared_locator, *, expected_prepared_locator_file_sha256,
        expected_input_file_sha256, expected_preflight_file_sha256, expected_source_manifest_file_sha256,
        expected_main_pin_file_sha256, expected_old_t550_seal_file_sha256, expected_code_bundle_sha256):
    """Reconstruct the external pin without accepting caller rows or roots."""
    physical = helper._reconstruct_physical(owned_root, prepared_locator,
        expected_prepared_locator_file_sha256=expected_prepared_locator_file_sha256,
        expected_input_file_sha256=expected_input_file_sha256,
        expected_preflight_file_sha256=expected_preflight_file_sha256,
        expected_source_manifest_file_sha256=expected_source_manifest_file_sha256,
        expected_main_pin_file_sha256=expected_main_pin_file_sha256,
        expected_old_t550_seal_file_sha256=expected_old_t550_seal_file_sha256,
        expected_code_bundle_sha256=expected_code_bundle_sha256)
    payload = {"contract": helper.AUTHORITY_CONTRACT,
        "prepared_locator_file_sha256": expected_prepared_locator_file_sha256,
        "input_file_sha256": expected_input_file_sha256, "preflight_file_sha256": expected_preflight_file_sha256,
        "source_manifest_file_sha256": expected_source_manifest_file_sha256, "main_pin_file_sha256": expected_main_pin_file_sha256,
        "old_t550_seal_file_sha256": expected_old_t550_seal_file_sha256,
        "source_roles_root_sha256": helper.digest(physical["roles_payload"]),
        "saved_control_members_root_sha256": helper.digest(physical["members_payload"]),
        "chosen_intent_bindings_root_sha256": helper.digest(physical["intents_payload"]),
        "fixture_bindings_root_sha256": helper.digest(physical["fixtures_payload"]),
        "code_bundle_sha256": expected_code_bundle_sha256,
        "expected_population_root_sha256": helper.digest(physical["population"])}
    return {**payload, "pin_sha256": helper.digest(payload)}, physical["population"]
