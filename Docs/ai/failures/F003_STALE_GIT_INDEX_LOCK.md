# F003 空の Git index.lock

## Attempt

Codex からレビュー修正のローカルコミットを実行した。

## Problem

Git プロセスが存在しないにもかかわらず、空の `.git/index.lock` が残り、
`git add` / `git commit` が失敗した。このセッションでも同じ事象が2回発生した。

## Result

`git` プロセスが無いこと、対象が `C:\AIwolf\.git\index.lock` の空ファイルであることを
確認してから、そのファイルだけを削除した。続く権限付き Git コマンドでコミットに成功した。

## Do Not Repeat

Git ロックエラー時に `.git` 全体を削除・変更しない。実行中の Git プロセスを先に確認し、
プロセスが無く空の `index.lock` だけと確認できた場合に限り、そのファイルだけを削除して
再実行する。
