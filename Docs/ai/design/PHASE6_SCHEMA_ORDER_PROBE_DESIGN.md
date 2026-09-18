# C1 schema properties順の単独実験

Task: T434
Status: DRAFT

独立Reviewer承認前。設計だけであり実装・実行・製品採用の承認ではない。

## 1. 比較契約

C2は独立判定で不採用（act/text不一致23→26、HARD fail14→17、捏造0→0、全32件NONE）。この結果はMainの確定情報を再利用し、再採点しない。提案 `PHASE6_SPEECH_ACT_NONE_PROPOSAL_20260919.md` §4の次段階として、T427 qw9 baselineとC1だけを比較する。

新規32生成、旧baseline再生成0、retry0、repair0。製品code/schema/parser、prompt、fixture、model、sampling、context、grounding、oneOf枝順は不変。C2 reason追加、C3枝順変更、非NONEの必須field削減は混ぜない。旧baseline32入力とT428独立評価を既存hash bindingで再利用する。

## 2. 並べ替える場所と手順

対象はrequest内の次のdictだけ。

`response_format.json_schema.schema.properties.discussion.oneOf[i].properties`

全proposal枝のpropertiesを、その枝の既存required配列の順に復元する。配列自体は変更しない。現行著者順は次の12名で、speech_actは5番目になる。

```text
schema_version, base_revision, decision_kind, option_id, speech_act, reaction,
assessment_updates, claim_updates, relation_updates, strategy_update,
co_judgment, pre_vote_reassessment
```

固定手順:

1. 元bodyは `body_for(project(case, 'baseline'), qw9_model)`。C2のcandidate_bodyを経由しない。
2. `old_bytes = wire_bytes(body)`、`ordered = json.loads(old_bytes)`。これで対象外の全dictは従来のalphabetical順へ固定される。
3. 上記pathだけを辿る。discussion.oneOfは非空、各枝はclosed object、properties key集合と重複のないrequiredが上記12名と一致し、required順も上記と完全一致することを検査する。異なる場合は `SCHEMA_SHAPE_CHANGED` で生成前停止。requiredから順序を取得し、各propertyの値を一切変えずdictを組み直す。
4. `json.dumps(ordered, ensure_ascii=False, sort_keys=False, separators=(',', ':'), allow_nan=False).encode('utf-8')` で候補bytesを一度作る。元bodyを変更しない。
5. 候補bytesをJSONとして読み戻した値が元bodyと完全一致すること、従来wire_bytesで再直列化するとold_bytesへ戻ることを検査する。候補bytesはold_bytesと異なり、対象外のdict key順は同じでなければならない。

全dictの挿入順を無条件に保存するだけの変更は不可。現在のbodyはschema構築時にcanonical化されるため、全体sort_keys=falseだけでは著者順を復元できず、別dict順も巻き込む恐れがある。この手順ではJSON値と全array順は同一で、上記propertiesの列挙順だけが変わる。

schemaのroot properties、decisionの枝、$defs、speech_act.oneOf、各speech_act枝内propertiesは従来alphabeticalのまま。NONEでkindが早く、他actではaddressee/confidence等が早い非対称性も維持する。C1はproposal内speech_actを11番目から5番目へ置く限定実験であり、speech_actをroot先頭へ移動したり本文を後方へ動かしたりしない。rootのdecision→discussion順も不変。

## 3. JSON値hashと送信bytes hash

既存 `digest` / `common_hash` はsort_keys=trueであり、挿入順を無視する。この意味を変更しない。

| 値 | C1の期待 |
| --- | --- |
| `input_sha256` / `candidate_input_sha256` | 既存のcanonical JSON hash。baselineと等しい。 |
| `common_input_sha256` | modelを除いたcanonical hash。baselineと等しい。 |
| `schema_sha256` | schemaのcanonical hash。baselineと等しい。 |
| `baseline_wire_sha256` | SHA256(old_bytes)。既存baselineのinput_sha256と等しい。 |
| `candidate_wire_sha256` | SHA256(candidate_bytes)。baseline_wireとは異なる。 |
| `wire_size_bytes` | 実candidate bytes長。順序だけなのでbaselineと等しい。 |

hashだけでは差分場所を証明できないため、prepareの限定変換・deep equality・key順検査も必須。freezeは32case各々にこれらhash、target property order、serializer versionを保持する。profile/argv/runtime DLL/model/source、旧baseline plan/results/annotationsの既存bindingを維持する。runは生成前に32caseを再構築してcanonicalとwire双方を照合する。canonicalだけ一致してwireが違う場合は `FROZEN_WIRE_CHANGED` で停止する。

既存source bindingの差分除外は明示allowlistを実験別に扱う。T427以降に導入済みのC2 helper/testとrunner差分は由来を記録し、現在hashをfreezeする。C1実装で追加変更してよいのは下記C1単位だけ。製品・fixture・context_probe・sampling等の旧baseline一致は維持する。C2分岐を使わないことを検証し、未知file差分を広い除外規則で通さない。

## 4. HTTPと原本保存

`scripts/phase6_context_probe.py::wire_bytes`は不変。既存comparisonの `_request` / `request` に任意のimmutable `wire_payload: bytes | None` をkeyword-onlyで追加する。既定Noneは従来のwire_bytes(body)。C1でだけ固定bytesを渡す。呼出側はbodyとのJSON値一致と凍結hashを検査し、HTTP直前にも一致を検査する。`json=body`や再sortを挟まず、httpxの `content=wire_payload` に直接渡す。

override許可は `/apply-template` と `/v1/chat/completions` に限定し、bodyなし／他endpointへの指定は固定code `WIRE_OVERRIDE_INVALID` で拒否する。serializerを選ぶglobal mutable stateや環境変数は導入しない。baseline/C2は既定経路を維持する。

`count_prompt(body, *, wire_payload=None)` の最初の/apply-templateに、生成と同じcandidate bytesを渡す。schemaなしのbare apply-template、tokenize、health/props/slotsは従来経路。これにより実際のschema順で得たrendered promptをtokenizeし、生成前にtokens＋512＋1<=8192を確認する。provider内部で順序が再構成される可能性は残るため、受理やtemplateのtoken数だけで生成順強制を証明したことにしない。

生成送信前にOwner専用rootへcase ID由来のfilenameで `.request.bin` を排他的作成し、candidate bytesそのものを保存する。filenameへ自由入力を使わない。保存成功・hash一致を確認し、既存rowのcall消費記録をflushしてから同じbytesを一度だけ送信する。保存失敗では送信せず停止。開始済みrowや残存claimから自動retryしない。

既存private raw.jsonlにはinput JSON値・final contentを保存し、wire fileとのhash対応も持たせる。JSONの再dumpは実送信原本の代替ではない。保存前hashは送信予定の証拠であってtransport成功の証明ではなく、STARTED/GENERATED/ERRORを区別する。publicは固定experiment/case/順序・hash・数値・enumのみで、wire本文/private path/token列を出さない。

出力は変換せず従来の `screen(case, baseline_projection, raw)` を使う。C2 adapter、reason抽出は呼ばない。C1固有の補助観測としてrawからdiscussionの既知12 keyの順序とspeech_act位置を取得してよい。未知keyは文字列を出さずbool、欠落/JSON不正は位置nullとし、順序観測を意味PASSにしない。

## 5. 最小file/API差分

- 新規 `scripts/phase6_schema_order_probe.py`: pure `ordered_wire_bytes(body)` と限定shape/order検査。HTTP、process、baseline原本の読取を持たない。
- `scripts/phase6_model_comparison.py`: 固定experiment `schema_order_v1` をprepareだけで選択可能にする。qw9だけ、task T435、独立output/claim。新helper/testをsource freezeに含める。run時上書き不可。canonical bodyはそのまま、wire生成・freeze・上記HTTP引数・private bytes保存だけを追加する。出力採点はbaseline経路。
- 新規 `tests/test_phase6_schema_order_probe.py` と既存comparisonの必要なfocused regression。既存baseline/C2分岐の挙動は保持する。製品schemaやcontext_probeへserializer変更を入れない。

prepareには旧baseline sourceを必須とし、既存BASELINE_FILESで32件のcase/input/final-output/evaluation対応を確認する。C2結果をbaselineとして受け入れず、旧baseline原文の再読・再採点・再生成を要求しない。Mainの別集計はこの比較契約を拡張しない。

## 6. 非LLM focused

1. 32case全件: JSON値・canonical input/schema hash・全array順同一、wire hashだけ変化、byte数同一、対象proposal propertiesのみ12名の著者順、speech_act5番目。元body不変・再構築bytes決定的。
2. 対象外の全dictが従来順であることを再帰的に比較。$defs/speech_act branches内properties、root/decision、required/oneOf/enumの順序不変。C2 reason不在。schema全体同一なので合成の合法NONE/非NONEは旧schema/parserで同じ受理判定。
3. shape変更、properties/required重複・不足・extra、未知experiment、他profile、C2 baseline、canonical一致かつwireだけ異なるplan改ざんを生成前に拒否。
4. httpx MockTransportで `/v1/chat/completions` の実 `request.content` が凍結candidate bytesと完全一致することを検証。/apply-templateへも同bytes、他endpoint/bareへ既定bytesが渡る対照を検証。既存baseline/C2の送信bytes不変も確認。
5. wire保存内容・hashと捕捉したtransport bytes一致。保存失敗・source/runtime/input/wire差・claim再利用時に生成call0。途中HTTP failureの消費記録、timeoutと所有cleanup、private内容を含む例外の固定code化を既存mock regressionで確認する。

focusedにrealprocess/LLMは不要。設計審査→Main実装→独立tool審査の後、T435独立Testerが一回測定し、独立Reviewerが原本の意味を評価する。

## 7. 有限予算・判定・限界

既存C2設計§5〜7の共通運用だけを継承: loopback8082、同時request1/slot1、context8192、qw9 ngl99、seed4242026、max_tokens512、temperature0.2/top_k40/top_p0.95/min_p0.05、penalties、cache_prompt=false、thinking=false、native templateを不変。REAL request60/load180/model1200（load含む）/所有cleanup各25/outer1320秒。所有process/listener確認、Owner ACL、応答上限、送信前call消費、finally cleanupを維持。各case retry/repair0、失敗claim保持。

runtime/入力/wire/usage不一致、thinking不一致、transport・保存障害は停止。構造/意味不適合は測定値として残し残りcaseを継続。内部推論は要求・保存・採点しない。旧baselineの独立判定を再利用し、構造不合格・未生成を32件分母から外さずUNKNOWNを維持する。

採用候補の必要条件: 32件完走と証拠/cleanup整合、act/text不一致が23件から減少、HARD failが14件から増えずUNKNOWNも増えない、捏造は0件を維持（増加時は相殺せず不採用）、合法NONE/G14へ新規違反を作らない。非NONE出現・SEMANTIC改善・出力順変化だけでは成功にしない。比較不成立・欠測はUNKNOWN、条件を満たしても製品適用の承認ではない。

32件1seedであり母集団性能・統計的優劣を主張しない。wire順変更が実測出力順へ反映されなければ、その事実を記録し、追加retryや別変換を同runに混ぜない。反映された場合でもNONE固着の唯一原因やgrammar強制の完全証明にはならない。モデル能力とprompt負荷の影響は残る。次の変更は独立したscope判断とfreezeを必要とする。
