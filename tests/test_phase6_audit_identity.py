import json

import pytest

from scripts import run_phase5_local_smoke as runner


def generation(request_id, ordinal, schema="aiwolf.ai-discussion-generation.v2"):
    return {"schema_version": schema, "request_id": request_id,
            "attempt_ordinal": ordinal, "player_id": "player-0", "decision": None}


def summarize(tmp_path, records):
    path = tmp_path / "ai.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return runner._audit_summary(path)


@pytest.mark.parametrize("schema", ["aiwolf.ai-discussion-generation.v1", "aiwolf.ai-discussion-generation.v2"])
def test_repair_is_distinct_transport_attempt_without_dropping_records(tmp_path, schema):
    result = summarize(tmp_path, [generation("phase6:capture", 1, schema),
                                  generation("phase6:capture", 2, schema)])
    assert result["record_count"] == 2
    assert result["request_ids"] == ["phase6:capture:attempt:1", "phase6:capture:attempt:2"]


@pytest.mark.parametrize("request_id,ordinal", [(None, 1), ("", 1), (5, 1), ("capture", True),
    ("capture", 0), ("capture", 3), ("capture", "1")])
def test_malformed_identity_not_promoted_to_valid_string(tmp_path, request_id, ordinal):
    with pytest.raises(ValueError, match="request identity"):
        summarize(tmp_path, [generation(request_id, ordinal)])


def test_legacy_request_identity_unchanged(tmp_path):
    result = summarize(tmp_path, [generation("legacy", 1, "aiwolf.ai-generation.v1")])
    assert result["request_ids"] == ["legacy"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_existing_shard_validator_accepts_repair_and_rejects_duplicate_attempt(tmp_path, duplicate):
    mapping = {f"player-{i}": f"opaque-{i}" for i in range(9)}
    shards, statuses = {}, {}
    for player, opaque in mapping.items():
        folder = tmp_path / opaque
        folder.mkdir()
        records = [generation("phase6:" + opaque, 1), generation("phase6:" + opaque, 2)]
        for record in records:
            record["player_id"] = player
        if duplicate and player == "player-0":
            records.append(dict(records[-1]))
        audit = summarize(folder, records)
        shards[player] = {"path": f"{opaque}/ai.jsonl"}
        statuses[player] = {"player_id": player, "audit": audit}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"player_to_opaque_client_id": mapping, "shards": shards}))
    errors = runner._validate_private_shard_identity(statuses, manifest, mapping)
    assert errors == (["audit shard request IDs are not unique"] if duplicate else [])
