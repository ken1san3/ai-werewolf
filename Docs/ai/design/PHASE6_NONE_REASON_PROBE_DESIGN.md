# C2 NONE reason単独実験の詳細設計

Task: T431
Status: DRAFT

独立Reviewer承認前、実装・実行未承認。

## 1. 目的・根拠・対象外

T427のqw9共通条件baseline 32件と、C2だけを加えた新規32件を比較する。主目的はspeech_actと本文の不一致を減らせるかの有限観測。非NONEの出現だけで成功とはしない。製品schema、validator、prompt、brain policy、ゲーム規則、fixtureの正解は変更しない。

根拠は `PHASE6_SPEECH_ACT_NONE_PROPOSAL_20260919.md` のC2と検証条件、`PHASE6_MODEL_COMPARISON_20260918.md` の固定比較・評価契約。Mainによる保存観測はqw9/G01-1のspeech_actが12 field中11番目、alphabetical=true。これは観測順でありgrammarが順序を強制した証明ではない。C2の実施はR1の因果確定に依存させない。

現在 `_speech_schema()` のNONEはkindだけのclosed object。`SpeechActNone`もkindだけで、`ai_client/llm/decision.py::_parse_speech`はNONEの追加keyを拒否する。したがって候補rawをそのまま旧parserへ渡すと理由追加だけで失敗する。以下の実験専用adapterで境界を明示する。

## 2. 候補schemaの唯一の差分

baseline `body_for(project(case, 'baseline'), qw9_model)` をdeep copyし、`response_format.json_schema.schema.$defs.speech_act.oneOf` 内で `properties.kind.const == 'NONE'` の枝を一意に探す。該当枝は元契約と一致することをassertし、見つからない／複数／既に拡張済みなら固定code `SCHEMA_SHAPE_CHANGED` で生成前停止。

当該枝だけを次のobjectへ変更する。

```json
{"type":"object","properties":{"kind":{"const":"NONE"},"reason":{"type":"string","enum":["NO_NEW_INFORMATION","WAITING_FOR_OTHERS","DELIBERATE_SILENCE","INDEPENDENT_STATEMENT"]}},"required":["kind","reason"],"additionalProperties":false}
```

| reason | 実験上の意味 |
| --- | --- |
| NO_NEW_INFORMATION | 新たに述べる情報・接地されたactがない。 |
| WAITING_FOR_OTHERS | 他者の情報・応答を待つ。 |
| DELIBERATE_SILENCE | 戦略的に発言・開示を控える。 |
| INDEPENDENT_STATEMENT | 本文はあるが既存の非NONE actに該当しない合法な独立発言・短い同意等。 |

これは排他的な心理分類や内部推論の要求ではない。最初の3理由だけでは合法な本文付きNONEを塞ぎ得るため最後を残す。どのreasonも、本文に実際の質問・回答・反論等がある際のラベル不一致を免除しない。自由文reason、OTHER、推論説明field、enumの追加は不可。語義の補足promptやschema descriptionも追加せず、実験因子をこのclosed fieldの追加に限定する。

decision.kind=noneとspeech_act.kind=NONEは別概念。reasonは後者にだけ追加し、decisionや他のreason fieldを変更しない。非NONEの必須field、根拠参照、enum、oneOf枝順、既存required順、messages、model、sampling、wireのsort_keysを維持する。候補から今回のreason定義だけを戻したschemaがbaselineと完全一致し、response_format以外のbodyが完全一致することを32case全件で確認する。

## 3. 原本・実験有効性・旧契約の三段階

1. providerのfinal contentと実送信bodyをOwner専用rootへ原形保存し、raw SHA-256を確定する。rawの書換え、reason補完、再生成はしない。
2. JSONの重複key、非有限数、構文不正を拒否し、候補body内の実際のschema全体を既存依存 `jsonschema.Draft202012Validator` で厳密検査する。schema自体もprepare時にcheckする。remote refは使用せず、既存local $defsだけと確認する。error.message、instance、pathをpublicへ出さない。
3. 合格時だけdeep copy上の厳密path `discussion.speech_act` を扱う。kind=NONEのときだけreasonを1個削除し、その他の値は変更しない。非NONEは変換なし。変換結果を旧baseline projectionと既存 `parse_llm_output`／`screen` に渡す。既存validator・構造/意味チェックを緩和しない。

候補schema不合格ではadapterを実行せず、`candidate_schema_pass=false`、`legacy_contract_pass=null`、`adapter_status=NOT_APPLIED`を保存する。candidate schemaは合格しても旧validatorの意味整合で失敗し得る。これは `legacy_contract_pass=false` として保存し、実験schemaの成功と混ぜない。旧validator合格も意味品質の合格にはならない。

public各rowにはcase_id、baseline/candidate入力hash、raw/adapted出力hash（未生成はnull）、candidate_schema_pass、legacy_contract_pass、adapter_status、固定reasonまたはnull、既存固定screen値を保存する。adapter_statusは `NOT_APPLIED` / `NONE_REASON_REMOVED` / `UNCHANGED_NON_NONE` のみ。候補の従来structural_pass欄を使うなら `candidate_schema_pass AND legacy_contract_pass` とし、元の2判定も必ず保持する。

独立Reviewerは元入力・raw final contentを対象に本文とact/reasonの整合を判定する。adapter後の成功だけでrawを採点せず、reasonはモデルの自己申告として扱う。baseline rawにreasonを補って候補schemaへ通す行為は禁止。比較対象の旧baseline意味判定は再採点せず再利用する。

## 4. 最小実装境界

実装候補fileは次の3単位に限定する。新たな汎用runnerやproviderを作らない。

- `scripts/phase6_none_reason_probe.py`（新規pure helper）: `candidate_body(baseline_body)` と `assess_candidate(case, baseline_projection, body, raw)`。body copy/schema限定差分、厳密検査、adapter、固定結果を担当。process・HTTP・環境変更を持たない。
- `scripts/phase6_model_comparison.py`: `prepare(out, *, experiment='baseline', baseline_source=None)` に固定値 `none_reason_v1` を追加し、その場合だけqw9と32caseをprepareする。既定baseline動作を維持。runはplanの固定experimentを使い、未定義値・他profileを拒否。candidate body生成と候補screenだけを切替え、source freezeには新helperを含める。experiment選択はprepare時だけ、run時上書き不可。CLIに追加する値もこの2値に閉じる。
- `tests/test_phase6_none_reason_probe.py`（新規）と必要な既存comparison focused test: 下記の契約を非LLMで検証する。

凍結manifestにexperiment/version、baseline plan/results/独立評価artifactのhash、case対応32件、baseline入力hashとcandidate入力hash、candidate schema hash、現在source/model/runtime DLL/argvのidentityを保存する。旧source mapと現在mapはrunner/helper変更で異なるため、旧全map一致を要求しない。既存製品・fixture・projection・samplingを定義するsourceは旧hashと一致を要求し、許容差分fileを今回の3単位へ限定して凍結する。未知の差分は生成前停止。実行中の現在source一致検査は既存どおり維持する。

baseline_sourceはMainが確認したT427 qw9の32件＋T428独立判定に限定。T406等の旧条件baselineや他modelを自動探索しない。case_id重複・不足、raw hashと評価参照の不一致、32/32のbaseline共通入力hash不一致、model/runtime/settings差があれば `BASELINE_MISMATCH` で停止。Mainは現在入力hash32/32とtool sourceの照合済みだが、実装後のfreezeでも再現可能な検査として保持する。旧baseline原文の再読・再生成をprepareの必須作業にしない。新source差分がこの設計境界かは独立Reviewerが確認する。

## 5. 有限実行・保存・停止

新規生成はqw9/C2だけ最大32call、既存baseline生成0、retry0、repair0。同じcaseの再送なし。専用outputの排他的claimをprovider起動前に作り、失敗時も保持する。claim名・task metadataにはC2の実行packet IDを使い旧T424結果へ混在させない。

既存のloopback8082、slot1、request同時数1、context8192、seed4242026、max_tokens512、temperature0.2/top_k40/top_p0.95/min_p0.05、penalties、cache_prompt=false、thinking=false、native GGUF template、qw9 ngl99を継承する。request60 REAL秒、model1200秒（loadを含む）、load180秒、所有process cleanup各25秒、outer1320秒を維持する。生成callを送信前に消費記録し、前回途中終了を自動再開しない。

実candidate bodyのrendered token数＋512＋1<=8192を各call前に確認。response_format変更がtemplate出力を変える可能性は記録し、schema追加に伴うtoken差を隠さない。usage不一致、runtime/入力hash差、thinking不一致、transport failureは全体停止。schema/旧契約/意味不適合は観測値として記録し、残りcaseを継続する。

process objectとlistener所有を確認し、起動したprovider/monitorだけをfinallyでcleanupする。Owner ACL、外部送信禁止、HTTP応答上限、private runtime/GPU/raw保存、public固定codeだけという既存境界を再利用する。推論contentは要求・保存・採点しない。private原文を通常log/tool表示へ複写しない。外側timeoutとReviewer→実装審査→独立Tester→独立意味判定のgateはMainの実行packetで具体化する。

## 6. focused検証・独立判定

非LLM focused必須項目:

1. 全32caseの元body不変、候補からreasonだけ戻したschemaの完全一致、schema以外のbody完全一致、非NONE枝／順序不変。baseline既定のwire hash不変。
2. 4 reasonそれぞれのNONEを受理し、missing/unknown/null/numeric/extra fieldを拒否。非NONEへのreason追加、別pathのreason、重複JSON key、NaN、壊れたJSONを拒否。無関係fieldをadapterが消さないことを確認。
3. candidate全schemaに適合したNONEをadapter後に旧parserが受理し、同rawを旧parserへ直接渡すと拒否する対照。非NONEは深い値一致を維持。合法なdecision noneと本文付きNONEの双方の表現可能性を合成focusedで確認する。
4. schemaでは許すが旧validatorが拒否する参照/整合違反を通過させない。raw・baseline projectionを変更しない。candidate不合格でadapter未実行、旧contract結果null。
5. baseline artifact mismatch、case重複/不足、source/model/runtime変更、未知variant、C2で他profile、2回目claimをネットワーク前に拒否。HTTP timeout/停止/所有cleanup/secret-safe例外の既存regressionを維持。focusedはmockで行いrealprocessを起動しない。

独立評価は既存32case全件を分母とし、構造不合格や未生成を除いて率を良く見せない。HARD/SEMANTIC/STYLE、act/text不一致、FABRICATED_EVIDENCE、NONE reason妥当性、G14合法NONE対照をcase対応で記録する。未知はUNKNOWNのまま。欠測がある場合は完全比較成功としない。

採用候補判定は全条件を満たすこと: (a) 32件完走・原本/hash/cleanup整合、(b) act/text不一致数が旧baselineより減少、(c) HARD fail数が増えずUNKNOWNも増えない、(d) FABRICATED_EVIDENCEが1件でも増えたら他の改善と相殺せず不採用、(e) G14等の合法なNONEを失わせた新規違反がない。reasonが説明として不整合なら別固定理由で記録し、C2有効性の判断へ含める。非NONE数とSEMANTIC改善だけでは採用しない。候補に残っても製品へ適用する承認ではなく、32件1seedの有限結果であり因果証明・母集団性能の主張は禁止。

## 7. 順序に関する限界・C1の将来境界

sort_keysはproposalだけでなく各speech_act枝のpropertiesにも適用される。NONEはkindが先になる一方、非NONEはaddresseeやconfidence等が先になる場合がある。C2ではこの非対称性も変更しない。結果からモデル能力を完全に除外したり、1出力の保存順一致からgrammar強制やNONE固着の因果を確定したりしない。必須理由の追加による効果自体も現時点で未立証。

C2が不足なら、C1は別packet・別freeze・独立審査・有限実行として元baselineから開始する。C2 reasonを残したまま順序変更しない。C1はschema部分木のwire順だけを扱い、現在のalphabetical観測からgrammar因果を断定しない。本文からラベルを後付けするC6、根拠必須fieldを減らすC4、2call化C5は今回対象外。
