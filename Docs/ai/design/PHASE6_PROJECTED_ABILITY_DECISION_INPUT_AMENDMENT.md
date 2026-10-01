# Phase 6 projected ability decision input 限定amendment

Status: APPROVED
Approved-Content-SHA-256: b1aab22992f09e76453a2d6b048a85374975141e6716b7fb0e7ba9ea952b3bb5
Approved-By: Independent Design Reviewer
Task: T555
Base contract: `S4_PROJECTED_ABILITY_PROVENANCE_V1`

## 1. 対象と非変更点

承認済みT554設計のdecision入力だけを補う。旧設計bytes、旧 `S4_COMMON_PROVENANCE_V1`、全laneの投影・意味・priority、freeze作成、製品schema/runnerは変更しない。

現signatureの `merged_records` と `ProjectedSemanticObservationV1` だけでは、新 `ProjectedRowBindingV1` のplan/catalog/binding-set hashをtrusted inputから復元できず、semanticの `witness_sha256` を照合するrow witnessも無い。semanticへbindingを追加する補完やrecordsからの推測は禁止する。

## 2. API差分

```python
decide_projected_ability_s4(*,
    execution: ExecutionV1,
    audit: AuditV1,
    conversion: ConversionV1,
    row: ProjectedAbilityRowV1,
    expected_freeze_sha256: Digest,
    merged_records: tuple[MechanicalRecordV1 | ProjectedMechanicalRecordV1, ...],
    semantic: ProjectedSemanticObservationV1,
) -> ProjectedS4DecisionV1
```

`row` と `expected_freeze_sha256` は明示trust入力である。expected digestをrow/freezeから生成してはならず、既存task preflightが別系統に固定した値だけを渡す。hash自体を認証とは扱わない。

## 3. accepted後の機械照合

既存どおりexecution/audit/conversion enumをstrict parseする。`run_status != COMPLETE` または `structural_status != ACCEPTED` はrow、expected root、records、semanticを読まず、`binding=null / MEASUREMENT_NOT_OBSERVED / PUBLIC_SURFACE_NOT_OBSERVED / INVALID` を返す。

acceptedの場合だけ、次を番号順に全て行う。

1. `row` と `expected_freeze_sha256` を `project_selected_abilities` へ渡し、freeze/root、全row hash、witness、selection/envelopeを再検査して `expected_projected_records` を得る。
2. `merged_records` のうちcontractが本contractであるrecord列を順序保持抽出し、`expected_projected_records` と件数・順序・全exact fieldが一致することを要求する。0件が正しいrowでは双方0件を要求する。余分、欠落、順序差、field差を許さない。
3. merge後の全provenance ID一意性と旧base record shapeを既存規則で検査する。非projected laneをrow情報から作り直さない。
4. decisionの唯一のnew bindingを `row.binding` とする。`semantic.blind_id / row_sha256 / surface_sha256 / accepted_final_sha256 / custodian_freeze_sha256` はrow.bindingとexact一致し、`semantic.witness_sha256 == row.witness.witness_sha256` を要求する。
5. `semantic.base.binding` は旧 `RowBindingV1` exact型のまま検査し、row.bindingとの対応をT554 §4どおり全件照合する。旧bindingを新型へ変換しない。

1〜5のfailureはpayloadを含まないsafe reasonで `UNKNOWN / INVALID`。root/freezeは `PROJECTED_TRUST_ROOT_UNAVAILABLE`、row/base/witness/semantic hashは `PROJECTED_ROW_BINDING_MISMATCH`、projected列またはmerge差は `PROJECTED_PROVENANCE_MERGE_INVALID` とする。この三reasonのpriorityは記載順で、既存input integrity段内に置く。

## 4. priorityと返値

MNOを最優先とし、accepted後は上記input integrity、既存 `S4_INPUT_INTEGRITY`、ABSENT、FORGED、AMBIGUOUS、authority availability、comparison unavailable、mismatch、all-matchの承認済み順を変えない。mixed mismatch+unknownはUNKNOWN、全件comparableで一件以上mismatchだけFAIL、全件matchだけPASSである。

accepted後に `row.binding` をexact parseできなければdecision bindingはnull。parse後のroot/row/record/semantic不一致ではparse済み `row.binding` を返す。例外本文、入力値、pathをreasonへ含めない。

## 5. 限定tests

- 正: rowから再投影したPROJECTED列とmerged抽出列が同順exact一致し、semantic witness/final/base対応も一致する。
- 正: SOURCE_REFとPROJECTED_ABILITY混在で非projected base recordを維持し、既存全件比較を行う。
- 負: expected freeze違い、row freeze自己整合だけ、row/witness/final/base hash差はUNKNOWN。
- 負: projected recordの欠落、余分、順序差、field差、別row record混入はUNKNOWN。
- 境界: MNOは壊れたrow/rootを読まずnull binding。accepted後は同じ入力をUNKNOWNにする。
- 回帰: ABSENT、FORGED、AMBIGUOUS、合法CLAIMED_REPORT、mixed mismatch+unknownの既存結果を維持する。

独立design APPROVED前にdecision実装を確定しない。provider、旧annotation、旧評価は実行しない。
