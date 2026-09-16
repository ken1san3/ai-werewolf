# Phase 6 output budget 数値選択

Status: DRAFT — 未承認。これは T247 の次段である数値選択案であり、実装許可でも
Phase 6-I の開始許可でもない。

## 結論

選択値は **UNKNOWN** とする。既存の operational `96` は据え置くが、その十分性を
示す結論ではない。`96` も `1..512` の有限値 `B` も、この記録だけからは承認可能な
設定値として選ばない。従って T247 §5 の runner 定数、bootstrap、broker 再構成、
fingerprint、テストの実装 scope は発生しない。

## 照合した固定証拠

T281 corpus と T283 final seal
`fe7d6846483d91f41b9fdaeb939d9fe847cab5dd799b28e8acc53660e703aea7`
を静的に照合した。T283 は 12 witness × compact/indented の 24 strings を一回ずつ
数え、すべて initial/repair parser `PASS`、78–510 tokens と記録する。最大は
`nine_seat_pre_vote` の indented encoding で 510 である。各 string は
`add_special=false` と `parse_special=false` であり、T283 `identity.json` は
`provider_non_content_cost: "UNKNOWN"` を明記する。これは tokenizer、モデル、GPU、
provider、corpusを再実行又は再計測した結果ではない。

T247 §4 の制約どおり、これらは代表 witness の content count であって、全合法 proposal
又は実際に生成される全 response の上限ではない。特に indented/compact 以外の整形、
長い ID、許可された任意 update、生成された stop/EOG、未観測の control/reasoning を
上限化していない。`96` 超の有効 witness は存在するが、それだけで全 response が `96`
超であることも、`96` が不十分であることも、この選択の結論にはしない。

## matching server accounting の評価

T248 に保存された同一 server source では OpenAI `max_tokens` と `n_predict` が同じ
`n_predict` field に対応する。slot は request `n_predict` を `n_predict_max` に採用し、
sampled token ごとに `stats.n_gen` を増やす。budget check は `n_gen` と
`n_predict_max` を比較する。`process_token` は EOG を `STOP_TYPE_EOS` にするが、
`n_gen` の増加はその前に起こる。stop string は response text から除かれ得る。

よって static source は provider cap が生成側の `n_predict` であること、可視 content と
budget accounting が必ずしも同一ではないことを示す。一方、content tokenizer の
`add_special=false` / `parse_special=false` count と、実際の request ごとの `n_gen` の
差を 0、1、又は 2 として上限化する根拠はない。`enable_thinking=false` は T248 profile
の事実だが、EOG、stop、control token又は他の未観測費用を 0 と証明しない。

そのため `B=512` は「510 に 2 を足す」だけでは防御できない。`B<=509` は最大 witness
を収めず、`B=510..512` も対象 coverage の上限と追加 accounting の上限が不明なままで
ある。これは 512 が不適切と実測した主張ではなく、選択に必要な保証をこの証拠から
導けないという判断である。

## 維持事項と次の条件

timeout、ShortChatConfig、schema、initial/repair の一回制約、CHAT cap、legacy Phase 5/Q8
の `96` は変更しない。P6-I の offline/finite completion、人間品質、H/J を含む既存 gate
も閉じない。

有限値を改めて選択するには、別 packet で少なくとも次を固定して fresh review に渡す必要が
ある。

1. 選ぶ coverage（整形、ID、optional updateを含む）に対する全 response content の上限。
2. 同一 server/request path で content tokenizer count から `stats.n_gen` への差を、EOG、
   stop、control、reasoning を含めて有限上限として示す根拠。未観測値を 0 と置かない。
3. その上限と既存 `1..512` の範囲で実際に守れる `B`、又は守れないという結論。

この DRAFT は fresh Reviewer の独立承認を要求する。承認前に config、runner、backend、
test、CI、corpus、既存 evidence を変更しない。
