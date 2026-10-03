"""Local model settings and ownership-safe Windows process supervision."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from .llm import SharedLLM

LLAMA = Path(r'C:\AIagent\llama-server.exe')
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Model:
    id: str
    path: str
    rate: float
    generation: float
    ingestion: float
    gpu_only: bool = False


MODELS = [
    Model('qwen35-9b', r'C:\models\Qwen3.5-9\Qwen3.5-9B-Q4_K_M.gguf', 1, 81, 2709, True),
    Model('swallow-20b', r'C:\models\GPT-OSS-Swallow-20B\GPT-OSS-Swallow-20B-RL-v0.1-MXFP4_MOE.gguf', .4, 51, 985),
    Model('gemma4-12b', r'C:\models\Gemma-4-12B\gemma-4-12b-it-qat-q4_0.gguf', .4, 27, 1272),
    Model('gemma4-26b', r'C:\models\Gemma-4-26B-A4B\Gemma-4-26B_q4_0-it.gguf', .3, 34, 625),
    Model('qwen36-35b', r'C:\models\Qwen3.6-35B-A3B\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf', .2, 30, 378),
    Model('qwen38-27b', r'C:\models\Qwen3.8-27B\Qwen3.8-27B-UD-Q4_K_M.gguf', .08, 4.5, 289),
]
MODEL_MAP = {m.id: m for m in MODELS}


def settings():
    rows = [{'id': f'{m.id}/{mode}', 'model': m.id, 'mode': mode,
             'rate': m.rate if mode in {'off', 'low'} else m.rate * .35}
            for m in MODELS for mode in (['low', 'high'] if m.id == 'swallow-20b'
                                          else ['off'] if m.id == 'qwen38-27b' else ['off', 'on'])]
    path = ROOT / 'content/marathon_calibration.json'
    if path.exists():
        measured = read_json(path)['settings']
        for row in rows:
            row.update(measured.get(row['id'], {}))
        fastest = {m.id: min(r.get('expected_game_sec', 750/r['rate']) for r in rows if r['model'] == m.id) for m in MODELS}
        rows.sort(key=lambda r: (fastest[r['model']], r.get('expected_game_sec', 750/r['rate'])))
    return rows


def make_llm(setting, port):
    mode = setting['mode']
    thinking = mode != 'off'
    options = {'chat_template_kwargs': {'enable_thinking': thinking}}
    if mode in {'low', 'high'}:
        options['reasoning_effort'] = mode
        options['chat_template_kwargs']['reasoning_effort'] = mode
    # Per-request cap lets slow models finish a short decision in the same phase.
    budget = 768 if mode == 'low' else 1536 if thinking else 0
    if thinking:
        options['reasoning_budget_tokens'] = budget
    return SharedLLM(f'http://127.0.0.1:{port}/v1/chat/completions',
                     request_options=options, thinking_tokens=budget, request_timeout=300, context_limit=8192)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(10):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(.05 * 2**attempt, .5))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def powershell(script):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                             "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script],
                            capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[:500])
    return result.stdout.decode('utf-8-sig').strip()


def process_inventory():
    raw = powershell("@(Get-CimInstance Win32_Process -Filter \"Name = 'llama-server.exe'\" | Select-Object ProcessId,CreationDate,ExecutablePath) | ConvertTo-Json -Compress")
    data = json.loads(raw or '[]')
    return data if isinstance(data, list) else [data]


def memory_status():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(n, ctypes.c_ulonglong) for n in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]]
    data = MEMORYSTATUSEX()
    data.length = ctypes.sizeof(data)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise OSError('Windowsのメモリを確認できません')
    result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.free,memory.total', '--format=csv,noheader,nounits'],
                            capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('GPUの空きを確認できません: ' + result.stderr[:200])
    name, free, total = result.stdout.strip().splitlines()[0].split(',')
    return {'gpu': name.strip(), 'gpu_free_mb': int(free), 'gpu_total_mb': int(total),
            'ram_free_mb': data.available / 2**20, 'ram_total_mb': data.total / 2**20}


def check_resources(model=None):
    status = memory_status()
    required = 4096 if model is None else max(4096, Path(model.path).stat().st_size / 2**20 - status['gpu_free_mb'] + 2048)
    if status['gpu_free_mb'] < 5500 or status['ram_free_mb'] < required:
        raise RuntimeError(f'メモリ不足: GPU空き{status["gpu_free_mb"]}MiB、RAM空き{status["ram_free_mb"]:.0f}MiB、RAM必要目安{required:.0f}MiB')
    return status


class ModelServer:
    def __init__(self, directory, port=8091):
        if port == 8090 or not 1024 <= port <= 65535:
            raise ValueError('実験専用ポートを指定してください（8090は禁止）')
        self.directory, self.port = Path(directory), port
        self.process, self.model, self.owner = None, None, None
        self.lock_file = None
        self.owner_path = self.directory / 'llama_owner.json'

    def recover(self):
        if self.owner_path.exists():
            self.owner = read_json(self.owner_path)
            self.stop()

    def healthy(self):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=3) as response:
                return response.status == 200
        except Exception:
            return False

    def stop(self):
        if self.owner:
            matches = [p for p in process_inventory() if p['ProcessId'] == self.owner['pid']
                       and p['CreationDate'] == self.owner['created'] and p['ExecutablePath'] == self.owner['path']]
            if matches:
                powershell(f'Stop-Process -Id {int(self.owner["pid"])} -Force -ErrorAction Stop')
                for _ in range(100):
                    if not any(p['ProcessId'] == self.owner['pid'] for p in process_inventory()):
                        break
                    time.sleep(.1)
        if self.process:
            if not self.owner and self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise RuntimeError('自分が起動したLLMの停止が完了しません')
        self.process, self.model, self.owner = None, None, None
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None
        save_json(self.owner_path, {})

    def start(self, model):
        if self.model == model.id and self.process is not None and self.process.poll() is None and self.healthy():
            return 0
        self.stop()
        import msvcrt
        (ROOT / 'runs').mkdir(exist_ok=True)
        self.lock_file = (ROOT / 'runs/llama.lock').open('a+b')
        self.lock_file.seek(0)
        if not self.lock_file.read(1):
            self.lock_file.write(b'0')
            self.lock_file.flush()
        self.lock_file.seek(0)
        try:
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError('他の実験ランナーがLLMを管理中です')
        if process_inventory():
            raise RuntimeError('他のllama-serverが稼働中です。停止してから再実行してください')
        check_resources(model)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', self.port))
        started = time.monotonic()
        command = [str(LLAMA), '-m', model.path, '--host', '127.0.0.1', '--port', str(self.port),
                   '-c', '8192', '-np', '1', '--jinja']
        command += ['-ngl', '99'] if model.gpu_only else ['--fit', 'on']
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f'llama_{model.id}.log').open('a', encoding='utf-8') as log:
            self.process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        # Persist ownership before waiting for model load, including exact creation time.
        for _ in range(50):
            candidate = next((p for p in process_inventory() if p['ProcessId'] == self.process.pid), None)
            if candidate:
                self.owner = {'pid': candidate['ProcessId'], 'created': candidate['CreationDate'], 'path': candidate['ExecutablePath']}
                save_json(self.owner_path, self.owner)
                break
            if self.process.poll() is not None:
                raise RuntimeError('LLMの起動に失敗しました。実行ログを確認してください')
            time.sleep(.2)
        if not self.owner:
            self.process.terminate()
            raise RuntimeError('起動プロセスの所有権を確認できません')
        while time.monotonic() - started < 300:
            if self.process.poll() is not None:
                raise RuntimeError('モデルの読み込み中にLLMが終了しました')
            if self.healthy():
                self.model = model.id
                return round(time.monotonic() - started, 2)
            time.sleep(1)
        raise TimeoutError('モデルの読み込みが300秒で終わりませんでした')


class Awake:
    def __enter__(self):
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
            raise OSError('スリープ抑止の設定に失敗しました')
        return self

    def __exit__(self, *args):
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
