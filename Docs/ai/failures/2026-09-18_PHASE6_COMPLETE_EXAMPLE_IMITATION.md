# 完成JSON例による本文模倣

Task: T406 / T407
Scope: 合成32case、同一Qwen provider、実gameなし

## 観測

baselineはNONE32/32。systemへrequest-local合法完成例を付けるとNONE4/32へ下がったが、
新規生成28件中26件の本文が例文と完全一致した。同一入力4件は再利用で新規生成していない。
疑いの理由・生死・能力結果について具体的に答える能力の改善は確認できなかった。
NONE対照で待機文にQUESTIONを付ける意味不一致も発生。

## 分離した事実

schema/backendはANSWER/REBUTTAL/QUESTIONを出せる。NONE固定をgrammar制約のせいにできない。
実tokenはcontextに十分収まり、60callすべてgeneration usageと事前tokenizer計数が一致した。
例自体はschema/semantic validator上合法だが、合法な形式の例と適切な会話内容は別物。
候補の秘密自白0・不正出力0は、例文反復によって状況依存発言が減った影響を含みうる。

## 処置

候補不採用、製品無変更、実game0、同条件retry0。別のprompt方法をこの人工suiteで検証するまで
品質改善扱いしない。内部推論や役職別の固定戦略ルールを導入しない。
安全なcase別判定/hashは `Docs/ai/handoffs/tasks/T406_SAFE_SUITE_RESULTS.json`。
