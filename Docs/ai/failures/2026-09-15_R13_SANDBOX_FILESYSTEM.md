# R13独立回帰のsandbox filesystem拒否

## Attempt

T339、2026-09-15 15:55 JST開始。`logs/t339-r13/run-start.json` と `run-result.json` にPID/時刻/環境/コマンドを保存。Windows Python3.13.3、実行ツールは `use_default` のworkspace-write sandbox。USERNAMEがOwnerであることだけで通常host測定とはしない。

## Problem

一括6moduleは55.318秒で完遂し、exit1、225 PASS＋169 subtests PASS、16 FAIL、43 ERROR、skip0。43 ERRORはpytest `tmp_path` 初期化で `C:/Users/<user>/AppData/Local/Temp/pytest-of-<user>` のscandirがWinError5となった。16 FAILにも一時ディレクトリ内操作またはcleanupのPermissionErrorがある。raw/JUnitは同じlog領域に保存。

今回のdrain修正nodeとACK欠測nodeは同一batch内でPASSしたが、これを6module全体PASSに読み替えない。175 sourceのfreeze/前後hashは一致。OS内部の根因や通常hostでの成功は本測定から推測しない。

## Recovery

MainはT339 packetに通常host測定を一回追加割当。`sandbox_permissions=require_escalated` のautomatic approval reviewで認められた場合に、同一source/6module/外側300秒を `logs/t339-r13-host/` へ別保存する。初回FAIL/ERRORを上書きしない。承認拒否ならその操作と理由を報告し、回避経路を作らない。

## Do Not Repeat

同じrestricted環境でblind retryしない。ACL/security/TEMP、製品/test、skip/timeoutを変更して通過させない。旧テスト一時領域を広域削除しない。通常hostの実行と独立Reviewerの対象hash/原本照合後にだけ今回の全体gateを判定する。

## 通常hostでの別測定結果

automatic approval reviewの拒否なしでT339が通常host測定を実行し、284 PASS＋175 subtests、FAIL/ERROR/skip0、107.434秒、175 source不変。T340が直接照合しAPPROVED。原本 `logs/t339-r13-host/`。初回sandbox失敗・一時領域のcleanup限界は保持し、広域削除やACL変更は行っていない。
