# F005 PowerShell 経由の local LLM diff 要約で不正サロゲート

## Attempt

レビュー修正の staged diff を、`git diff --cached | python -B tools\summarize.py --kind diff`
で開発補助の local LLM に要約させた。

## Problem

llama-server が HTTP 500 を返し、`invalid string: surrogate U+DC00 ...` と報告した。
同じ経路で以前にも再現しており、通常の ASCII / 日本語本文の `/tokenize` と要約は動作する。
PowerShell のパイプ処理で差分中の文字列に非対 Unicode サロゲートが混入することが観測されている。

## Result

この要約結果は判断材料に使わず、対象ファイルごとの staged diff を直接確認した。R-63 の実サーバ
検証は、パイプを介さない 100,000文字の ASCII / 日本語入力で別途実行した。

## Do Not Repeat

PowerShell から `git diff` を直接 `summarize.py` へパイプした HTTP 500 をモデルや実装の失敗と
混同しない。再発時は対象ファイルの差分を直接確認し、必要なら Unicode を保持できる別の入力経路を
使う。要約が失敗しただけで検証結果を推測しない。
