# Phase 6 CO Consistency 限定schema設計

Task: T485
Status: DRAFT

## 1. 目的と既知原因

本候補はoffline test-only helperで、`CO_OPPORTUNITY`における既存legacy validatorのcross-field受理集合を生成schemaへ同値に表す。LLM生成、保存run変更、再採点、製品変更は行わない。

既知のgapは、現C5 schemaがtop-level `decision`と`discussion.co_judgment`を個別には閉じる一方、両者の等値制約を表していないことである。公開synthetic fixtureでは`none + DECLARE`をschemaが受理し、legacy `decision.py:571`が`VALUE_NOT_OFFERED`で拒否する。G15-1のsafe boolean証拠も同じ分類を示す。

- `logs/t476-continuation/gb1-g15-consistency.json`: SHA-256 `583f9b12eb08347789933ed17a6c1513c5079b6d5b92cef577d2e75c55da4acb`
- `logs/t476-continuation/co-consistency-synthetic-proof.json`: SHA-256 `3e8fc3cc2970ceb16eee0c3c8514036f4bf28c7649fe0140ba05d5e3472d78ff`

oneOf枝順変更、NONE誘導、adapter補正ではなく、既存validatorの次の同値制約だけをschemaへ有限展開する。

- 他fieldがschema/legacy双方で合法であるCO相関軸に限り、`NoDecision` iff `decision_kind=none`かつ`option_id=null`かつCO judgmentは`SILENCE`または`DEFER`、selected option/claimed roleはいずれもnull。
- 同じ限定matrixで、`CoDeclareDecision(option, role)` iff `decision_kind=co_declare`かつ同じoption、CO judgmentは`DECLARE`かつ同じoption/role。

## 2. 実装境界とpublic API

新規 `scripts/phase6_co_consistency_probe.py` だけにpure adapterを置く。既存GB1/C5 helper、runner、83-source manifest、保存artifactを変更しない。

```python
co_consistent_schema(candidate_schema, projection) -> dict
output_body(baseline, choice, projection) -> dict
validate_final(raw, choice, projection) -> dict
```

`output_body`は既存GB1 helperが作るC5 candidate bodyをdeep copyし、`co_consistent_schema`だけを適用する。`validate_final`はstrict JSON、新schema、既存GB1/C5 kind/basis/legacy validatorを全て通し、値を修復・補完しない。入力はplain JSONへ閉じ、unknown schema shape、remote ref、unknown keyword、open object、不正required、重複branchを`SCHEMA_SHAPE_CHANGED`で拒否する。

triggerは`projection.discussion_capture.trigger.kind`だけから取得する。role名や戦略によるbranchを作らず、hostはoption、role、CO judgment、decision、本文を選ばない。

## 3. CO schemaの有限直積

`CO_OPPORTUNITY`だけ、rootを次のclosed `oneOf`へ置換する。共有`$defs`、speech act、updates、strategy、reaction、C5 grounding制約は同じobjectをdeep copyして参照する。

1. `NoDecision × SILENCE`
2. `NoDecision × DEFER`
3. 各offered `co_declare` optionと各`claimed_role_id`について、`CoDeclareDecision(option, role) × DECLARE(option, role)`

DECLARE枝ではdecisionの`option_id`と`claimed_role_id`、discussionの`decision_kind`、`option_id`、`co_judgment.selected_option_id`、`co_judgment.claimed_role_id`を全て`const`で同じtupleへ固定する。commentと他discussion fieldの既存制約は変更しない。SILENCE/DEFER枝はdecisionを既存none branchへ固定し、discussion optionとjudgment option/roleをnullへ固定する。

option順と各option内role順は既存schema内の順序を維持し、重複option ID、同一option内の重複role、重複`(option_id, claimed_role_id)` tupleをconverter前に拒否する。0 optionの場合は合法なSILENCE/DEFER二枝だけを保持する。DECLARE tuple数上限を64とし、root branch総数はSILENCE/DEFER二枝を加えた最大66とする。65 DECLARE tuple以上は切捨てず`CO_TUPLE_LIMIT`でoffline failureとする。上限は既存合法集合を狭めるprovider動作ではなく、本probeを実行不能として止める有限resource gateである。

通常の0/1/複数option fixtureは、公開synthetic `BrainInput`、action handle、CO captureから現行canonical projectorを通して構築し、0 matching `co_declare` optionを含むCO captureがconstructor/projectorで合法かをfocused testで先に証明する。64 DECLARE tuple fixtureも、optionごとの`claimed_role_ids`合計64が現行constructor/projectorを合法に通る場合だけcanonical fixtureと呼ぶ。現行上限により到達不能なら、plain schema inputによるhelper shape/resource fixtureへ明示的に分離し、canonical到達性の証拠として扱わない。

公式schema converterへ0/1/複数/最大64 DECLARE tuple（最大66 root枝）を通す。providerなしtool gateが記録するのはcanonical schema bytes、converter成功、grammar bytes、rule数等の有限compiler resource証拠である。grammar/token proxyをserver-native prompt context tokenと比較せず、今回のoffline helperではcontext 8192の採否を主張しない。実prompt contextを測る後続実験を設ける場合は、serialized message bytes、server-native rendered prompt、native tokenizerを別契約として新たに承認する。枝順だけを変えた比較やprovider生成は行わない。

## 4. 非CO exact不変

`INITIAL_CHAT`、`PEER_CHAT`、`PRE_VOTE`、`ABILITY`では`co_consistent_schema`が入力candidate schemaのdeep copyを返し、canonical JSON bytes/SHAがexact一致しなければ失敗する。CO以外のdecision kind、pre-vote reassessment、reaction、ability target、speech act、grounding、updatesの受理集合を変更しない。

CO triggerであってもspeech-act 7 kind、GB1 basis 0～2、C5 refs/claim tuple/empty-array rulesは不変である。このadapterはdecision/co_judgment consistency以外を閉じない。

## 5. acceptance matrix

focused testsは少なくとも次を網羅する。

- CO positive: NoDecisionとSILENCE/DEFERの双方、全offered option×roleのDECLARE tuple、複数option/role、合法speech kind 7値、basis 0/1/2。
- CO negative: none+DECLARE、co_declare+SILENCE/DEFER、decision/discussion kind差、option差、role差、null/missing/extra、offeredでないoption/role、decision comment以外の補正試行。
- action/trigger: 5 trigger、none/chat/vote/ability/co_declareの既存decision、全triggerで不対象field null契約を回帰する。
- shape: unknown keyword/ref、missing/duplicate/open branch、不正required、unknown decision/co judgment、duplicate option/role/tuple、65 DECLARE tupleをfail-closedにする。
- CO consistency equivalence: evidence、actor、reaction、text bound等の他fieldを既存schemaとlegacyの双方で合法な固定値にしたfixtureを基準とし、decision kind、option、role、CO judgmentの相関軸だけを変えるmatrix内でschema PASS iff legacy PASSを確認する。adapterがlegacy rejected tupleを変更して受理させない。
- legacy-only semantics: evidence authority、actor、reaction、text bound等の代表negativeは新schemaが受理してもよいが、`validate_final`の既存legacy段が従来どおり拒否することを別群で回帰する。schemaだけを最終受理とせず、これらをCO consistencyのiff対象へ含めない。
- bytes: 非CO全fixtureはinput/output schema canonical bytes exact一致。COでは変更pathがroot decision/discussion correlationだけで、`$defs`とC5差分のhashは不変。
- converter: canonical projector由来かshape-onlyかをfixtureごとに表示し、0、1、複数、64 DECLARE tuple（root枝2/3以上/66）を公式converterで成功させ、有限grammar/hash/countを保存する。65 DECLARE tupleはconverter前拒否する。compiler resourceをprompt context tokenとして記録しない。
- privacy: raw、本文、秘密、保存GB1 outputを読まず、公開synthetic fixtureと生成した構造fixtureだけを使う。

全trigger/action/kindのauthority positiveと既存代表negativeを回帰し、既存testをimportして結果だけ流用せず、新adapter経由のschema/legacy同値を測る。

## 6. source・実行・停止条件

実装scopeは新helper、新focused test、handoffだけである。source manifestには新2fileと依存するGB1/C5 helper exact SHA、製品projection/validator exact SHA、公開synthetic proof SHAを固定する。既存GB1/C5/SC2 sourceやresultをsource deltaへ追加変更しない。

独立design review承認後にMainがoffline実装し、独立tool reviewでschema equivalence、converter、非CO exact bytesを確認する。provider/model/game/通常runは0、生成call 0、旧raw再読・意味再採点0である。

次のいずれかで完了扱いにせず設計へ戻す。

- legacy合法fixtureを新schemaが拒否する。
- CO consistency matrix内でlegacy不合法なtupleを新schemaが受理する。
- 非CO canonical schema bytesが変わる。
- COでdecision/co_judgment以外のconstraint hashが変わる。
- converterが最大64 DECLARE tuple（66 root枝）を処理できない、または承認したcompiler resource上限を越える。
- unknown shapeや65 DECLARE tupleを切捨てて続行する。

このoffline修正はGB1の意味採否や新generation権限を与えず、既存G15-1保存resultも変更しない。
