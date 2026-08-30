# F002 ai_status の CP932 出力エラー

## Attempt

Windows PowerShell の既定出力エンコーディングのまま `python scripts/ai_status.py` を実行した。

## Problem

`CURRENT_STATE.md` の em dash（`—`）を標準出力へ書く際、CP932 がその文字をエンコード
できず `UnicodeEncodeError` で停止する。

## Result

暫定回避は `$env:PYTHONIOENCODING='utf-8'; python scripts/ai_status.py`。

その後 Reviewer が `scripts/ai_status.py` を修正し、
スクリプト自身が `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` で
出力を切り替えるようにした。**環境変数の指定は不要になった。**

## Do Not Repeat

呼び出し側に環境変数の設定を要求する形で回避を終わらせない。
このリポジトリのスクリプトは Windows の既定コンソール（CP932）で
そのまま動くこと。日本語ドキュメントを読む以上、必ず踏む。
