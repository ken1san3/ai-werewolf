from dataclasses import replace

import pytest

from scripts import phase6_derived_grounding_snapshot as snapshot
from scripts import phase6_minimal_output_probe as probe
from tests.test_phase6_derived_grounding_probe import fixture


def validated(number=4):
    value, binding = fixture(number)
    raw = probe.canonical_bytes(value)
    return raw, probe.validate_without_grounding(raw, binding)


def test_private_snapshot_round_trip_binds_request_and_probe_input_separately(tmp_path, monkeypatch):
    raw, validation = validated()
    request_hash, schema_hash = "a" * 64, "b" * 64
    locator = snapshot.write_validation_snapshot(
        tmp_path, case_id="G01-1", raw_sha256=probe.sha256(raw),
        input_sha256=request_hash, schema_sha256=schema_hash, validation=validation)
    assert locator.path.parent == tmp_path and len(locator.content_sha256) == 64
    assert validation.probe_result.input_sha256 != request_hash
    monkeypatch.setattr(probe, "derive_grounding_view",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not rederive")))
    restored = snapshot.read_validation_snapshot(
        tmp_path, case_id="G01-1", raw_sha256=probe.sha256(raw),
        input_sha256=request_hash, schema_sha256=schema_hash,
        expected_probe_result=validation.probe_result)
    assert restored == validation


@pytest.mark.parametrize("binding", ["case", "raw", "input", "schema", "result"])
def test_snapshot_reader_rejects_every_binding_mismatch(tmp_path, binding):
    raw, validation = validated()
    snapshot.write_validation_snapshot(
        tmp_path, case_id="G01-1", raw_sha256=probe.sha256(raw),
        input_sha256="a" * 64, schema_sha256="b" * 64, validation=validation)
    kwargs = dict(case_id="G01-1", raw_sha256=probe.sha256(raw), input_sha256="a" * 64,
                  schema_sha256="b" * 64, expected_probe_result=validation.probe_result)
    if binding == "case": kwargs["case_id"] = "G02-1"
    elif binding == "raw": kwargs["raw_sha256"] = "0" * 64
    elif binding == "input": kwargs["input_sha256"] = "0" * 64
    elif binding == "schema": kwargs["schema_sha256"] = "0" * 64
    else: kwargs["expected_probe_result"] = replace(validation.probe_result, semantic_status="CHANGED")
    with pytest.raises(snapshot.SnapshotError, match="PRIVATE_EVIDENCE_ERROR"):
        snapshot.read_validation_snapshot(tmp_path, **kwargs)


def test_snapshot_content_and_locator_tamper_fail_closed(tmp_path):
    raw, validation = validated()
    locator = snapshot.write_validation_snapshot(
        tmp_path, case_id="G01-1", raw_sha256=probe.sha256(raw),
        input_sha256="a" * 64, schema_sha256="b" * 64, validation=validation)
    locator.path.write_bytes(locator.path.read_bytes() + b" ")
    with pytest.raises(snapshot.SnapshotError, match="PRIVATE_EVIDENCE_ERROR"):
        snapshot.read_validation_snapshot(
            tmp_path, case_id="G01-1", raw_sha256=probe.sha256(raw), input_sha256="a" * 64,
            schema_sha256="b" * 64, expected_probe_result=validation.probe_result)


def test_snapshot_is_create_new(tmp_path):
    raw, validation = validated()
    kwargs = dict(case_id="G01-1", raw_sha256=probe.sha256(raw), input_sha256="a" * 64,
                  schema_sha256="b" * 64, validation=validation)
    snapshot.write_validation_snapshot(tmp_path, **kwargs)
    with pytest.raises(snapshot.SnapshotError, match="PRIVATE_EVIDENCE_ERROR"):
        snapshot.write_validation_snapshot(tmp_path, **kwargs)
