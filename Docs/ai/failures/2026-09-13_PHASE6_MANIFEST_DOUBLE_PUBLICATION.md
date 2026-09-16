# P6-F 最終manifestの二重公開

Status: OPEN（T253修正とT254限定試験PASS、fresh review/修正後完走は未実行）

## 初回観測

T252は通常の非昇格Python3.13.3、restricting SIDなし、未変更system TEMPで元16 filesystem
nodesを一度測定し、16 passed/6 subtests passed、exit0を得た。その後、元の
`test_p6f_nine_client_semantic_completion` を一度だけ実行した。
2026-09-13 13:47:47–14:01:27 UTC、1 failed/820.09s、exit1、timeout=false。
`machine_semantic_pass=False`、errorsは原文 `['FileExistsError']`。
初回assertionはtests/test_phase6_semantic_completion.py:138。WinError5ではない。

serverのgame_end/success/listener_closed、brokerのsuccess/shutdown_clean/listener_closed、
9clientsのGAME_ENDED/runtime_success/owned_task_names_alive=[]を記録した。
11子とpytestは終了。終了後に再利用されたPID25948は生成FILETIMEが異なるpwshで、操作していない。
全hostの所有権確認とは扱わない。

## 原因の最小境界

`_write_manifest` はPhase5形式のmanifest.jsonを作成する。続く `_write_phase6_evidence` は
accepted-text.jsonlを保存した後、Phase6情報を加えて同じmanifest.jsonを新規作成専用writerへ渡す。
共通 `_write_private_bytes_atomic` は既存pathを検出すると、OS write前にFileExistsErrorを投げる。

保存artifactはこの呼出し順と一致する。manifest.jsonはPhase5 schemaのまま、2958bytes、
SHA-256 `50bc300afaa75ed81dad6a2e1566101eeb72d8a28c734de07fd396eb1f53345d`。
その後にaccepted-text.jsonl（41743bytes、SHA-256
`7fa2549f0b2baed9bb6781392c709e33b0ad5c9d13fa307adbcca8ecf02989fc`）が作成された。
元のexception stackはclassのみを残す実装で失われており、正確なraise位置はソースとartifactからの
推論として区別する。ACL/security設定による新たな拒否を示す証拠はない。

## 保存・修正境界

日本語の全報告は [T252 handoff](../handoffs/tasks/T252_PHASE6_REMAINING_FILESYSTEM_COMPLETION_TEST.md)。
raw結果、PID世代、絶対path、時刻、全61artifactのhashはlogs/t252-testに保存した。
Mainは40fileの提出sealと凍結10入力、handoff hashを照合し、別の通常文脈の読取りでも
元の61artifact全てのbytes/hash一致を確認した。元runへの書込みは0。

T253は完成Phase6 manifestを一度だけ公開する修正を納品し、T254の限定回帰はPASSした。
共通writerの既存output拒否、atomic/private公開、元のsemantic受入、Phase5互換を緩めない。
元の失敗runを消したり上書きして成功へ変換しない。修正と独立確認後の別packetによる
必須完走の一回測定、およびfresh Reviewer確認までF全体を閉じない。

後続T254の原本照合で旧runの不在が判明したため、上記の61件照合は過去時点の事実に限定する。
現在の保全は未成立。別の [証拠保全失敗](2026-09-13_PHASE6_TEMP_EVIDENCE_RETENTION.md) により
Mainは停止し、原本復元可否または欠落を記録した再開方針のユーザー判断を待つ。
