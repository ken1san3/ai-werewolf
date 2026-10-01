"""Main-only independent source-derived trust root for the saved connector."""
from dataclasses import replace
import hashlib

from scripts import phase6_quality_probe_v2 as probe
from tests.fixtures.phase6_s4_projected_ability_provenance import (
    ProjectedAbilityInputError, freeze_projected_ability_trust,
)


def verify_pinned_code(*, path, expected_sha256):
    if (type(expected_sha256) is not str or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256):
        raise ProjectedAbilityInputError("SOURCE_CODE")


def derive_source_freeze(*, fixture, probe_source_sha256):
    try:
        probe.verify_bindings(fixture)
    except Exception:
        raise ProjectedAbilityInputError("FIXTURE") from None
    selected = fixture
    if fixture.channel is None:
        public = [entry["channel_id"] for entry in fixture.source["context"]["chat_channels"]
                  if entry.get("is_public") is True]
        if len(public) != 1:
            raise ProjectedAbilityInputError("SOURCE")
        selected = replace(fixture, channel=public[0])
    return freeze_projected_ability_trust(
        fixture=selected, fixture_builder_source_sha256=probe_source_sha256,
        binding_verifier_source_sha256=probe_source_sha256)
