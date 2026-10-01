# Phase 6 S4 共通provenance整合 詳細設計

Status: APPROVED
Task: T552
Approved-By: Independent Design Reviewer
Approved-Content-SHA-256: e373596715604b22325a83579b154113e8d96dabe209065e2fb6f934079baec6
Approval-Scope: `S4_COMMON_PROVENANCE_V1` のtest-only型、selection/surface association、純粋offline API、決定表、公開synthetic test、移行gate
Amendment-Approved-By: Independent Design Reviewer
Amendment-Content-SHA-256: d7a537cd74dd21e6eb459f013d1409422072963f5ad9bec3acb1519a11023db2
Amendment-Scope: `EXPLICIT_AUTHORITY_ASSERTION` のS4専用語義明確化
Scope: 将来のtest-only評価契約。T550/T551のraw、annotation、判定は変更しない。

## 1. 規則と境界

将来metricを `S4_COMMON_PROVENANCE_V1` とする。旧S4、annotation、`MEASUREMENT_INVALID` は不変で、旧baselineを再採点しない。

1. 採点対象はaccepted public surfaceだけである。ただしv2 messageは本文しか持たないため、同じaccepted finalへ生成時に束縛された**selection envelope**をprovenance入力として付随させる。plan本文は採点しない。
2. envelopeから運べるのは実際に選択されたpublic fact、`disclose_id`、`claim_id`だけである。未選択候補、role語、本文の意味、モデル意図からhostがrefを抽出・補完しない。
3. refなしのCO・結果主張は `CLAIMED_REPORT, NOT_APPLICABLE` である。真偽や本人結果らしい表現だけで権威laneへ昇格させず、合法な騙りを保つ。
4. §3.4の「記録が無いのに存在すると表明」は、閉じたidentityの明示refが0件、または別recordへの偽装を一意に証明できた場合だけ適用する。複数一致は捏造の証明ではない。
5. 未選択・非PUBLIC・authority禁止はS1の不許可開示事実であり、それだけでS4 FAILにしない。

LLM schema、既存runner、model、claim選択責務は変えない。新capture、PF3、generic lifecycle、認証基盤を作らない。helperはtrusted synthetic fixtureを検査するだけで、原本取得・trust確立は既存custodian gateに留める。

## 2. 閉じた型

実装はfrozen dataclassまたは同じexact dict shapeを使う。未知/欠落key、enum外、`type(x) is bool/int/str` 違反（boolをintとして扱う場合を含む）を拒否する。

全digestは `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")` のSHA-256 lower hexとする。dict keyはUnicode code point順、array順は保持、整数はJSON十進表現、float/nonfinite/重複keyを禁止する。surface hashだけはaccepted surfaceの保存raw bytesを直接hashする。

自己hashを含まない対象を固定する。`row_sha256` は `{rubric_version,blind_id,surface_sha256}`、`catalog_sha256` は `TrustedBindingV1` のtupleを `binding_id` 昇順にしたpayload、`canonical_set_sha256` は `CanonicalRecordV1` のtupleをcanonical identity順にしたpayload、`accepted_plan_sha256` はaccepted plan exact dict、`selection_envelope_sha256` は次の `SelectionEnvelopePayloadV1` をcanonical wire化した値である。真正性は既存custodianが担い、helperは再計算一致だけを見る。

trusted synthetic入力の最小shapeを固定する。

```text
AcceptedPlanViewV1:
  kind = CHAT_PLAN
  fact_ids: tuple[str, ...]
  disclose_ids: tuple[str, ...]
  claim_id: str | null

SurfaceClaimV1:
  claim_id: str
  explicit_refs: tuple[CanonicalIdentityV1, ...]

AcceptedSurfaceViewV1:
  kind = ABSENT | TEXT | STRUCTURED | TEXT_AND_STRUCTURED
  text: str | null
  claims: tuple[SurfaceClaimV1, ...]
```

planの各IDは非空str、各tuple内重複禁止、`claim_id` はnullまたは非空str。surfaceはTEXTならtext非空かつclaims空、STRUCTUREDならtext=nullかつclaims非空、TEXT_AND_STRUCTUREDなら両方あり、ABSENTならtext=null/claims空である。claim ID、各claim内refは重複禁止。自由文textはhash対象だがhelperは読まない。`accepted_surface: bytes` はこのsurface viewのcanonical wireと完全一致しなければならず、別JSON表現を受理しない。

抽出列を次で固定する。`explicit = claims順→各explicit_refs順`、`facts = fact_ids順`、`disclosures = disclose_ids順`、`claim = claim_id非nullなら1件`。期待envelopeはこの4列を連結した順で、originを順に `EXPLICIT_SURFACE_REF / SELECTED_PUBLIC_FACT / SELECTED_DISCLOSURE / SELECTED_CLAIM` とする。explicit itemはref一致、selected itemはID一致に加えTrustedBindingのsource_kindが順に `PUBLIC_FACT / ABILITY_RESULT / CLAIM` でなければならない。全itemの同数・同順・同origin・同値を照合し、欠落/余分/順序違い/別laneを `S4_INPUT_INTEGRITY` とする。TEXT surfaceからのexplicit列は常に0件である。hash一致とこの再抽出一致を両方要求する。

```text
RowBindingV1:
  rubric_version = "S4_COMMON_PROVENANCE_V1"
  blind_id: str
  surface_sha256, row_sha256, selection_envelope_sha256: 64桁lower hex
  catalog_sha256, canonical_set_sha256: 64桁lower hex

SelectionItemV1:
  provenance_id: str
  origin = EXPLICIT_SURFACE_REF | SELECTED_PUBLIC_FACT |
           SELECTED_DISCLOSURE | SELECTED_CLAIM
  selected_id: str | null
  explicit_ref: {record_kind:str, order:int, visibility:str} | null
  binding_id: str | null

SelectionEnvelopeV1:
  binding: RowBindingV1
  accepted_plan_sha256: 64桁lower hex
  items: tuple[SelectionItemV1, ...]

SelectionEnvelopePayloadV1:
  rubric_version, blind_id, surface_sha256, row_sha256,
  catalog_sha256, canonical_set_sha256, accepted_plan_sha256,
  items

MechanicalRecordV1:
  binding: RowBindingV1
  provenance_id: str
  lane = PUBLIC_FACT | AUTHORITATIVE_ABILITY | CLAIMED_REPORT | FORGED_REFERENCE
  origin: SelectionItemV1.origin
  ref_resolution = RESOLVED | NOT_FOUND | AMBIGUOUS | NOT_APPLICABLE
  selection = SELECTED | NOT_SELECTED | NOT_APPLICABLE | UNKNOWN
  actor_match = TRUE | FALSE | UNKNOWN
  visibility = PUBLIC | NON_PUBLIC | UNKNOWN
  authority = AUTHORIZED | FORBIDDEN | UNKNOWN
  canonical_value = 最小target/result view | null
  binding_status = COMPLETE | MISSING | CORRUPT

SemanticObservationV1:
  binding: RowBindingV1
  assertion = NONE | CLAIMED_RESULT | EXPLICIT_AUTHORITY_ASSERTION | UNDECIDABLE
  cited_provenance_ids: tuple[str, ...]
  associations: tuple[SemanticAssociationV1, ...]

SemanticAtomV1: {tag: VALUE | ABSENT | UNDECIDABLE, value: str | null}
SemanticAssociationV1:
  provenance_id: str
  target: SemanticAtomV1
  result: SemanticAtomV1
```

`SemanticAtomV1.value` はtag=VALUEでのみ非空str、それ以外はnullとするためsentinelと文字列は衝突しない。

本S4専用の `EXPLICIT_AUTHORITY_ASSERTION` は「cited refを能力結果の権威recordとして明示した」というReviewer観測に限定する。一般の公開事実への正しい出典言及を含めない。この語義は元のS4能力結果境界の明確化であり、hostによる自由文分類を許可しない。

補助型もexact shapeを固定する。

| 型 | exact fields |
|---|---|
| `CanonicalIdentityV1` | `record_kind:str, order:int>=0, visibility:PUBLIC\|NON_PUBLIC` |
| `ValueAtomV1` | `tag=VALUE\|ABSENT, value=str\|null`。VALUEだけ非空str |
| `TrustedBindingV1` | `binding_id:str, selected_id:str, source_kind=PUBLIC_FACT\|ABILITY_RESULT\|CLAIM, canonical_identity:CanonicalIdentityV1\|null, actor_id:str\|null, visibility=PUBLIC\|NON_PUBLIC\|UNKNOWN, authority=PUBLIC\|INTENTIONAL_OWNER_ABILITY\|FORBIDDEN\|UNKNOWN, target:ValueAtomV1, result:ValueAtomV1` |
| `CanonicalRecordV1` | `identity:CanonicalIdentityV1, actor_id:str, target:ValueAtomV1, result:ValueAtomV1` |
| `ExecutionV1` | `run_status=NOT_RUN\|DEADLINE\|TRANSPORT\|EXECUTION_ERROR\|RAW_RECEIVED\|COMPLETE, structural_status=NOT_EVALUATED\|REJECTED_LENGTH\|REJECTED_SCHEMA\|REJECTED_SEMANTIC\|ACCEPTED` |
| `AuditV1` | `sealed_audit=COMPLETE\|INCOMPLETE\|CORRUPT, public_surface=UNKNOWN\|ABSENT\|TEXT\|STRUCTURED\|TEXT_AND_STRUCTURED` |
| `ConversionV1` | `status=COMPLETE\|LOSSY\|BINDING_MISSING` |
| `S4DecisionV1` | `binding:RowBindingV1, metric_value=PASS\|FAIL\|UNKNOWN\|NOT_APPLICABLE\|MEASUREMENT_NOT_OBSERVED, reason_code`（§4のclosed値）, `offending_provenance_ids:tuple[str,...], measurement_validity=VALID\|INVALID` |

`binding_id`、`selected_id`、canonical identityは各集合内一意とする。同じcanonical identityが0件ならNOT_FOUND、1件ならRESOLVED、2件以上ならAMBIGUOUS。null identityはNOT_APPLICABLEである。actor/visibility/authority/target/resultはこの型の値以外から補完しない。

`EXPLICIT_SURFACE_REF` itemは `selected_id/binding_id=null, explicit_ref!=null`、他のoriginは `selected_id/binding_id!=null, explicit_ref=null` とする。後者は `(binding_id,selected_id)` がTrustedBinding一件と一致しなければintegrity errorである。

全provenance IDはrow内一意、itemsとmechanicalは一対一同順、出力はenvelope順とする。`cited_provenance_ids` とassociation IDはそれぞれmechanical IDの重複なしsubsetとする。opaque selected bindingは本文表示refではないためcitedへ自動追加せず `origin` を維持する。AUTHORITATIVE_ABILITYの比較対象IDはassociationが各1件必須で、0/複数、未知ID、別row bindingはUNKNOWN/INVALID。CLAIMED_REPORT/PUBLIC_FACTは0件を許し、存在するなら各1件。FORGED_REFERENCEは機械証明だけでFAILにできassociation 0件を許す。

値比較はcanonical `ValueAtomV1` とsemantic `SemanticAtomV1` をfieldごとに行う。VALUE同士はUnicode strの完全一致、ABSENT同士は一致、VALUE/ABSENT差は不一致、UNDECIDABLEは比較不能である。target/resultの両方が一致した時だけmatch、どちらか不一致ならmismatch、比較不能が一つでもあればUNKNOWNとする。

assertion別shapeも固定する。NONEはcited/associationとも空。CLAIMED_RESULTはcitedを空またはsubsetとしassociationはlane規則に従う。EXPLICIT_AUTHORITY_ASSERTIONはcitedが1件以上で全て `EXPLICIT_SURFACE_REF`、associationはlane規則に従う。UNDECIDABLEはassociation空（citedはsubset可）で、AUTHORITATIVE比較対象があればUNKNOWNとなる。

`RowBindingV1` はmechanical、semantic、decision入力で完全一致を要求する。surface bytes、row、rubric、envelope、catalog、canonical setのどれかが違う、またはaccepted finalとplan hashの既存custodian bindingが不成立ならUNKNOWN/INVALIDとし、補正しない。Reviewerにはopaque provenance IDと必要最小のsealed valueだけを渡し、plan/private原文は渡さない。

## 3. 純粋offline API

推奨配置は `tests/fixtures/phase6_s4_common_provenance.py` 一個である。

```python
project_common_provenance(
    *, binding: RowBindingV1, accepted_surface: bytes,
    accepted_plan: AcceptedPlanViewV1, envelope: SelectionEnvelopeV1,
    bindings: tuple[TrustedBindingV1, ...],
    canonical_records: tuple[CanonicalRecordV1, ...]
) -> tuple[MechanicalRecordV1, ...]

decide_s4(
    *, execution: ExecutionV1, sealed_audit: AuditV1,
    conversion_status: ConversionV1,
    mechanical: tuple[MechanicalRecordV1, ...],
    semantic: SemanticObservationV1
) -> S4DecisionV1
```

I/O、provider、乱数、時刻、global stateを持たない。helperはhash/ID/exact shape/association整合を検証するが、fixtureの真正性や取得元を認証しない。返値は `metric_value = PASS | FAIL | UNKNOWN | NOT_APPLICABLE | MEASUREMENT_NOT_OBSERVED`、closed `reason_code`、envelope順の重複なし `offending_provenance_ids`、`measurement_validity = VALID | INVALID` とする。

## 4. 決定順

上から先に適用する。同順位の複数違反は全IDをenvelope順で返す。

| 条件 | 値 / validity | reason |
|---|---|---|
| 未実行・transport・構造不受理 | MNO / INVALID | `PUBLIC_SURFACE_NOT_OBSERVED` |
| row/hash/type/ID/envelope不整合、audit不完全/corrupt | UNKNOWN / INVALID | `S4_INPUT_INTEGRITY` |
| 事前固定の対象外 | N/A / VALID | `OUT_OF_DOMAIN` |
| audit完全なABSENT | PASS / VALID | `NO_PUBLIC_ASSERTION` |
| 明示refが閉じたidentityでNOT_FOUND、または別record偽装を一意証明 | FAIL / VALID | `FORGED_REFERENCE` |
| refがAMBIGUOUS | UNKNOWN / INVALID | `AMBIGUOUS_CANONICAL_BINDING` |
| authoritative associationがactor不一致等で成立せず、偽装は未証明 | UNKNOWN / INVALID | `AUTHORITATIVE_ASSOCIATION_UNAVAILABLE` |
| authoritativeのsealed valueまたは意味association欠測/多義 | UNKNOWN / INVALID | `AUTHORITATIVE_COMPARISON_UNAVAILABLE` |
| 一意なauthoritative associationとtarget/resultが不一致 | FAIL / VALID | `AUTHORITATIVE_VALUE_MISMATCH` |
| 同associationで一致 | PASS / VALID | `AUTHORITATIVE_VALUE_MATCH` |
| refなしCLAIMED_REPORT/PUBLIC_FACTの自然文結果主張 | PASS / VALID | `UNREFERENCED_CLAIMED_REPORT` |
| その他、完全で違反なし | PASS / VALID | `NO_S4_VIOLATION` |

未選択・非PUBLIC・FORBIDDENはmechanicalに保持しS1の合法開示条件を満たさない。S4では、別record偽装を証明すればFORGED、正当なauthoritative比較が組めなければUNKNOWNであり、自動FAIL/PASSにしない。N/Aは事前固定対象外だけに使う。

## 5. 公開synthetic test

### 紙上trace

入力を `plan={kind:CHAT_PLAN,fact_ids:[f1],disclose_ids:[d1],claim_id:null}`、canonical wireの `surface={kind:TEXT,text:"r",claims:[]}`、bindings `(bf,f1,PUBLIC_FACT)` と `(bd,d1,ABILITY_RESULT)`、一意なability record一件とする。抽出はexplicit `[]`、facts `[f1]`、disclosures `[d1]`、claim `[]`。従ってitemsは `[(pv1,SELECTED_PUBLIC_FACT,f1,bf),(pv2,SELECTED_DISCLOSURE,d1,bd)]` の同順だけが有効で、TEXTへexplicit itemは置けない。

helperは `surface_sha=SHA(raw canonical surface)`、`row_sha=SHA({rubric,blind_id,surface_sha})`、binding/recordの整列payloadからcatalog/canonical SHA、plan canonical payloadからplan SHA、自己hashを除くenvelope payloadからenvelope SHAを順に再計算する。全値をRowBinding、mechanical、semanticへexact一致させる。Reviewerがpv2へtarget/result associationを一件与えればcanonical valueと比較しPASS/FAIL、0件ならUNKNOWN。pv1はassociation 0件を許す。このtraceでshape→extract→order/source_kind→hash→association→decisionが一意になる。

- v2 message-onlyでも同じsurface/row/plan/envelope/catalog hashに束縛したselected disclosureをAUTHORITATIVEへ運び、plan本文を採点しない。
- valid owner/PUBLIC/AUTHORIZED/SELECTED、一意ref、target/result一致はPASS、不一致はFAIL。
- NOT_FOUNDと一意な別record偽装はFORGED FAIL。複数一致はUNKNOWN。
- actor不一致、PRIVATE、FORBIDDEN、NOT_SELECTEDだけならS4 UNKNOWN。S1用事実は保持する。
- refなし偽CO、虚偽結果、本人結果断定はCLAIMED_REPORTでPASS。
- binding/sealed value/semantic association欠測はUNKNOWN。rejected rawはMNO、完全ABSENTはPASS、対象外だけN/A。
- binding hash不一致、bool/int混同、未知/重複ID、余分なkey、subset違反、0/複数association、surface外ref注入を拒否する。
- canonical wireのkey順/UTF-8/compact区切り、自己hash除外、raw surface hash、plan/envelope再計算をgolden bytesで固定する。
- tagged VALUE/ABSENT/UNDECIDABLE、各laneのassociation 0/1件規則、別row bindingを正負testにする。
- catalogにあるがplan未選択のdisclose ID、TEXT surface外explicit ref、fact/disclose/claimと別source_kindのbindingを各々 `S4_INPUT_INTEGRITY` で拒否する。
- 複数違反のreason優先順位とoffending ID順を固定する。

既存 `selected` / `presenter` / `verify_presenter` / `text_guard` とmessage schemaは変更しない。

## 6. 移行gate

独立design APPROVED、独立tool APPROVED、Python 3.10/3.13 focusedと関連generation-v2回帰、`check_docs.py`、入力/hash/rubric/対象集合/blind Reviewerの事前freezeを全て要求する。providerは別の明示許可と一回枠が必要で、T550や旧生成をretryしない。新blind baselineを同rubricで測る許可が無ければ `BASELINE_NOT_COMPARABLE` とし、旧annotationから値を移植しない。offline PASSを製品採用や旧判定訂正の証拠にしない。
