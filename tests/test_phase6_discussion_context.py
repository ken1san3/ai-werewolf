from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from ai_client.discussion.context import (
    BOOTSTRAP_SCHEMA_VERSION,
    CONTEXT_SCHEMA_VERSION,
    MAX_BOOTSTRAP_BYTES,
    MAX_CONTEXT_BYTES,
    MAX_MANIFEST_BYTES,
    MANIFEST_SCHEMA_VERSION,
    AuthorizedChatChannelContext,
    AuthorizedDiscussionContext,
    CountParityWinCondition,
    DiscussionAbilityContext,
    DiscussionContextError,
    DiscussionPassiveContext,
    EliminateRoleTagWinCondition,
    SurviveWhenOthersWinCondition,
    canonical_json_bytes,
    canonical_sha256,
    parse_authorized_discussion_context,
    validate_discussion_bootstrap,
)
from ai_client.discussion.model import MAX_ID_SCALARS, MAX_ID_UTF8_BYTES
from ai_client.world import (
    Freshness,
    HistoryRetention,
    PhaseView,
    PlayerView,
    SelfView,
    WorldSnapshot,
)


def _rules() -> dict[str, object]:
    return {
        "first_night_seer": "opaque-policy",
        "vote": {
            "runoff": True,
            "tie_after_runoff": "opaque-tie-a",
            "tie_without_runoff": "opaque-tie-b",
            "abstain": {"enabled": True, "max_per_player": None},
            "self_vote": False,
            "reveal": "opaque-reveal",
        },
        "guard": {"consecutive": False, "self_guard": False},
        "night_action": {"no_selection": None},
        "medium": {"notify_timing": "opaque-timing"},
        "wolf_attack": {"target_decision": "opaque-choice", "tie": "opaque-tie"},
        "co": {"max_per_day": 3, "allow_villager_claim": False},
        "sudden_death": {"enabled": False},
        "death": {"public_detail": "opaque-detail"},
        "graveyard": {"view_public": True, "speak": False, "reveal_roles": False},
        "role_missing": {"enabled": False, "replacement_role_id": "opaque-role"},
        "day_seconds": 180,
        "vote_seconds": 60,
        "night_seconds": 60,
        "silence_after_dawn_seconds": 15,
        "extension": {"max_count": 0, "seconds_per_extension": 120, "approval": "opaque-approval"},
        "shortening": {"enabled": False, "approval": "opaque-approval"},
        "win_evaluation_order": ["opaque-team"],
    }


def _manifest() -> dict[str, object]:
    condition = {
        "type": "count_parity",
        "data": {
            "type": "count_parity",
            "subject": "opaque-side-a",
            "against": "opaque-side-b",
            "operator": "gte",
        },
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "content_pack": {
            "teams": {
                "opaque-team": {
                    "id": "opaque-team",
                    "name": "private-team-name-sentinel",
                    "default_count_as": "opaque-side-a",
                    "default_inspect_result": "opaque-result-a",
                    "default_medium_result": "opaque-result-b",
                    "win_conditions": [condition],
                }
            },
            "roles": {
                "opaque-role": {
                    "id": "opaque-role",
                    "name": "other-seat-role-name-sentinel",
                    "claimable": False,
                    "attributes": {
                        "team": "opaque-team",
                        "count_as": "opaque-side-a",
                        "attack_result": "opaque-attack",
                        "inspect_result": "opaque-result-a",
                        "medium_result": "opaque-result-b",
                    },
                    "tags": [],
                    "knowledge": {"declarations": {"knows_teammates": False}},
                    "chat_channels": ["opaque-private", "opaque-public"],
                    "abilities": [],
                    "passives": [],
                    "options": {},
                }
            },
            "effects": {},
            "passives": {},
            "selectors": {},
            "restriction_types": {},
            "action_timings": {},
            "chat_channels": {
                "opaque-private": {
                    "id": "opaque-private",
                    "name": "other-channel-private-sentinel",
                    "phases": ["opaque-phase"],
                    "allows_co": False,
                    "is_public": False,
                },
                "opaque-public": {
                    "id": "opaque-public",
                    "name": "other-channel-public-sentinel",
                    "phases": ["opaque-phase"],
                    "allows_co": True,
                    "is_public": True,
                },
            },
            "death_causes": {},
            "modifiers": {},
        },
        "effective_preset": {
            "name": "preset-count-sentinel",
            "rules": _rules(),
            "role_counts": {"opaque-role": 1},
        },
    }


def _context(manifest_hash: str) -> AuthorizedDiscussionContext:
    return AuthorizedDiscussionContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        game_id="opaque-game",
        player_id="opaque-player",
        role_id="opaque-role",
        modifier_ids=(),
        content_manifest_sha256=manifest_hash,
        team="opaque-team",
        count_as="opaque-side-a",
        attack_result="opaque-attack",
        inspect_result="opaque-result-a",
        medium_result="opaque-result-b",
        win_conditions=(
            CountParityWinCondition(
                type="count_parity",
                subject="opaque-side-a",
                against="opaque-side-b",
                operator="gte",
            ),
        ),
        abilities=(),
        passives=(),
        chat_channels=(
            AuthorizedChatChannelContext("opaque-private", False),
            AuthorizedChatChannelContext("opaque-public", True),
        ),
        knows_teammates=False,
        authorized_known_player_ids=(),
        known_players_complete=False,
    )


def _payload(value: object) -> object:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _envelope() -> dict[str, object]:
    manifest = _manifest()
    manifest_hash = canonical_sha256(manifest)
    context = _context(manifest_hash)
    return {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "manifest_material": manifest,
        "manifest_sha256": manifest_hash,
        "context_payload": _payload(context),
        "context_sha256": canonical_sha256(context),
    }


def _rehash_envelope(envelope: dict[str, object]) -> None:
    manifest_hash = canonical_sha256(envelope["manifest_material"])
    envelope["manifest_sha256"] = manifest_hash
    envelope["context_payload"]["content_manifest_sha256"] = manifest_hash
    envelope["context_sha256"] = canonical_sha256(envelope["context_payload"])


def _chat_channel(channel_id: str, *, is_public: bool = False) -> dict[str, object]:
    return {
        "id": channel_id,
        "name": f"name-{channel_id}",
        "phases": ["opaque-phase"],
        "allows_co": False,
        "is_public": is_public,
    }


def _modifier(
    modifier_id: str,
    *,
    overrides: dict[str, object] | None = None,
    win_condition: dict[str, object] | None = None,
) -> dict[str, object]:
    effective_overrides = {
        "team": None,
        "count_as": None,
        "attack_result": None,
        "inspect_result": None,
        "medium_result": None,
    }
    if overrides is not None:
        effective_overrides.update(overrides)
    return {
        "id": modifier_id,
        "name": f"name-{modifier_id}",
        "grant": {
            "timing": "assignment",
            "duration": "permanent",
            "duration_nights": None,
        },
        "win_condition": win_condition
        or {"mode": "none", "value": None, "priority": None},
        "passives": [],
        "knowledge": {"declarations": {"knows_teammates": False}},
        "chat_channels": [],
        "overrides": effective_overrides,
        "exclusions": [],
    }


def _select_modifiers(
    envelope: dict[str, object],
    modifiers: tuple[dict[str, object], ...],
) -> None:
    modifier_ids = tuple(sorted(modifier["id"] for modifier in modifiers))
    envelope["manifest_material"]["content_pack"]["modifiers"] = {
        modifier["id"]: modifier for modifier in modifiers
    }
    envelope["context_payload"]["modifier_ids"] = list(modifier_ids)


def _count_parity_manifest_condition() -> dict[str, object]:
    return deepcopy(
        _manifest()["content_pack"]["teams"]["opaque-team"]["win_conditions"][0]
    )


def _snapshot(*, role_id: str = "opaque-role", modifiers: tuple[str, ...] = ()) -> WorldSnapshot:
    return WorldSnapshot(
        version=1,
        freshness=Freshness.CURRENT,
        is_caught_up=True,
        last_applied_seq=1,
        players=(PlayerView("opaque-player", "owner-private-display"),),
        alive_player_ids=("opaque-player",),
        phase=PhaseView("opaque-phase", 1),
        self_view=SelfView("opaque-player", role_id, modifiers),
        history_retention=HistoryRetention(
            total_seen=0,
            retained_count=0,
            retained_bytes=0,
            dropped_count=0,
            dropped_through_order=None,
            first_retained_order=None,
            last_order=None,
            max_history_records=16,
            max_history_bytes=4096,
            complete=True,
        ),
    )


def test_canonical_json_is_deterministic_closed_and_unicode_preserving() -> None:
    left = {"z": [2, 1], "a": {"β", "a"}, "旗": True}
    right = {"旗": True, "a": {"a", "β"}, "z": (2, 1)}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert b"\\u" not in canonical_json_bytes({"日本": "語"})
    assert canonical_sha256(left) == canonical_sha256(right)
    for invalid in (1.0, b"bytes", object(), "\ud800"):
        with pytest.raises(DiscussionContextError):
            canonical_json_bytes(invalid)


def test_canonical_json_fixed_unicode_known_answer() -> None:
    value = {"z": (2, 1), "a": {"a", "β"}, "旗": True}
    expected_bytes = (
        b'{"a":["a","\xce\xb2"],"z":[2,1],"\xe6\x97\x97":true}'
    )
    assert len(expected_bytes) == 37
    assert canonical_json_bytes(value) == expected_bytes
    assert canonical_sha256(value) == (
        "9d83d20ac92c10f6b26990d54926e1191c63ba6759feb371561fdc8e501bf892"
    )


def test_design_context_and_identifier_limits_are_literal() -> None:
    assert MAX_CONTEXT_BYTES == 8192
    assert MAX_MANIFEST_BYTES == 65536
    assert MAX_BOOTSTRAP_BYTES == 81920
    assert MAX_ID_SCALARS == 128
    assert MAX_ID_UTF8_BYTES == 512


@pytest.mark.parametrize(
    "channel_id,accepted,expected_scalars,expected_bytes",
    (
        ("x" * 127, True, 127, 127),
        ("x" * 128, True, 128, 128),
        ("x" * 129, False, 129, 129),
        ("\N{GRINNING FACE}" * 127 + "界", True, 128, 511),
        ("\N{GRINNING FACE}" * 128, True, 128, 512),
        ("\N{GRINNING FACE}" * 128 + "x", False, 129, 513),
    ),
)
def test_identifier_scalar_and_utf8_boundaries(
    channel_id: str,
    accepted: bool,
    expected_scalars: int,
    expected_bytes: int,
) -> None:
    assert len(channel_id) == expected_scalars
    assert len(channel_id.encode("utf-8")) == expected_bytes
    if accepted:
        assert AuthorizedChatChannelContext(channel_id, False).channel_id == channel_id
    else:
        with pytest.raises(DiscussionContextError):
            AuthorizedChatChannelContext(channel_id, False)


def test_context_is_frozen_and_rejects_descriptor_bool_order_and_bounds() -> None:
    context = _context("0" * 64)
    with pytest.raises(FrozenInstanceError):
        context.player_id = "changed"  # type: ignore[misc]
    with pytest.raises(DiscussionContextError):
        AuthorizedChatChannelContext("opaque", 1)  # type: ignore[arg-type]
    with pytest.raises(DiscussionContextError):
        AuthorizedDiscussionContext(
            **{
                **context.__dict__,
                "chat_channels": tuple(reversed(context.chat_channels)),
            }
        )
    with pytest.raises(DiscussionContextError):
        AuthorizedDiscussionContext(
            **{
                **context.__dict__,
                "chat_channels": tuple(
                    AuthorizedChatChannelContext(f"opaque-{index}", False)
                    for index in range(9)
                ),
            }
        )
    assert AuthorizedChatChannelContext("x" * 128, True).channel_id == "x" * 128
    with pytest.raises(DiscussionContextError):
        AuthorizedChatChannelContext("x" * 129, True)
    assert AuthorizedChatChannelContext("界" * 128, True).channel_id == "界" * 128
    assert AuthorizedChatChannelContext("\N{GRINNING FACE}" * 128, True).channel_id == "\N{GRINNING FACE}" * 128
    with pytest.raises(DiscussionContextError):
        AuthorizedChatChannelContext("界" * 128 + "x" * 129, True)


def test_context_closed_union_count_and_serialized_byte_ceilings() -> None:
    base = _context("0" * 64)
    wins = tuple(
        EliminateRoleTagWinCondition("eliminate_role_tag", f"tag-{index}")
        for index in range(8)
    )
    wins = tuple(sorted(wins, key=canonical_json_bytes))
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "win_conditions": wins[:-1]}).win_conditions) == 7
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "win_conditions": wins}).win_conditions) == 8
    with pytest.raises(DiscussionContextError):
        AuthorizedDiscussionContext(
            **{
                **base.__dict__,
                "win_conditions": tuple(
                    sorted(
                        wins + (SurviveWhenOthersWinCondition("survive_when_others_win", False),),
                        key=canonical_json_bytes,
                    )
                ),
            }
        )

    effects = tuple(f"effect-{index}" for index in range(8))
    ability = DiscussionAbilityContext(
        ability_id="ability",
        timing="timing",
        available_from_night=0,
        priority=0,
        resolution="resolution",
        target_selector="selector",
        target_count=1,
        uses_per_night=None,
        uses_per_game=None,
        no_selection="skip",
        effect_ids=effects,
    )
    assert len(
        DiscussionAbilityContext(**{**ability.__dict__, "effect_ids": effects[:-1]}).effect_ids
    ) == 7
    assert ability.effect_ids == effects
    with pytest.raises(DiscussionContextError):
        DiscussionAbilityContext(
            **{**ability.__dict__, "effect_ids": effects + ("effect-8",)}
        )

    abilities = tuple(
        DiscussionAbilityContext(**{**ability.__dict__, "ability_id": f"ability-{index:02d}"})
        for index in range(16)
    )
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "abilities": abilities[:-1]}).abilities) == 15
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "abilities": abilities}).abilities) == 16
    with pytest.raises(DiscussionContextError):
        AuthorizedDiscussionContext(
            **{
                **base.__dict__,
                "abilities": abilities
                + (DiscussionAbilityContext(**{**ability.__dict__, "ability_id": "ability-16"}),),
            }
        )

    passives = tuple(
        DiscussionPassiveContext(f"passive-{index:02d}", 0, ()) for index in range(16)
    )
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "passives": passives[:-1]}).passives) == 15
    assert len(AuthorizedDiscussionContext(**{**base.__dict__, "passives": passives}).passives) == 16
    with pytest.raises(DiscussionContextError):
        AuthorizedDiscussionContext(
            **{
                **base.__dict__,
                "passives": passives + (DiscussionPassiveContext("passive-16", 0, ()),),
            }
        )

    oversized_ability = DiscussionAbilityContext(
        ability_id="a" * 128,
        timing="b" * 128,
        available_from_night=0,
        priority=0,
        resolution="c" * 128,
        target_selector="d" * 128,
        target_count=1,
        uses_per_night=None,
        uses_per_game=None,
        no_selection="e" * 128,
        effect_ids=tuple(chr(ord("f") + index) * 128 for index in range(8)),
    )
    with pytest.raises(DiscussionContextError, match="8 KiB"):
        AuthorizedDiscussionContext(
            **{
                **base.__dict__,
                "abilities": tuple(
                    DiscussionAbilityContext(
                        **{
                            **oversized_ability.__dict__,
                            "ability_id": f"{index:02d}" + "a" * 126,
                        }
                    )
                    for index in range(16)
                ),
            }
        )


def _envelope_with_manifest_size(target_size: int) -> dict[str, object]:
    envelope = _envelope()
    manifest = envelope["manifest_material"]
    team = manifest["content_pack"]["teams"]["opaque-team"]
    base_size = len(canonical_json_bytes(manifest))
    team["name"] += "x" * (target_size - base_size)
    assert len(canonical_json_bytes(manifest)) == target_size
    _rehash_envelope(envelope)
    return envelope


@pytest.mark.parametrize("target_size", (65535, 65536))
def test_manifest_accepts_one_under_and_equal_64k(target_size: int) -> None:
    envelope = _envelope_with_manifest_size(target_size)
    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    assert len(pending.canonical_manifest_bytes()) == target_size


def test_manifest_rejects_one_byte_over_64k() -> None:
    oversized = _envelope_with_manifest_size(65537)
    with pytest.raises(DiscussionContextError, match="64 KiB"):
        validate_discussion_bootstrap(
            oversized,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


def test_valid_bootstrap_binds_once_and_drops_full_manifest() -> None:
    envelope = _envelope()
    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    manifest_bytes = pending.canonical_manifest_bytes()
    assert b"other-seat-role-name-sentinel" in manifest_bytes
    assert "manifest=<redacted>" in repr(pending)
    bound = pending.bind(_snapshot())
    assert not pending.manifest_is_retained
    with pytest.raises(DiscussionContextError):
        pending.canonical_manifest_bytes()
    with pytest.raises(DiscussionContextError):
        pending.bind(_snapshot())
    retained = canonical_json_bytes(bound)
    for sentinel in (
        b"other-seat-role-name-sentinel",
        b"other-channel-private-sentinel",
        b"preset-count-sentinel",
    ):
        assert sentinel not in retained
    assert bound.context.chat_channels == (
        AuthorizedChatChannelContext("opaque-private", False),
        AuthorizedChatChannelContext("opaque-public", True),
    )


def test_valid_none_win_condition_modifier_preserves_team_objective() -> None:
    envelope = _envelope()
    modifier_id = "opaque-modifier"
    envelope["manifest_material"]["content_pack"]["modifiers"][modifier_id] = {
        "id": modifier_id,
        "name": "private-modifier-name-sentinel",
        "grant": {
            "timing": "assignment",
            "duration": "permanent",
            "duration_nights": None,
        },
        "win_condition": {"mode": "none", "value": None, "priority": None},
        "passives": [],
        "knowledge": {"declarations": {"knows_teammates": False}},
        "chat_channels": [],
        "overrides": {
            "team": None,
            "count_as": None,
            "attack_result": None,
            "inspect_result": None,
            "medium_result": None,
        },
        "exclusions": [],
    }
    envelope["context_payload"]["modifier_ids"] = [modifier_id]
    envelope["manifest_sha256"] = canonical_sha256(envelope["manifest_material"])
    envelope["context_payload"]["content_manifest_sha256"] = envelope["manifest_sha256"]
    envelope["context_sha256"] = canonical_sha256(envelope["context_payload"])

    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    bound = pending.bind(_snapshot(modifiers=(modifier_id,)))
    assert bound.context.modifier_ids == (modifier_id,)
    assert bound.context.win_conditions == _context("0" * 64).win_conditions

    invalid = deepcopy(envelope)
    invalid["manifest_material"]["content_pack"]["modifiers"][modifier_id][
        "win_condition"
    ]["mode"] = "inherit"
    invalid["manifest_sha256"] = canonical_sha256(invalid["manifest_material"])
    invalid["context_payload"]["content_manifest_sha256"] = invalid[
        "manifest_sha256"
    ]
    invalid["context_sha256"] = canonical_sha256(invalid["context_payload"])
    with pytest.raises(DiscussionContextError, match="mode is invalid"):
        validate_discussion_bootstrap(
            invalid,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )

    unknown_effect = deepcopy(envelope)
    unknown_effect["manifest_material"]["content_pack"]["passives"][
        "opaque-passive"
    ] = {"id": "opaque-passive", "name": "opaque passive"}
    unknown_effect["manifest_material"]["content_pack"]["modifiers"][modifier_id][
        "passives"
    ] = [
        {
            "type": "opaque-passive",
            "priority": 0,
            "rules": [{}],
            "effects": [{"id": "missing-effect", "priority": 0}],
        }
    ]
    unknown_effect["context_payload"]["passives"] = [
        {"type": "opaque-passive", "priority": 0, "effect_ids": ["missing-effect"]}
    ]
    unknown_effect["manifest_sha256"] = canonical_sha256(
        unknown_effect["manifest_material"]
    )
    unknown_effect["context_payload"]["content_manifest_sha256"] = unknown_effect[
        "manifest_sha256"
    ]
    unknown_effect["context_sha256"] = canonical_sha256(
        unknown_effect["context_payload"]
    )
    with pytest.raises(DiscussionContextError, match="unknown effect"):
        validate_discussion_bootstrap(
            unknown_effect,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize(
    "mutate,rehash",
    [
        (lambda value: value.__setitem__("manifest_sha256", "f" * 64), False),
        (lambda value: value.__setitem__("context_sha256", "f" * 64), False),
        (lambda value: value["context_payload"].__setitem__("extra", 1), True),
        (
            lambda value: value["context_payload"]["chat_channels"][0].__setitem__("is_public", True),
            True,
        ),
        (lambda value: value["context_payload"]["chat_channels"].pop(), True),
        (lambda value: value["context_payload"]["win_conditions"][0].__setitem__("type", "future"), True),
    ],
)
def test_bootstrap_damage_fails_closed(mutate, rehash: bool) -> None:
    envelope = deepcopy(_envelope())
    mutate(envelope)
    if rehash and set(envelope["context_payload"]) == {
        "schema_version",
        "game_id",
        "player_id",
        "role_id",
        "modifier_ids",
        "content_manifest_sha256",
        "team",
        "count_as",
        "attack_result",
        "inspect_result",
        "medium_result",
        "win_conditions",
        "abilities",
        "passives",
        "chat_channels",
        "knows_teammates",
        "authorized_known_player_ids",
        "known_players_complete",
    }:
        envelope["context_sha256"] = canonical_sha256(envelope["context_payload"])
    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


def test_runtime_and_first_current_identity_are_separate_fail_closed_checks() -> None:
    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(
            _envelope(), network_game_id="cross-game", player_id="opaque-player"
        )
    pending = validate_discussion_bootstrap(
        _envelope(), network_game_id="opaque-game", player_id="opaque-player"
    )
    with pytest.raises(DiscussionContextError):
        pending.bind(_snapshot(role_id="cross-role"))


def test_manifest_is_closed_and_bool_is_not_an_integer() -> None:
    extra = deepcopy(_envelope())
    extra["manifest_material"]["content_pack"]["chat_channels"]["opaque-public"]["extra"] = 1
    extra["manifest_sha256"] = canonical_sha256(extra["manifest_material"])
    extra["context_payload"]["content_manifest_sha256"] = extra["manifest_sha256"]
    extra["context_sha256"] = canonical_sha256(extra["context_payload"])
    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(extra, network_game_id="opaque-game", player_id="opaque-player")

    bool_as_int = deepcopy(_envelope())
    bool_as_int["manifest_material"]["content_pack"]["chat_channels"]["opaque-public"]["is_public"] = 1
    bool_as_int["manifest_sha256"] = canonical_sha256(bool_as_int["manifest_material"])
    bool_as_int["context_payload"]["content_manifest_sha256"] = bool_as_int["manifest_sha256"]
    bool_as_int["context_sha256"] = canonical_sha256(bool_as_int["context_payload"])
    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(bool_as_int, network_game_id="opaque-game", player_id="opaque-player")


@pytest.mark.parametrize("count", (0, 7, 8))
def test_authorized_channel_descriptor_count_boundaries_accept(count: int) -> None:
    envelope = _envelope()
    channel_ids = tuple(f"opaque-channel-{index:02d}" for index in range(count))
    envelope["manifest_material"]["content_pack"]["chat_channels"] = {
        channel_id: _chat_channel(channel_id) for channel_id in channel_ids
    }
    envelope["manifest_material"]["content_pack"]["roles"]["opaque-role"][
        "chat_channels"
    ] = list(channel_ids)
    envelope["context_payload"]["chat_channels"] = [
        {"channel_id": channel_id, "is_public": False}
        for channel_id in channel_ids
    ]
    _rehash_envelope(envelope)

    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    assert len(pending.context.chat_channels) == count


def test_authorized_channel_descriptor_count_nine_is_rejected() -> None:
    envelope = _envelope()
    channel_ids = tuple(f"opaque-channel-{index:02d}" for index in range(9))
    envelope["manifest_material"]["content_pack"]["chat_channels"] = {
        channel_id: _chat_channel(channel_id) for channel_id in channel_ids
    }
    envelope["manifest_material"]["content_pack"]["roles"]["opaque-role"][
        "chat_channels"
    ] = list(channel_ids)
    envelope["context_payload"]["chat_channels"] = [
        {"channel_id": channel_id, "is_public": False}
        for channel_id in channel_ids
    ]
    _rehash_envelope(envelope)

    with pytest.raises(DiscussionContextError, match="chat_channels"):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize("damage", ("duplicate", "unknown", "extra"))
def test_authorized_channel_descriptor_identity_failures(damage: str) -> None:
    envelope = _envelope()
    if damage == "duplicate":
        envelope["context_payload"]["chat_channels"].insert(
            1,
            deepcopy(envelope["context_payload"]["chat_channels"][0]),
        )
    elif damage == "unknown":
        envelope["manifest_material"]["content_pack"]["roles"]["opaque-role"][
            "chat_channels"
        ].append("zz-unknown")
        envelope["context_payload"]["chat_channels"].append(
            {"channel_id": "zz-unknown", "is_public": False}
        )
    else:
        envelope["manifest_material"]["content_pack"]["chat_channels"][
            "zz-extra"
        ] = _chat_channel("zz-extra")
        envelope["context_payload"]["chat_channels"].append(
            {"channel_id": "zz-extra", "is_public": False}
        )
    _rehash_envelope(envelope)

    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize("count", (0, 7, 8))
def test_selected_modifier_count_boundaries_accept(count: int) -> None:
    envelope = _envelope()
    modifiers = tuple(
        _modifier(f"opaque-modifier-{index:02d}") for index in range(count)
    )
    _select_modifiers(envelope, modifiers)
    _rehash_envelope(envelope)

    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    assert len(pending.context.modifier_ids) == count


@pytest.mark.parametrize("damage", ("nine", "duplicate", "win-priority"))
def test_selected_modifier_count_and_priority_collisions_fail(damage: str) -> None:
    envelope = _envelope()
    if damage == "nine":
        modifiers = tuple(
            _modifier(f"opaque-modifier-{index:02d}") for index in range(9)
        )
        _select_modifiers(envelope, modifiers)
    elif damage == "duplicate":
        modifier = _modifier("opaque-modifier")
        _select_modifiers(envelope, (modifier,))
        envelope["context_payload"]["modifier_ids"] = [
            "opaque-modifier",
            "opaque-modifier",
        ]
    else:
        condition = _count_parity_manifest_condition()
        modifiers = tuple(
            _modifier(
                f"opaque-modifier-{index}",
                win_condition={
                    "mode": "override",
                    "value": deepcopy(condition),
                    "priority": 1,
                },
            )
            for index in range(2)
        )
        _select_modifiers(envelope, modifiers)
    _rehash_envelope(envelope)

    with pytest.raises(DiscussionContextError):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize(
    "first_value,second_value",
    (
        ("opaque-replacement", "opaque-replacement"),
        ("opaque-first", "opaque-second"),
    ),
)
def test_selected_modifier_attribute_override_collisions_fail(
    first_value: str,
    second_value: str,
) -> None:
    envelope = _envelope()
    _select_modifiers(
        envelope,
        (
            _modifier("opaque-modifier-a", overrides={"count_as": first_value}),
            _modifier("opaque-modifier-b", overrides={"count_as": second_value}),
        ),
    )
    envelope["context_payload"]["count_as"] = second_value
    _rehash_envelope(envelope)

    with pytest.raises(DiscussionContextError, match="multiple selected modifiers"):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize("first_value", (None, "by_role"))
def test_null_and_by_role_overrides_do_not_collide_with_effective_override(
    first_value: str | None,
) -> None:
    envelope = _envelope()
    replacement = "opaque-replacement"
    _select_modifiers(
        envelope,
        (
            _modifier("opaque-modifier-a", overrides={"count_as": first_value}),
            _modifier("opaque-modifier-b", overrides={"count_as": replacement}),
        ),
    )
    envelope["context_payload"]["count_as"] = replacement
    _rehash_envelope(envelope)

    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    assert pending.context.count_as == replacement


@pytest.mark.parametrize("priority", (-1, 0, 1))
def test_modifier_override_priority_accepts_any_exact_integer(priority: int) -> None:
    envelope = _envelope()
    _select_modifiers(
        envelope,
        (
            _modifier(
                "opaque-modifier",
                win_condition={
                    "mode": "override",
                    "value": _count_parity_manifest_condition(),
                    "priority": priority,
                },
            ),
        ),
    )
    _rehash_envelope(envelope)

    pending = validate_discussion_bootstrap(
        envelope,
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    assert pending.context.modifier_ids == ("opaque-modifier",)


@pytest.mark.parametrize("priority", (False, True))
def test_modifier_override_priority_rejects_bool(priority: bool) -> None:
    envelope = _envelope()
    _select_modifiers(
        envelope,
        (
            _modifier(
                "opaque-modifier",
                win_condition={
                    "mode": "override",
                    "value": _count_parity_manifest_condition(),
                    "priority": priority,
                },
            ),
        ),
    )
    _rehash_envelope(envelope)

    with pytest.raises(DiscussionContextError, match="exact integer"):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


def _context_payload_with_canonical_size(target: int) -> dict[str, object]:
    payload = _payload(_context("0" * 64))
    passives = [
        {
            "type": f"passive-{passive_index:02d}",
            "priority": 0,
            "effect_ids": [
                f"effect-{passive_index:02d}-{effect_index:02d}-"
                for effect_index in range(8)
            ],
        }
        for passive_index in range(16)
    ]
    payload["passives"] = passives
    remaining = target - len(canonical_json_bytes(payload))
    assert remaining >= 0
    for passive in passives:
        for index, effect_id in enumerate(passive["effect_ids"]):
            added = min(remaining, 128 - len(effect_id))
            passive["effect_ids"][index] += "x" * added
            remaining -= added
    assert remaining == 0
    payload["passives"] = sorted(passives, key=canonical_json_bytes)
    assert len(canonical_json_bytes(payload)) == target
    return payload


@pytest.mark.parametrize("size", (8191, 8192))
def test_context_byte_boundary_accepts_one_under_and_equal(size: int) -> None:
    payload = _context_payload_with_canonical_size(size)
    context = parse_authorized_discussion_context(payload)
    assert len(canonical_json_bytes(context)) == size


def test_context_byte_boundary_rejects_one_over() -> None:
    payload = _context_payload_with_canonical_size(8193)
    with pytest.raises(DiscussionContextError, match="8 KiB"):
        parse_authorized_discussion_context(payload)


def _bootstrap_with_canonical_size(target: int) -> dict[str, object]:
    envelope = _envelope()
    current = len(canonical_json_bytes(envelope))
    assert target >= current
    envelope["manifest_material"]["content_pack"]["teams"]["opaque-team"][
        "name"
    ] += "x" * (target - current)
    _rehash_envelope(envelope)
    assert len(canonical_json_bytes(envelope)) == target
    return envelope


@pytest.mark.parametrize("size", (81919, 81920))
def test_bootstrap_byte_boundary_allows_envelope_gate_at_one_under_and_equal(
    size: int,
) -> None:
    envelope = _bootstrap_with_canonical_size(size)
    with pytest.raises(DiscussionContextError, match="64 KiB"):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


def test_bootstrap_byte_boundary_rejects_one_over_before_nested_validation() -> None:
    envelope = _bootstrap_with_canonical_size(81921)
    with pytest.raises(DiscussionContextError, match="80 KiB"):
        validate_discussion_bootstrap(
            envelope,
            network_game_id="opaque-game",
            player_id="opaque-player",
        )


@pytest.mark.parametrize(
    "damage",
    (
        "wrong-player",
        "wrong-role",
        "wrong-modifier",
        "duplicate-modifier",
        "missing-self-view",
        "empty",
        "stale",
        "ended",
        "failed",
    ),
)
def test_first_sync_identity_modifier_and_freshness_mismatches_fail_closed(
    damage: str,
) -> None:
    snapshot = _snapshot()
    if damage == "wrong-player":
        snapshot = replace(
            snapshot,
            self_view=SelfView("other-player", "opaque-role", ()),
        )
    elif damage == "wrong-role":
        snapshot = replace(
            snapshot,
            self_view=SelfView("opaque-player", "other-role", ()),
        )
    elif damage == "wrong-modifier":
        snapshot = replace(
            snapshot,
            self_view=SelfView("opaque-player", "opaque-role", ("modifier-a",)),
        )
    elif damage == "duplicate-modifier":
        snapshot = replace(
            snapshot,
            self_view=SelfView(
                "opaque-player",
                "opaque-role",
                ("modifier-a", "modifier-a"),
            ),
        )
    elif damage == "missing-self-view":
        snapshot = replace(snapshot, self_view=None)
    else:
        snapshot = replace(
            snapshot,
            freshness=Freshness[damage.upper()],
            is_caught_up=False,
        )
    pending = validate_discussion_bootstrap(
        _envelope(),
        network_game_id="opaque-game",
        player_id="opaque-player",
    )
    with pytest.raises(DiscussionContextError):
        pending.bind(snapshot)
