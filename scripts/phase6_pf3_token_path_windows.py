"""Windows owner/process boundary for the T534 proof runner.

This module is imported only by the real CLI.  Focused tests inject a synthetic backend
and therefore cannot start or load native code accidentally.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import json
import msvcrt
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Mapping

from scripts import phase6_pf3_token_path as core
from scripts import phase6_pf3_counterexample as t527


DEBUG_ONLY_THIS_PROCESS = 0x00000002
CREATE_NO_WINDOW = 0x08000000
EXCEPTION_DEBUG_EVENT = 1
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
UNLOAD_DLL_DEBUG_EVENT = 7
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
ERROR_SEM_TIMEOUT = 121
LIST_MODULES_ALL = 0x03
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]


class _JOB_BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _JOB_EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _JOB_BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _EXCEPTION_RECORD(ctypes.Structure):
    _fields_ = [("ExceptionCode", wintypes.DWORD), ("ExceptionFlags", wintypes.DWORD),
                ("ExceptionRecord", ctypes.c_void_p), ("ExceptionAddress", ctypes.c_void_p),
                ("NumberParameters", wintypes.DWORD), ("ExceptionInformation", ctypes.c_size_t * 15)]


class _EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("ExceptionRecord", _EXCEPTION_RECORD), ("dwFirstChance", wintypes.DWORD)]


class _CREATE_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hThread", wintypes.HANDLE), ("lpThreadLocalBase", ctypes.c_void_p),
                ("lpStartAddress", ctypes.c_void_p)]


class _CREATE_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hFile", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE), ("lpBaseOfImage", ctypes.c_void_p),
                ("dwDebugInfoFileOffset", wintypes.DWORD), ("nDebugInfoSize", wintypes.DWORD),
                ("lpThreadLocalBase", ctypes.c_void_p), ("lpStartAddress", ctypes.c_void_p),
                ("lpImageName", ctypes.c_void_p), ("fUnicode", wintypes.WORD)]


class _EXIT_THREAD_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class _EXIT_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class _LOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("hFile", wintypes.HANDLE), ("lpBaseOfDll", ctypes.c_void_p),
                ("dwDebugInfoFileOffset", wintypes.DWORD), ("nDebugInfoSize", wintypes.DWORD),
                ("lpImageName", ctypes.c_void_p), ("fUnicode", wintypes.WORD)]


class _UNLOAD_DLL_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", ctypes.c_void_p)]


class _OUTPUT_DEBUG_STRING_INFO(ctypes.Structure):
    _fields_ = [("lpDebugStringData", ctypes.c_void_p), ("fUnicode", wintypes.WORD),
                ("nDebugStringLength", wintypes.WORD)]


class _RIP_INFO(ctypes.Structure):
    _fields_ = [("dwError", wintypes.DWORD), ("dwType", wintypes.DWORD)]


class _DEBUG_UNION(ctypes.Union):
    _fields_ = [("Exception", _EXCEPTION_DEBUG_INFO), ("CreateThread", _CREATE_THREAD_DEBUG_INFO),
                ("CreateProcessInfo", _CREATE_PROCESS_DEBUG_INFO), ("ExitThread", _EXIT_THREAD_DEBUG_INFO),
                ("ExitProcess", _EXIT_PROCESS_DEBUG_INFO), ("LoadDll", _LOAD_DLL_DEBUG_INFO),
                ("UnloadDll", _UNLOAD_DLL_DEBUG_INFO), ("DebugString", _OUTPUT_DEBUG_STRING_INFO),
                ("RipInfo", _RIP_INFO)]


class _DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wintypes.DWORD), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD), ("u", _DEBUG_UNION)]


def _strict_config_bytes(raw: bytes) -> dict[str, object]:
    value = core.strict_json(raw)
    required = {"schema_version", "run_id", "nonce", "design_sha256", "paths", "hashes",
                "approved_non_system", "module_artifacts", "sampler_record", "symbols", "expected_abi"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != "aiwolf.pf3-token-path-run.v1":
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "config shape")
    if value["design_sha256"] != core.DESIGN_SHA256:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "design binding")
    if not isinstance(value["run_id"], str) or not re_full_id(value["run_id"]):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "run id")
    if not isinstance(value["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["nonce"]):
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "nonce")
    if not isinstance(value["symbols"], dict) or set(value["symbols"]) != {"emitter", "json_parse", "json_dump", "json_destroy"}:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "symbols")
    if any(not isinstance(item, str) or not re.fullmatch(r"\?[A-Za-z0-9_@$?]{8,512}", item)
           for item in value["symbols"].values()):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "symbol spelling")
    if (not isinstance(value["expected_abi"], dict) or set(value["expected_abi"]) != core.ABI_KEYS
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                   for item in value["expected_abi"].values())):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "expected ABI")
    if not isinstance(value["sampler_record"], dict):
        raise core.ProofError("UNKNOWN_EFFECTIVE_SAMPLER", "sampler record")
    try:
        core.validate_sampler_record(core.EffectiveSamplerRecord(**value["sampler_record"]))
    except (TypeError, ValueError) as exc:
        raise core.ProofError("UNKNOWN_EFFECTIVE_SAMPLER", "sampler record") from exc
    if not isinstance(value["module_artifacts"], dict) or len(value["module_artifacts"]) > core.MODULE_LIMIT:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "module artifacts")
    for name, item in value["module_artifacts"].items():
        if (not isinstance(name, str) or name != name.casefold() or Path(name).name != name
                or not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str) or not Path(item["path"]).is_absolute()
                or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "module artifact entry")
    approved = value["approved_non_system"]
    if (not isinstance(approved, dict) or len(approved) > core.MODULE_LIMIT
            or any(not isinstance(name, str) or name != name.casefold() or Path(name).name != name
                   or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                   for name, sha in approved.items())):
        raise core.ProofError("UNKNOWN_MODULE_SET", "module allowlist")
    return value


def _strict_config(path: Path) -> dict[str, object]:
    return _strict_config_bytes(path.read_bytes())


def re_full_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "whole-run deadline")


def _descriptor_hash_deadline(fd: int, deadline: float) -> str:
    import hashlib
    os.lseek(fd, 0, os.SEEK_SET)
    size = os.fstat(fd).st_size
    count = 0; hasher = hashlib.sha256()
    while count < size:
        _check_deadline(deadline)
        chunk = os.read(fd, min(1024 * 1024, size - count))
        if not chunk:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "artifact short read")
        hasher.update(chunk); count += len(chunk)
    _check_deadline(deadline)
    if count != size:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "artifact size drift")
    return hasher.hexdigest()


def _descriptor_bytes_deadline(fd: int, deadline: float, maximum: int = 16 * 1024 * 1024) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    size = os.fstat(fd).st_size
    if size > maximum:
        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "artifact bytes")
    chunks = []
    count = 0
    while count < size:
        _check_deadline(deadline)
        chunk = os.read(fd, min(1024 * 1024, size - count))
        if not chunk:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "artifact short read")
        chunks.append(chunk); count += len(chunk)
    _check_deadline(deadline)
    return b"".join(chunks)


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
        "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
        "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS")]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]


def _windows_handle_identity(handle: int) -> tuple[int, int, int]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    info = _BY_HANDLE_FILE_INFORMATION()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "input handle identity")
    return (info.dwVolumeSerialNumber, (info.nFileIndexHigh << 32) | info.nFileIndexLow,
            (info.nFileSizeHigh << 32) | info.nFileSizeLow)


def _file_version(path: Path, deadline: float) -> str:
    _check_deadline(deadline)
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                      ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not size or size > 4 * 1024 * 1024:
        raise core.ProofError("UNKNOWN_MODULE_SET", "system module version size")
    raw = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, raw):
        raise core.ProofError("UNKNOWN_MODULE_SET", "system module version read")
    pointer = ctypes.c_void_p(); length = wintypes.UINT()
    if (not version.VerQueryValueW(raw, "\\", ctypes.byref(pointer), ctypes.byref(length))
            or length.value < ctypes.sizeof(_VS_FIXEDFILEINFO)):
        raise core.ProofError("UNKNOWN_MODULE_SET", "system module version query")
    info = ctypes.cast(pointer, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
    _check_deadline(deadline)
    return ".".join(str(value) for value in (
        info.dwFileVersionMS >> 16, info.dwFileVersionMS & 0xffff,
        info.dwFileVersionLS >> 16, info.dwFileVersionLS & 0xffff))


def _identity_from_fd(fd: int, path: Path, deadline: float) -> core.ModuleIdentity:
    info = os.fstat(fd)
    sha = _descriptor_hash_deadline(fd, deadline)
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    try:
        classification = "SYSTEM" if os.path.commonpath((str(path), str(system))).casefold() == str(system).casefold() else "NON_SYSTEM"
    except ValueError:
        classification = "NON_SYSTEM"
    version = _file_version(path, deadline) if classification == "SYSTEM" else None
    return core.ModuleIdentity(str(path), info.st_dev, info.st_ino, info.st_size, sha, classification, version)


class WindowsDebugBackend:
    def __init__(self, child: Path, input_fd: int, approved_paths: Mapping[str, str], *,
                 model_path: Path, native_dir: Path, symbols: Mapping[str, str],
                 static_verify, run_deadline: float):
        if os.name != "nt":
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "Windows required")
        self.child = child
        self.input_fd = input_fd
        self.approved_paths = approved_paths
        self.model_path = model_path
        self.native_dir = native_dir
        self.symbols = dict(symbols)
        self.static_verify = static_verify
        self.run_deadline = run_deadline
        self.stack = ExitStack()
        self.lock = threading.Lock()
        self._events: list[core.ModuleEvent] = []
        self._unloads: list[int] = []
        self._lines: queue.Queue[bytes] = queue.Queue()
        self._proc_queue: queue.Queue[object] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.nonce = ""
        self._debug_error: BaseException | None = None
        self._start_cancel = threading.Event()
        self._snapshot_pins: dict[tuple[object, ...], core.ModuleIdentity] = {}
        self.process_identity: Mapping[str, object] | None = None
        self.started_count = 0
        self.started_at_monotonic: float | None = None
        self.ended_at_monotonic: float | None = None
        self.exit_code: int | None = None
        self.timed_out = False
        self.cleanup_result = "NOT_STARTED"

    def _record_event(self, kind: str, module: core.ModuleIdentity | None) -> None:
        with self.lock:
            if len(self._events) >= core.MODULE_EVENT_LIMIT:
                raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "module events")
            self._events.append(core.ModuleEvent(len(self._events), kind, module))

    def _assign_job(self, proc: subprocess.Popen[bytes]) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        limits = _JOB_EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY |
                                                   JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        limits.ProcessMemoryLimit = core.CHILD_COMMIT_LIMIT
        if (not kernel.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                               ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(job, int(proc._handle))):
            error = ctypes.get_last_error(); kernel.CloseHandle(job)
            raise OSError(error, "proof child job")
        self.stack.callback(kernel.CloseHandle, job)

    def start(self, envelope: bytes, nonce: str, deadline: float) -> None:
        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "child start deadline")
            return value
        remaining()
        if self._thread is not None:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "child already started")
        os.lseek(self.input_fd, 0, os.SEEK_SET)
        remaining()
        if os.write(self.input_fd, envelope) != len(envelope):
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "input write")
        remaining()
        os.fsync(self.input_fd)
        remaining()
        os.lseek(self.input_fd, 0, os.SEEK_SET)
        native_handle = msvcrt.get_osfhandle(self.input_fd)
        input_identity = _windows_handle_identity(native_handle)
        os.set_handle_inheritable(native_handle, True)
        self.nonce = nonce
        self._thread = threading.Thread(target=self._debug_owner,
            args=(native_handle, input_identity), name="pf3-debug-owner", daemon=False)
        self._thread.start()
        try:
            item = self._proc_queue.get(timeout=min(10.0, remaining()))
        except queue.Empty as exc:
            self._start_cancel.set()
            self._cleanup_owned(self.run_deadline)
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "spawn deadline") from exc
        finally:
            os.set_handle_inheritable(native_handle, False)
        if isinstance(item, BaseException):
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "spawn") from item
        if item is not self.proc:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "spawn ownership")
        threading.Thread(target=self._read_lines, name="pf3-control-reader", daemon=True).start()

    def _debug_owner(self, input_handle: int, input_identity: tuple[int, int, int]) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForDebugEvent.argtypes = [ctypes.POINTER(_DEBUG_EVENT), wintypes.DWORD]
        kernel.WaitForDebugEvent.restype = wintypes.BOOL
        kernel.ContinueDebugEvent.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        kernel.ContinueDebugEvent.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [input_handle]}
        try:
            if self._start_cancel.is_set():
                raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "spawn cancelled")
            proc = subprocess.Popen([str(self.child), "--input-handle", str(input_handle),
                                     "--input-volume", str(input_identity[0]),
                                     "--input-file-id", str(input_identity[1]),
                                     "--input-size", str(input_identity[2]),
                                     "--model", str(self.model_path),
                                     "--native-dir", str(self.native_dir),
                                     "--emitter-symbol", self.symbols["emitter"],
                                     "--json-parse-symbol", self.symbols["json_parse"],
                                     "--json-dump-symbol", self.symbols["json_dump"],
                                     "--json-destroy-symbol", self.symbols["json_destroy"]],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=DEBUG_ONLY_THIS_PROCESS | CREATE_NO_WINDOW,
                startupinfo=startup, close_fds=True)
            with self.lock:
                self.proc = proc
                self.started_at_monotonic = time.monotonic()
                self.started_count = 1
            self._assign_job(proc)
            self.process_identity = t527.creation_identity(proc, self.child)
            self._proc_queue.put(proc)
            while True:
                event = _DEBUG_EVENT()
                if not kernel.WaitForDebugEvent(ctypes.byref(event), 100):
                    error = ctypes.get_last_error()
                    if error == ERROR_SEM_TIMEOUT:
                        if proc.poll() is not None:
                            break
                        continue
                    raise OSError(error, "WaitForDebugEvent")
                code = event.dwDebugEventCode
                continue_status = DBG_CONTINUE
                module_handle = None
                if code == CREATE_PROCESS_DEBUG_EVENT:
                    info = event.u.CreateProcessInfo
                    module_handle = info.hFile
                    kernel.CloseHandle(info.hThread)
                    kernel.CloseHandle(info.hProcess)
                elif code == LOAD_DLL_DEBUG_EVENT:
                    module_handle = event.u.LoadDll.hFile
                elif code == UNLOAD_DLL_DEBUG_EVENT:
                    with self.lock:
                        self._unloads.append(int(event.u.UnloadDll.lpBaseOfDll or 0))
                    self._record_event("UNLOAD_DLL", None)
                elif code == EXCEPTION_DEBUG_EVENT:
                    exception_code = event.u.Exception.ExceptionRecord.ExceptionCode
                    continue_status = DBG_CONTINUE if exception_code == 0x80000003 else DBG_EXCEPTION_NOT_HANDLED
                    if exception_code != 0x80000003 and event.u.Exception.dwFirstChance == 0:
                        self._debug_error = core.ProofError("UNKNOWN_RUNTIME_INVALID", "unhandled child exception")
                elif code == 9:  # RIP_EVENT
                    self._debug_error = core.ProofError("UNKNOWN_RUNTIME_INVALID", "child RIP event")
                if module_handle:
                    try:
                        identity = self._pin_debug_handle(module_handle)
                        kind = "CREATE_PROCESS" if code == CREATE_PROCESS_DEBUG_EVENT else "LOAD_DLL"
                        self._record_event(kind, identity)
                    finally:
                        kernel.CloseHandle(module_handle)
                if not kernel.ContinueDebugEvent(event.dwProcessId, event.dwThreadId, continue_status):
                    raise OSError("ContinueDebugEvent")
                if code == EXIT_PROCESS_DEBUG_EVENT:
                    break
        except BaseException as exc:
            self._debug_error = exc
            try: self._proc_queue.put_nowait(exc)
            except queue.Full: pass

    def _pin_debug_handle(self, handle: int) -> core.ModuleIdentity:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        duplicate = wintypes.HANDLE()
        process = kernel.GetCurrentProcess()
        if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
            raise OSError("DuplicateHandle")
        resolved = ctypes.create_unicode_buffer(32768)
        length = kernel.GetFinalPathNameByHandleW(duplicate, resolved, len(resolved), 0)
        if not 0 < length < len(resolved):
            kernel.CloseHandle(duplicate)
            raise OSError("module final path")
        path = Path(resolved.value.removeprefix("\\\\?\\"))
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | os.O_RDONLY)
        self.stack.callback(os.close, fd)
        identity = _identity_from_fd(fd, path, self.run_deadline)
        # A second no-write/no-delete pin closes the event-handle-to-path race.
        pin = self.stack.enter_context(t527.pinned_static_file(path))
        check = _identity_from_fd(pin["fd"], Path(pin["path"]), self.run_deadline)
        if check.key() != identity.key():
            raise core.ProofError("UNKNOWN_MODULE_SET", "event path race")
        return identity

    def _read_lines(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, b""):
            self._lines.put(line.rstrip(b"\r\n"))

    def _line(self, timeout: float) -> bytes:
        if self._debug_error is not None:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "debug owner") from self._debug_error
        try: return self._lines.get(timeout=timeout)
        except queue.Empty as exc: raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "control timeout") from exc

    def wait_ready(self, phase: str, timeout: float) -> None:
        expected = f"READY_{phase} {self.nonce}".encode("ascii")
        if self._line(timeout) != expected:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "ready protocol")

    def continue_child(self, phase: str, nonce: str) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(f"CONTINUE_{phase} {nonce}\n".encode("ascii")); self.proc.stdin.flush()

    def snapshot(self) -> tuple[core.ModuleIdentity, ...]:
        _check_deadline(self.run_deadline)
        if self.proc is None:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "no process")
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        psapi.EnumProcessModulesEx.restype = wintypes.BOOL
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        psapi.GetModuleFileNameExW.restype = wintypes.DWORD
        modules = (wintypes.HMODULE * core.MODULE_LIMIT)(); needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(int(self.proc._handle), modules, ctypes.sizeof(modules),
                                          ctypes.byref(needed), LIST_MODULES_ALL):
            raise core.ProofError("UNKNOWN_MODULE_SET", "module snapshot")
        count = needed.value // ctypes.sizeof(wintypes.HMODULE)
        if not 0 < count <= core.MODULE_LIMIT:
            raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "module count")
        result = []
        with self.lock:
            for module in modules[:count]:
                _check_deadline(self.run_deadline)
                target = ctypes.create_unicode_buffer(32768)
                n = psapi.GetModuleFileNameExW(int(self.proc._handle), module, target, len(target))
                if not 0 < n < len(target):
                    raise core.ProofError("UNKNOWN_MODULE_SET", "module path")
                pin = self.stack.enter_context(t527.pinned_static_file(Path(target.value)))
                identity = _identity_from_fd(pin["fd"], Path(pin["path"]), self.run_deadline)
                self._snapshot_pins[identity.key()] = identity
                result.append(identity)
        return tuple(sorted(result, key=lambda item: os.path.normcase(item.final_path)))

    def result(self, timeout: float) -> core.ChildResult:
        raw = self._line(timeout)
        try: value = core.strict_json(raw)
        except Exception as exc: raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "result JSON") from exc
        if not isinstance(value, dict):
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "result object")
        return core.parse_child_result(value)

    def events(self) -> tuple[core.ModuleEvent, ...]:
        with self.lock: return tuple(self._events)

    def reap(self, timeout: float) -> None:
        if self.proc is None:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "no process")
        try: code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            self.proc.terminate()
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired as exc: raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "unreaped child") from exc
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "child timeout")
        self.exit_code = code
        self.ended_at_monotonic = time.monotonic()
        if code != 0 or self._debug_error is not None:
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "child exit")
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "debug thread")

    def _cleanup_owned(self, deadline: float) -> None:
        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())
        thread = self._thread
        while self.proc is None and thread is not None and thread.is_alive() and remaining() > 0:
            thread.join(timeout=min(0.05, remaining()))
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=min(5.0, remaining()))
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=min(5.0, remaining()))
                except subprocess.TimeoutExpired as exc:
                    raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "owned child cleanup") from exc
        if thread is not None and thread.is_alive():
            thread.join(timeout=min(5.0, remaining()))
        if ((proc is not None and proc.poll() is None)
                or (thread is not None and thread.is_alive())):
            raise core.ProofError("UNKNOWN_RUNTIME_INVALID", "owned child exit unconfirmed")
        self.exit_code = proc.poll() if proc is not None else self.exit_code
        self.ended_at_monotonic = time.monotonic()
        self.cleanup_result = "NOT_STARTED" if proc is None else "REAPED"

    def close(self) -> None:
        try:
            self._start_cancel.set()
            self._cleanup_owned(self.run_deadline)
        finally:
            self.stack.close()

    def verify_static(self, phase: str) -> None:
        self.static_verify(phase)


def _validate_execution_bundle_hash(observed: str) -> None:
    if observed != core.EXECUTION_BUNDLE_SHA256:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "execution bundle approval")


def _pin_configured(stack: ExitStack, config: Mapping[str, object], runner_source: Path,
                    deadline: float) -> tuple[dict[str, object], dict[str, str]]:
    paths = config["paths"]; hashes = config["hashes"]
    if not isinstance(paths, dict) or not isinstance(hashes, dict) or set(paths) != set(hashes):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "artifact manifest")
    required = set(core.APPROVED_HASHES) | {"proof_child", "child_source", "runner_source", "windows_source",
                                           "build_helper_source", "build_manifest", "compiler", "linker",
                                           "dumpbin", "sdk_manifest", "product_source_manifest",
                                           "execution_bundle"}
    if set(paths) != required:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "artifact keys")
    pins: dict[str, object] = {}; observed: dict[str, str] = {}
    for key in sorted(paths):
        _check_deadline(deadline)
        path = Path(paths[key])
        if not path.is_absolute():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "absolute artifact")
        pin = stack.enter_context(t527.pinned_static_file(path)); pins[key] = pin
        observed[key] = _descriptor_hash_deadline(pin["fd"], deadline)
        if observed[key] != hashes[key]:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"hash {key}")
        if key in core.APPROVED_HASHES and hashes[key] != core.APPROVED_HASHES[key]:
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"approved hash {key}")
        if key == "execution_bundle":
            _validate_execution_bundle_hash(hashes[key])
    approved_modules = config["approved_non_system"]
    if not isinstance(approved_modules, dict):
        raise core.ProofError("UNKNOWN_MODULE_SET", "module allowlist")
    for name, item in sorted(config["module_artifacts"].items()):
        _check_deadline(deadline)
        key = "module:" + name
        pin = stack.enter_context(t527.pinned_static_file(Path(item["path"])))
        pins[key] = pin; observed[key] = _descriptor_hash_deadline(pin["fd"], deadline)
        if observed[key] != item["sha256"] or approved_modules.get(name) != observed[key]:
            raise core.ProofError("UNKNOWN_MODULE_SET", "module artifact identity")
    if Path(pins["runner_source"]["path"]) != runner_source.resolve():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "runner source path")
    if Path(pins["windows_source"]["path"]) != Path(__file__).resolve():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "Windows source path")
    if Path(pins["child_source"]["path"]) != (core.ROOT / "scripts/native/phase6_pf3_token_path.cpp").resolve():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "child source path")
    if Path(pins["build_helper_source"]["path"]) != (core.ROOT / "scripts/build_phase6_pf3_token_path.py").resolve():
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "build helper path")
    if Path(pins["schema"]["path"]) != core.COMPANION_SCHEMA.resolve():
        raise core.ProofError("UNKNOWN_INPUT_INVALID", "schema path")
    if Path(pins["product_source_manifest"]["path"]) != (core.ROOT / "scripts/native/phase6_pf3_product_source_certificate.json").resolve():
        raise core.ProofError("UNKNOWN_EFFECTIVE_SAMPLER", "product certificate path")
    native_dir = Path(pins["llama.dll"]["path"]).parent
    for key in ("llama.dll", "llama-common.dll", "ggml.dll", "ggml-base.dll", "libomp.dll"):
        if Path(pins[key]["path"]).parent != native_dir or Path(pins[key]["path"]).name.casefold() != key.casefold():
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", "native layout")
        if approved_modules.get(key.casefold()) != observed[key]:
            raise core.ProofError("UNKNOWN_MODULE_SET", "native allowlist")
    proof_name = Path(pins["proof_child"]["path"]).name.casefold()
    if approved_modules.get(proof_name) != observed["proof_child"]:
        raise core.ProofError("UNKNOWN_MODULE_SET", "proof child allowlist")
    fixed_names = {"llama.dll", "llama-common.dll", "ggml.dll", "ggml-base.dll", "libomp.dll", proof_name}
    if fixed_names.intersection(config["module_artifacts"]):
        raise core.ProofError("UNKNOWN_MODULE_SET", "duplicate module artifact")
    if set(approved_modules) != fixed_names | set(config["module_artifacts"]):
        raise core.ProofError("UNKNOWN_MODULE_SET", "module allowlist closure")
    return pins, observed


def _descriptor_bytes(pin: Mapping[str, object], deadline: float) -> bytes:
    return _descriptor_bytes_deadline(pin["fd"], deadline)


def _validate_certificate_bundle(config: Mapping[str, object], pins: Mapping[str, object],
                                 identities: Mapping[str, str], deadline: float) -> dict[str, object]:
    _validate_execution_bundle_hash(identities.get("execution_bundle", ""))
    product_raw = _descriptor_bytes(pins["product_source_manifest"], deadline)
    core.validate_product_certificate(product_raw)
    sampler = core.EffectiveSamplerRecord(**config["sampler_record"])
    core.validate_sampler_record(sampler)
    manifest = core.strict_json(_descriptor_bytes(pins["build_manifest"], deadline))
    keys = {"schema_version", "mode", "source_sha256", "output_sha256", "compiler_sha256",
            "linker_sha256", "dumpbin_sha256", "msvc_version", "sdk_version", "argv",
            "closure", "symbols", "abi", "sdk_manifest_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != keys:
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "build manifest shape")
    closure = manifest["closure"]
    if (manifest["schema_version"] != "aiwolf.pf3-build-manifest.v1" or manifest["mode"] != "actual"
            or manifest["source_sha256"] != identities["child_source"]
            or manifest["output_sha256"] != identities["proof_child"]
            or manifest["compiler_sha256"] != identities["compiler"]
            or manifest["linker_sha256"] != identities["linker"]
            or manifest["dumpbin_sha256"] != identities["dumpbin"]
            or manifest["symbols"] != config["symbols"] or manifest["abi"] != config["expected_abi"]
            or manifest["sdk_manifest_sha256"] != identities["sdk_manifest"]
            or not isinstance(manifest["argv"], list) or not manifest["argv"]
            or not isinstance(closure, dict)
            or closure.get("archive_sha256") != core.SOURCE_ARCHIVE_SHA256
            or closure.get("archive_size") != core.SOURCE_ARCHIVE_SIZE
            or not isinstance(closure.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", closure["manifest_sha256"])
            or not isinstance(closure.get("member_count"), int) or closure["member_count"] < 1):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "build manifest binding")
    sdk = core.strict_json(_descriptor_bytes(pins["sdk_manifest"], deadline))
    if (not isinstance(sdk, dict)
            or set(sdk) != {"schema_version", "msvc_version", "sdk_version", "compiler_sha256",
                            "linker_sha256", "dumpbin_sha256", "selected_headers_and_libraries"}
            or sdk["schema_version"] != "aiwolf.pf3-toolchain-manifest.v1"
            or sdk["msvc_version"] != manifest["msvc_version"]
            or sdk["sdk_version"] != manifest["sdk_version"]
            or sdk["compiler_sha256"] != identities["compiler"]
            or sdk["linker_sha256"] != identities["linker"]
            or sdk["dumpbin_sha256"] != identities["dumpbin"]
            or not isinstance(sdk["selected_headers_and_libraries"], dict)
            or not sdk["selected_headers_and_libraries"]
            or any(not isinstance(name, str) or not isinstance(sha, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", sha)
                   for name, sha in sdk["selected_headers_and_libraries"].items())):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "SDK manifest binding")
    execution = core.strict_json(_descriptor_bytes(pins["execution_bundle"], deadline))
    execution_keys = {"schema_version", "design_sha256", "proof_child_sha256",
                      "child_source_sha256", "build_manifest_sha256", "sdk_manifest_sha256",
                      "compiler_sha256", "linker_sha256", "dumpbin_sha256",
                      "closure_manifest_sha256", "source_archive_sha256", "source_archive_size",
                      "product_certificate_sha256", "symbols", "abi"}
    if (not isinstance(execution, dict) or set(execution) != execution_keys
            or execution["schema_version"] != "aiwolf.pf3-reviewed-execution-bundle.v1"
            or execution["design_sha256"] != core.DESIGN_SHA256
            or execution["proof_child_sha256"] != identities["proof_child"]
            or execution["child_source_sha256"] != identities["child_source"]
            or execution["build_manifest_sha256"] != identities["build_manifest"]
            or execution["sdk_manifest_sha256"] != identities["sdk_manifest"]
            or execution["compiler_sha256"] != identities["compiler"]
            or execution["linker_sha256"] != identities["linker"]
            or execution["dumpbin_sha256"] != identities["dumpbin"]
            or execution["closure_manifest_sha256"] != closure["manifest_sha256"]
            or execution["source_archive_sha256"] != core.SOURCE_ARCHIVE_SHA256
            or execution["source_archive_size"] != core.SOURCE_ARCHIVE_SIZE
            or execution["product_certificate_sha256"] != identities["product_source_manifest"]
            or execution["symbols"] != manifest["symbols"] or execution["abi"] != manifest["abi"]):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "reviewed execution bundle binding")
    return {"execution_bundle": execution, "build_manifest": manifest, "sdk_manifest": sdk,
            "product_certificate": core.strict_json(product_raw)}


@contextmanager
def _claim_private_v2(directory: Path, config: Mapping[str, object], deadline: float):
    """Claim before any static hashing while retaining T527's private ACL/lock rules."""
    with ExitStack() as stack:
        _check_deadline(deadline)
        target = t527._plain_absolute(directory, must_exist=True)
        stack.enter_context(t527._locked_path(target, directory=True))
        _check_deadline(deadline)
        if any(target.iterdir()):
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "container already used")
        hashes = config.get("hashes")
        if not isinstance(hashes, dict):
            raise core.ProofError("UNKNOWN_INPUT_INVALID", "claim identities")
        tool_keys = ("runner_source", "windows_source", "child_source", "build_helper_source",
                     "build_manifest", "proof_child")
        expected = {key: hashes.get(key) for key in tool_keys}
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in expected.values()):
            raise core.ProofError("UNKNOWN_INPUT_INVALID", "claim identities")
        store = t527.PrivateEvidence(target, stack)
        info = target.stat()
        claim = {"schema_version": "aiwolf.pf3-private-claim.v1", "run_id": config["run_id"],
                 "nonce": config["nonce"], "expected_tool_sha256": expected,
                 "container_identity": {"device": info.st_dev, "inode": info.st_ino}}
        store.write("claim.json", core.canonical_bytes(claim), claim=True)
        _check_deadline(deadline)
        yield store


def _verify_configured_pins(pins: Mapping[str, object], expected: Mapping[str, str], deadline: float) -> None:
    if set(pins) != set(expected):
        raise core.ProofError("UNKNOWN_ABI_IDENTITY", "pin set")
    for key, pin in pins.items():
        _check_deadline(deadline)
        if (_descriptor_hash_deadline(pin["fd"], deadline) != expected[key]
                or t527.file_identity(pin["fd"]) != pin["file_identity"]):
            raise core.ProofError("UNKNOWN_ABI_IDENTITY", f"pin drift {key}")


def _public_report(config: Mapping[str, object], summary: Mapping[str, object], aggregate: str,
                   duration: float) -> dict[str, object]:
    return {
        "schema_version": "aiwolf.pf3-token-path-public.v1", "task": "T534",
        "run_id": config["run_id"], "candidate_id": core.CANDIDATE_ID,
        "build_identity": core.BUILD_IDENTITY, "aggregate": aggregate,
        "pf3": "FAIL" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "path_token_count": summary.get("path_token_count"),
        "eog_reserve": summary.get("eog_reserve"),
        "accounted_generated_tokens": summary.get("accounted_generated_tokens"),
        "hard_cap": core.HARD_CAP, "child_count": summary.get("_child_count", 0),
        "provider_count": 0, "inference_count": 0, "server_count": 0, "gpu_count": 0,
        "game_count": 0, "actions_count": 0, "duration_seconds": round(duration, 6),
        "approved_hashes": dict(core.APPROVED_HASHES),
        "approved_non_system_hashes": dict(config["approved_non_system"]),
        "logical_validation_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "emitter_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "prefix_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "roundtrip_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "eog_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "module_status": "VALID" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
        "accounting_status": "OVER_BUDGET" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
    }


def _evidence_size(evidence, deadline: float) -> int:
    _check_deadline(deadline)
    total = sum(os.fstat(fd).st_size for fd in evidence.files.values())
    if total > core.PRIVATE_EVIDENCE_LIMIT:
        raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "private evidence")
    return total


def _evidence_write(evidence, name: str, raw: bytes, deadline: float) -> None:
    _check_deadline(deadline)
    evidence.write(name, raw)
    _check_deadline(deadline)


def _evidence_names(evidence, deadline: float) -> list[str]:
    _check_deadline(deadline)
    names = evidence.names()
    _check_deadline(deadline)
    return names


@contextmanager
def _open_public_exclusive(path: Path):
    """Create a non-private public file with a delete-share handle for atomic rename."""
    if os.name != "nt":
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            yield fd
        finally:
            os.close(fd)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0xC0010000, 0x7, None, 1, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "exclusive public create")
    fd = -1
    try:
        attributes = (wintypes.DWORD * 2)()
        if (not kernel.GetFileInformationByHandleEx(handle, 9, attributes, ctypes.sizeof(attributes))
                or attributes[0] & 0x400 or attributes[0] & 0x10):
            raise OSError("public output type")
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        handle = None
        yield fd
    finally:
        if fd >= 0:
            os.close(fd)
        elif handle not in {None, ctypes.c_void_p(-1).value}:
            kernel.CloseHandle(handle)


def _publish_public(output: Path, raw: bytes, deadline: float, *,
                    opener=None, write=os.write, fsync=os.fsync,
                    rename=t527.rename_open_file) -> None:
    """Publish one complete public report or leave the final locator absent."""
    _check_deadline(deadline)
    if not output.is_absolute() or output.exists():
        raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public output locator")
    parent = output.parent
    if not parent.is_dir() or parent.is_symlink():
        raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public output parent")
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public partial exists")
    try:
        with ExitStack() as stack:
            if opener is None:
                fd = stack.enter_context(_open_public_exclusive(temporary))
            else:
                fd = opener(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
                stack.callback(os.close, fd)
            offset = 0
            while offset < len(raw):
                _check_deadline(deadline)
                count = write(fd, raw[offset:offset + 1024 * 1024])
                if not isinstance(count, int) or count <= 0 or count > len(raw) - offset:
                    raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public short write")
                offset += count
            fsync(fd)
            _check_deadline(deadline)
            if _descriptor_bytes_deadline(fd, deadline, maximum=2 * 1024 * 1024) != raw:
                raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public verification")
            _check_deadline(deadline)
            rename(fd, output)
    except core.ProofError:
        raise
    except Exception as exc:
        raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "public publication") from exc


def run_real_proof(args, namespace: Mapping[str, object]) -> int:
    started = time.monotonic()
    deadline = started + core.RUN_TIMEOUT_SECONDS
    with ExitStack() as config_stack:
        _check_deadline(deadline)
        config_fd = config_stack.enter_context(t527._locked_path(args.config.resolve(), create=False))
        if os.fstat(config_fd).st_size > 2 * 1024 * 1024:
            raise core.ProofError("UNKNOWN_RESOURCE_BOUND", "run config")
        config = _strict_config_bytes(_descriptor_bytes_deadline(config_fd, deadline, maximum=2 * 1024 * 1024))
        _check_deadline(deadline)
        return _run_claimed_real_proof(args, config, started, deadline)


def _run_claimed_real_proof(args, config: Mapping[str, object], started: float, deadline: float) -> int:
    state = core.StateMachine(); summary: dict[str, object] = {}
    runner_source = Path(core.__file__)
    aggregate = "UNKNOWN_RUNTIME_INVALID"
    with _claim_private_v2(args.private.resolve(), config, deadline) as evidence, ExitStack() as stack:
        state.advance(core.State.CLAIMED)
        pins: dict[str, object] = {}; identities: dict[str, str] = {}
        static_snapshots: list[dict[str, object]] = []
        schema = b""; raw = b""; backend = None
        try:
            pins, identities = _pin_configured(stack, config, runner_source, deadline)
            def verify_static(phase: str) -> None:
                _verify_configured_pins(pins, identities, deadline)
                static_snapshots.append({"phase": phase, "hashes": dict(identities)})
            verify_static("INITIAL")
            certificate_projection = _validate_certificate_bundle(config, pins, identities, deadline)
            sampler_record = core.EffectiveSamplerRecord(**config["sampler_record"])
            core.validate_sampler_record(sampler_record)
            schema, raw = core.build_witness()
            input_fd = evidence.stack.enter_context(t527._locked_path(evidence.directory / "input.bin", create=True))
            evidence.files["input.bin"] = input_fd
            approved_modules = config["approved_non_system"]
            if not isinstance(approved_modules, dict):
                raise core.ProofError("UNKNOWN_MODULE_SET", "module allowlist")
            backend = WindowsDebugBackend(Path(pins["proof_child"]["path"]), input_fd, approved_modules,
                model_path=Path(pins["model"]["path"]),
                native_dir=Path(pins["llama.dll"]["path"]).parent,
                symbols=config["symbols"],
                static_verify=verify_static, run_deadline=deadline)
            summary, state = core.run_backend(backend, nonce=config["nonce"], schema=schema, raw=raw,
                sampler_input=config["sampler_record"], approved_modules=approved_modules,
                expected_abi=config["expected_abi"], run_deadline=deadline)
            aggregate = summary["aggregate"]
        except core.ProofError as error:
            aggregate = error.aggregate; state.fail(aggregate)
        except Exception:
            aggregate = "UNKNOWN_RUNTIME_INVALID"; state.fail(aggregate)
        summary["_child_count"] = backend.started_count if backend is not None else 0
        if pins:
            try:
                _verify_configured_pins(pins, identities, deadline)
                static_snapshots.append({"phase": "FINAL", "hashes": dict(identities)})
            except core.ProofError as error:
                aggregate = error.aggregate; state.fail(aggregate)
        planned_history = list(state.history)
        if aggregate == "FAIL_BUDGET" and state.state == core.State.CHILD_REAPED:
            planned_history += [core.State.PRIVATE_SEALED.value]
        else:
            planned_history += [core.State.UNKNOWN_SEALED.value]
        detail = {"schema_version": "aiwolf.pf3-token-path-private.v1", "state_history": planned_history,
                  "aggregate": aggregate, "artifact_identities": identities,
                  "artifact_file_identities": {key: pin["file_identity"] for key, pin in pins.items()},
                  "static_snapshots": static_snapshots,
                  "certificate_projection": locals().get("certificate_projection"),
                  "candidate_id": core.CANDIDATE_ID,
                  "schema_bytes": schema.decode("utf-8") if schema else None,
                  "raw_hex": raw.hex() if raw else None, "summary": summary,
                  "observed_pre_seal_history": list(state.history),
                  "child_lifecycle": None if backend is None else {
                      "started_at_monotonic": backend.started_at_monotonic,
                      "ended_at_monotonic": backend.ended_at_monotonic,
                      "exit_code": backend.exit_code, "timed_out": backend.timed_out,
                      "cleanup_result": backend.cleanup_result,
                  }}
        _evidence_write(evidence, "detail.json", core.canonical_bytes(detail), deadline)
        _evidence_size(evidence, deadline)
        manifest = {"schema_version": "aiwolf.pf3-token-path-manifest.v1",
                    "files": [{"name": name, "sha256": _descriptor_hash_deadline(fd, deadline),
                               "size": os.fstat(fd).st_size} for name, fd in sorted(evidence.files.items())],
                    "aggregate": aggregate}
        _evidence_write(evidence, "manifest.json", core.canonical_bytes(manifest), deadline)
        manifest_sha = core.digest(_descriptor_bytes_deadline(evidence.files["manifest.json"], deadline))
        _evidence_write(evidence, "seal.json", core.canonical_bytes({
            "manifest_sha256": manifest_sha, "aggregate": aggregate,
            "publication_candidate": "FAIL_BUDGET" if aggregate == "FAIL_BUDGET" else "UNKNOWN",
            "canonical_state_at_seal": planned_history[-1],
        }), deadline)
        _evidence_size(evidence, deadline)
        for item in manifest["files"]:
            if _descriptor_hash_deadline(evidence.files[item["name"]], deadline) != item["sha256"]:
                raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "manifest verification")
        expected_names = {item["name"] for item in manifest["files"]} | {"manifest.json", "seal.json"}
        if set(_evidence_names(evidence, deadline)) != expected_names:
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "manifest membership")
        if aggregate == "FAIL_BUDGET" and state.state == core.State.CHILD_REAPED:
            state.advance(core.State.PRIVATE_SEALED)
        else:
            state.seal_unknown()
        if state.history != planned_history:
            raise core.ProofError("UNKNOWN_EVIDENCE_INVALID", "terminal state")
        report = _public_report(config, summary, aggregate, time.monotonic() - started)
        output = args.output.resolve(strict=False)
        try:
            _publish_public(output, core.canonical_bytes(report) + b"\n", deadline)
        except core.ProofError:
            state.fail("UNKNOWN_EVIDENCE_INVALID")
            return 2
        if aggregate == "FAIL_BUDGET":
            state.advance(core.State.FAIL_BUDGET_PUBLISHED)
    return 0 if aggregate in {"FAIL_BUDGET"} or aggregate.startswith("UNKNOWN_") else 2
