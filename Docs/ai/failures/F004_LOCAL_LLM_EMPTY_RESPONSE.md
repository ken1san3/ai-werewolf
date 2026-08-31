# F004 ローカルLLMが応答本文を返さない

## Attempt

Implementer から D034 のローカルLLMへ差分確認を依頼した。

## Problem

応答本文（`choices[0].message.content`）が空で返り、検証に使えなかった。
エラーにもならないため、呼び出し側からは「無言で失敗した」ようにしか見えない。

原因は Qwen3.x の thinking モード。llama-server の起動フラグに
`--chat-template-kwargs '{"enable_thinking":false}'` が無いと thinking が有効なままになり、
推論トークンだけで `max_tokens` を使い切る。推論は `content` ではなく
`reasoning_content` へ入るため、`content` は空のまま返る。

## Result

サーバ起動フラグとクライアント送信ペイロードの両方で thinking を切った。
クライアントは本文が空のとき、`finish_reason` と `reasoning_content` の有無から
原因を切り分けて表示するようにした（黙って空文字を返さない）。

## Do Not Repeat

ローカルLLMの応答が空でも、**空の結果をそのまま検証へ使わない。** これは守られていた。

呼び出し側は本文の空を必ず異常として扱うこと。原因の切り分けは
`python C:\AIagent\agent\tools\probe.py` を実行し、`finish_reason` と
`reasoning_content` の文字数を見る。

新しいモデルを追加したときは、まず `probe.py` で本文が返ることを確認してから使う。
thinking を持つモデルは、既定で有効になっていると考えて疑うこと。
