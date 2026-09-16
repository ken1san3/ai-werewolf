# Phase 6 R12 P2 asyncio subprocess 限定補遺

Status: DRAFT

T335 Architect作成。T333独立Reviewer承認前は実装不可。

## 1. 適用範囲

本補遺は、承認済み `PHASE6_R12_DIAGNOSTIC_DESIGN.md`（SHA-256 `9ccad44cfd4b4f8150d96215cc01b184431671dc5d81662a07b4058c707fd516`）のP2 command observationにあるbounded stdout readerの実装方式だけを置き換える。元design bytesは変更しない。

P1、private ACL/handle、redaction/hash/schema、memory上限131072 bytes、PowerShell structured status/exit code、秘密非公開、2+1+1秒のprocess cleanup、P3除外、product/game/provider非操作は変更しない。thread、`subprocess.Popen.communicate()`、同期 `BufferedReader.close()`、新しいWin32 pipe FFIは使わない。

## 2. 採用方式

`scripts/run_phase5_local_smoke.py` の観測helperを次の一つのasync coroutineとして実装する。

```python
async def _observe_windows_process_command_line(
    pid: int,
    *,
    create_subprocess_exec: Callable[..., Awaitable[asyncio.subprocess.Process]]
        = asyncio.create_subprocess_exec,
) -> ProviderCommandObservation:
    ...
```

Windows CPythonのdefault Proactor event loop上で `asyncio.create_subprocess_exec` を使う。固定list argv、`shell=False`相当、stdin/stderrは `DEVNULL`、stdoutだけ `PIPE` とする。reader threadと共有bufferは作らない。既存runnerのasync call chainから直接 `await` し、async関数内で `asyncio.run()` や別event loopを作らない。同期CLI最上位から必要な場合に限り、processをまだ生成していない最上位entrypointが一回だけ `asyncio.run(async_main(...))` を所有する。running loop内のnested `asyncio.run` は禁止する。

## 3. 有限read契約

process生成直後にreader taskを一つだけ作り、mainと共有する `oversize_event: asyncio.Event` を持つ。readerは `await process.stdout.read(4096)` を繰り返す。ローカル `bytearray` が131072 bytesを超える最初のchunkでは、そのchunkをappendせずeventをsetし、以後もreturnせずEOFまで全chunkをdiscard drainする。したがって保持量は131072 bytes以下であり、pipe backpressureによるprocess wait停止を作らない。EOFならbounded bytesとoversize flagを返す。stdoutが存在しない、read例外、reader task cancellationはstable内部状態だけを返し、bytes/exception本文を公開しない。

process wait、reader task、oversize event wait taskは同じ `asyncio.timeout(2.0)` scopeでfirst-completedを監視する。正常候補はprocess exitとstdout EOFの両方が2秒内に成立した場合だけである。oversize eventが先に成立したらmainは直ちにcleanupへ移る一方、reader taskは並行discard drainを続ける。timeout、JSON/status不整合も成功parseへ進まずcleanupへ移る。event wait taskはfinallyでcancelしdoneを確認する。

## 4. 有限cleanup契約

cleanupは一つの `finally` helperでexact一回実行する。

1. processが生存中なら `terminate()` を一回呼び、`await wait_for(process.wait(), 1.0)`。
2. なお生存なら `kill()` を一回呼び、`await wait_for(process.wait(), 1.0)`。
3. kill後1秒枠ではprocess waitとreader taskを同時に待つ。reader taskが未完了ならstdout EOF取得を残時間内で `await wait_for(shield(reader_task), remaining)`。
4. processの成否にかかわらずreader未完了なら `cancel()` を一回行う。kill後1秒枠の残時間が正なら `await wait_for(gather(reader_task, return_exceptions=True), remaining)`、残時間が0なら新たなgather/waitを開始せず一回だけevent loopへ制御を返して `reader_task.done()` を検査する。標準 `StreamReader.read` のcancellationを抑止するhandlerは置かない。同じdeadline内にdoneを確認できなければstable cleanup errorとする。
5. coroutine return前に `process.returncode is not None` と `reader_task.done()` の両方を必須assertionとする。どちらか未成立なら結果を返さずstable `ProviderObservationCleanupError` とし、上位preflightを失敗させる。成功/UNKNOWN record、argv/hash、raw stdoutは生成しない。

`StreamReader` やtransportへ同期 `close()` を呼ばない。process exit/killがWindows pipe handleを閉じ、Proactor readをEOFまたはcancellationへ完了させる。reader taskを残したままevent loopを閉じたりdaemon扱いで成功させたりしない。cleanup exceptionにはPID、command line、stdout、stderr、PowerShell本文を含めない。

2+1+1秒は、通常read/process待ち2秒、terminate待ち最大1秒、kill後のprocess exitとreader完了を共有する最大1秒である。reader cancellation用に追加deadlineを足さない。kill後1秒枠は `loop.time()` で残時間を計算し、各awaitへ渡す。process生成自体が失敗した場合はtask/pipeがないため即closed stateへ戻る。

## 5. 状態写像

- 正常exit 0、EOF、bounded exact JSON/status、base64/argv/redaction成功だけが `OBSERVED`。
- structured exit 10/11/12/13は元designどおり `PROCESS_NOT_FOUND / ACCESS_DENIED / EMPTY / OS_ERROR`。
- stdout oversize、invalid JSON/base64、exit/status不一致、read errorは `PARSE_FAILED`。
- 2秒timeout後にbounded cleanupが完了した場合は `OS_ERROR`、argv/hash null。
- processまたはreader taskのcleanup未完了はrecord化せずpreflight failure。未完了をOS_ERROR artifactとして継続しない。

## 6. focused test差替え

元design test 10/11のnode名と総12件は維持し、P2 fixtureだけ次へ差し替える。

- test 10はfake `asyncio.subprocess.Process` とchunked `StreamReader` を注入し、4096-byte read、131072-byte exact受理、131073-byte以降非保持かつEOFまでdiscard drain、oversize eventでmain cleanup開始、fixed argv、stdin/stderr/PIPE設定、正常EOF後の既存redaction/hashを検証する。
- test 11は、正常、structured exit 10–13、read error、2秒timeout、oversize中discard、terminate内exit、kill内exit、kill後EOF、reader cancellation、kill後残時間0をfake clock/processで個別に通す。各branchでterminate/kill/cancel回数、`returncode`、`reader_task.done()`、総deadline 2+1+1、残時間0で新規gatherなし、argv/hash null、raw/exception/repr/stdout/stderr非流出をliteral assertionする。
- kill後も `process.wait()` が完了しないfixtureはreader cancellation/doneまで完了した後、観測recordを返さず `ProviderObservationCleanupError` となることを検証する。標準StreamReaderのcancellationを抑止する非準拠fakeはfixtureに使わない。live taskを残してPASSするfixtureは禁止する。

関連6 module、`python scripts/check_docs.py`、P3非変更という元designの受入範囲は変えない。T334の新19 cases成功は履歴として保持し、この補遺の未実装/PASS証拠には使わない。別の既存testにあるCLAIM 50ms timeout 1 FAILも変更・成功読替えしない。
