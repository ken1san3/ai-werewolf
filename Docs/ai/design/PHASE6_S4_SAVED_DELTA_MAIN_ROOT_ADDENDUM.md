# 保存S4 delta Main独立rootの実装補遺

Status: APPROVED  
Task: T565  
Responsibility: Architect

## Scope

T563承認契約のMain別系統rootを、candidate/runtimeの転写で代替しないための限定補遺。
製品・S4意味・authority・privacy・旧artifact・T560/T562は変更しない。
Mainは新しい別fileからfixed physical rootsを直接読み、candidateとは別に全192を再構成する。
本補遺が独立APPROVEDになるまでこの新builderを実装しない。

## Fixed inputs

Mainが指定するpathと、別に照合済みのphysical SHAをexactな外部引数で受け取る。
`FixedRootFileV1`は `{role,group,path,sha256,size_bytes}` exact keys。
groupは`T556`または`T560`、pathはMainが既存private container内で解決した絶対str、
SHAはphysical bytes、sizeはstrict int>=0。順序は次の15roleを固定する。

```text
T556_INPUT, T556_PREPARED, T556_SOURCE_MANIFEST, T556_MAIN_PIN,
T556_PACKET, T556_PACKET_FREEZE, T556_ANNOTATIONS, T556_ANNOTATION_FREEZE,
T556_FINALIZED, T556_FINAL_FREEZE,
T560_SOURCE_MANIFEST, T560_CANDIDATE, T560_MAIN_PIN, T560_WITNESSES,
T556_SAFE_RESULTS
```

T560_*だけgroup=T560、他はT556。source manifestやpinの同名fileもrole/groupで区別する。
T556_PACKETはnative凍結sealed wrapperのphysical fileを指し、既存exact型
`{contract,candidate_manifest,pin_sha256,packet,packet_sha256}`の`/packet`だけをpacketとして使う。
wrapperを作り直して新しいphysical SHAへ置き換えない。
外部expected fixed-root SHAはこの順のdescriptor列のcanonical SHAで、candidateから受け取らない。
本補遺の15roleであり、T565の13出力artifactはfixed inputとして受理しない。
欠落・追加・順序変更・同一実体alias・symlink・group違い・path container外を拒否。
pathはprivate内部だけで利用し、public、blind、exceptionには出さない。
全fixed fileおよび元395 source snapshotを開始・終了で再hashする。
source_manifest/preflight/candidateは比較対象であり、期待rows、partition、root、witnessの入力にはしない。
新Main builderはT565 candidate builderを呼ばない。

## Independent reconstruction

1. T556 native INPUT/PREPAREDのexact192順とexecution/host状態から5 CO gap、7 root gap、167 prior、13 MNOを再構成する。
2. 既存source verifierで原packet/mechanical/mapping、accepted member locator、fixture source/bindings/catalogを照合する。
3. 原binding/envelope/mechanical row/saved surface/final member/旧annotation/旧decisionを同evaluationへ結合し、SourceRowを自身以外全fieldのcanonical SHAで再構成する。
   profile keyはcanonical `{condition:<既存condition>}` のSHA。old mechanical row SHAは旧mechanical snapshot内の同blind行全体のSHA。
   MNOのaccepted finalはnull。旧projected decisionがnullのMNOは既存MNO契約の固定decisionを参照し、旧finalized行を丸ごと保全する。
4. q.prepareのnative fixtureをcurrent承認byteで再構成し、source/bindings/catalog SHAを旧INPUTとexact比較する。
5. targetだけに既存T556 `saved._surface_view`を使う。新しい意味projectionではない。
   原4key surfaceのSHAと、既存bound viewのSHAを別保持し、view SHA==old_binding.surface_sha256を要求する。
   Main実測ではこの同値が12/12で成立し、同viewを通すT562 source rootも12/12成立した。旧surfaceは上書きしない。
6. T562のfreeze/build row/validateを同native sourceから独立に実施。選択>0、ref/owner/source mismatchは閉じたINPUT_UNKNOWN。意味deciderは呼ばない。
7. COはT560の承認済みMain別witness builderで再構成し、凍結済みT560 witnessと全field exact比較する。
   CO witness contractは`S4_CO_SURFACE_SOURCE_BINDING_V1`。既承認witnessの`witness_sha256`
   （自身を除くpayload canonical SHA）をそのまま使い、witness全object SHAとは区別する。
   ROOTは以下の内部witnessを作る。これは存在する構造上の関連のhashであり、authority/evidenceを新設しない。

```text
ExistingSurfaceWitnessV1 exact keys:
  contract: "S4_EXISTING_SURFACE_WITNESS_V1"
  evaluation_id: nonempty str
  old_row_binding_sha256, saved_surface_sha256, bound_surface_sha256, accepted_final_sha256: Digest
  witness_sha256: Digest = canonical SHA of the other fields
```

`saved_surface_sha256`は原4key accepted_surface全体のcanonical SHA、
`bound_surface_sha256`は既存3key public viewのcanonical SHA。
前者を同source row.saved_surface_sha256、後者を同old binding.surface_sha256とexact一致させる。
evaluation/old binding/final memberは同T556 native行からのみ採り、cross-row流用を拒否する。

8. expected coverage rowsはT563 CandidateRow exact型と順を維持する。
   `delta_root_sha256`は同T562 row全体のcanonical SHA、
   `surface_witness_sha256`はCOのT560 witness SHAまたはROOTの上記witness SHA。
   **SavedDeltaBinding全体のSHAにしない**。bindingにはmain_pin_sha256があり、candidate→pin→bindingの循環を避ける。
9. source/scopes/bundles/preflightを独立再計算して比較し、expected CandidateManifest全体を構成する。
   candidateの入力は最後のexact比較だけ。一致後、T563 MainPinnedSavedDeltaRootsV1を独立expected値から生成する。
   同target rootとwitnessからSavedDeltaBindingを作り、確定pin SHAを最後に入れる。

## Exact output / one-way binding

Mainのprivate再構成結果`RebuiltMainStateV1`は次のexact keysで保存する。

```text
contract: "S4_SAVED_DELTA_MAIN_STATE_V1"
fixed_roots_sha256: external fixed descriptor root SHA
source_rows: ordered exact192 SourceRow (T563 + source_row_sha256)
expected_candidate: exact CandidateManifest (T563)
independent_roots: {
  contract: "S4_SAVED_DELTA_MAIN_ROOTS_V1",
  rows: exact192 ordered {
    evaluation_id, expected_source_row_sha256,
    expected_delta_root_sha256: Digest|null,
    expected_surface_witness_sha256: Digest|null
  },
  roots_sha256: canonical SHA of contract+rows
}
target_roots: ordered exact12 {
  evaluation_id, delta_input_status: "READY"|"INPUT_UNKNOWN",
  t562_row: exact UnselectedAbilityRowV2|null,
  surface_witness: existing CO or ROOT witness|null
}
state_sha256: canonical SHA of all other fields
```

旧意味値を新生成しない。targetのper-row root/witness不成立は両nullable field=null、
status=INPUT_UNKNOWNとしてcoverageにも同じnullable・closed reasonを反映する。
元snapshot/integrity/row関連が破損した場合はglobal failureでartifactをCOMPLETE化しない。
例外表示は固定 `SOURCE/OLD_REFERENCE/CATALOG/PARTITION/WITNESS/ROOT/PIN` だけ。

dependency bundleは固定pathのcurrent bytesとMain承認pinから、scopesは再構成source_rowsから、
preflightはそのsource manifest SHA、fixed rootsのrole順SHA、current helper source SHA、
既定rubric SHAから再計算する。tool自己申告preflight/candidateはexact比較だけに用いる。
順序は fixed roots → SourceRows/source/scopes/preflight → expected candidate → independent roots
→ Main pin → SavedDeltaBinding。binding SHAをcandidate/root/pinへ逆参照させない。
T565 helperはstate/roots両selfhashとMain外部expected state/root SHAを検査する。
bindingの確定pinを後固定してもexpected candidate/root SHAが不変であることを検査する。

## Integration / retained gates

新 `tests/fixtures/phase6_s4_saved_delta_main_pin.py`はMain専有。
T565 helperのpin入口はMain独立root結果と外部expected SHAを検査し、candidate比較だけを行う。
同helper内のsource/runtime再計算をMain別系統の代替にしない。
private適用は新builderを含むfocused/regression、tool APPROVED後のみ。
新意味評価には別fresh Reviewerを使い、旧167/MNO13を再評価しない。

## Acceptance

公開synthetic/native fixtureで、candidate未入力のroot再構成、12/167/13 exact partition、
native source hash、bound viewとのhash同値、CO witness、root witness、全192順、物理drift、
cross-row/ref/fixture/partition/alias/symlink、candidateの自己整合改変、pin循環なしを検査する。
current code、source、T560/T562承認bundleの開始終了一致を要求する。
意味決定0/provider0を維持。検証未実施をPASSにしない。

## 次gate

独立design APPROVED → Main別builder＋custody限定修正 → focused → 独立tool → source custody → fresh12評価。

## 独立承認

2026-10-02、T565_MAIN_ROOT_DESIGN_REVIEW.mdで独立APPROVED。
承認content SHA: eb4660a454b975d8a8d12cd627e1fe7a2c7ad146193e68268243c512f286fe25。
report SHA: 48fc052a5f4695489646964d3decf705140db9e6e81dba667f95426f5a6001e2。
このstatus/照合記録だけをMainが追記し、承認contentとは区別する。
