"""Finite build helper for the T534 native proof child.

The helper never downloads input.  Synthetic mode needs only the checked-in child and
the already installed MSVC toolchain.  Actual mode additionally requires a pinned
official archive and a closed closure manifest supplied by the later Tester gate.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from typing import Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import phase6_pf3_token_path as core
from scripts import phase6_pf3_counterexample as t527


ARCHIVE_LIMIT = 512 * 1024 * 1024
MEMBER_LIMIT = 4096
MEMBER_SIZE_LIMIT = 16 * 1024 * 1024
TOTAL_EXTRACT_LIMIT = 128 * 1024 * 1024
MEMBER_ABI_DUMP_SYMBOL = "?dump@common_json@@QEBA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@H@Z"
FORBIDDEN_PMF_FLAGS = {"/vmg", "/vmb", "/vmm", "/vms", "/vmv"}
MEMBER_COMPILE_KEYS = {
    "schema_version", "target_arch", "compiler_sha256", "linker_sha256", "msvc_version",
    "_MSC_VER", "_MSC_FULL_VER", "_MSVC_LANG", "dynamic_crt", "iterator_debug_level",
    "header_relative_path", "header_sha256", "source_archive_sha256", "class_declaration",
    "no_base_clause", "dll_sha256", "decorated_symbol", "export_kind", "pmf_mode", "pmf_size",
    "pmf_alignment", "farproc_size", "pointer_size", "pmf_trivially_copyable", "binding_method",
}
MEMBER_CERTIFICATE_EXTRA_KEYS = {
    "sdk_manifest_sha256", "mock_dll_source_sha256", "mock_dll_sha256", "mock_dll_argv_sha256",
    "mock_caller_source_sha256", "mock_caller_exe_sha256", "mock_caller_argv_sha256",
    "mock_result_sha256", "actual_smoke_source_sha256", "actual_smoke_exe_sha256",
    "actual_smoke_argv_sha256",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name or re.match(r"^[A-Za-z]:", name):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "unsafe archive member")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "unsafe archive member")
    return path


def validate_closure_manifest(value: object) -> tuple[dict[str, tuple[int, str]], tuple[str, ...],
                                                       tuple[str, ...], Mapping[str, object]]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "members", "include_dirs",
                                                     "compile_sources", "saved_partial"}:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "closure manifest shape")
    if value["schema_version"] != "aiwolf.pf3-build-closure.v1":
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "closure manifest version")
    members = value["members"]
    if not isinstance(members, list) or not 1 <= len(members) <= MEMBER_LIMIT:
        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "closure members")
    result: dict[str, tuple[int, str]] = {}
    total = 0
    for item in members:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "closure member shape")
        path = _safe_member(item["path"] if isinstance(item["path"], str) else "")
        key = path.as_posix()
        if key in result:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "duplicate closure member")
        size = core.checked_size(item["size"], MEMBER_SIZE_LIMIT, "member size")
        sha = item["sha256"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member hash")
        total += size
        if total > TOTAL_EXTRACT_LIMIT:
            raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "closure total")
        result[key] = (size, sha)
    include_dirs = value["include_dirs"]
    if not isinstance(include_dirs, list) or not include_dirs:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "include dirs")
    normalized_dirs = tuple(_safe_member(item).as_posix() for item in include_dirs if isinstance(item, str))
    if len(normalized_dirs) != len(include_dirs) or len(set(normalized_dirs)) != len(normalized_dirs):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "include dirs")
    saved = value["saved_partial"]
    if not isinstance(saved, dict):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "saved partial")
    sources = value["compile_sources"]
    if not isinstance(sources, list):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "compile sources")
    normalized_sources = tuple(_safe_member(item).as_posix() for item in sources if isinstance(item, str))
    if len(normalized_sources) != len(sources) or len(set(normalized_sources)) != len(normalized_sources):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "compile sources")
    if any(item not in result for item in normalized_sources):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "compile source closure")
    return result, normalized_dirs, normalized_sources, saved


def _archive_entries(archive: Path, needed: set[str]) -> dict[str, tuple[bytes, bool]]:
    if archive.stat().st_size > ARCHIVE_LIMIT:
        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive size")
    entries: dict[str, tuple[bytes, bool]] = {}
    count = 0
    uncompressed_total = 0
    seen: set[str] = set()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                count += 1
                if count > MEMBER_LIMIT * 8:
                    raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive member count")
                name = _safe_member(item.filename).as_posix()
                if name in seen:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "duplicate archive member")
                seen.add(name); uncompressed_total += item.file_size
                if uncompressed_total > ARCHIVE_LIMIT:
                    raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive expanded size")
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive link")
                if item.is_dir():
                    if name in needed: entries[name] = (b"", True)
                else:
                    if item.file_size > MEMBER_SIZE_LIMIT:
                        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive member size")
                    if name in needed: entries[name] = (source.read(item), False)
    else:
        try:
            source = tarfile.open(archive, mode="r:*")
        except tarfile.TarError as exc:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive format") from exc
        with source:
            for item in source.getmembers():
                count += 1
                if count > MEMBER_LIMIT * 8:
                    raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive member count")
                name = _safe_member(item.name).as_posix()
                if name in seen:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "duplicate archive member")
                seen.add(name); uncompressed_total += max(0, item.size)
                if uncompressed_total > ARCHIVE_LIMIT:
                    raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive expanded size")
                if item.issym() or item.islnk() or item.isdev():
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive link")
                if item.isdir():
                    if name in needed: entries[name] = (b"", True)
                elif item.isfile():
                    if item.size > MEMBER_SIZE_LIMIT:
                        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "archive member size")
                    stream = source.extractfile(item)
                    if stream is None:
                        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive member read")
                    if name in needed: entries[name] = (stream.read(), False)
                else:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive member type")
    return entries


def extract_closed_archive(archive: Path, closure: Mapping[str, tuple[int, str]], destination: Path) -> None:
    entries = _archive_entries(archive, set(closure))
    for name, (size, sha) in closure.items():
        item = entries.get(name)
        if item is None or item[1] or len(item[0]) != size or _sha(item[0]) != sha:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"closure mismatch {name}")
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "extract collision")
        with target.open("xb") as stream:
            stream.write(item[0])
    for name, (size, sha) in closure.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        raw = target.read_bytes()
        if len(raw) != size or _sha(raw) != sha or target.is_symlink():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "post-extract mismatch")


def _read_json(path: Path) -> object:
    return core.strict_json(path.read_bytes())


def _tool_hash(path: Path) -> str:
    with t527.pinned_static_file(path) as pin:
        return t527.descriptor_hash(pin["fd"])


def _verify_pinned_identity(pin: Mapping[str, object], expected_sha256: str,
                            expected_identity: object) -> None:
    if (t527.descriptor_hash(pin["fd"]) != expected_sha256
            or t527.file_identity(pin["fd"]) != expected_identity):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "pinned artifact drift")


def validate_member_abi_compile_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != MEMBER_COMPILE_KEYS:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member compile record shape")
    sha_keys = {"compiler_sha256", "linker_sha256", "header_sha256", "source_archive_sha256", "dll_sha256"}
    if any(not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None for key in sha_keys):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member compile record hash")
    if (value["schema_version"] != "aiwolf.pf3-dump-member-abi-compile-record.v1"
            or value["target_arch"] != "x86_64-pc-windows-msvc" or value["dynamic_crt"] is not True
            or value["iterator_debug_level"] != 0 or value["class_declaration"] != "common_json"
            or value["no_base_clause"] is not True or value["decorated_symbol"] != MEMBER_ABI_DUMP_SYMBOL
            or value["export_kind"] != "DIRECT_EXECUTABLE"
            or value["pmf_mode"] != "MSVC_DEFAULT_BEST_CASE_NO_BASE"
            or value["pmf_size"] != 8 or value["farproc_size"] != 8 or value["pointer_size"] != 8
            or value["pmf_trivially_copyable"] is not True
            or value["binding_method"] != "MEMCPY_FARPROC_BYTES_TO_PMF_V1"):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member compile record literal")
    for key in ("_MSC_VER", "_MSC_FULL_VER", "_MSVC_LANG", "pmf_alignment"):
        if isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member compile record integer")
    return value


def validate_member_abi_certificate(value: object, compile_record: Mapping[str, object]) -> dict[str, object]:
    keys = (MEMBER_COMPILE_KEYS - {"schema_version"}) | MEMBER_CERTIFICATE_EXTRA_KEYS | {"schema_version"}
    if not isinstance(value, dict) or set(value) != keys:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member certificate shape")
    validate_member_abi_compile_record(compile_record)
    if (value["schema_version"] != "aiwolf.pf3-dump-member-abi-build-certificate.v1"
            or any(value[key] != compile_record[key] for key in MEMBER_COMPILE_KEYS - {"schema_version"})):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member certificate compile projection")
    for key in MEMBER_CERTIFICATE_EXTRA_KEYS:
        if not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member certificate hash")
    return value


def _argv_sha(arguments: Sequence[str]) -> str:
    return _sha(core.canonical_bytes(list(arguments)))


def _validate_pmf_argv(arguments: Sequence[str]) -> None:
    lowered = {item.lower() for item in arguments}
    if lowered.intersection(FORBIDDEN_PMF_FLAGS):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "PMF representation flag")


def _run_build(arguments: Sequence[str], *, cwd: Path, env: Mapping[str, str], detail: str) -> None:
    _validate_pmf_argv(arguments)
    completed = subprocess.run(arguments, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False, timeout=180)
    if completed.returncode != 0:
        diagnostic = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", "replace")[-2000:]
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"{detail}: {diagnostic}")


def _export_symbols(dumpbin: str, binary: Path, *, cwd: Path,
                    env: Mapping[str, str]) -> tuple[str, ...]:
    completed = subprocess.run([dumpbin, "/nologo", "/exports", str(binary)], cwd=cwd, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    if completed.returncode != 0 or len(completed.stdout) > 4 * 1024 * 1024:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "export inspection")
    return tuple(match.decode("ascii", "strict") for match in re.findall(rb"\s(\?[^\s]+)", completed.stdout))


def _direct_executable_export(raw: bytes, dumpbin_output: bytes, symbol: str) -> None:
    escaped = re.escape(symbol.encode("ascii"))
    rows = re.findall(rb"(?m)^\s*\d+\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8,16})\s+" + escaped + rb"\s*$",
                      dumpbin_output)
    if len(rows) != 1:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "export row")
    rva = int(rows[0], 16)
    if len(raw) < 0x100 or raw[:2] != b"MZ":
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "PE header")
    nt = struct.unpack_from("<I", raw, 0x3C)[0]
    if nt + 24 > len(raw) or raw[nt:nt + 4] != b"PE\0\0":
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "PE signature")
    section_count = struct.unpack_from("<H", raw, nt + 6)[0]
    optional_size = struct.unpack_from("<H", raw, nt + 20)[0]
    optional = nt + 24
    if optional + optional_size > len(raw) or struct.unpack_from("<H", raw, optional)[0] != 0x20B:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "PE optional header")
    export_rva, export_size = struct.unpack_from("<II", raw, optional + 112)
    if export_rva <= rva < export_rva + export_size:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "forwarded export")
    section = optional + optional_size
    executable = False
    for index in range(section_count):
        offset = section + index * 40
        if offset + 40 > len(raw):
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "PE section table")
        virtual_size, virtual_address, raw_size = struct.unpack_from("<III", raw, offset + 8)
        characteristics = struct.unpack_from("<I", raw, offset + 36)[0]
        if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
            executable = bool(characteristics & 0x20000000)
            break
    if not executable:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "non-executable export")


def build_member_abi_mock(output_dir: Path, *, vswhere: Path | None = None) -> dict[str, object]:
    """Build and run the approved model-free member ABI mock exactly once."""
    dll_source = core.ROOT / "scripts/native/phase6_pf3_member_abi_mock_dll.cpp"
    caller_source = core.ROOT / "scripts/native/phase6_pf3_member_abi_mock_caller.cpp"
    header = core.ROOT / "scripts/native/phase6_pf3_member_abi.h"
    dll = output_dir / "phase6_pf3_member_abi_mock.dll"
    caller = output_dir / "phase6_pf3_member_abi_mock.exe"
    result_path = output_dir / "phase6_pf3_member_abi_mock_result.json"
    manifest_path = output_dir / "phase6_pf3_member_abi_mock_manifest.json"
    if (not dll_source.is_file() or not caller_source.is_file() or not header.is_file()
            or any(item.exists() for item in (dll, caller, result_path, manifest_path))):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock build arguments")
    output_dir.mkdir(parents=True, exist_ok=True)
    env, tools = _msvc_environment(vswhere)
    common = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro",
              "/utf-8", "/W4", "/WX", "/DUNICODE", "/D_UNICODE",
              f"/I{(core.ROOT / 'scripts/native').resolve()}"]
    dll_argv = [*common, "/LD", str(dll_source.resolve()), f"/Fe:{dll.resolve()}", "/link", "/Brepro"]
    _run_build(dll_argv, cwd=output_dir, env=env, detail="mock DLL compile")
    if not dll.is_file():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock DLL missing")
    symbols = _export_symbols(tools["dumpbin"], dll, cwd=output_dir, env=env)
    matches = {item for item in symbols if item.startswith("?dump@Pf3MockJson@@")}
    if len(matches) != 1:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock dump symbol")
    symbol = next(iter(matches))
    caller_argv = [*common, str(caller_source.resolve()), f"/Fe:{caller.resolve()}", "/link", "/Brepro"]
    _run_build(caller_argv, cwd=output_dir, env=env, detail="mock caller compile")
    if not caller.is_file():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock caller missing")
    dll_sha = _tool_hash(dll); caller_sha = _tool_hash(caller)
    observed = subprocess.run([str(caller), str(dll), symbol], cwd=output_dir, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    if observed.returncode != 0 or observed.stderr or len(observed.stdout) > 64 * 1024:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock caller execution")
    parsed = core.strict_json(observed.stdout)
    if not isinstance(parsed, dict):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock result shape")
    # Binary self-hashes cannot be compile-time literals without a hash cycle.  The
    # trusted builder replaces the two explicit sentinels after pinning both files.
    if (parsed.get("mock_dll_sha256") != "PARENT_VALIDATES"
            or parsed.get("mock_caller_exe_sha256") != "PARENT_VALIDATES"):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "mock hash sentinel")
    parsed["mock_dll_sha256"] = dll_sha
    parsed["mock_caller_exe_sha256"] = caller_sha
    core.validate_member_abi_mock_result(parsed, dll_sha256=dll_sha, caller_sha256=caller_sha)
    result_raw = core.canonical_bytes(parsed) + b"\n"
    with result_path.open("xb") as stream:
        stream.write(result_raw)
    record = {
        "schema_version": "aiwolf.pf3-member-abi-mock-build.v1",
        "compiler_sha256": _tool_hash(Path(tools["compiler"])),
        "linker_sha256": _tool_hash(Path(tools["linker"])),
        "dumpbin_sha256": _tool_hash(Path(tools["dumpbin"])),
        "header_sha256": _tool_hash(header),
        "mock_dll_source_sha256": _tool_hash(dll_source), "mock_dll_sha256": dll_sha,
        "mock_dll_argv_sha256": _argv_sha(dll_argv),
        "mock_caller_source_sha256": _tool_hash(caller_source),
        "mock_caller_exe_sha256": caller_sha, "mock_caller_argv_sha256": _argv_sha(caller_argv),
        "mock_symbol": symbol, "mock_result_sha256": _sha(result_raw),
    }
    with manifest_path.open("xb") as stream:
        stream.write(core.canonical_bytes(record) + b"\n")
    return record


def _msvc_environment(vswhere: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    located = shutil.which("cl.exe")
    if located:
        linker = shutil.which("link.exe")
        if not linker:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "linker")
        env = dict(os.environ)
        dumpbin = Path(located).resolve().parent / "dumpbin.exe"
        if not dumpbin.is_file():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "dumpbin")
        return env, {"compiler": str(Path(located).resolve()), "linker": str(Path(linker).resolve()),
                     "dumpbin": str(dumpbin.resolve()), "sdk_version": "environment",
                     "msvc_version": "environment", "vc_root": "", "sdk_root": ""}
    if vswhere is None:
        default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        vswhere = default
    if not vswhere.is_file():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "vswhere")
    query = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    root = Path(query.stdout.decode("utf-8", "strict").strip())
    tools_root = root / "VC" / "Tools" / "MSVC"
    versions = sorted((item for item in tools_root.iterdir() if item.is_dir()), reverse=True) if tools_root.is_dir() else []
    if query.returncode != 0 or not versions:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "MSVC installation")
    vc = versions[0]
    compiler = vc / "bin" / "Hostx64" / "x64" / "cl.exe"
    linker = vc / "bin" / "Hostx64" / "x64" / "link.exe"
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10"
    sdk_versions = sorted((item.name for item in (kits / "Include").iterdir()
                           if item.is_dir() and (kits / "Lib" / item.name).is_dir()), reverse=True) if (kits / "Include").is_dir() else []
    if not compiler.is_file() or not linker.is_file() or not sdk_versions:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "MSVC/SDK tools")
    sdk = sdk_versions[0]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join((str(compiler.parent), str(kits / "bin" / sdk / "x64"), env.get("PATH", "")))
    env["INCLUDE"] = os.pathsep.join((str(vc / "include"), str(kits / "Include" / sdk / "ucrt"),
                                      str(kits / "Include" / sdk / "shared"), str(kits / "Include" / sdk / "um"),
                                      str(kits / "Include" / sdk / "winrt"), str(kits / "Include" / sdk / "cppwinrt")))
    env["LIB"] = os.pathsep.join((str(vc / "lib" / "x64"), str(kits / "Lib" / sdk / "ucrt" / "x64"),
                                  str(kits / "Lib" / sdk / "um" / "x64")))
    dumpbin = compiler.parent / "dumpbin.exe"
    if not dumpbin.is_file():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "dumpbin")
    return env, {"compiler": str(compiler.resolve()), "linker": str(linker.resolve()),
                 "dumpbin": str(dumpbin.resolve()),
                 "sdk_version": sdk, "msvc_version": vc.name,
                 "vc_root": str(vc.resolve()), "sdk_root": str(kits.resolve())}


def _abi_probe_source() -> str:
    fields = [
        ("llama_model_params_size", "sizeof(llama_model_params)"),
        ("llama_model_params_vocab_only_offset", "offsetof(llama_model_params, vocab_only)"),
        ("llama_model_params_n_gpu_layers_offset", "offsetof(llama_model_params, n_gpu_layers)"),
        ("llama_model_params_devices_offset", "offsetof(llama_model_params, devices)"),
        ("llama_token_data_size", "sizeof(llama_token_data)"),
        ("llama_token_data_id_offset", "offsetof(llama_token_data, id)"),
        ("llama_token_data_logit_offset", "offsetof(llama_token_data, logit)"),
        ("llama_token_data_p_offset", "offsetof(llama_token_data, p)"),
        ("llama_token_data_array_size", "sizeof(llama_token_data_array)"),
        ("llama_token_data_array_data_offset", "offsetof(llama_token_data_array, data)"),
        ("llama_token_data_array_count_offset", "offsetof(llama_token_data_array, size)"),
        ("llama_token_data_array_selected_offset", "offsetof(llama_token_data_array, selected)"),
        ("llama_token_data_array_sorted_offset", "offsetof(llama_token_data_array, sorted)"),
    ]
    body = []
    for index, (key, expression) in enumerate(fields):
        separator = "std::cout << ','; " if index else ""
        body.append(f'    {separator}std::cout << "\\\"{key}\\\":" << {expression};')
    return ("#include <cstddef>\n#include <iostream>\n#include \"llama.h\"\n"
            "int main() { std::cout << '{';\n" + "\n".join(body) +
            "\n    std::cout << \"}\\n\"; return 0; }\n")


def _build_abi_manifest(build_root: Path, env: Mapping[str, str], tools: Mapping[str, str],
                        include_args: Sequence[str]) -> dict[str, int]:
    source = build_root / "pf3-abi-probe.cpp"
    executable = build_root / "pf3-abi-probe.exe"
    source.write_text(_abi_probe_source(), encoding="utf-8", newline="\n")
    command = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro",
               "/utf-8", "/W4", "/WX", *include_args, str(source), f"/Fe:{executable}",
               "/link", "/Brepro"]
    completed = subprocess.run(command, cwd=build_root, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False, timeout=180)
    if completed.returncode != 0 or not executable.is_file():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "ABI probe compile")
    observed = subprocess.run([str(executable)], cwd=build_root, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, timeout=30)
    if observed.returncode != 0:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "ABI probe run")
    try:
        value = core.strict_json(observed.stdout)
    except Exception as exc:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "ABI probe output") from exc
    if (not isinstance(value, dict) or set(value) != core.ABI_KEYS
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value.values())):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "ABI probe shape")
    return value


def _member_abi_probe_source() -> str:
    return r'''#include <iostream>
#include <string>
#include <type_traits>
#include <windows.h>
#include "common/json.h"
int main() {
    using member = std::string (common_json::*)(int) const;
    std::cout << "{\"_ITERATOR_DEBUG_LEVEL\":" << _ITERATOR_DEBUG_LEVEL
              << ",\"_MSC_FULL_VER\":" << _MSC_FULL_VER
              << ",\"_MSC_VER\":" << _MSC_VER
              << ",\"_MSVC_LANG\":" << _MSVC_LANG
              << ",\"farproc_size\":" << sizeof(FARPROC)
              << ",\"pmf_alignment\":" << alignof(member)
              << ",\"pmf_size\":" << sizeof(member)
              << ",\"pmf_trivially_copyable\":" << (std::is_trivially_copyable_v<member> ? "true" : "false")
              << ",\"pointer_size\":" << sizeof(void *) << "}\n";
    return 0;
}
'''


def _member_abi_probe(build_root: Path, env: Mapping[str, str], tools: Mapping[str, str],
                      include_args: Sequence[str]) -> tuple[dict[str, object], list[str]]:
    source = build_root / "pf3-member-abi-probe.cpp"
    executable = build_root / "pf3-member-abi-probe.exe"
    source.write_text(_member_abi_probe_source(), encoding="utf-8", newline="\n")
    argv = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro", "/utf-8",
            "/W4", "/WX", *include_args, str(source), f"/Fe:{executable}", "/link", "/Brepro"]
    _run_build(argv, cwd=build_root, env=env, detail="member ABI probe compile")
    observed = subprocess.run([str(executable)], cwd=build_root, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, timeout=30)
    value = core.strict_json(observed.stdout) if observed.returncode == 0 and not observed.stderr else None
    keys = {"_ITERATOR_DEBUG_LEVEL", "_MSC_FULL_VER", "_MSC_VER", "_MSVC_LANG", "farproc_size",
            "pmf_alignment", "pmf_size", "pmf_trivially_copyable", "pointer_size"}
    if (not isinstance(value, dict) or set(value) != keys or value["_ITERATOR_DEBUG_LEVEL"] != 0
            or value["pmf_size"] != 8 or value["farproc_size"] != 8 or value["pointer_size"] != 8
            or value["pmf_trivially_copyable"] is not True):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member ABI probe")
    return value, argv


def _toolchain_manifest(env: Mapping[str, str], tools: Mapping[str, str]) -> dict[str, object]:
    roots = [Path(item) for name in ("INCLUDE", "LIB") for item in env.get(name, "").split(os.pathsep) if item]
    files: dict[str, str] = {}
    selected = (
        "cstddef", "cstdint", "vector", "string", "iostream", "limits", "stdexcept", "algorithm",
        "array", "cmath", "cstdio", "cstdlib", "cstring", "string_view", "windows.h", "minwindef.h",
        "winnt.h", "sdkddkver.h", "stdint.h", "stdlib.h", "kernel32.lib", "ucrt.lib", "msvcrt.lib",
        "msvcprt.lib", "vcruntime.lib", "oldnames.lib",
    )
    for name in selected:
        matches = [root / name for root in roots if (root / name).is_file()]
        if matches:
            path = matches[0].resolve()
            files[name] = _tool_hash(path)
    required = {"windows.h", "kernel32.lib", "ucrt.lib", "msvcrt.lib", "msvcprt.lib", "vcruntime.lib"}
    if not required.issubset(files):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "SDK manifest closure")
    return {
        "schema_version": "aiwolf.pf3-toolchain-manifest.v1",
        "msvc_version": tools["msvc_version"], "sdk_version": tools["sdk_version"],
        "compiler_sha256": _tool_hash(Path(tools["compiler"])),
        "linker_sha256": _tool_hash(Path(tools["linker"])),
        "dumpbin_sha256": _tool_hash(Path(tools["dumpbin"])),
        "selected_headers_and_libraries": files,
    }


def build(mode: str, source: Path, output: Path, manifest_out: Path, *, archive: Path | None = None,
          closure_manifest: Path | None = None, llama_common: Path | None = None,
          vswhere: Path | None = None, sdk_manifest_out: Path | None = None) -> dict[str, object]:
    sdk_manifest_out = sdk_manifest_out or manifest_out.with_name(manifest_out.stem + "-sdk.json")
    compile_record_out = manifest_out.with_name("member-abi-compile-record.json")
    certificate_out = manifest_out.with_name("member-abi-build-certificate.json")
    generated_header_out = manifest_out.with_name("phase6_pf3_member_abi_generated.h")
    smoke_out = output.with_name("phase6_pf3_member_abi_smoke.exe")
    if (mode not in {"synthetic", "actual"} or not source.is_file() or output.exists()
            or manifest_out.exists() or sdk_manifest_out.exists()):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "build arguments")
    if mode == "actual" and any(item.exists() for item in
                                (compile_record_out, certificate_out, generated_header_out, smoke_out)):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "member ABI build collision")
    output.parent.mkdir(parents=True, exist_ok=True)
    env, tools = _msvc_environment(vswhere)
    with tempfile.TemporaryDirectory(prefix="pf3-build-", dir=output.parent) as temporary, ExitStack() as artifact_pins:
        build_root = Path(temporary)
        include_args: list[str] = []
        extra_sources: list[str] = []
        symbols: dict[str, str] | None = None
        closure_record: dict[str, object] | None = None
        member_record: dict[str, object] | None = None
        member_record_sha: str | None = None
        member_probe: dict[str, object] | None = None
        mock_record: dict[str, object] | None = None
        smoke_arguments: list[str] | None = None
        if mode == "actual":
            if archive is None or closure_manifest is None or llama_common is None:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "actual closure")
            if archive.stat().st_size != core.SOURCE_ARCHIVE_SIZE or _tool_hash(archive) != core.SOURCE_ARCHIVE_SHA256:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "archive hash")
            closure_value = _read_json(closure_manifest)
            closure, include_dirs, compile_sources, saved_partial = validate_closure_manifest(closure_value)
            extract_closed_archive(archive, closure, build_root)
            for relative, item in saved_partial.items():
                if not isinstance(item, dict) or set(item) != {"repo_path", "sha256"}:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "saved partial shape")
                target = build_root.joinpath(*_safe_member(relative).parts)
                repo_path = core.ROOT / item["repo_path"]
                if not target.is_file() or not repo_path.is_file() or _sha(target.read_bytes()) != item["sha256"] or _sha(repo_path.read_bytes()) != item["sha256"]:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", "saved partial mismatch")
            include_args = [f"/I{build_root.joinpath(*PurePosixPath(item).parts)}" for item in include_dirs]
            extra_sources = [str(build_root.joinpath(*PurePosixPath(item).parts)) for item in compile_sources]
            common_pin = artifact_pins.enter_context(t527.pinned_static_file(llama_common))
            common_sha = t527.descriptor_hash(common_pin["fd"])
            common_identity = t527.file_identity(common_pin["fd"])
            if common_sha != core.APPROVED_HASHES["llama-common.dll"]:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "llama-common hash")
            exports = subprocess.run([tools["dumpbin"], "/nologo", "/exports", str(common_pin["path"])],
                                     cwd=build_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     check=False, timeout=30)
            if exports.returncode != 0:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "emitter exports")
            all_symbols = tuple(match.decode("ascii", "strict") for match in
                                re.findall(rb"\s(\?[^\s]+)", exports.stdout))
            prefixes = {"emitter": "?json_schema_to_grammar@@", "json_parse": "?parse@common_json@@",
                        "json_dump": "?dump@common_json@@", "json_destroy": "??1common_json@@"}
            symbols = {}
            for key, prefix in prefixes.items():
                matches = {item for item in all_symbols if item.startswith(prefix)}
                if len(matches) != 1:
                    raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"export symbol {key}")
                symbols[key] = next(iter(matches))
            if symbols["json_dump"] != MEMBER_ABI_DUMP_SYMBOL:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "dump member symbol")
            _direct_executable_export(t527.descriptor_bytes(common_pin["fd"]), exports.stdout,
                                      symbols["json_dump"])
            _verify_pinned_identity(common_pin, common_sha, common_identity)
            header_relative = next((name for name in closure if name.endswith("/common/json.h")), None)
            if header_relative is None:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "common/json.h closure")
            header_path = build_root.joinpath(*PurePosixPath(header_relative).parts)
            header_raw = header_path.read_bytes()
            if b"class common_json {" not in header_raw or re.search(rb"class\s+common_json\s*:", header_raw):
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "common_json base clause")
            member_probe, _probe_argv = _member_abi_probe(build_root, env, tools, include_args)
            mock_record = build_member_abi_mock(manifest_out.parent / "member-abi-mock", vswhere=vswhere)
            member_record = {
                "schema_version": "aiwolf.pf3-dump-member-abi-compile-record.v1",
                "target_arch": "x86_64-pc-windows-msvc",
                "compiler_sha256": _tool_hash(Path(tools["compiler"])),
                "linker_sha256": _tool_hash(Path(tools["linker"])),
                "msvc_version": tools["msvc_version"],
                "_MSC_VER": member_probe["_MSC_VER"], "_MSC_FULL_VER": member_probe["_MSC_FULL_VER"],
                "_MSVC_LANG": member_probe["_MSVC_LANG"], "dynamic_crt": True,
                "iterator_debug_level": member_probe["_ITERATOR_DEBUG_LEVEL"],
                "header_relative_path": header_relative, "header_sha256": _sha(header_raw),
                "source_archive_sha256": core.SOURCE_ARCHIVE_SHA256,
                "class_declaration": "common_json", "no_base_clause": True,
                "dll_sha256": common_sha, "decorated_symbol": symbols["json_dump"],
                "export_kind": "DIRECT_EXECUTABLE", "pmf_mode": "MSVC_DEFAULT_BEST_CASE_NO_BASE",
                "pmf_size": member_probe["pmf_size"], "pmf_alignment": member_probe["pmf_alignment"],
                "farproc_size": member_probe["farproc_size"], "pointer_size": member_probe["pointer_size"],
                "pmf_trivially_copyable": member_probe["pmf_trivially_copyable"],
                "binding_method": "MEMCPY_FARPROC_BYTES_TO_PMF_V1",
            }
            validate_member_abi_compile_record(member_record)
            member_record_raw = core.canonical_bytes(member_record) + b"\n"
            member_record_sha = _sha(member_record_raw)
            with compile_record_out.open("xb") as stream:
                stream.write(member_record_raw)
            generated = ("#pragma once\n#define PF3_MEMBER_ABI_COMPILE_RECORD_SHA \"" + member_record_sha
                         + "\"\n#define PF3_MEMBER_ABI_EXPECTED_SYMBOL \"" + symbols["json_dump"] + "\"\n"
                         + f"#define PF3_MEMBER_ABI_EXPECTED_MSC_VER {member_probe['_MSC_VER']}\n"
                         + f"#define PF3_MEMBER_ABI_EXPECTED_MSC_FULL_VER {member_probe['_MSC_FULL_VER']}\n"
                         + f"#define PF3_MEMBER_ABI_EXPECTED_MSVC_LANG {member_probe['_MSVC_LANG']}\n"
                         + f"#define PF3_MEMBER_ABI_EXPECTED_ITERATOR_DEBUG_LEVEL {member_probe['_ITERATOR_DEBUG_LEVEL']}\n"
                         + f"#define PF3_MEMBER_ABI_EXPECTED_PMF_ALIGNMENT {member_probe['pmf_alignment']}\n")
            with generated_header_out.open("x", encoding="ascii", newline="\n") as stream:
                stream.write(generated)
            closure_record = {"manifest_sha256": _tool_hash(closure_manifest),
                              "archive_sha256": core.SOURCE_ARCHIVE_SHA256,
                              "archive_size": core.SOURCE_ARCHIVE_SIZE, "member_count": len(closure)}
        arguments = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro",
                     "/utf-8", "/W4", "/WX", "/DUNICODE", "/D_UNICODE"]
        if mode == "synthetic":
            arguments.append("/DPF3_SYNTHETIC")
        else:
            arguments += [f"/FI{generated_header_out.resolve()}",
                          f"/I{(core.ROOT / 'scripts/native').resolve()}"]
        arguments += include_args + [str(source.resolve())] + extra_sources + [f"/Fe:{output.resolve()}", "/link", "/Brepro"]
        _run_build(arguments, cwd=build_root, env=env, detail="proof child compile")
        if not output.is_file():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "proof child missing")
        if mode == "actual":
            smoke_arguments = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro",
                               "/utf-8", "/W4", "/WX", "/DUNICODE", "/D_UNICODE",
                               "/DPF3_ABI_SMOKE_ONLY", f"/FI{generated_header_out.resolve()}",
                               f"/I{(core.ROOT / 'scripts/native').resolve()}", *include_args,
                               str(source.resolve()), f"/Fe:{smoke_out.resolve()}", "/link", "/Brepro"]
            _run_build(smoke_arguments, cwd=build_root, env=env, detail="actual ABI smoke compile")
            if not smoke_out.is_file():
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "actual ABI smoke missing")
        abi = _build_abi_manifest(build_root, env, tools, include_args) if mode == "actual" else {key: 0 for key in core.ABI_KEYS}
    sdk_manifest = _toolchain_manifest(env, tools)
    with sdk_manifest_out.open("xb") as stream:
        stream.write(core.canonical_bytes(sdk_manifest) + b"\n")
    certificate: dict[str, object] | None = None
    if mode == "actual":
        assert member_record is not None and member_record_sha is not None and member_probe is not None
        assert mock_record is not None and smoke_arguments is not None and symbols is not None
        certificate = {
            "schema_version": "aiwolf.pf3-dump-member-abi-build-certificate.v1",
            **{key: member_record[key] for key in member_record if key != "schema_version"},
            "sdk_manifest_sha256": _tool_hash(sdk_manifest_out),
            **{key: mock_record[key] for key in (
                "mock_dll_source_sha256", "mock_dll_sha256", "mock_dll_argv_sha256",
                "mock_caller_source_sha256", "mock_caller_exe_sha256", "mock_caller_argv_sha256",
                "mock_result_sha256")},
            "actual_smoke_source_sha256": _tool_hash(source),
            "actual_smoke_exe_sha256": _tool_hash(smoke_out),
            "actual_smoke_argv_sha256": _argv_sha(smoke_arguments),
        }
        validate_member_abi_certificate(certificate, member_record)
        with certificate_out.open("xb") as stream:
            stream.write(core.canonical_bytes(certificate) + b"\n")
    record: dict[str, object] = {
        "schema_version": "aiwolf.pf3-build-manifest.v2" if mode == "actual" else "aiwolf.pf3-build-manifest.v1", "mode": mode,
        "source_sha256": _tool_hash(source), "output_sha256": _tool_hash(output),
        "compiler_sha256": _tool_hash(Path(tools["compiler"])),
        "linker_sha256": _tool_hash(Path(tools["linker"])),
        "dumpbin_sha256": _tool_hash(Path(tools["dumpbin"])),
        "msvc_version": tools["msvc_version"], "sdk_version": tools["sdk_version"],
        "argv": [Path(item).name if index == 0 else item for index, item in enumerate(arguments)],
        "closure": closure_record, "symbols": symbols, "abi": abi,
        "sdk_manifest_sha256": _tool_hash(sdk_manifest_out),
    }
    if mode == "actual":
        record["member_abi"] = {
            "compile_record": member_record,
            "compile_record_sha256": member_record_sha,
            "generated_header_sha256": _tool_hash(generated_header_out),
            "build_certificate_sha256": _tool_hash(certificate_out),
            "actual_smoke_exe_sha256": _tool_hash(smoke_out),
            "qualification_sha256": None,
            "gate": "ACTUAL_SMOKE_NOT_RUN",
        }
        record["implementation_dependencies"] = {
            "member_abi_header": _tool_hash(core.ROOT / "scripts/native/phase6_pf3_member_abi.h"),
            "proof_child_source": _tool_hash(source),
            "build_helper_source": _tool_hash(Path(__file__)),
            "mock_dll_source": mock_record["mock_dll_source_sha256"],
            "mock_caller_source": mock_record["mock_caller_source_sha256"],
        }
    with manifest_out.open("xb") as stream:
        stream.write(core.canonical_bytes(record) + b"\n")
    return record


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build the T534 PF3 proof child")
    result.add_argument("--mode", choices=("synthetic", "actual"), required=True)
    result.add_argument("--source", type=Path, default=core.ROOT / "scripts/native/phase6_pf3_token_path.cpp")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--manifest-out", type=Path, required=True)
    result.add_argument("--archive", type=Path)
    result.add_argument("--closure-manifest", type=Path)
    result.add_argument("--llama-common", type=Path)
    result.add_argument("--vswhere", type=Path)
    result.add_argument("--sdk-manifest-out", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    build(args.mode, args.source.resolve(), args.output.resolve(), args.manifest_out.resolve(),
          archive=args.archive.resolve() if args.archive else None,
          closure_manifest=args.closure_manifest.resolve() if args.closure_manifest else None,
          llama_common=args.llama_common.resolve() if args.llama_common else None,
          vswhere=args.vswhere.resolve() if args.vswhere else None,
          sdk_manifest_out=args.sdk_manifest_out.resolve() if args.sdk_manifest_out else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
