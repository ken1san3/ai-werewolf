# Phase 6 S4 delta blind provenance 補遺

Status: APPROVED  
Task: T565  
Responsibility: Architect

## 1. 目的と境界

本補遺は `SavedDeltaBlindRowV1.provenance` の未定義型 `BlindBaseProvenanceV1` だけを閉じる。T563の対象集合、S4意味、T556のauthority判定、T562のpure decision、privacy、集計、旧artifactは変更しない。実装は独立Reviewer承認後の別gateである。

入力正本は、旧bindingでexact検証済みの `MechanicalRecordV1` 列である。公開packetは12 targetのREADY行だけを含み、現観測ではCO 5行が各2 record、ROOT 7行が0 recordである。この件数をcanonical値やinventoryから補完しない。

## 2. 公開exact型

```text
BlindBaseProvenanceV1 (exact keys):
  provenance_id: non-empty str
  lane: "PUBLIC_FACT" | "AUTHORITATIVE_ABILITY" |
        "CLAIMED_REPORT" | "FORGED_REFERENCE"
  ref_resolution: "RESOLVED" | "NOT_FOUND" | "AMBIGUOUS" |
                  "NOT_APPLICABLE"
  selection: "SELECTED" | "NOT_SELECTED" | "NOT_APPLICABLE" | "UNKNOWN"
  actor_match: "TRUE" | "FALSE" | "UNKNOWN"
  visibility: "PUBLIC" | "NON_PUBLIC" | "UNKNOWN"
  authority: "AUTHORIZED" | "FORBIDDEN" | "UNKNOWN"
  binding_status: "COMPLETE" | "MISSING" | "CORRUPT"
  identity_kind: "SOURCE_REF" | null
```

`provenance_id` をpacket、annotation、semantic wrapperの唯一のID名とする。T556の `opaque_provenance_id` はwire互換名として本delta型へ持ち込まない。

公開型にold binding、origin、source_kind、selected ID、binding ID、canonical identity、actor/owner ID、inventory、pointer、private canonical truth、condition、`canonical_value`、target/resultを含めない。`identity_kind` は元recordの `origin == EXPLICIT_SURFACE_REF` のときだけ `SOURCE_REF`、それ以外はnullである。hostや評価者は本文やlaneからこの値を推測しない。

## 3. 決定論的写像とprivate再結合

各旧 `MechanicalRecordV1` を旧envelope順に一回だけ読み、次をfield-for-fieldで写す。

```text
provenance_id <- record.provenance_id
lane <- record.lane
ref_resolution <- record.ref_resolution
selection <- record.selection
actor_match <- record.actor_match
visibility <- record.visibility
authority <- record.authority
binding_status <- record.binding_status
identity_kind <- "SOURCE_REF" iff record.origin == "EXPLICIT_SURFACE_REF" else null
```

この公開列は上記の安全な構造fieldについてlosslessである。元recordの順序、件数、ID一意性を保持し、追加・削除・merge・並べ替えを禁止する。`base_provenance_sha256` はこの公開exact列のcanonical SHAであり、private old mechanical row SHAの代替ではない。

公開列からprivate `MechanicalRecordV1` を復元してはならない。annotation freeze後、custodianだけがprivate mappingでevaluation IDへ戻し、固定済みold mechanical row SHAとbase bindingを再検証し、`provenance_id` の全件・同順一致および上記公開fieldの再写像一致を確認する。T562 pure decisionへ渡す `base_records` は、このprivate原record列そのものであり、packetから再生成したrecord列ではない。不一致は `S4_INPUT_INTEGRITY / UNKNOWN / INVALID` とし、canonical値、authority、associationを補完しない。

## 4. annotation規則

- cited IDとassociation IDは当該rowの `provenance_id` の重複なしsubsetとする。
- cited IDは、公開surfaceが明示したrefに対応し、かつ対応recordの `identity_kind == SOURCE_REF` が確定する場合だけ許す。selected recordを自動でcitedにしない。
- `NONE` はcited/associationsとも空である。
- `UNDECIDABLE` はassociations空である。citedは明示refを識別できた場合だけsubsetを許す。
- `CLAIMED_RESULT` はcitedを空またはSOURCE_REF subsetとし、発言上に明示されたtarget/resultだけをassociationへ置く。
- `EXPLICIT_AUTHORITY_ASSERTION` は公開surface上で能力結果の権威として明示したという観測を保持する。citedは空または1件以上であり、非空なら全件 `identity_kind == SOURCE_REF` とする。表面上の権威主張にrefが無い場合もcited空のままこのassertionを維持し、refを発明せず `CLAIMED_RESULT` へ再分類しない。後段T562はcited空を既存どおり `EXPLICIT_AUTHORITY_REFERENCE_UNAVAILABLE / UNKNOWN / INVALID` とする。
- associationは一IDにつき最大1件で、exact `{provenance_id,target,result}`。tagは `VALUE|ABSENT|UNDECIDABLE`、VALUEだけ非空str、他はnull。発言欠落はABSENT、解釈不能はUNDECIDABLEであり、private canonical値から埋めない。
- association IDが未知、重複、別row、cited規則違反、または公開列とprivate再結合が不成立なら意味比較を行わず `UNKNOWN / INVALID` とする。
- private baseのAUTHORITATIVE比較対象についてassociation欠測またはUNDECIDABLEが一件でもあればUNKNOWNを優先する。全件比較可能な場合だけ既存T562がPASS/FAILを決める。refなしの虚偽・騙り自体をFAILへしない。

## 5. privacyと受入条件

packet validatorはexact key、closed enum、行内ID一意性、旧順序、公開列SHAを検査する。private rejoin validatorはold mechanical row/binding SHA、全ID・順序、公開再写像、fresh annotation freezeを検査する。どちらも本文からref、target/result、authorityを抽出しない。

正例はCO 5行各2 record、ROOT 7行0 record、SOURCE_REFとnon-SOURCE_REFの決定論的区別、NONE/UNDECIDABLE、明示ref associationを含む。負例はextra/private field、origin/source_kind/canonical value漏洩、ID rename/duplicate/reorder、identity_kind改変、unknown/cross-row association、canonical値による欠落補完を拒否する。

独立design APPROVED後にだけT565 helper/testへ実装し、Python 3.10/3.13 focused、関連回帰、`check_docs.py`、独立tool reviewを要求する。

## 独立承認記録

§4の既存UNKNOWN契約整合を含むcontent SHA 776c4ff9a94a622a34d6adc311473842f8002f368a16f15f430ff4a38d27ae11 を別ReviewerがAPPROVED。report SHA 2b2d9b606b7246688f4f7773f15e9012da4f55739d60763b507330350452d85e。Mainはstatus/記録だけを更新し、設計自己承認していない。
