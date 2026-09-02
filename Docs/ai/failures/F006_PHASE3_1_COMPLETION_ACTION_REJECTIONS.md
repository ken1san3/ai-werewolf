# F006 Phase 3.1 completion test: phase-boundary action rejections

## Attempt

Phase 3.2 の R-111〜115 修正後、ローカルLLMを起動せずに `python -m pytest -q` を再実行した。
Phase 3.1 の9プロセス完走テストだけを続けて単独再実行した。

## Problem

`tests/test_phase3_1_completion.py::PhaseThreeOneCompletionTests::test_nine_protocol_clients_complete_and_one_resumes`
が2回連続で失敗した。ゲーム自体は終了し全 client は `GAME_ENDED` になったが、1秒の
phase 境界をまたいだ action が `vote_unavailable` / `action_unavailable` と拒否され、
line 131 の「拒否0件」アサーションに反した。実行時ログには0.1〜0.24秒の asyncio slow
callback が出ている。

## Result

Phase 3.2 の対象テストは `18 passed in 33.17s`、`check_docs.py` と `git diff --check` は成功した。
Phase 3.1 の client action timing は今回のレビュー修正対象外なので変更していない。全件テストの
成功判定はこの失敗を解消するまで保留する。

## Do Not Repeat

この失敗を Phase 3.2 の world state 修正の回帰と断定しない。次の Phase 3.1 修正では、
fixture の action 送信が stale な action state を使わないことと、短い phase の実プロセス試験が
Windows scheduler の遅延下でも拒否0件という契約を満たすことを、修正前後で確認する。
