"""有限のREAL時間でGPU稼働状況をJSONLへ記録する（LLM要求・process操作なし）。"""

from __future__ import annotations

import argparse
import csv
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import time


FIELDS = (
    "gpu_util_pct", "memory_controller_util_pct", "vram_used_mib",
    "vram_total_mib", "temperature_c", "power_w",
)
QUERY = "index,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw"


def parse_sample(output: str) -> list[dict]:
    rows = list(csv.reader(output.strip().splitlines(), skipinitialspace=True))
    if not rows:
        raise ValueError("empty GPU sample")
    result = []
    for row in rows:
        if len(row) != 8 or not row[0].isdigit() or not row[1].startswith("GPU-"):
            raise ValueError("invalid GPU sample")
        values = {}
        missing = []
        for key, raw in zip(FIELDS, row[2:]):
            if raw.strip() in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
                values[key] = None
                missing.append(key)
                continue
            number = float(raw)
            if not math.isfinite(number) or number < 0 or (key.endswith("_pct") and number > 100):
                raise ValueError("invalid GPU measurement")
            values[key] = number
        result.append({"gpu_index": int(row[0]), "gpu_uuid": row[1], **values,
                       "missing_fields": missing})
    return result


def sample_gpu(timeout_seconds: float) -> dict:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + QUERY, "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        if result.returncode:
            return {"status": "ERROR", "error": "NVIDIA_SMI_EXIT", "gpus": []}
        return {"status": "OK", "gpus": parse_sample(result.stdout.decode("utf-8"))}
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": "NVIDIA_SMI_TIMEOUT", "gpus": []}
    except OSError:
        return {"status": "ERROR", "error": "NVIDIA_SMI_UNAVAILABLE", "gpus": []}
    except (ValueError, UnicodeError):
        return {"status": "ERROR", "error": "NVIDIA_SMI_FORMAT", "gpus": []}


class ProcessWatch:
    """Windowsでは同じprocess objectのhandleを保持し、PID再利用を追跡しない。"""

    def __init__(self, pid: int | None):
        self.pid = pid
        self.handle = None
        if pid is not None and os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            self.kernel.OpenProcess.restype = ctypes.c_void_p
            self.kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
            self.kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            self.kernel.CloseHandle.restype = ctypes.c_int
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
            if not self.handle:
                raise OSError("watch process unavailable")

    def alive(self) -> bool:
        if self.pid is None:
            return True
        if self.handle:
            value = self.kernel.WaitForSingleObject(self.handle, 0)
            if value not in (0, 258):
                raise OSError("watch process observation failed")
            return value == 258
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def monitor(output: Path, *, max_seconds: float, interval_seconds: float = 1,
            query_timeout_seconds: float = 2, watch=None, sampler=sample_gpu,
            clock=time.monotonic, sleep=time.sleep) -> dict:
    for value in (max_seconds, interval_seconds, query_timeout_seconds):
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("monitor bounds must be finite and positive")
    if max_seconds > 86400 or interval_seconds < 0.1:
        raise ValueError("monitor bounds exceeded")
    started = clock()
    samples = errors = 0
    reason = "REAL_LIMIT"
    # Exclusive creation preserves earlier measurements; no append/retry loop.
    with output.open("x", encoding="utf-8") as handle:
        while clock() - started < max_seconds:
            if watch is not None and not watch.alive():
                reason = "WATCH_PROCESS_EXITED"
                break
            remaining = max_seconds - (clock() - started)
            if remaining <= 0:
                break
            row = sampler(min(query_timeout_seconds, remaining))
            errors += row.get("status") != "OK"
            samples += 1
            handle.write(json.dumps({"record": "sample", "clock_domain": "REAL",
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "real_elapsed_sec": clock() - started, **row}, allow_nan=False) + "\n")
            handle.flush()
            remaining = max_seconds - (clock() - started)
            if remaining > 0:
                sleep(min(interval_seconds, remaining))
        summary = {"record": "summary", "clock_domain": "REAL", "reason": reason,
                   "real_duration_sec": clock() - started, "samples": samples, "errors": errors,
                   "max_real_seconds": max_seconds, "interval_real_seconds": interval_seconds,
                   "query_timeout_real_seconds": query_timeout_seconds}
        handle.write(json.dumps(summary, allow_nan=False) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, required=True, help="REAL seconds")
    parser.add_argument("--interval-seconds", type=float, default=1)
    parser.add_argument("--query-timeout-seconds", type=float, default=2)
    parser.add_argument("--watch-pid", type=int)
    args = parser.parse_args()
    if args.watch_pid is not None and args.watch_pid <= 0:
        parser.error("watch-pid must be positive")
    watch = None
    try:
        watch = ProcessWatch(args.watch_pid)
        result = monitor(args.output, max_seconds=args.max_seconds,
                         interval_seconds=args.interval_seconds,
                         query_timeout_seconds=args.query_timeout_seconds, watch=watch)
        print(json.dumps(result))
        return 0 if result["samples"] and result["errors"] == 0 else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "ERROR", "error": type(error).__name__}))
        return 2
    finally:
        if watch is not None:
            watch.close()


if __name__ == "__main__":
    raise SystemExit(main())
