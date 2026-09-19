# speech_act kind-first単独probe

Task: T437
Status: DRAFT

独立承認前。製品未変更、実LLM未実行。

## 目的と差分

元T427 qw9の32件・T428独立評価を再利用し、speech_act枝内のkind位置だけを変えた新32件と比較する。C1/C2の不採用を再審査しない。C1のproposal順変更、C2 reason、C3枝順変更、prompt追加、grounding緩和、モデル交換を混ぜない。

変更pathは `response_format.json_schema.schema.$defs.speech_act.oneOf[*].properties` のみ。各枝でkindを先頭、残りをalphabeticalにする。NONEはkindだけなのでその枝は変化なし。oneOf枝順、required配列、全schema値、他dict順、全array順を維持する。discussion proposal propertiesは元baselineのalphabeticalへ戻り、C1順を使わない。

## 純粋なwire変換

新helper `scripts/phase6_kind_first_probe.py::kind_first_wire_bytes(body)` は次だけを行う。

1. `original=wire_bytes(body)`、`ordered=json.loads(original)` で従来alphabetical順を復元する。元bodyは `body_for(project(case,'baseline'), qw9_model)`。既存C1/C2 helperを経由しない。
2. 上記pathのoneOfが現行7枝（NONE/CLAIM/QUESTION/ANSWER/REBUTTAL/OPINION_CHANGE/RELATION_HYPOTHESIS）の元の順であることを確認する。各枝はclosed object、kindがconst、requiredにkindが一度だけあり、propertiesとrequiredのkey集合が一致する。NONEはkindのみ。ずれは固定 `SCHEMA_SHAPE_CHANGED` で生成前停止。
3. 各propertiesを `kind`、残りkeyのsorted順で作り直す。kindのconstや他propertyの値を触らない。
4. ensure_ascii=false、sort_keys=false、compact separators、allow_nan=falseでUTF-8 bytesを作る。JSON値が元bodyと同一、従来wire_bytesで再encodeするとoriginalへ戻る、長さ同一、全bodyではbytes差ありを確認する。

このprobeはJSON Schemaの受理集合を変更しない。動的refs/options、非NONE必須fieldは元のまま。出力adapterも不要で、rawを従来projection/parser/screenへ直接渡す。NONEnessやkind出現を機械的に意味PASSへ変換しない。

## runner・freeze・保存

既存 `scripts/phase6_model_comparison.py` にprepare専用固定experiment `speech_act_kind_first_v1` を追加する。qw9のみ、task T439、独立output/claim。runでexperimentを変更できない。既存C1のwire経路を共有し、変換関数と凍結target metadataだけを実験別に選ぶ。製品code、context_probeのwire_bytes、既存C1/C2変換は不変。

既存canonical input/common/schema hashはbaselineと同値を維持し、別 `candidate_wire_sha256=SHA256(payload)` はbaselineと異なる。32case各々にbaseline/candidate wire hash、byte数、serializer version、枝ごとのkindとproperty順をfreezeする。run開始前に全件を再構築して両hash群・target順を照合し、送信直前のbodyとも一致を確認する。

元T427のplan/results/annotationsを既存BASELINE_FILESで束縛し、case ID・入力hash・出力hash・評価対応32件を維持。model/runtime DLL/argv/settingsは同一。`source_identity(experiment='baseline')` はkind-first時だけ新helper/testを追加し、既定baseline/C1/C2のsource集合は従来のままとする。prepare/run/終端のsource照合でkind-firstだけ明示引数を渡し、他経路の呼出しは変えない。旧baselineとの差分除外はkind-firstが既知5path＋新helper/testのexact7path、C1/C2は既存exact5pathを維持する。追加の由来と対象全fileの現在hashをplanへ記録し、未知pathを許すdirectory wildcardは使わない。今回C1/C2 helper/testの内容変更はしない。この限定deltaはT438独立Reviewerの承認に基づく。

再利用するHTTP契約: immutable payloadを `/apply-template` と `/v1/chat/completions` の `content` にそのまま渡す。他endpointとbare apply-templateは既定serializer。同じpayloadをOwner専用rootのcase別 `.request.bin` へ送信前に排他的保存し、保存hashを確認する。実送信原本をJSONの再dumpで代替しない。raw.jsonlはinput JSON値、final content、wire hashの対応を保持する。publicは固定ID/enum/順序/hash/数値だけ。

旧baseline rawの再読・再生成・再採点は不要。caseのcall消費記録を送信前にflushし、claimと途中失敗を保持、retry/repair0。保存・hash不一致は送信せず停止。provider内部が順序を維持するかは未知であり、wire保存は意味品質の証明ではない。

## 有限実行とfocused

新規最大32生成。既存qw9設定、loopback8082、同時1/slot1/context8192/max_tokens512/seed4242026、sampling/penalty/cache_prompt=false/thinking=false/native template/ngl99不変。REAL request60/load180/model1200（load含む）/所有cleanup各25/outer1320秒を維持する。実bodyのrendered tokens＋512＋1<=8192、usage一致、source/runtime一致、listener所有、finallyで所有provider/monitorだけcleanupを再利用する。transport/hash/token/thinking不一致は停止。構造/意味不適合は測定として記録して残りcaseを継続する。内部推論は要求・保存・採点しない。

新規 `tests/test_phase6_kind_first_probe.py` と既存runner focusedで、非LLMにより次を確認する。

- 32case全件のcanonical値/hash同一・wire差・byte数同一・元body不変。変わったdictがspeech_act各枝propertiesだけで、kind先頭/残りsorted。NONE枝不変、C1 proposal順なし、C2 reasonなし、required/oneOf/grounding不変。
- shape変更、kind欠落/重複/未知枝、未知experiment、他profile、baseline artifact不一致、canonicalだけ一致するwire改ざんを生成前拒否。
- MockTransportで保存payload・/apply-template・生成request.contentの完全一致とhashを確認。baseline/C1/C2既存送信bytesは不変。
- source/runtime差、wire保存失敗、claim再利用時に生成0。timeout、送信前消費、所有cleanup、secret-safe例外を既存mock regressionで維持。実processはfocusedで起動しない。

## 独立評価と判定

Reviewer設計承認→Main実装→独立tool審査→T439独立Tester一回測定→独立意味判定。全32件を分母にし、未生成/構造不適合を除かずUNKNOWNを保持する。基準値は旧独立annotationに束縛し、以下を**個別に**比較する。

| 指標 | relative improvementの必要条件 |
| --- | --- |
| act/text不一致 | 旧23件より減少 |
| HARD fail | 旧14件以下 |
| 根拠捏造 | 旧0件から増えない |
| 不当な真の秘密開示 | 旧5件から増えない |
| 現stateとの矛盾 | 旧STATE_CONTRADICTION 3件以下（Main提供の既存集計） |
| 不正ability／ability整合違反 | 旧ABILITY_CONTRADICTION 0件以下（Main提供の既存集計） |

各軸のUNKNOWNも増やさない。秘密減少とstate増加等を相殺しない。state/abilityの数を0と推測せず、既存の固定分類の集計からprepare前にbaseline値をfreezeする。旧annotationにその分類の判定がなければUNKNOWNとし、追加原文再採点を勝手に行わず成功判定を保留する。G14等の合法NONEに新規違反がないことも必須。非NONE率、SEMANTIC改善、順序変化だけでは採用候補にしない。

上記は研究候補の相対改善条件であり、製品合格ではない。D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0は製品へ進む必要条件として維持する。baselineに残る違反を許容したまま製品へ進めない。改善があってもschema/実装の製品採用・短い実gameには別の独立gateと許可が必要。通常game/Master Run/Phase7は行わない。

32件1seedの有限結果で、grammar強制の因果やモデル能力の完全除外を主張しない。Mainのconverter静的分析は別根拠として扱い、probe実測を代替しない。token accountingの仕様改善は独立ticket候補であり、この実験中に計数式・budgetを変更しない。
