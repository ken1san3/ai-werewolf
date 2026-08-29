# F001 サンドボックス内の JSONL ログテスト書込み

## Attempt

Phase 1.2 の `GameState` ユニットテストを通常のサンドボックス権限で実行した。
テストは一時ディレクトリ配下に `logs/<game_id>/` を作成して JSONL を検証する。

## Problem

Python プロセスによる子ディレクトリ作成が `PermissionError` で拒否される。
一時ディレクトリをユーザー領域からワークスペース直下へ移しても同じだった。

## Result

テスト用一時ディレクトリはワークスペース配下に置き、テスト実行だけ
`require_escalated` で行うと成功する。失敗実行で残った `tmp*` は検証して削除した。

## Do Not Repeat

JSONL を実際に書く GameState テストを通常サンドボックスで繰り返し実行しない。
最初から書込みを許可したテスト実行を使う。
