from __future__ import annotations

import json
import re
from contextlib import contextmanager
from types import SimpleNamespace
import os
import threading
import subprocess
from pathlib import Path

import pytest
from scripts import phase6_pf3_counterexample as tool

SCHEMA = tool.ROOT / "Docs/ai/design/PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json"


def test_static_allowlist_contains_complete_sha256_digests():
    # T528 stopped before native execution because one allowlist digest lost a nibble.
    assert len(tool.HASHES) == 11
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in tool.HASHES.values())


@pytest.fixture
def schemas():
    return tool.validate_bindings(tool.strict_parse(SCHEMA.read_bytes()))


def test_fixed_population_and_logical_validation(schemas):
    ids = tool.candidate_ids()
    assert len(ids) == len(set(ids)) == 24
    for cid in ids:
        raw = tool.build_candidate(cid)
        tool.validate_candidate(cid, raw, schemas)
        stage, payload, serialization = cid.split(".")
        value = tool.strict_parse(raw)
        assert len(value["message" if stage == "MESSAGE" else "comment"]) == 200
        assert raw == tool.build_candidate(cid)
        if serialization == "MAX_SPACE_RULE":
            assert raw.endswith(tool.SPACE + b"}")
            assert b"\n\n" in raw


@pytest.mark.parametrize("mutation", ["bit", "order", "id", "199", "201"])
def test_fixture_mutations_rejected(schemas, mutation):
    cid = "CO_DECLARE.CONTROL_NUL.COMPACT"
    raw = tool.build_candidate(cid)
    if mutation == "bit":
        raw = raw.replace(b"\\u0000", b"\\u0001", 1)
    else:
        value = tool.strict_parse(raw)
        if mutation == "order":
            value = dict(reversed(list(value.items())))
        elif mutation == "id":
            value["co_option_id"] = "o101"
        else:
            value["comment"] = "\0" * int(mutation)
        raw = tool.compact(value)
    with pytest.raises(ValueError):
        tool.validate_candidate(cid, raw, schemas)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{}x', b'"\xff"'])
def test_strict_rejection(raw):
    with pytest.raises((ValueError, UnicodeError)):
        tool.strict_parse(raw)


@pytest.mark.parametrize("raw", [b'{"response_format":{"json_schema":{"schema":{},"schema":{}}}}',
    b'{"response_format":{"json_schema":{"schema":[]}}}', b'{"schema":{}}'])
def test_wire_rejects_duplicates_types_and_wrong_path(raw):
    with pytest.raises(ValueError):
        tool.schema_span(raw)


def test_binding_rejects_property_order(monkeypatch):
    companion = tool.strict_parse(SCHEMA.read_bytes())
    companion["schemas"]["message"] = dict(reversed(list(companion["schemas"]["message"].items())))
    with pytest.raises(ValueError):
        tool.validate_bindings(companion)


class Child:
    def __init__(self, mode, counts, calls):
        self.mode, self.counts, self.calls = mode, counts, calls
        self.returncode = None
        self.pid = 123
        self.killed = False
    def communicate(self, input=None, timeout=None):
        self.calls.append(("communicate", input, timeout))
        if self.mode == "timeout" and not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        if self.mode == "cleanup":
            raise subprocess.TimeoutExpired("fake", timeout)
        self.returncode = 1 if self.mode == "nonzero" else 0
        raw = {"invalid": b"secret stderr", "shape": b"[2147483648]\n"}.get(self.mode,
              b"[" + b", ".join([b"42"] * self.counts) + b"]\n")
        return raw, b"SECRET-STDERR"
    def kill(self):
        self.calls.append(("kill",))
        self.killed = True
        if self.mode == "cleanup":
            raise OSError("kill failed")
        self.returncode = -9
    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        return self.returncode
    def poll(self):
        return self.returncode


class FakeStore:
    def __init__(self, directory, fail_write=None):
        self.directory, self.fail_write = directory, fail_write
    def write(self, name, raw):
        if name == self.fail_write:
            raise OSError("write failure")
        with (self.directory / name).open("xb") as stream:
            stream.write(raw)
    def read(self, name):
        return (self.directory / name).read_bytes()
    def names(self):
        return sorted(path.name for path in self.directory.iterdir())


def exercise(tmp_path, *, mode="ok", count=513, drift=None, fail_write=None, owner_fail=False,
             deadline=False, source_drift=False, private_claim=None, pin_fail=False):
    private = tmp_path / "private"
    private.mkdir()
    paths = {key: str(tmp_path / key) for key in tool.HASHES}
    paths["schema"] = str(SCHEMA)
    calls, observations, children = [], [], []
    def identity():
        observations.append(1)
        if len(observations) == drift:
            raise ValueError("drift")
        return dict(tool.HASHES)
    @contextmanager
    def artifacts(_):
        if pin_fail:
            raise OSError("pin denied")
        yield SimpleNamespace(paths=paths, identity=identity, read=lambda key: SCHEMA.read_bytes(), file_identities={})
    @contextmanager
    def claim(directory, run_id, source_hash):
        store = FakeStore(directory, fail_write)
        store.write("claim.json", tool.compact({"run_id": run_id, "runner_sha256": source_hash}))
        yield store
    def spawn(argv, **kwargs):
        calls.append(("spawn", argv, kwargs))
        child = Child(mode if not children else "ok", count, calls)
        children.append(child)
        return child
    def owner(_, executable):
        if owner_fail:
            raise RuntimeError("owner")
        return {"pid": 123, "handle": 7, "creation_time": 777, "image_path": executable}
    ticks = iter([0] + [901] * 100) if deadline else None
    source = tool.source_digest()
    report = tool.run(paths, private, "T527-20260930-120000", approved_runner_sha256=source,
        popen=spawn, owner=owner, artifact_pin=artifacts, private_claim=private_claim or claim,
        source_reader=lambda: "0" * 64 if source_drift and children else source,
        clock=(lambda: next(ticks)) if deadline else lambda: 0)
    return report, calls, observations, private, children


@pytest.mark.parametrize("count,status", [(513, "UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND"), (512, "UNKNOWN_NO_FIXED_CANDIDATE")])
def test_truth_table_command_stdin_privacy(tmp_path, count, status):
    report, calls, observations, private, children = exercise(tmp_path, count=count)
    assert report["status"] == status
    assert report["pf3"] == "UNKNOWN"
    assert len(children) == 24
    assert len(observations) == 50
    sends = [c for c in calls if c[0] == "communicate"]
    assert [c[1] for c in sends] == [tool.build_candidate(cid) for cid in tool.candidate_ids()]
    assert all(c[2] == 30 for c in sends)
    for call in [c for c in calls if c[0] == "spawn"]:
        assert call[1][3:] == ["--stdin", "--ids", "--no-bos", "--no-escape", "--no-parse-special", "--offline", "-ngl", "0", "--device", "none"]
    public = tool.compact(report)
    for secret in [b"SECRET", b"raw_sha256", b"raw_hex", b"token_ids", b"stdout", b"stderr", b"locator", str(tmp_path).encode()]:
        assert secret not in public
    detail = tool.strict_parse((private / "detail.json").read_bytes())
    assert detail["actual_grammar_sha256"] is None
    assert detail["minimum_generation_token_count"] is None
    assert detail["roundtrip_status"] == "NOT_AVAILABLE"
    assert detail["token_lower_bound_status"] == detail["pf3_evidence_status"] == "UNPROVEN"
    assert all(r["grammar_membership_status"] == r["runtime_loaded_modules_status"] == "UNPROVEN" for r in report["rows"])
    manifest = tool.strict_parse((private / "manifest.json").read_bytes())
    assert report["private_manifest_sha256"] == tool.digest((private / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        assert entry["sha256"] == tool.digest((private / entry["name"]).read_bytes())
    assert report["provider_count"] == report["inference_count"] == report["server_count"] == 0


@pytest.mark.parametrize("mode,reason", [("timeout", "TOKENIZE_TIMEOUT_REAPED"), ("nonzero", "TOKENIZE_NONZERO_REAPED"),
    ("invalid", "TOKENIZE_STDOUT_INVALID"), ("shape", "TOKEN_COUNT_SHAPE_INVALID")])
def test_local_failure_continues_without_retry(tmp_path, mode, reason):
    report, calls, observations, _, children = exercise(tmp_path, mode=mode)
    assert report["status"] == "UNKNOWN_DIAGNOSTIC_INCOMPLETE"
    assert report["rows"][0]["reason"] == reason
    assert len(children) == 24 and len(observations) == 50
    assert report["rows"][1]["canonical_encode_token_count"] == 513
    if mode == "timeout":
        assert ("kill",) in calls and ("wait", 5) in calls


@pytest.mark.parametrize("kwargs,expected,children_count", [
    ({"drift": 1}, "UNKNOWN_RUNTIME_INVALID", 0),
    ({"drift": 3}, "UNKNOWN_RUNTIME_INVALID", 1),
    ({"owner_fail": True}, "UNKNOWN_RUNTIME_INVALID", 1),
    ({"mode": "cleanup"}, "UNKNOWN_RUNTIME_INVALID", 1),
    ({"deadline": True}, "UNKNOWN_RUNTIME_INVALID", 0),
    ({"fail_write": "row-00.json"}, "UNKNOWN_EVIDENCE_INVALID", 1),
    ({"fail_write": "manifest.json"}, "UNKNOWN_EVIDENCE_INVALID", 24),
    ({"fail_write": "seal.json"}, "UNKNOWN_EVIDENCE_INVALID", 24),
])
def test_closed_systemic_failure_matrix(tmp_path, kwargs, expected, children_count):
    report, _, observations, _, children = exercise(tmp_path, **kwargs)
    assert report["status"] == expected and report["pf3"] == "UNKNOWN"
    assert len(children) == children_count
    assert len(observations) >= 2
    if "rows" in report:
        assert all(r["status"] == "NOT_RUN" for r in report["rows"][children_count:])


def test_static_identity_rejects_missing_paths_and_shape(tmp_path):
    with pytest.raises(ValueError), tool.pin_artifacts({}):
        pass
    with pytest.raises(OSError), tool.pin_artifacts({key: tmp_path / key for key in tool.HASHES}):
        pass


def test_no_network_or_native_in_offline_tests(monkeypatch, schemas):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("socket forbidden"))
    for cid in tool.candidate_ids():
        tool.validate_candidate(cid, tool.build_candidate(cid), schemas)

@pytest.mark.parametrize("stdout", [b"", b"-1", b"1,2", b"[1,2]", b"1.0", b"1 extra", b"True"])
def test_closed_token_stdout(stdout):
    with pytest.raises(ValueError):
        tool.token_ids(stdout)


def test_wire_order_drift_rejected(monkeypatch):
    async def changed(schema):
        return tool.compact({"response_format": {"json_schema": {"schema": dict(reversed(list(schema.items())))}}})
    monkeypatch.setattr(tool, "offline_wire", changed)
    with pytest.raises(ValueError):
        tool.validate_bindings(tool.strict_parse(SCHEMA.read_bytes()))


def test_schema_wire_no_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network/native forbidden")
    monkeypatch.setattr(httpx := __import__("httpx"), "post", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert len(tool.validate_bindings(tool.strict_parse(SCHEMA.read_bytes()))) == 2


def test_manifest_binds_all_24_private_rows(tmp_path):
    _, _, _, private, _ = exercise(tmp_path)
    manifest = tool.strict_parse((private / "manifest.json").read_bytes())
    assert len(manifest["files"]) == 26
    detail = tool.strict_parse((private / "detail.json").read_bytes())
    assert len(detail["candidates"]) == 24
    assert detail["states"] == ["NEW", "INPUT_BOUND", "CANDIDATES_BUILT", "LOGICAL_VALIDATED",
                               "DIAGNOSTIC_RUNNING", "DIAGNOSTIC_COMPLETE", "UNKNOWN_SEALED"]

def test_converter_exact_space_positions():
    s = tool.SPACE
    raw = tool.build_candidate("MESSAGE.QUOTE.MAX_SPACE_RULE")
    assert raw == b"{" + s + b'"message"' + s + b":" + s + b'"' + b'\\"' * 200 + b'"' + s + b"}"
    co = tool.build_candidate("CO_DECLARE.QUOTE.MAX_SPACE_RULE")
    assert co.count(s) == 19
    assert b'"DECLARE",' + s in co
    assert b'"fact_ids"' + s + b":" + s + b"[" + s + b'"f000",' + s + b'"f001"' + s + b"]" + s + b"}" in co
    assert not co.endswith(s)


@pytest.mark.parametrize("raw", [b"[0, 42, 123]\n", b"[0, 42, 123]\r\n"])
def test_exact_native_stdout_capture(raw):
    assert tool.token_ids(raw) == [0, 42, 123]


@pytest.mark.parametrize("raw", [b"[]\n", b"[01]\n", b"[1,2]\n", b"[1]\nextra", b"1 2\n", b"[1]", b"[-1]\n"])
def test_native_stdout_noncanonical_rejected(raw):
    with pytest.raises(ValueError):
        tool.token_ids(raw)

def test_private_container_mismatch_never_spawns_or_writes(tmp_path):
    (tmp_path / "existing.txt").write_text("preserve", encoding="utf-8")
    calls = []
    report = tool.run({}, tmp_path, "T527-20260930-120000", approved_runner_sha256=tool.source_digest(),
        popen=lambda *a, **kw: calls.append("spawn"))
    assert report["status"] == "UNKNOWN_INPUT_INVALID"
    assert calls == []
    assert (tmp_path / "existing.txt").read_text() == "preserve"


def test_final_identity_drift_invalidates_observed_counts(tmp_path):
    report, _, observations, _, children = exercise(tmp_path, drift=50)
    assert len(children) == 24 and len(observations) == 50
    assert report["status"] == "UNKNOWN_RUNTIME_INVALID"
    assert all(row["canonical_encode_token_count"] is None for row in report["rows"])

@pytest.fixture
def test_acl(monkeypatch):
    # ACL validation itself is covered by the existing helper. These tests isolate
    # actual Windows handle sharing/rename behavior using synthetic temp files.
    import scripts.phase6_private_review as helper
    monkeypatch.setattr(helper, "_windows_private_path", lambda *a, **kw: True)


def test_real_private_handles_and_atomic_seal_with_fake_child(tmp_path, test_acl):
    report, _, _, private, children = exercise(tmp_path, private_claim=tool.claim_private)
    assert report["status"] == "UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND"
    assert len(children) == 24
    claim = tool.strict_parse((private / "claim.json").read_bytes())
    assert claim["runner_sha256"] == tool.source_digest()
    assert claim["container_identity"]["inode"] == private.stat().st_ino
    assert report["private_manifest_sha256"] == tool.digest((private / "manifest.json").read_bytes())


def test_second_run_claim_competition_has_zero_children(tmp_path, test_acl):
    private = tmp_path / "private"
    private.mkdir()
    entered, release = threading.Event(), threading.Event()
    errors = []
    def first():
        try:
            with tool.claim_private(private, "T527-20260930-120000", tool.source_digest()):
                entered.set()
                assert release.wait(5)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        children = []
        report = tool.run({}, private, "T527-20260930-120001", approved_runner_sha256=tool.source_digest(),
                          popen=lambda *a, **kw: children.append(1))
        assert report["status"] == "UNKNOWN_INPUT_INVALID"
        assert children == []
    finally:
        release.set()
        thread.join(5)
    assert not errors and not thread.is_alive()
    assert tool.strict_parse((private / "claim.json").read_bytes())["run_id"] == "T527-20260930-120000"


def test_private_ancestor_and_write_swap_are_denied(tmp_path, test_acl):
    parent = tmp_path / "parent"
    parent.mkdir()
    directory = parent / "private"
    directory.mkdir()
    with tool.claim_private(directory, "T527-20260930-120000", tool.source_digest()) as store:
        for original, replacement in [(directory, parent / "replaced"), (parent, tmp_path / "replaced")]:
            with pytest.raises(OSError):
                original.rename(replacement)
        store.write("row-00.json", b'{"secret":"synthetic"}')
        with pytest.raises(OSError):
            (directory / "row-00.json").write_bytes(b"swap")
        assert store.read("row-00.json") == b'{"secret":"synthetic"}'
    assert not (tmp_path / "replaced").exists()


def test_private_atomic_handle_rename_failure(tmp_path, test_acl, monkeypatch):
    def rejected(*args):
        raise OSError("rename denied")
    monkeypatch.setattr(tool, "rename_open_file", rejected)
    report, _, _, private, children = exercise(tmp_path, private_claim=tool.claim_private)
    assert len(children) == 1
    assert report["status"] == "UNKNOWN_EVIDENCE_INVALID"
    assert not (private / "row-00.json").exists()
    assert not (private / "seal.json").exists()


def test_public_static_pin_blocks_all_swap_windows(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    artifact = parent / "artifact.bin"
    artifact.write_bytes(b"fixed synthetic artifact")
    with tool.pinned_static_file(artifact) as pin:
        expected = tool.descriptor_hash(pin["fd"])
        for phase in ("before spawn", "during child", "before post hash"):
            with pytest.raises(OSError):
                artifact.write_bytes(phase.encode())
            with pytest.raises(OSError):
                artifact.rename(parent / "swapped.bin")
            with pytest.raises(OSError):
                parent.rename(tmp_path / "swapped-parent")
            assert tool.descriptor_hash(pin["fd"]) == expected
        assert pin["file_identity"] == tool.file_identity(pin["fd"])
        assert pin["path"] == artifact
    artifact.write_bytes(b"released")
    assert artifact.read_bytes() == b"released"


def test_static_pin_open_writer_is_rejected(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"fixed")
    with path.open("r+b"):
        with pytest.raises(OSError), tool.pinned_static_file(path):
            pass


def test_pin_failure_stops_before_any_child(tmp_path):
    report, _, _, private, children = exercise(tmp_path, pin_fail=True)
    assert report["status"] == "UNKNOWN_RUNTIME_INVALID" and children == []
    assert sorted(path.name for path in private.iterdir()) == ["claim.json"]


def test_approved_runner_hash_required_before_claim(tmp_path):
    report = tool.run({}, tmp_path, "T527-20260930-120000", approved_runner_sha256="0" * 64)
    assert report["status"] == "UNKNOWN_INPUT_INVALID"
    assert list(tmp_path.iterdir()) == []


def test_runner_mid_run_drift_stops_and_invalidates(tmp_path):
    report, _, _, private, children = exercise(tmp_path, source_drift=True)
    assert len(children) == 1
    assert report["status"] == "UNKNOWN_RUNTIME_INVALID"
    assert all(row["canonical_encode_token_count"] is None for row in report["rows"])
    detail = tool.strict_parse((private / "detail.json").read_bytes())
    assert detail["runner_start_sha256"] != detail["runner_end_sha256"]


@pytest.mark.parametrize("where", ["report", "row"])
def test_closed_public_serializer_rejects_unknown_fields(tmp_path, where):
    report, *_ = exercise(tmp_path)
    target = report if where == "report" else report["rows"][0]
    target["unexpected_secret"] = "never serialize"
    with pytest.raises(ValueError):
        tool.public_bytes(report)


@pytest.mark.parametrize("mutation", ["argv", "stdin", "pre", "post", "stdout", "source", "cleanup"])
def test_durable_row_provenance_rejects_mismatch(tmp_path, mutation):
    _, _, _, private, _ = exercise(tmp_path)
    detail = tool.strict_parse((private / "detail.json").read_bytes())
    item = detail["candidates"][0]
    if mutation == "argv":
        item["argv"][-1] = "nond"
    elif mutation == "stdin":
        item["stdin_sha256"] = "0" * 64
    elif mutation in {"pre", "post"}:
        item[mutation + "_identity"] = detail["candidates"][1][mutation + "_identity"]
    elif mutation == "stdout":
        item["stdout_byte_count"] += 1
    elif mutation == "source":
        item["runner_sha256"] = "0" * 64
    else:
        item["cleanup_result"] = "UNPROVEN"
    with pytest.raises(ValueError):
        tool.validate_private_row(item, detail["identities"], detail["paths"], detail["approved_runner_sha256"])


def test_durable_identity_and_public_manifest_binding(tmp_path):
    report, _, _, private, _ = exercise(tmp_path)
    detail = tool.strict_parse((private / "detail.json").read_bytes())
    assert detail["approved_runner_sha256"] == detail["runner_start_sha256"] == detail["runner_end_sha256"] == report["runner_sha256"]
    assert report["tokenizer_sha256"] == tool.HASHES["llama-tokenize.exe"]
    assert report["private_manifest_sha256"] == tool.digest((private / "manifest.json").read_bytes())
    assert detail["public_report_core"] == {key: value for key, value in report.items() if key != "private_manifest_sha256"}
    for index, item in enumerate(detail["candidates"]):
        tool.validate_private_row(item, detail["identities"], detail["paths"], detail["approved_runner_sha256"])
        assert item["pre_identity"]["index"] == 1 + index * 2
        assert item["post_identity"]["index"] == 2 + index * 2

class NativeFunction:
    def __init__(self, function):
        self.function = function
    def __call__(self, *args):
        return self.function(*args)


def test_static_pin_rejects_ancestor_reparse_before_leaf(tmp_path, monkeypatch):
    opened, closed = [], []
    def create(path, *args):
        opened.append(path)
        return len(opened)
    def information(handle, kind, attributes, size):
        attributes[0] = 0x10 | (0x400 if handle == 2 else 0)
        return 1
    def final_path(handle, target, capacity, flags):
        target.value = "\\\\?\\" + opened[handle - 1]
        return len(target.value)
    kernel = SimpleNamespace(
        CreateFileW=NativeFunction(create), CloseHandle=NativeFunction(lambda handle: closed.append(handle)),
        GetFileInformationByHandleEx=NativeFunction(information), GetFinalPathNameByHandleW=NativeFunction(final_path))
    monkeypatch.setattr(tool.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    with pytest.raises(ValueError, match="reparse"), tool.pinned_static_file(tmp_path / "artifact.bin"):
        pass
    assert len(opened) == 2 and closed == [2, 1]


@pytest.mark.parametrize("mismatch", [False, True])
def test_process_image_is_bound_to_pinned_executable(tmp_path, monkeypatch, mismatch):
    from ctypes import wintypes
    executable = str(tmp_path / "llama-tokenize.exe")
    def times(handle, creation, exit_time, kernel_time, user_time):
        creation._obj.dwLowDateTime = 777
        creation._obj.dwHighDateTime = 1
        return 1
    def image(handle, flags, target, size):
        target.value = str(tmp_path / "different.exe") if mismatch else executable
        size._obj.value = len(target.value)
        return 1
    kernel = SimpleNamespace(GetProcessTimes=NativeFunction(times), QueryFullProcessImageNameW=NativeFunction(image))
    monkeypatch.setattr(tool.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
    process = SimpleNamespace(_handle=7, pid=123)
    if mismatch:
        with pytest.raises(RuntimeError, match="image mismatch"):
            tool.creation_identity(process, executable)
    else:
        identity = tool.creation_identity(process, executable)
        assert identity == {"pid": 123, "handle": 7, "image_path": executable, "creation_time": (1 << 32) | 777}

def test_all_static_allowlist_files_remain_pinned_until_context_exit(tmp_path):
    paths = {key: tmp_path / key for key in tool.HASHES}
    for key, path in paths.items():
        path.write_bytes(key.encode("ascii"))
    with tool.pin_artifacts(paths) as artifacts:
        assert set(artifacts.paths) == set(tool.HASHES)
        expected = {key: tool.digest(key.encode("ascii")) for key in paths}
        assert artifacts.identity() == expected
        assert artifacts.identity() == expected  # hash seeks back to the same pinned handle
        for key, path in paths.items():
            with pytest.raises(OSError):
                path.write_bytes(b"changed")
            assert artifacts.read(key) == key.encode("ascii")
    for path in paths.values():
        path.write_bytes(b"released")
