# T348で特定したthink prefixと出力grammarの衝突

T344の107 HTTP400を、T348が保存原本とexact b10697-093adb242 sourceで調査した。全件でsampler初期化時の固定piece `<think>` 受入れ後にgrammar stackが空になる。同版Qwen3.5対応chat処理はreasoning_format noneの場合にreasoning grammarを含めず、JSON schemaだけを要求する。一方、保存templateの生成prefixにはthink markerが入り、無効化分岐でも空think blockが残る。

純schema107件は同版公式Python converterと実llama.dllのnull-vocab parseで全PASS。構文parse成功を、実C++chat/prefill/tokenizationや修正後HTTP200の成功へ拡張しない。詳細根拠/同版source URL/hash/環境/再現限界は `Docs/ai/handoffs/tasks/T348_SAMPLER_FAILURE_INVESTIGATION.md`。

別原因群として、Mainは実 `_run_game` のserver/status/broker各待機にTimeoutErrorを入れる有限synthetic診断で、原本read/writeを飛ばしprovider_calls0となる経路を3/3再現した。正常対照は107、集計器単独もusage欠測を含む107を107と数える。子process/network/modelは起動していない。`logs/t347-sampler/collection-path-results.json` を参照。

今回製品修正/新推論/gameは0。新規独立Tester session生成がagent thread limit reachedで失敗し、T349/T350は未実施・未承認。新しい独立session確保後に同じ証拠を検証し、template/prefill/grammar整合と失敗時集計回収を別scopeとして最小修正する。structured outputを削除せず、無変更game再試行をしない。P3早期停止保留・provider操作境界・旧FAIL/UNKNOWNを維持する。
