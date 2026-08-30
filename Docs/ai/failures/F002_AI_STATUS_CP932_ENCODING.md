# F002 ai_status の CP932 出力エラー

## Attempt

Windows PowerShell の既定出力エンコーディングのまま `python scripts/ai_status.py` を実行した。

## Problem

`CURRENT_STATE.md` の em dash（`—`）を標準出力へ書く際、CP932 がその文字をエンコード
できず `UnicodeEncodeError` で停止する。

## Result

`$env:PYTHONIOENCODING='utf-8'; python scripts/ai_status.py` として実行すると正常に状態を
取得できる。リポジトリの内容は変更しない。

## Do Not Repeat

Windows PowerShell から状態スクリプトを実行するときは、最初から
`PYTHONIOENCODING=utf-8` を指定する。
