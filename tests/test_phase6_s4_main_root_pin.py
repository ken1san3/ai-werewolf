from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from scripts import phase6_quality_probe_v2 as q
from tests.fixtures.phase6_s4_main_root_pin import derive_source_freeze, verify_pinned_code
from tests.fixtures.phase6_s4_projected_ability_provenance import (
    ProjectedAbilityInputError, freeze_projected_ability_trust,
)


def code_sha():
    return hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest()


def no_action_fixture():
    for f in q.prepare()[0]:
        if f.channel is None:
            try:
                derive_source_freeze(fixture=f, probe_source_sha256=code_sha())
            except ProjectedAbilityInputError:
                continue
            return f
    raise AssertionError("public no-action fixture missing")


def test_explicit_source_channel_can_pin_without_an_action_channel():
    f = no_action_fixture()
    original_source = deepcopy(f.source)
    with pytest.raises(ProjectedAbilityInputError):
        freeze_projected_ability_trust(fixture=f, fixture_builder_source_sha256=code_sha(),
                                      binding_verifier_source_sha256=code_sha())
    channels = [e["channel_id"] for e in f.source["context"]["chat_channels"] if e.get("is_public") is True]
    expected = freeze_projected_ability_trust(fixture=replace(f, channel=channels[0]),
        fixture_builder_source_sha256=code_sha(), binding_verifier_source_sha256=code_sha())
    actual = derive_source_freeze(fixture=f, probe_source_sha256=code_sha())
    assert actual == expected
    assert actual["public_channel_id"] == channels[0]
    assert f.channel is None and f.source == original_source


def test_already_explicit_action_channel_is_unchanged():
    f = next(f for f in q.prepare()[0] if f.channel is not None)
    direct = freeze_projected_ability_trust(fixture=f, fixture_builder_source_sha256=code_sha(),
        binding_verifier_source_sha256=code_sha())
    assert derive_source_freeze(fixture=f, probe_source_sha256=code_sha()) == direct


def test_source_pin_rejects_changed_code_bytes(tmp_path):
    path = tmp_path / "helper-source.py"
    path.write_bytes(b"original logic")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    verify_pinned_code(path=path, expected_sha256=expected)
    path.write_bytes(b"different logic")
    with pytest.raises(ProjectedAbilityInputError, match="S4_INPUT_INTEGRITY") as failure:
        verify_pinned_code(path=path, expected_sha256=expected)
    assert failure.value.detail == "SOURCE_CODE"


@pytest.mark.parametrize("mutation", ["missing", "ambiguous", "bool-like"])
def test_source_does_not_supply_one_explicit_public_channel(mutation):
    f = no_action_fixture()
    source = deepcopy(f.source)
    channels = source["context"]["chat_channels"]
    public = [e for e in channels if e.get("is_public") is True]
    if mutation == "missing":
        for e in public: e["is_public"] = False
    elif mutation == "ambiguous":
        channels.append({**public[0], "channel_id": "another-public-channel"})
    else:
        for e in public: e["is_public"] = 1
    altered = replace(f, source_bytes=q.wire(source))
    with pytest.raises(ProjectedAbilityInputError):
        derive_source_freeze(fixture=altered, probe_source_sha256=code_sha())
