from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

import pytest

from scripts import build_phase6_pf3_token_path as build_tool
from scripts import phase6_pf3_token_path as proof
from scripts import phase6_pf3_token_path_windows as windows_proof


def sampler_values() -> dict[str, object]:
    return {
        "response_type": "json_schema", "strict": True, "max_tokens": 512,
        "n_predict_override": False, "grammar_type": "JSON_SCHEMA_GBNF",
        "grammar_root": "root", "grammar_nonempty": True, "ignore_eos": False,
        "request_logit_bias_count": 0, "base_logit_bias_count": 0,
        "eog_bias_count": 0, "grammar_lazy": False, "grammar_trigger_count": 0,
        "preserved_token_count": 0, "stop_count": 0, "prefill_prefix_count": 0,
        "alternate_grammar_count": 0, "unknown_hard_mask_count": 0,
        "chain_certificate_sha256": proof.product_certificate_sha256(),
    }


def good_result(*, count: int = 512) -> proof.ChildResult:
    calls = {
        "emitter": 1, "tokenize": 2, "detokenize": 2,
        "prefix_apply": count, "prefix_accept": count, "vocab_is_eog": 1025,
        "eog_apply": 1, "eog_accept": 1, "token_to_piece": 0, "provider": 0,
        "inference": 0, "server": 0, "gpu": 0, "game": 0, "actions": 0,
        "constant_native": 8,
    }
    token_ids = tuple(range(count))
    return proof.ChildResult(
        nonce="a" * 32, grammar_size=2, grammar_bytes=b"{}", token_ids=token_ids,
        prefix_before_ids=token_ids, prefix_after_ids=token_ids,
        prefix_allowed=(True,) * count, roundtrip_exact=True, decoder_bytes=b"raw",
        vocab_size=1025, eog_ids=(1024,), eog_before_ids=(1024,), eog_after_ids=(1024,),
        eog_allowed=(True,), accepted_eog=1024, accounted_generated_tokens=count + 1,
        calls=calls, sampler_record=proof.EffectiveSamplerRecord(**sampler_values()),
        abi={key: 0 for key in proof.ABI_KEYS},
        status="PROOF_COMPLETE")


def one_module() -> tuple[proof.ModuleIdentity, proof.ModuleEvent, dict[str, str]]:
    module = proof.ModuleIdentity("C:/proof/proof.exe", 1, 2, 3, "2" * 64, "NON_SYSTEM")
    event = proof.ModuleEvent(0, "CREATE_PROCESS", module)
    return module, event, {"proof.exe": module.sha256}


def test_witness_is_fixed_and_bounded() -> None:
    schema, raw = proof.build_witness()
    assert proof.strict_json(raw) == {"message": "\0" * 200}
    assert raw == proof.t527.compact({"message": "\0" * 200})
    assert len(schema) <= proof.SCHEMA_LIMIT
    assert len(raw) <= proof.RAW_LIMIT


def test_product_certificate_is_exact_and_binds_sampler() -> None:
    raw = proof.product_certificate_bytes()
    proof.validate_product_certificate(raw)
    assert proof.digest(raw) == sampler_values()["chain_certificate_sha256"]
    with pytest.raises(proof.ProofError) as captured:
        proof.validate_product_certificate(raw[:-2] + b"x\n")
    assert captured.value.aggregate == "UNKNOWN_EFFECTIVE_SAMPLER"


@pytest.mark.parametrize("field,bad", [
    ("response_type", "text"), ("strict", False), ("max_tokens", 513),
    ("n_predict_override", True), ("grammar_root", "other"),
    ("ignore_eos", True), ("request_logit_bias_count", 1),
    ("base_logit_bias_count", 1), ("eog_bias_count", 1),
    ("grammar_lazy", True), ("grammar_trigger_count", 1),
    ("preserved_token_count", 1), ("stop_count", 1),
    ("prefill_prefix_count", 1), ("alternate_grammar_count", 1),
    ("unknown_hard_mask_count", 1),
])
def test_sampler_closed_record_rejects_each_drift(field: str, bad: object) -> None:
    values = sampler_values(); values[field] = bad
    with pytest.raises(proof.ProofError, match=field.replace("_", " ") if field == "chain_certificate_sha256" else field):
        proof.validate_sampler_record(proof.EffectiveSamplerRecord(**values))


def test_sampler_certificate_shape_is_closed() -> None:
    values = sampler_values(); values["chain_certificate_sha256"] = "x" * 64
    with pytest.raises(proof.ProofError):
        proof.validate_sampler_record(proof.EffectiveSamplerRecord(**values))


def test_result_512_plus_eog_is_budget_counterexample() -> None:
    expected = {key: 0 for key in proof.ABI_KEYS}
    assert proof.validate_child_result(good_result(), nonce="a" * 32, expected_raw=b"raw", expected_abi=expected)["aggregate"] == "FAIL_BUDGET"


@pytest.mark.parametrize("mutator,aggregate", [
    (lambda item: item.__dict__.update(prefix_allowed=(True,) * 511 + (False,)), "UNKNOWN_PREFIX_REJECTED"),
    (lambda item: item.__dict__.update(roundtrip_exact=False), "UNKNOWN_DECODER_MISMATCH"),
    (lambda item: item.__dict__.update(accepted_eog=None), "UNKNOWN_EOG_OR_BUDGET"),
    (lambda item: item.__dict__.update(accounted_generated_tokens=512), "UNKNOWN_EOG_OR_BUDGET"),
])
def test_result_failure_matrix(mutator, aggregate: str) -> None:
    # Frozen dataclass mutation is used only to produce a malformed child result.
    item = good_result(); mutator(item)
    with pytest.raises(proof.ProofError) as captured:
        proof.validate_child_result(item, nonce="a" * 32)
    assert captured.value.aggregate == aggregate


@pytest.mark.parametrize("item,aggregate", [
    (good_result(count=511), "UNKNOWN_EOG_OR_BUDGET"),
    (replace(good_result(), token_ids=(1024,) + tuple(range(1, 512)),
             prefix_before_ids=(1024,) + tuple(range(1, 512)),
             prefix_after_ids=(1024,) + tuple(range(1, 512))), "UNKNOWN_EOG_OR_BUDGET"),
    (replace(good_result(), eog_ids=tuple(range(proof.EOG_LIMIT + 1))), "UNKNOWN_RESOURCE_BOUND"),
    (replace(good_result(), abi={**{key: 0 for key in proof.ABI_KEYS}, "extra": 0}), "UNKNOWN_ABI_IDENTITY"),
])
def test_result_boundary_matrix(item: proof.ChildResult, aggregate: str) -> None:
    with pytest.raises(proof.ProofError) as captured:
        proof.validate_child_result(item, nonce="a" * 32)
    assert captured.value.aggregate == aggregate


def test_result_rejects_call_count_and_resource_overflow() -> None:
    item = good_result(); item.calls["provider"] = 1
    with pytest.raises(proof.ProofError) as captured:
        proof.validate_child_result(item, nonce="a" * 32)
    assert captured.value.aggregate == "UNKNOWN_RESOURCE_BOUND"


def test_module_snapshot_event_and_allowlist_are_joint() -> None:
    module, event, allow = one_module()
    proof.validate_module_sets((module,), (module,), (event,), allow)
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (), (event,), allow)
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (module,), (proof.ModuleEvent(1, "CREATE_PROCESS", module),), allow)
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (module,), (event, proof.ModuleEvent(1, "UNLOAD_DLL", None)), allow)
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (module,), (event,), {"proof.exe": "3" * 64})
    transient = proof.ModuleIdentity("C:/proof/late.dll", 1, 9, 3, "4" * 64, "NON_SYSTEM")
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (module,),
            (event, proof.ModuleEvent(1, "LOAD_DLL", transient)), {**allow, "late.dll": transient.sha256})
    with pytest.raises(proof.ProofError):
        proof.validate_module_sets((module,), (module,),
            (proof.ModuleEvent(0, "LOAD_DLL", module),), allow)


def test_state_machine_and_synthetic_backend_close_success_path() -> None:
    module, event, allow = one_module()
    schema, raw = proof.build_witness()
    backend = proof.SyntheticBackend(replace(good_result(), decoder_bytes=raw), (module,), (event,))
    summary, state = proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
                                       sampler_input=sampler_values(), approved_modules=allow)
    assert summary["aggregate"] == "FAIL_BUDGET"
    assert state.state == proof.State.CHILD_REAPED
    assert backend.started == 1 and backend.ready == ["PRE", "POST"] and backend.reaped


def test_backend_failure_is_one_shot_and_closes() -> None:
    class Rejecting(proof.SyntheticBackend):
        def result(self, timeout: float) -> proof.ChildResult:
            raise proof.ProofError("UNKNOWN_NATIVE_CHILD_FAILED", "synthetic crash")
    module, event, allow = one_module()
    backend = Rejecting(good_result(), (module,), (event,))
    schema, raw = proof.build_witness()
    with pytest.raises(proof.ProofError) as captured:
        proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
                          sampler_input=sampler_values(), approved_modules=allow)
    assert captured.value.aggregate == "UNKNOWN_NATIVE_CHILD_FAILED"
    assert backend.started == 1


@pytest.mark.parametrize("stage", ["ready", "snapshot", "static", "reap"])
def test_backend_runtime_failure_points_never_publish_fail(stage: str) -> None:
    class Failing(proof.SyntheticBackend):
        def wait_ready(self, phase: str, timeout: float) -> None:
            if stage == "ready": raise proof.ProofError("UNKNOWN_RUNTIME_INVALID", "ready")
            super().wait_ready(phase, timeout)
        def snapshot(self) -> tuple[proof.ModuleIdentity, ...]:
            if stage == "snapshot": raise proof.ProofError("UNKNOWN_MODULE_SET", "snapshot")
            return super().snapshot()
        def verify_static(self, phase: str) -> None:
            if stage == "static": raise proof.ProofError("UNKNOWN_ABI_IDENTITY", "static")
        def reap(self, timeout: float) -> None:
            if stage == "reap": raise proof.ProofError("UNKNOWN_RUNTIME_INVALID", "reap")
            super().reap(timeout)
    module, event, allow = one_module()
    schema, raw = proof.build_witness()
    backend = Failing(replace(good_result(), decoder_bytes=raw), (module,), (event,))
    with pytest.raises(proof.ProofError) as captured:
        proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
                          sampler_input=sampler_values(), approved_modules=allow)
    assert captured.value.aggregate.startswith("UNKNOWN_")
    assert backend.started == 1


def test_child_identity_and_abi_drift_are_unknown() -> None:
    with pytest.raises(proof.ProofError) as nonce_error:
        proof.validate_child_result(good_result(), nonce="b" * 32)
    assert nonce_error.value.aggregate == "UNKNOWN_RUNTIME_INVALID"
    expected = {key: 0 for key in proof.ABI_KEYS}; expected[next(iter(proof.ABI_KEYS))] = 1
    with pytest.raises(proof.ProofError) as abi_error:
        proof.validate_child_result(good_result(), nonce="a" * 32, expected_abi=expected)
    assert abi_error.value.aggregate == "UNKNOWN_ABI_IDENTITY"


def test_input_envelope_is_length_delimited_and_nonce_bound() -> None:
    raw = proof.encode_input_envelope("a" * 32, b"{}", b"{}", sampler_values())
    magic, nonce_size, schema_size, raw_size, sampler_size = struct.unpack("<8sIIII", raw[:24])
    assert magic == b"PF3PATH1"
    assert len(raw) == 24 + nonce_size + schema_size + raw_size + sampler_size
    backend = proof.SyntheticBackend(good_result())
    backend.start(raw, "a" * 32)
    with pytest.raises(proof.ProofError):
        backend.start(raw + b"x", "a" * 32)


def test_child_result_parser_rejects_extra_key() -> None:
    item = good_result()
    value = {**item.__dict__, "token_ids": list(item.token_ids),
             "grammar_b64": __import__("base64").b64encode(item.grammar_bytes).decode("ascii"),
             "decoder_b64": __import__("base64").b64encode(item.decoder_bytes).decode("ascii"),
             "prefix_before_ids": list(item.prefix_before_ids), "prefix_after_ids": list(item.prefix_after_ids),
             "prefix_allowed": list(item.prefix_allowed), "eog_ids": list(item.eog_ids),
             "eog_before_ids": list(item.eog_before_ids), "eog_after_ids": list(item.eog_after_ids),
             "eog_allowed": list(item.eog_allowed),
             "calls": dict(item.calls), "sampler_record": sampler_values(), "abi": dict(item.abi), "extra": 1}
    value.pop("grammar_bytes"); value.pop("decoder_bytes")
    with pytest.raises(proof.ProofError):
        proof.parse_child_result(value)
    value.pop("extra"); value.pop("grammar_b64")
    with pytest.raises(proof.ProofError):
        proof.parse_child_result(value)


def test_build_product_sdk_certificate_bundle_is_closed(tmp_path: Path, monkeypatch) -> None:
    identities = {
        "child_source": "1" * 64, "proof_child": "2" * 64,
        "compiler": "3" * 64, "linker": "4" * 64, "dumpbin": "5" * 64,
    }
    sdk = {
        "schema_version": "aiwolf.pf3-toolchain-manifest.v1", "msvc_version": "m",
        "sdk_version": "s", "compiler_sha256": identities["compiler"],
        "linker_sha256": identities["linker"], "dumpbin_sha256": identities["dumpbin"],
        "selected_headers_and_libraries": {"windows.h": "6" * 64},
    }
    symbols = {"emitter": "?emitter123", "json_parse": "?parse12345",
               "json_dump": "?dump123456", "json_destroy": "?destroy12"}
    abi = {key: 0 for key in proof.ABI_KEYS}
    sdk_path = tmp_path / "sdk.json"; sdk_path.write_bytes(proof.canonical_bytes(sdk) + b"\n")
    identities["sdk_manifest"] = proof.digest(sdk_path.read_bytes())
    build = {
        "schema_version": "aiwolf.pf3-build-manifest.v1", "mode": "actual",
        "source_sha256": identities["child_source"], "output_sha256": identities["proof_child"],
        "compiler_sha256": identities["compiler"], "linker_sha256": identities["linker"],
        "dumpbin_sha256": identities["dumpbin"], "msvc_version": "m", "sdk_version": "s",
        "argv": ["cl.exe"], "closure": {"archive_sha256": proof.SOURCE_ARCHIVE_SHA256,
            "archive_size": proof.SOURCE_ARCHIVE_SIZE, "manifest_sha256": "7" * 64,
            "member_count": 17}, "symbols": symbols, "abi": abi,
        "sdk_manifest_sha256": identities["sdk_manifest"],
    }
    build_path = tmp_path / "build.json"; build_path.write_bytes(proof.canonical_bytes(build) + b"\n")
    identities["build_manifest"] = proof.digest(build_path.read_bytes())
    product_path = tmp_path / "product.json"; product_path.write_bytes(proof.product_certificate_bytes())
    identities["product_source_manifest"] = proof.digest(product_path.read_bytes())
    execution = {
        "schema_version": "aiwolf.pf3-reviewed-execution-bundle.v1",
        "design_sha256": proof.DESIGN_SHA256,
        "proof_child_sha256": identities["proof_child"],
        "child_source_sha256": identities["child_source"],
        "build_manifest_sha256": identities["build_manifest"],
        "sdk_manifest_sha256": identities["sdk_manifest"],
        "compiler_sha256": identities["compiler"], "linker_sha256": identities["linker"],
        "dumpbin_sha256": identities["dumpbin"], "closure_manifest_sha256": "7" * 64,
        "source_archive_sha256": proof.SOURCE_ARCHIVE_SHA256,
        "source_archive_size": proof.SOURCE_ARCHIVE_SIZE,
        "product_certificate_sha256": identities["product_source_manifest"],
        "symbols": symbols, "abi": abi,
    }
    execution_path = tmp_path / "execution.json"
    execution_path.write_bytes(proof.canonical_bytes(execution) + b"\n")
    identities["execution_bundle"] = proof.digest(execution_path.read_bytes())
    fds = [os.open(path, os.O_RDONLY | os.O_BINARY)
           for path in (build_path, sdk_path, product_path, execution_path)]
    try:
        pins = {"build_manifest": {"fd": fds[0]}, "sdk_manifest": {"fd": fds[1]},
                "product_source_manifest": {"fd": fds[2]}, "execution_bundle": {"fd": fds[3]}}
        config = {"sampler_record": sampler_values(), "symbols": symbols, "expected_abi": abi}
        deadline = __import__("time").monotonic() + 10
        # A self-consistent caller-selected bundle is not authority until its hash is tool-fixed.
        assert identities["execution_bundle"] != proof.EXECUTION_BUNDLE_SHA256
        with pytest.raises(proof.ProofError):
            windows_proof._validate_execution_bundle_hash(identities["execution_bundle"])
        monkeypatch.setattr(proof, "EXECUTION_BUNDLE_SHA256", identities["execution_bundle"])
        windows_proof._validate_execution_bundle_hash(identities["execution_bundle"])
        projection = windows_proof._validate_certificate_bundle(config, pins, identities, deadline)
        assert projection["execution_bundle"] == execution
        broken = dict(build); broken["output_sha256"] = "8" * 64
        os.close(fds[0]); build_path.write_bytes(proof.canonical_bytes(broken) + b"\n")
        fds[0] = os.open(build_path, os.O_RDONLY | os.O_BINARY); pins["build_manifest"]["fd"] = fds[0]
        with pytest.raises(proof.ProofError):
            windows_proof._validate_certificate_bundle(config, pins, identities, deadline)
    finally:
        for fd in fds:
            try: os.close(fd)
            except OSError: pass


def test_public_report_excludes_private_material() -> None:
    config = {"run_id": "T534-test", "approved_non_system": {"proof.exe": "1" * 64}}
    report = windows_proof._public_report(config, {"_child_count": 1}, "UNKNOWN_RUNTIME_INVALID", 1.0)
    encoded = proof.canonical_bytes(report)
    assert report["pf3"] == "UNKNOWN"
    assert b"private_manifest" not in encoded
    assert b"raw" not in encoded and b"token_ids" not in encoded and b"module_path" not in encoded


@pytest.mark.parametrize("change,aggregate", [
    ({"grammar_size": 1}, "UNKNOWN_GRAMMAR_EMITTER"),
    ({"decoder_bytes": b"bad"}, "UNKNOWN_DECODER_MISMATCH"),
    ({"prefix_after_ids": tuple(range(511)) + (999,)}, "UNKNOWN_PREFIX_REJECTED"),
    ({"eog_after_ids": (999,)}, "UNKNOWN_EOG_OR_BUDGET"),
])
def test_private_native_observation_drift_is_unknown(change: dict[str, object], aggregate: str) -> None:
    with pytest.raises(proof.ProofError) as captured:
        proof.validate_child_result(replace(good_result(), **change), nonce="a" * 32,
                                    expected_raw=b"raw", expected_abi={key: 0 for key in proof.ABI_KEYS})
    assert captured.value.aggregate == aggregate


def test_grammar_and_decoder_resource_limits_are_closed() -> None:
    with pytest.raises(proof.ProofError) as grammar:
        proof.validate_child_result(replace(good_result(), grammar_size=proof.GRAMMAR_LIMIT + 1,
                                            grammar_bytes=b"x" * (proof.GRAMMAR_LIMIT + 1)),
                                    nonce="a" * 32)
    assert grammar.value.aggregate == "UNKNOWN_RESOURCE_BOUND"
    with pytest.raises(proof.ProofError) as decoder:
        proof.validate_child_result(replace(good_result(), decoder_bytes=b"x" * (proof.DETOKENIZED_LIMIT + 1)),
                                    nonce="a" * 32)
    assert decoder.value.aggregate == "UNKNOWN_RESOURCE_BOUND"


@pytest.mark.parametrize("stage", ["open", "write", "fsync", "rename"])
def test_publication_failure_never_creates_public_fail(tmp_path: Path, stage: str) -> None:
    output = (tmp_path / "public.json").resolve()
    deadline = __import__("time").monotonic() + 10
    def fail(*_args, **_kwargs):
        raise OSError(stage)
    kwargs = {stage if stage != "open" else "opener": fail}
    with pytest.raises(proof.ProofError) as captured:
        windows_proof._publish_public(output, b'{"pf3":"FAIL"}\n', deadline, **kwargs)
    assert captured.value.aggregate == "UNKNOWN_EVIDENCE_INVALID"
    assert not output.exists()


def test_publication_short_write_never_creates_public_fail(tmp_path: Path) -> None:
    output = (tmp_path / "public.json").resolve()
    with pytest.raises(proof.ProofError):
        windows_proof._publish_public(output, b'{"pf3":"FAIL"}\n',
                                      __import__("time").monotonic() + 10,
                                      write=lambda _fd, _raw: 0)
    assert not output.exists()


def test_publication_rejects_existing_output_and_missing_parent(tmp_path: Path) -> None:
    deadline = __import__("time").monotonic() + 10
    existing = (tmp_path / "existing.json").resolve(); existing.write_bytes(b"old")
    with pytest.raises(proof.ProofError):
        windows_proof._publish_public(existing, b"new", deadline)
    missing = (tmp_path / "missing" / "public.json").resolve()
    with pytest.raises(proof.ProofError):
        windows_proof._publish_public(missing, b"new", deadline)
    assert not missing.exists()


@pytest.mark.skipif(os.name != "nt", reason="handle-based publication is Windows-only")
def test_publication_success_is_exact_and_atomic(tmp_path: Path) -> None:
    output = (tmp_path / "public.json").resolve()
    raw = b'{"pf3":"FAIL"}\n'
    windows_proof._publish_public(output, raw, __import__("time").monotonic() + 10)
    assert output.read_bytes() == raw
    assert not output.with_name(output.name + ".partial").exists()


def test_whole_run_deadline_applies_before_hash_child_and_publication(tmp_path: Path) -> None:
    expired = __import__("time").monotonic() - 1
    artifact = tmp_path / "artifact"; artifact.write_bytes(b"x")
    fd = os.open(artifact, os.O_RDONLY | os.O_BINARY)
    try:
        with pytest.raises(proof.ProofError, match="deadline"):
            windows_proof._descriptor_hash_deadline(fd, expired)
    finally:
        os.close(fd)
    module, event, allow = one_module()
    schema, raw = proof.build_witness()
    backend = proof.SyntheticBackend(replace(good_result(), decoder_bytes=raw), (module,), (event,))
    with pytest.raises(proof.ProofError, match="deadline"):
        proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
                          sampler_input=sampler_values(), approved_modules=allow,
                          run_deadline=expired)
    assert backend.started == 0
    with pytest.raises(proof.ProofError, match="deadline"):
        windows_proof._publish_public((tmp_path / "late.json").resolve(), b"{}\n", expired)


def test_whole_run_deadline_closes_mid_child_post_hash_and_seal(tmp_path: Path, monkeypatch) -> None:
    module, event, allow = one_module()
    schema, raw = proof.build_witness()
    class SlowStart(proof.SyntheticBackend):
        def start(self, envelope: bytes, nonce: str, deadline: float | None = None) -> None:
            self.started += 1
    backend = SlowStart(replace(good_result(), decoder_bytes=raw), (module,), (event,))
    ticks = iter((0.0, 0.0, 0.002))
    with monkeypatch.context() as patch:
        patch.setattr(proof.time, "monotonic", lambda: next(ticks))
        with pytest.raises(proof.ProofError, match="deadline"):
            proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
                              sampler_input=sampler_values(), approved_modules=allow,
                              run_deadline=20.001)
    assert backend.started == 1
    expired = windows_proof.time.monotonic() - 1
    artifact = tmp_path / "artifact"; artifact.write_bytes(b"x")
    fd = os.open(artifact, os.O_RDONLY | os.O_BINARY)
    try:
        pin = {"fd": fd, "file_identity": proof.t527.file_identity(fd)}
        with pytest.raises(proof.ProofError, match="deadline"):
            windows_proof._verify_configured_pins({"artifact": pin}, {"artifact": proof.digest(b"x")}, expired)
    finally:
        os.close(fd)
    class Evidence:
        called = False
        def write(self, _name, _raw):
            self.called = True
    evidence = Evidence()
    with pytest.raises(proof.ProofError, match="deadline"):
        windows_proof._evidence_write(evidence, "detail.json", b"{}", expired)
    assert not evidence.called


@pytest.mark.skipif(os.name != "nt", reason="Windows child owner boundary")
def test_start_queue_wait_uses_absolute_deadline_and_cleans_partial(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "input.bin"; path.write_bytes(b"")
    fd = os.open(path, os.O_RDWR | os.O_BINARY)
    backend = windows_proof.WindowsDebugBackend(
        tmp_path / "child.exe", fd, {}, model_path=tmp_path / "model.gguf",
        native_dir=tmp_path, symbols={}, static_verify=lambda _phase: None,
        run_deadline=__import__("time").monotonic() + 20)
    observed: dict[str, object] = {}
    class EmptyQueue:
        def get(self, timeout: float):
            observed["timeout"] = timeout
            raise __import__("queue").Empty
    backend._proc_queue = EmptyQueue()
    monkeypatch.setattr(backend, "_debug_owner", lambda *_args: None)
    monkeypatch.setattr(backend, "_cleanup_owned", lambda deadline: observed.setdefault("cleanup", deadline))
    try:
        deadline = __import__("time").monotonic() + 0.25
        with pytest.raises(proof.ProofError, match="spawn deadline"):
            backend.start(b"input", "a" * 32, deadline)
        assert 0 < observed["timeout"] <= 0.25
        assert observed["cleanup"] == backend.run_deadline
    finally:
        backend.stack.close(); os.close(fd)


def test_partial_spawn_cleanup_confirms_owned_child_exit() -> None:
    class Process:
        code = None
        terminated = False
        def poll(self): return self.code
        def terminate(self): self.terminated = True; self.code = 0
        def wait(self, timeout): return self.code
        def kill(self): self.code = -9
    process = Process()
    backend = object.__new__(windows_proof.WindowsDebugBackend)
    backend.proc = process; backend._thread = None; backend.exit_code = None
    backend.ended_at_monotonic = None; backend.cleanup_result = "NOT_STARTED"
    backend._cleanup_owned(__import__("time").monotonic() + 20)
    assert process.terminated and backend.exit_code == 0 and backend.cleanup_result == "REAPED"


def test_closed_archive_extracts_exact_member(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"; content = b"header"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("root/include/a.h", content)
    destination = tmp_path / "out"; destination.mkdir()
    build_tool.extract_closed_archive(archive, {"root/include/a.h": (len(content), proof.digest(content))}, destination)
    assert (destination / "root/include/a.h").read_bytes() == content


def test_closed_archive_rejects_missing_hash_drift_and_duplicate(tmp_path: Path) -> None:
    missing = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing, "w") as stream:
        stream.writestr("root/other.h", b"x")
    with pytest.raises(proof.ProofError):
        build_tool.extract_closed_archive(missing, {"root/a.h": (1, proof.digest(b"x"))}, tmp_path / "out1")
    drift = tmp_path / "drift.zip"
    with zipfile.ZipFile(drift, "w") as stream:
        stream.writestr("root/a.h", b"y")
    with pytest.raises(proof.ProofError):
        build_tool.extract_closed_archive(drift, {"root/a.h": (1, proof.digest(b"x"))}, tmp_path / "out2")
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as stream:
            stream.writestr("root/a.h", b"x")
            stream.writestr("root/a.h", b"x")
    with pytest.raises(proof.ProofError):
        build_tool.extract_closed_archive(duplicate, {"root/a.h": (1, proof.digest(b"x"))}, tmp_path / "out3")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "a\\b"])
def test_archive_member_rejects_escape(name: str) -> None:
    with pytest.raises(proof.ProofError):
        build_tool._safe_member(name)


@pytest.mark.skipif(os.name != "nt", reason="T534 child is Windows-only")
def test_compile_and_run_model_free_native_child(tmp_path: Path) -> None:
    child = tmp_path / "pf3-synthetic.exe"; manifest = tmp_path / "build.json"
    build_tool.build("synthetic", proof.ROOT / "scripts/native/phase6_pf3_token_path.cpp", child, manifest)
    assert child.is_file() and manifest.is_file()
    schema, raw = proof.build_witness()
    envelope = proof.encode_input_envelope("a" * 32, schema, raw, sampler_values())
    input_path = tmp_path / "input.bin"; input_path.write_bytes(envelope)
    fd = os.open(input_path, os.O_RDONLY | os.O_BINARY)
    try:
        handle = __import__("msvcrt").get_osfhandle(fd)
        identity = windows_proof._windows_handle_identity(handle)
        os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO(); startup.lpAttributeList = {"handle_list": [handle]}
        process = subprocess.Popen([str(child), "--input-handle", str(handle),
                                    "--input-volume", str(identity[0]), "--input-file-id", str(identity[1]),
                                    "--input-size", str(identity[2])], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startup,
                                   close_fds=True)
        os.set_handle_inheritable(handle, False)
        assert process.stdout is not None and process.stdin is not None
        assert process.stdout.readline().strip() == b"READY_PRE " + b"a" * 32
        process.stdin.write(b"CONTINUE_PRE " + b"a" * 32 + b"\n"); process.stdin.flush()
        result = proof.parse_child_result(proof.strict_json(process.stdout.readline()))
        assert result.accounted_generated_tokens == len(raw) + 1
        assert process.stdout.readline().strip() == b"READY_POST " + b"a" * 32
        process.stdin.write(b"CONTINUE_POST " + b"a" * 32 + b"\n"); process.stdin.flush()
        assert process.wait(timeout=30) == 0
    finally:
        os.close(fd)


# T538: all process objects below are synthetic; no model/runtime is loaded.
def io_backend():
    import time
    return windows_proof.WindowsDebugBackend(Path("synthetic-child"), -1, {},
        model_path=Path("synthetic-model"), native_dir=Path("."), symbols={},
        static_verify=lambda phase: None, run_deadline=time.monotonic() + 5)


def test_stderr_pipe_drains_beyond_pipe_capacity_without_native():
    import io
    import threading
    from types import SimpleNamespace
    backend = io_backend()
    read_fd, write_fd = os.pipe()
    payload = b"synthetic-private-stderr" * 32768
    stream = os.fdopen(read_fd, "rb")
    backend.proc = SimpleNamespace(stderr=stream)
    def write():
        with os.fdopen(write_fd, "wb") as target:
            target.write(payload)
    writer = threading.Thread(target=write, daemon=True)
    reader = threading.Thread(target=backend._drain_stream, args=("stderr",), daemon=True)
    reader.start(); writer.start()
    writer.join(3); reader.join(3)
    assert not writer.is_alive() and not reader.is_alive()
    assert backend.stderr_bytes() == payload and backend._stderr_eof
    assert backend._io_error is None
    stream.close()


@pytest.mark.parametrize("name", ["stdout", "stderr"])
def test_stream_overflow_drains_but_retains_only_bound(monkeypatch, name):
    import io
    from types import SimpleNamespace
    monkeypatch.setattr(windows_proof, "STREAM_LIMIT", 128)
    monkeypatch.setattr(windows_proof, "STREAM_CHUNK", 64)
    backend = io_backend()
    stream = io.BytesIO(b"private" * 200)
    backend.proc = SimpleNamespace(**{name: stream})
    backend._drain_stream(name)
    assert stream.tell() == 1400
    assert backend._stream_counts[name] == 129
    assert len(backend.stderr_bytes()) <= 128
    with pytest.raises(proof.ProofError, match="stream overflow") as caught:
        backend._line(1)
    assert caught.value.aggregate == "UNKNOWN_RESOURCE_BOUND"


def test_debug_error_wakes_existing_control_wait():
    import threading
    import time
    backend = io_backend()
    result = []
    entered = threading.Event()
    original_get = backend._lines.get
    def get(*args, **kwargs):
        entered.set()
        return original_get(*args, **kwargs)
    backend._lines.get = get
    def wait():
        try: backend._line(4)
        except proof.ProofError as error: result.append(error)
    waiter = threading.Thread(target=wait, daemon=True)
    waiter.start(); assert entered.wait(1)
    backend._debug_error = OSError(5, "synthetic-private-path")
    backend._wake_control()
    waiter.join(1)
    assert not waiter.is_alive()
    assert result[0].detail == "debug owner" and not backend.timed_out
    assert backend.private_observation()["debug_failure"]["win32_error"] == 5


def test_control_timeout_sets_flag_and_eof_does_not():
    backend = io_backend()
    with pytest.raises(proof.ProofError, match="control timeout"): backend._line(0)
    assert backend.timed_out
    backend = io_backend(); backend._stdout_eof = True
    with pytest.raises(proof.ProofError, match="control EOF"): backend._line(1)
    assert not backend.timed_out


def test_stdout_protocol_is_bounded_and_preserves_lines():
    import io
    from types import SimpleNamespace
    backend = io_backend()
    backend.proc = SimpleNamespace(stdout=io.BytesIO(b"READY_PRE x\r\n{\"status\":1}\nREADY_POST x\n"))
    backend._drain_stream("stdout")
    assert [backend._line(1) for _ in range(3)] == [b"READY_PRE x", b'{"status":1}', b"READY_POST x"]
    with pytest.raises(proof.ProofError, match="control EOF"): backend._line(1)


def test_failure_observation_preserves_actual_stage_and_redacts_text():
    class Failing(proof.SyntheticBackend):
        def wait_ready(self, phase, timeout):
            raise OSError(5, "synthetic-private-path-and-raw")
    module, event, allow = one_module()
    backend = Failing(good_result(), (module,), (event,))
    observation = {}
    schema, raw = proof.build_witness()
    with pytest.raises(OSError):
        proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
            sampler_input=sampler_values(), approved_modules=allow, observation=observation)
    assert observation["last_operation"] == "WAIT_READY_PRE"
    assert observation["last_state"] == "CHILD_STARTED"
    assert observation["state_history"] == ["NEW", "CLAIMED", "INPUT_BOUND", "TOOL_BOUND", "CHILD_STARTED"]
    assert observation["failure"]["exception_kind"] == "OSError"
    assert "synthetic-private" not in json.dumps(observation)


def test_cleanup_failure_preserves_primary_failure():
    class Failing(proof.SyntheticBackend):
        def wait_ready(self, phase, timeout): raise proof.ProofError("UNKNOWN_RUNTIME_INVALID", "control timeout")
        def close(self): raise proof.ProofError("UNKNOWN_RUNTIME_INVALID", "owned child cleanup")
    module, event, allow = one_module(); observation = {}
    backend = Failing(good_result(), (module,), (event,))
    schema, raw = proof.build_witness()
    with pytest.raises(proof.ProofError, match="owned child cleanup"):
        proof.run_backend(backend, nonce="a" * 32, schema=schema, raw=raw,
            sampler_input=sampler_values(), approved_modules=allow, observation=observation)
    assert observation["failure"]["code"] == "control timeout"
    assert observation["cleanup_failure"]["code"] == "owned child cleanup"


def test_private_runtime_keeps_loader_observation_but_public_does_not():
    backend = io_backend(); module, event, allow = one_module()
    backend._events.append(event)
    backend.process_identity = {"synthetic-private-process": 1}
    backend._stderr.extend(b"synthetic-private-raw")
    runtime = backend.private_observation()
    assert runtime["module_events"][0]["module"]["final_path"] == module.final_path
    assert runtime["process_identity"] == backend.process_identity
    public = windows_proof._public_report({"run_id": "synthetic", "approved_non_system": allow},
        {"_child_count": 1, "_private": runtime}, "UNKNOWN_RUNTIME_INVALID", 0.1)
    assert "synthetic-private" not in json.dumps(public)
    assert "module_events" not in public and "process_identity" not in public


@pytest.mark.parametrize("seal_failure", [False, True])
def test_runner_failure_seals_stage_stream_and_cleanup_without_public_leak(tmp_path, monkeypatch, seal_failure):
    from contextlib import contextmanager, ExitStack
    from types import SimpleNamespace
    import time
    directory = tmp_path / "private"; directory.mkdir()
    import scripts.phase6_private_review as private_helper
    # ACL policy has its own tests; keep real handles, writer, rename and seal here.
    monkeypatch.setattr(private_helper, "_windows_private_path", lambda *a, **kw: True)
    original_write = windows_proof.TokenPathPrivateEvidence.write
    if seal_failure:
        def reject_seal(self, name, raw, **kwargs):
            if name == "seal.json":
                raise proof.ProofError("UNKNOWN_EVIDENCE_INVALID", "synthetic seal failure")
            return original_write(self, name, raw, **kwargs)
        monkeypatch.setattr(windows_proof.TokenPathPrivateEvidence, "write", reject_seal)
    module,event,allow=one_module()
    class Backend(proof.SyntheticBackend):
        def __init__(self):
            super().__init__(good_result(),(module,),(event,))
            self.started_count=0; self.started_at_monotonic=1; self.ended_at_monotonic=None
            self.exit_code=None; self.timed_out=False; self.cleanup_result="NOT_STARTED"
        def start(self,*args): self.started_count+=1
        def wait_ready(self,*args):
            self.timed_out=True
            raise proof.ProofError("UNKNOWN_RUNTIME_INVALID","control timeout")
        def close(self):
            self.exit_code=1; self.ended_at_monotonic=2; self.cleanup_result="REAPED"
        def private_observation(self): return {"module_events":[event.kind], "termination_requested":True}
        def stderr_bytes(self): return b"synthetic-private-stderr"
    backend=Backend()
    pins={key:{"path":str(tmp_path/key),"file_identity":[1,2,3]} for key in ("proof_child","model","llama.dll")}
    monkeypatch.setattr(windows_proof,"_pin_configured",lambda *args:(pins,{}))
    monkeypatch.setattr(windows_proof,"_verify_configured_pins",lambda *args:None)
    monkeypatch.setattr(windows_proof,"_validate_certificate_bundle",lambda *args:{})
    monkeypatch.setattr(windows_proof,"WindowsDebugBackend",lambda *args,**kwargs:backend)
    monkeypatch.setattr(windows_proof,"_publish_public",lambda path,raw,deadline:path.write_bytes(raw))
    args=SimpleNamespace(private=directory,output=tmp_path/"public.json")
    config={"run_id":"synthetic","nonce":"a"*32,"approved_non_system":allow,
            "sampler_record":sampler_values(),"symbols":{},"expected_abi":{},
            "hashes":{key:"a"*64 for key in ("runner_source","windows_source","child_source",
                "build_helper_source","build_manifest","proof_child")}}
    if seal_failure:
        with pytest.raises(proof.ProofError,match="synthetic seal failure"):
            windows_proof._run_claimed_real_proof(args,config,time.monotonic(),time.monotonic()+60)
        assert not args.output.exists()
    else:
        assert windows_proof._run_claimed_real_proof(args,config,time.monotonic(),time.monotonic()+60)==0
        public=args.output.read_text()
        assert "synthetic-private-stderr" not in public
        assert json.loads(public)["aggregate"]=="UNKNOWN_RUNTIME_INVALID"
        seal=json.loads((directory/"seal.json").read_text())
        manifest=json.loads((directory/"manifest.json").read_text())
        assert seal["manifest_sha256"]==proof.digest((directory/"manifest.json").read_bytes())
        assert "child-stderr.bin" in {entry["name"] for entry in manifest["files"]}
        for entry in manifest["files"]:
            saved = (directory / entry["name"]).read_bytes()
            assert entry["sha256"] == proof.digest(saved)
            assert entry["size"] == len(saved)
    detail=json.loads((directory/"detail.json").read_text())
    assert detail["backend_observation"]["last_state"]=="CHILD_STARTED"
    assert detail["backend_observation"]["failure"]["code"]=="control timeout"
    assert detail["state_history"][-2:]==["CHILD_STARTED","UNKNOWN_SEALED"]
    assert detail["child_lifecycle"]["cleanup_result"]=="REAPED"
    assert (directory/"child-stderr.bin").read_bytes()==b"synthetic-private-stderr"
    assert backend.started_count==1


def test_reader_cleanup_unknown_is_fail_closed():
    import io
    from types import SimpleNamespace
    backend=io_backend()
    class Reader:
        def join(self,timeout): pass
        def is_alive(self): return True
    backend._readers=[Reader()]
    with pytest.raises(proof.ProofError,match="reader cleanup"):
        backend.close()
    assert backend.cleanup_result=="UNKNOWN_READERS"


def test_debug_ledger_is_bounded_and_retains_exit_and_exception(monkeypatch):
    backend=io_backend()
    event=windows_proof._DEBUG_EVENT(); event.dwDebugEventCode=windows_proof.EXCEPTION_DEBUG_EVENT
    event.u.Exception.ExceptionRecord.ExceptionCode=0xC0000005; event.u.Exception.dwFirstChance=0
    backend._record_debug_event(event)
    event=windows_proof._DEBUG_EVENT(); event.dwDebugEventCode=windows_proof.EXIT_PROCESS_DEBUG_EVENT
    event.u.ExitProcess.dwExitCode=1
    backend._record_debug_event(event)
    monkeypatch.setattr(proof,"MODULE_EVENT_LIMIT",2)
    with pytest.raises(proof.ProofError,match="module events"): backend._record_debug_event(event)
    ledger=backend.private_observation()["debug_events"]
    assert ledger[0]["exception_code"]==0xC0000005 and ledger[0]["first_chance"]==0
    assert ledger[1]["exit_code"]==1 and len(ledger)==2


def test_evidence_limit_rejects_before_write(monkeypatch):
    import time
    monkeypatch.setattr(windows_proof,"STREAM_LIMIT",8)
    class Evidence:
        files={}
        called=False
        def write(self,*args):self.called=True
    evidence=Evidence()
    with pytest.raises(proof.ProofError,match="private evidence"):
        windows_proof._evidence_write(evidence,"synthetic",b"x"*9,time.monotonic()+1)
    assert not evidence.called


def test_model_free_child_uses_actual_debug_and_io_owner(tmp_path, monkeypatch):
    import time
    child=tmp_path/"pf3-owner.exe"
    build_tool.build("synthetic",proof.ROOT/"scripts/native/phase6_pf3_token_path.cpp",child,tmp_path/"build.json")
    allow={child.name:proof.digest(child.read_bytes())}
    path=tmp_path/"input.bin";path.write_bytes(b"")
    fd=os.open(path,os.O_RDWR|os.O_BINARY)
    backend=windows_proof.WindowsDebugBackend(child,fd,allow,model_path=tmp_path/"unused",
        native_dir=tmp_path,symbols={key:"unused" for key in ("emitter","json_parse","json_dump","json_destroy")},
        static_verify=lambda phase:None,run_deadline=time.monotonic()+30)
    monkeypatch.setattr(proof,"CHILD_TIMEOUT_SECONDS",5)
    schema,raw=proof.build_witness(); observation={}
    try:
        summary,state=proof.run_backend(backend,nonce="a"*32,schema=schema,raw=raw,
            sampler_input=sampler_values(),approved_modules=allow,observation=observation)
        assert summary["aggregate"]=="FAIL_BUDGET" and backend.started_count==1
        assert backend.cleanup_result=="REAPED" and backend.exit_code==0
        assert backend._stdout_eof and backend._stderr_eof
        assert all(not reader.is_alive() for reader in backend._readers)
        assert backend.private_observation()["debug_events"]
    except BaseException:
        runtime=backend.private_observation()
        pytest.fail(json.dumps({"observation":observation,"debug_operation":runtime["debug_operation"],
            "debug_failure":runtime["debug_failure"],"cleanup_result":backend.cleanup_result,
            "debug_event_count":len(runtime["debug_events"])}))
    finally:
        os.close(fd)



def test_debug_process_thread_handles_remain_os_owned(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    backend=io_backend()
    closed=[]; continued=[]; pinned=[]
    created=windows_proof._DEBUG_EVENT()
    created.dwDebugEventCode=windows_proof.CREATE_PROCESS_DEBUG_EVENT
    created.u.CreateProcessInfo.hFile=11
    created.u.CreateProcessInfo.hProcess=22
    created.u.CreateProcessInfo.hThread=33
    exited=windows_proof._DEBUG_EVENT()
    exited.dwDebugEventCode=windows_proof.EXIT_PROCESS_DEBUG_EVENT
    events=iter([created,exited])
    class Function:
        def __init__(self,fn):self.fn=fn
        def __call__(self,*args):return self.fn(*args)
    def wait(ptr,timeout):
        event=next(events)
        ctypes.memmove(ptr,ctypes.byref(event),ctypes.sizeof(event))
        return True
    kernel=SimpleNamespace(WaitForDebugEvent=Function(wait),
        ContinueDebugEvent=Function(lambda *args:continued.append(args) or True),
        CloseHandle=Function(lambda handle:closed.append(handle) or True))
    monkeypatch.setattr(windows_proof.ctypes,"WinDLL",lambda *args,**kwargs:kernel)
    process=SimpleNamespace()
    monkeypatch.setattr(windows_proof.subprocess,"Popen",lambda *args,**kwargs:process)
    monkeypatch.setattr(backend,"_assign_job",lambda proc:None)
    monkeypatch.setattr(windows_proof.t527,"creation_identity",lambda *args:{"synthetic":1})
    module,_,_=one_module()
    monkeypatch.setattr(backend,"_pin_debug_handle",lambda handle:pinned.append(handle) or module)
    backend.symbols={key:"unused" for key in ("emitter","json_parse","json_dump","json_destroy")}
    backend._debug_owner(44,(1,2,3))
    assert backend._debug_error is None
    assert pinned==[11] and closed==[11]
    assert len(continued)==2
    assert [event["event_code"] for event in backend.private_observation()["debug_events"]]==[3,5]


@pytest.mark.parametrize("failure", ["extra", "duplicate", "claim", "limit", "write", "rename", "read"])
def test_token_path_private_blob_closed_contract(tmp_path, monkeypatch, failure):
    from contextlib import ExitStack
    import scripts.phase6_private_review as private_helper
    monkeypatch.setattr(private_helper, "_windows_private_path", lambda *a, **kw: True)
    with ExitStack() as stack:
        store = windows_proof.TokenPathPrivateEvidence(tmp_path, stack)
        raw = b"private-stderr"
        if failure == "extra":
            with pytest.raises(ValueError): store.write("other.bin", raw)
        elif failure == "claim":
            with pytest.raises(ValueError): store.write("child-stderr.bin", raw, claim=True)
        elif failure == "duplicate":
            store.write("child-stderr.bin", raw)
            assert store.read("child-stderr.bin") == raw
            with pytest.raises(ValueError): store.write("child-stderr.bin", raw)
        elif failure == "limit":
            monkeypatch.setattr(windows_proof, "STREAM_LIMIT", len(raw)-1)
            with pytest.raises(proof.ProofError): store.write("child-stderr.bin", raw)
        else:
            if failure == "write":
                monkeypatch.setattr(windows_proof.os, "write", lambda fd, data: len(data)-1)
            elif failure == "rename":
                def rejected(*args): raise OSError("synthetic rename")
                monkeypatch.setattr(windows_proof.t527, "rename_open_file", rejected)
            else:
                monkeypatch.setattr(windows_proof.t527, "descriptor_bytes", lambda fd: b"wrong")
            with pytest.raises(OSError): store.write("child-stderr.bin", raw)
        if failure != "duplicate":
            assert "child-stderr.bin" not in store.files
        assert not (tmp_path / "manifest.json").exists()
        assert not (tmp_path / "seal.json").exists()


def test_shared_private_evidence_contract_is_unchanged(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        shared = windows_proof.t527.PrivateEvidence(tmp_path, stack)
        with pytest.raises(ValueError): shared.write("child-stderr.bin", b"private")
    assert not list(tmp_path.iterdir())
