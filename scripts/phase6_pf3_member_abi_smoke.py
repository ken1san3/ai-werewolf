"""One-shot, model-free actual-DLL smoke for the reviewed T543 member ABI gate."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Mapping, Sequence

if __package__ in {None, ""}:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import phase6_pf3_counterexample as t527
from scripts import phase6_pf3_token_path as core
from scripts import phase6_pf3_token_path_windows as windows
from scripts import build_phase6_pf3_token_path as builder


SCHEMA = "aiwolf.pf3-dump-abi-smoke-run.v1"
EXECUTION_BUNDLE_SHA256 = "0fdada60b73f852b6cc0105614913ccdd4210484adfdbc62e8131bba66bf4890"
PUBLIC_HASH_KEYS = {
    "runner_source", "windows_source", "build_helper_source", "actual_smoke_source",
    "actual_smoke_exe", "build_manifest", "build_certificate", "llama_common",
    "dependency_manifest",
}
CONFIG_KEYS = {"schema_version", "run_id", "nonce", "paths", "hashes", "module_paths",
               "approved_non_system", "symbols", "execution_bundle"}


def _sha(fd: int) -> str:
    return t527.descriptor_hash(fd)


def strict_config(raw: bytes) -> dict[str, object]:
    value = core.strict_json(raw)
    if not isinstance(value, dict) or set(value) != CONFIG_KEYS or value.get("schema_version") != SCHEMA:
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke config shape")
    if core.canonical_bytes(value) + b"\n" != raw:
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke config canonical bytes")
    if (not isinstance(value["run_id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{16,96}", value["run_id"]) is None
            or not isinstance(value["nonce"], str) or re.fullmatch(r"[0-9a-f]{32}", value["nonce"]) is None):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke run identity")
    paths = value["paths"]; hashes = value["hashes"]
    if (not isinstance(paths, dict) or not isinstance(hashes, dict) or set(paths) != PUBLIC_HASH_KEYS
            or set(hashes) != PUBLIC_HASH_KEYS):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke artifact shape")
    for key in PUBLIC_HASH_KEYS:
        if (not isinstance(paths[key], str) or not Path(paths[key]).is_absolute()
                or not isinstance(hashes[key], str) or re.fullmatch(r"[0-9a-f]{64}", hashes[key]) is None):
            raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke artifact identity")
    module_paths = value["module_paths"]
    approved = value["approved_non_system"]
    if (not isinstance(module_paths, dict) or not 1 <= len(module_paths) <= 16
            or not isinstance(approved, dict) or not approved):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke modules")
    if any(not isinstance(name, str) or not isinstance(path, str) or not Path(path).is_absolute()
           for name, path in module_paths.items()):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke module locator")
    if any(not isinstance(path, str) or not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None
           for path, sha in approved.items()):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke module allowlist")
    symbols = value["symbols"]
    if (not isinstance(symbols, dict) or set(symbols) != {"json_parse", "json_dump", "json_destroy"}
            or any(not isinstance(item, str) or not item for item in symbols.values())):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke symbols")
    execution = value["execution_bundle"]
    if not isinstance(execution, str) or not Path(execution).is_absolute():
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "smoke execution bundle")
    return value


def validate_execution_bundle(value: object, identities: Mapping[str, str]) -> dict[str, object]:
    keys = {"schema_version", "design_sha256", "state", "qualification_sha256", "artifacts", "symbols"}
    if (not isinstance(value, dict) or set(value) != keys
            or value.get("schema_version") != "aiwolf.pf3-member-abi-smoke-execution.v1"
            or value.get("state") != "ACTUAL_SMOKE_NOT_RUN" or value.get("qualification_sha256") is not None
            or value.get("design_sha256") != "c43ea9b5af384e08594f79d37d89b0ddd8a6e162d381b28fbe1c05c4e84cf3c2"
            or value.get("artifacts") != {key: identities[key] for key in (
                "actual_smoke_source", "actual_smoke_exe", "build_manifest", "build_certificate",
                "llama_common", "dependency_manifest")}
            or not isinstance(value.get("symbols"), dict)):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke execution bundle")
    return value


def _module_projection(items) -> list[dict[str, object]]:
    return [item.__dict__ for item in items]


def _validate_static_binding(config: Mapping[str, object], stack: ExitStack) -> dict[str, object]:
    """Pin and validate the full reviewed closure from the same descriptor bytes."""
    pins: dict[str, object] = {}
    identities: dict[str, str] = {}
    for key in sorted(PUBLIC_HASH_KEYS):
        pin = stack.enter_context(t527.pinned_static_file(Path(config["paths"][key])))
        observed = _sha(pin["fd"])
        if observed != config["hashes"][key]:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke artifact drift")
        pins[key] = pin
        identities[key] = observed
    execution_pin = stack.enter_context(t527.pinned_static_file(Path(config["execution_bundle"])))
    if _sha(execution_pin["fd"]) != EXECUTION_BUNDLE_SHA256:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "unreviewed smoke execution bundle")
    execution_raw = t527.descriptor_bytes(execution_pin["fd"])
    execution = core.strict_json(execution_raw)
    if core.canonical_bytes(execution) + b"\n" != execution_raw:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "execution bundle canonical bytes")
    validate_execution_bundle(execution, identities)
    if config["symbols"] != execution["symbols"]:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke symbol binding")
    dependency_raw = t527.descriptor_bytes(pins["dependency_manifest"]["fd"])
    dependency = core.strict_json(dependency_raw)
    if (core.canonical_bytes(dependency) + b"\n" != dependency_raw
            or not isinstance(dependency, dict) or set(dependency) != {"schema_version", "artifacts"}
            or dependency.get("schema_version") != "aiwolf.pf3-member-abi-dependencies.v1"
            or not isinstance(dependency.get("artifacts"), dict)
            or set(dependency["artifacts"]) != set(config["module_paths"])):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "dependency manifest")
    module_pins: dict[str, object] = {}
    for key, expected in sorted(dependency["artifacts"].items()):
        pin = stack.enter_context(t527.pinned_static_file(Path(config["module_paths"][key])))
        if _sha(pin["fd"]) != expected:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "dependency drift")
        module_pins[key] = pin
    expected_modules = {Path(config["module_paths"][key]).name.casefold(): value
                        for key, value in dependency["artifacts"].items()}
    expected_modules[Path(config["paths"]["actual_smoke_exe"]).name.casefold()] = identities["actual_smoke_exe"]
    if config["approved_non_system"] != expected_modules:
        raise core.ProofError("UNKNOWN_MODULE_SET", "smoke module allowlist closure")
    build_raw = t527.descriptor_bytes(pins["build_manifest"]["fd"])
    build_manifest = core.strict_json(build_raw)
    build_keys = {"schema_version", "mode", "source_sha256", "output_sha256", "compiler_sha256",
        "linker_sha256", "dumpbin_sha256", "msvc_version", "sdk_version", "argv", "closure",
        "symbols", "abi", "sdk_manifest_sha256", "member_abi", "implementation_dependencies"}
    if (core.canonical_bytes(build_manifest) + b"\n" != build_raw
            or not isinstance(build_manifest, dict) or set(build_manifest) != build_keys
            or build_manifest.get("schema_version") != "aiwolf.pf3-build-manifest.v2"
            or build_manifest.get("mode") != "actual"
            or build_manifest.get("source_sha256") != identities["actual_smoke_source"]
            or any(build_manifest.get("symbols", {}).get(key) != value
                   for key, value in config["symbols"].items())):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke build manifest shape")
    member = build_manifest.get("member_abi")
    if (not isinstance(member, dict) or set(member) != {"compile_record", "compile_record_sha256",
            "generated_header_sha256", "build_certificate_sha256", "actual_smoke_exe_sha256",
            "qualification_sha256", "gate"} or member.get("gate") != "ACTUAL_SMOKE_NOT_RUN"
            or member.get("qualification_sha256") is not None
            or member.get("actual_smoke_exe_sha256") != identities["actual_smoke_exe"]
            or member.get("build_certificate_sha256") != identities["build_certificate"]):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke build manifest")
    compile_record = member.get("compile_record")
    compile_sha = member.get("compile_record_sha256")
    if (not isinstance(compile_record, dict) or not isinstance(compile_sha, str)
            or core.digest(core.canonical_bytes(compile_record) + b"\n") != compile_sha):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke compile record")
    builder.validate_member_abi_compile_record(compile_record)
    certificate_raw = t527.descriptor_bytes(pins["build_certificate"]["fd"])
    certificate = core.strict_json(certificate_raw)
    if core.canonical_bytes(certificate) + b"\n" != certificate_raw:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke certificate canonical bytes")
    builder.validate_member_abi_certificate(certificate, compile_record)
    if (certificate["actual_smoke_source_sha256"] != identities["actual_smoke_source"]
            or certificate["actual_smoke_exe_sha256"] != identities["actual_smoke_exe"]
            or certificate["dll_sha256"] != identities["llama_common"]
            or certificate["sdk_manifest_sha256"] != build_manifest.get("sdk_manifest_sha256")):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "smoke certificate artifact binding")
    return {"pins": pins, "module_pins": module_pins, "identities": identities,
            "execution": execution, "dependency": dependency, "build_manifest": build_manifest,
            "compile_record": compile_record, "compile_record_sha256": compile_sha}


def preflight(args) -> int:
    """Validate every locator/binding without claiming evidence or starting a child."""
    with ExitStack() as stack:
        config_fd = stack.enter_context(t527._locked_path(args.config.resolve(), create=False))
        config = strict_config(t527.descriptor_bytes(config_fd))
        _validate_static_binding(config, stack)
        target = t527._plain_absolute(args.private.resolve(), must_exist=True)
        stack.enter_context(t527._locked_path(target, directory=True))
        if any(target.iterdir()):
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "container already used")
        output = args.output.resolve(strict=False)
        if output.exists() or output.with_name(output.name + ".partial").exists() or not output.parent.is_dir():
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public output locator")
    print('{"child_count":0,"status":"READY"}')
    return 0

def run(args, *, backend_factory=None, binding_validator=_validate_static_binding) -> int:
    started = time.monotonic()
    deadline = started + 30.0
    backend_factory = backend_factory or windows.WindowsDebugBackend
    with ExitStack() as config_stack:
        config_fd = config_stack.enter_context(t527._locked_path(args.config.resolve(), create=False))
        config = strict_config(t527.descriptor_bytes(config_fd))
        binding = binding_validator(config, config_stack)
        pins = binding["pins"]
        identities = binding["identities"]
        compile_record = binding["compile_record"]
        compile_sha = binding["compile_record_sha256"]
        claim_config = {"run_id": config["run_id"], "nonce": config["nonce"], "hashes": {
            "runner_source": config["hashes"]["runner_source"],
            "windows_source": config["hashes"]["windows_source"],
            "child_source": config["hashes"]["actual_smoke_source"],
            "build_helper_source": config["hashes"]["build_helper_source"],
            "build_manifest": config["hashes"]["build_manifest"],
            "proof_child": config["hashes"]["actual_smoke_exe"],
        }}
        with windows._claim_private_v2(args.private.resolve(), claim_config, deadline) as evidence:
            native_dir = Path(config["paths"]["llama_common"]).parent
            backend = None
            result = None
            failure = None
            pre = ()
            post = ()
            events = ()
            history = ["NEW", "CLAIMED", "INPUT_BOUND", "TOOL_BOUND"]
            input_stream = tempfile.TemporaryFile(mode="w+b")
            try:
                backend = backend_factory(Path(pins["actual_smoke_exe"]["path"]), input_stream.fileno(),
                    config["approved_non_system"], model_path=None, native_dir=native_dir,
                    symbols=config["symbols"], static_verify=lambda phase: None, run_deadline=deadline,
                    mode="abi_smoke")
                envelope = core.encode_input_envelope(config["nonce"], b"{}", b'{"pf3":1}', {})
                backend.start(envelope, config["nonce"], deadline - 20.0)
                history.append("CHILD_STARTED")
                backend.wait_ready("PRE", max(0.0, deadline - time.monotonic()))
                history.append("NATIVE_LOADED")
                pre = backend.snapshot()
                events = tuple(backend.events())
                core.validate_module_sets(pre, pre, events, config["approved_non_system"])
                history.append("MODULE_PRE_VERIFIED")
                backend.continue_child("PRE", config["nonce"])
                result = backend.raw_result(max(0.0, deadline - time.monotonic()))
                core.validate_member_abi_smoke_child(result, compile_record_sha256=compile_sha,
                    compile_record=compile_record)
                history.append("DUMP_VERIFIED")
                backend.wait_ready("POST", max(0.0, deadline - time.monotonic()))
                post = backend.snapshot()
                events = tuple(backend.events())
                core.validate_module_sets(pre, post, events, config["approved_non_system"])
                history.append("MODULE_POST_VERIFIED")
                backend.continue_child("POST", config["nonce"])
                backend.reap(max(0.0, deadline - time.monotonic()))
                history.append("CHILD_REAPED")
            except BaseException as exc:
                failure = core.failure_observation(exc)
            finally:
                if backend is not None:
                    try:
                        backend.close()
                    except BaseException as exc:
                        failure = failure or core.failure_observation(exc)
                input_stream.close()
            status = "ABI_COMPATIBLE" if failure is None and result is not None else "UNKNOWN_ABI_IDENTITY"
            module_projection = {"module_pre": _module_projection(pre), "module_post": _module_projection(post),
                "module_events": [{"ordinal": item.ordinal, "kind": item.kind,
                    "module": None if item.module is None else item.module.__dict__} for item in events]}
            planned_history = history + ["PRIVATE_SEALED" if status == "ABI_COMPATIBLE" else "UNKNOWN_SEALED"]
            detail = {"schema_version": "aiwolf.pf3-dump-abi-smoke-private.v1",
                "state_history": planned_history,
                "runtime_record": None if result is None else result.get("runtime_record"),
                "fixed_input": '{"pf3":1}',
                "fixed_output": None if result is None else result.get("output_utf8"),
                "call_counts": None if result is None else {key: result.get(key)
                    for key in ("parse_calls", "dump_calls", "destroy_calls")},
                "module_projection": module_projection,
                "module_projection_sha256": core.digest(core.canonical_bytes(module_projection)),
                "child_lifecycle": {
                    "exit_code": None if backend is None else backend.exit_code,
                    "timed_out": False if backend is None else backend.timed_out,
                    "cleanup_result": "NOT_STARTED" if backend is None else backend.cleanup_result},
                "failure": failure}
            evidence.write("child-stderr.bin", b"" if backend is None else backend.stderr_bytes())
            evidence.write("detail.json", core.canonical_bytes(detail))
            aggregate = status
            manifest = {"schema_version": "aiwolf.pf3-token-path-manifest.v1",
                "files": [{"name": name, "sha256": _sha(fd), "size": os.fstat(fd).st_size}
                          for name, fd in sorted(evidence.files.items())], "aggregate": aggregate}
            evidence.write("manifest.json", core.canonical_bytes(manifest))
            evidence.write("seal.json", core.canonical_bytes({
                "manifest_sha256": _sha(evidence.files["manifest.json"]), "aggregate": aggregate,
                "publication_candidate": status,
                "canonical_state_at_seal": "PRIVATE_SEALED" if status == "ABI_COMPATIBLE"
                    else "UNKNOWN_SEALED"}))
            history[:] = planned_history
            duration = time.monotonic() - started
            if not math.isfinite(duration) or duration < 0:
                raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "smoke duration")
            report = {"schema_version": "aiwolf.pf3-dump-abi-smoke-public.v1", "task": "T545",
                "run_id": config["run_id"], "status": status,
                "child_count": 0 if backend is None else backend.started_count,
                "duration_seconds": round(duration, 6), "provider_count": 0, "inference_count": 0,
                "server_count": 0, "gpu_count": 0, "game_count": 0, "actions_count": 0,
                "approved_hashes": identities}
            windows._publish_public(args.output.resolve(strict=False),
                                    core.canonical_bytes(report) + b"\n", deadline)
            return 0 if status == "ABI_COMPATIBLE" else 2

def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="T543 model-free actual DLL ABI smoke")
    value.add_argument("--config", type=Path, required=True)
    value.add_argument("--private", type=Path, required=True)
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--preflight-only", action="store_true")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return preflight(args) if args.preflight_only else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
