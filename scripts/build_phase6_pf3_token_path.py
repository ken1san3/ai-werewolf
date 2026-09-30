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
    if (mode not in {"synthetic", "actual"} or not source.is_file() or output.exists()
            or manifest_out.exists() or sdk_manifest_out.exists()):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "build arguments")
    output.parent.mkdir(parents=True, exist_ok=True)
    env, tools = _msvc_environment(vswhere)
    with tempfile.TemporaryDirectory(prefix="pf3-build-", dir=output.parent) as temporary:
        build_root = Path(temporary)
        include_args: list[str] = []
        extra_sources: list[str] = []
        symbols: dict[str, str] | None = None
        closure_record: dict[str, object] | None = None
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
            if _tool_hash(llama_common) != core.APPROVED_HASHES["llama-common.dll"]:
                raise core.ProofError("UNKNOWN_ABI_IDENTITY", "llama-common hash")
            exports = subprocess.run([tools["dumpbin"], "/nologo", "/exports", str(llama_common)],
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
            closure_record = {"manifest_sha256": _tool_hash(closure_manifest),
                              "archive_sha256": core.SOURCE_ARCHIVE_SHA256,
                              "archive_size": core.SOURCE_ARCHIVE_SIZE, "member_count": len(closure)}
        arguments = [tools["compiler"], "/nologo", "/std:c++20", "/EHsc", "/MD", "/Brepro",
                     "/utf-8", "/W4", "/WX", "/DUNICODE", "/D_UNICODE"]
        if mode == "synthetic":
            arguments.append("/DPF3_SYNTHETIC")
        arguments += include_args + [str(source.resolve())] + extra_sources + [f"/Fe:{output.resolve()}", "/link", "/Brepro"]
        completed = subprocess.run(arguments, cwd=build_root, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False, timeout=180)
        if completed.returncode != 0 or not output.is_file():
            diagnostic = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", "replace")[-2000:]
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"compile failed: {diagnostic}")
        abi = _build_abi_manifest(build_root, env, tools, include_args) if mode == "actual" else {key: 0 for key in core.ABI_KEYS}
    sdk_manifest = _toolchain_manifest(env, tools)
    with sdk_manifest_out.open("xb") as stream:
        stream.write(core.canonical_bytes(sdk_manifest) + b"\n")
    record: dict[str, object] = {
        "schema_version": "aiwolf.pf3-build-manifest.v1", "mode": mode,
        "source_sha256": _tool_hash(source), "output_sha256": _tool_hash(output),
        "compiler_sha256": _tool_hash(Path(tools["compiler"])),
        "linker_sha256": _tool_hash(Path(tools["linker"])),
        "dumpbin_sha256": _tool_hash(Path(tools["dumpbin"])),
        "msvc_version": tools["msvc_version"], "sdk_version": tools["sdk_version"],
        "argv": [Path(item).name if index == 0 else item for index, item in enumerate(arguments)],
        "closure": closure_record, "symbols": symbols, "abi": abi,
        "sdk_manifest_sha256": _tool_hash(sdk_manifest_out),
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
