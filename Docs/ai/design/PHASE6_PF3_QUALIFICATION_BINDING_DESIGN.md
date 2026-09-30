# Phase 6 PF3 qualification binding 限定詳細設計

Status: APPROVED

## 1. 目的と境界

本設計は、T543で承認済みのactual DLL ABI smoke後qualificationを、T544 R7 buildと既存full proof runnerへ
非循環に接続する。製品、schema、model、token予算、生成規則を変更しない。T544 R7
`build-manifest.json`の`member_abi.gate=ACTUAL_SMOKE_NOT_RUN`と`qualification_sha256=null`はbuild時点の証拠として
変更しない。実行許可は、独立承認された新smoke証拠から作るqualificationとreview済みexecution bundleだけが与える。

本単位はartifact生成・validator・offline synthetic testの設計である。actual smokeの再実行、model/vocab load、full proof、
provider、server、GPU、game、Actionsは含まない。smokeが失敗、UNKNOWN、未承認、または証拠欠測ならqualification 0、
full proof child 0、PF3 `UNKNOWN`、budget `UNSET`、provider gate `CLOSED`を維持する。

## 2. smoke result SHAのauthority

### 2.1 入力証拠

qualification作成者はowner contextでT548の次を同時に照合する。

1. T544 build approval bindingとT547 smoke-tool approval bindingのexact bytes/SHA、および各approved artifact SHA。
2. T548 owner-private `manifest.json`、`seal.json`、`detail.json`を同一read handleでhash・parseした値。
3. public resultを同一read handleでhash・parseした値。
4. 独立Reviewerが発行したT548 review reportと、3節のT548 review binding。

manifestはsorted file entryとして`claim.json`、`child-stderr.bin`、`detail.json`だけを一度ずつ持ち、各size/SHAが
同じcontainerのdescriptor bytesと一致する。保存memberの最終集合はこれら3件と`manifest.json`、`seal.json`の5件だけである。
sealの`manifest_sha256`はmanifest exact file bytes、aggregate/publication candidateは`ABI_COMPATIBLE`である。
publicは`child_count=1`、他count 0、`status=ABI_COMPATIBLE`、approved hashesが3節のT544 build＋T547 runner
mappingと一致する。
path、PID、raw loader log、fixed input/output、runtime recordはpublicへ出さない。

### 2.2 child resultの一意再構成

T547で独立承認されたrunnerはchild resultをstandalone fileとして保存しない。このため任意hashを受け取らず、validated private detailと
T544 compile recordから次のclosed `Pf3DumpActualAbiSmokeResultV1` objectを一意に再構成する。

```text
schema_version = aiwolf.pf3-dump-actual-abi-smoke.v1
status = ABI_COMPATIBLE
parse_calls = detail.call_counts.parse_calls = 1
dump_calls = detail.call_counts.dump_calls = 1
destroy_calls = detail.call_counts.destroy_calls = 1
input_utf8 = detail.fixed_input = {"pf3":1}
output_utf8 = detail.fixed_output = {"pf3":1}
output_exact_match = true
output_size = 9
output_sha256 = sha256(UTF-8 bytes of {"pf3":1})
compile_record_sha256 = T544 build manifest member_abi.compile_record_sha256
runtime_record = detail.runtime_record
exception = false
seh = false
timed_out = false
exit_code = 0
cleanup = PENDING_POST
```

`detail.child_lifecycle`は最終`exit_code=0,timed_out=false,cleanup_result=REAPED`、state historyはT543 6.1節の
success sequenceとexact一致を要求する。`runtime_record`はT543のclosed runtime schemaを満たし、compile recordの
macro、PMF、symbol、export fieldとexact一致し、全check trueである。上記objectにextra/missing fieldを許さない。

このobjectを`Pf3DumpActualAbiSmokeChildResultV1`と呼び、既存`validate_member_abi_smoke_child`を同じcompile recordで
実行してから`child_result_sha256 = sha256(canonical_bytes(child_result))`を得る。`canonical_bytes`は既存実装の
UTF-8・sorted key・compact JSONで、末尾LFを含めない。これはchild control lineからCR/LFを除去したsemantic result bytesと
同じである。detail field、固定値、runtime recordの1-bit差、UNKNOWN public、seal/manifest不一致ではhashを採用しない。

### 2.3 qualification authority result

T543 6節が要求するartifact/module bindingはchild wireとは別にparentが構築するclosed
`Pf3DumpActualAbiSmokeResultV2`へ保持する。shapeは次だけである。

```text
schema_version = aiwolf.pf3-dump-actual-abi-smoke-result.v2
status = ABI_COMPATIBLE
child_result_sha256
artifact_sha256 = {
  actual_smoke_exe, llama_common, llama, ggml, ggml_base, libomp,
  dependency_manifest
}
module_projection_sha256
```

`artifact_sha256`のkeyは上記7件exactである。`actual_smoke_exe`と`dependency_manifest`はT544 approval bindingの
approved file、`llama_common`はR7 build certificateの`dll_sha256`かつT544 member bundleの同値、残る4 dependencyは
hash照合済みdependency manifestの`artifacts`にある`llama.dll`、`ggml.dll`、`ggml-base.dll`、`libomp.dll`から得る。
hyphenを含むsource keyはResultV2では上記underscore keyへ一意に写像する。任意config mapやpublic自己申告から補完しない。
module validatorへ渡す`approved_non_system`は、`actual_smoke_exe`の承認済みbasename→同SHAと、固定basename
`llama-common.dll`、`llama.dll`、`ggml.dll`、`ggml-base.dll`、`libomp.dll`→対応するartifact SHAのexact 6 key mappingである。

private detailの`module_projection`を既存`ModuleIdentity`/`ModuleEvent`へclosed parseし、ordinal順を確認し、
`validate_module_sets(module_pre,module_post,module_events,approved_non_system)`がPASSした後だけ
`module_projection_sha256 = sha256(canonical_bytes(module_projection))`を採用する。detail保存済みSHAともexact一致させる。
UNLOADを含む不正ledger、path/hash/classification/version差、extra/missing fieldではResultV2を作らない。

`actual_smoke_result_sha256`はResultV2の`canonical_bytes`（末尾LFなし）のSHAを指す。child result SHAとResultV2 SHAを
同名扱いしない。現在のT545 runはsealed ledgerが既存validatorに拒否されたためResultV2 0、qualification 0であり、
positive fixtureや承認入力へ使わない。positiveは別taskの新source承認後に得た別runだけである。

## 3. smoke review bindingとqualification

Mainが独立review完了後に作る`Docs/ai/handoffs/tasks/T548_APPROVAL_BINDING.json`はcanonical LF終端のclosed
`aiwolf.pf3-dump-abi-smoke-review-binding.v1`とし、次だけを持つ。

```text
schema_version, measurement_task=T548, runner_public_task=T545, verdict=APPROVED, run_id
t544_build_approval_binding_sha256
smoke_tool_approval_binding_path, smoke_tool_approval_binding_sha256
private_manifest_sha256, private_seal_sha256, private_detail_sha256
public_result_sha256, child_result_sha256, actual_smoke_result_sha256
review_path, review_sha256
scope=actual DLL fixed JSON smoke only; child 1; retry/model/full proof/provider 0
```

`run_id`は実run config/public/detailの同じ`[A-Za-z0-9_-]{16,96}`、全SHAはlowercase 64 hexである。
`smoke_tool_approval_binding_path`は`Docs/ai/handoffs/tasks/T547_APPROVAL_BINDING.json`、`review_path`は
`Docs/ai/handoffs/tasks/T548_PF3_MEMBER_ABI_SMOKE_REVIEW.md`のexact literalとする。`runner_public_task=T545`は承認済runnerの
固定report literalであり、実測責任`measurement_task=T548`やrun IDの代用にしない。T545のCHANGES_REQUIRED run、別run ID、
superseded review、same-value別path/schemaを拒否する。

public `approved_hashes` 9 keyの期待値は次の一対一対応で得る。

| public key | authority |
|---|---|
| `runner_source` | T547 approval bindingの`approved_files["scripts/phase6_pf3_member_abi_smoke.py"]` |
| `windows_source` | T544 approval bindingの`approved_files["scripts/phase6_pf3_token_path_windows.py"]` |
| `build_helper_source` | T544 approval bindingの`approved_files["scripts/build_phase6_pf3_token_path.py"]` |
| `actual_smoke_source` | T544 approval bindingの`approved_files["scripts/native/phase6_pf3_token_path.cpp"]` |
| `actual_smoke_exe` | T544 approval bindingのR7 smoke exe |
| `build_manifest` | T544 approval bindingのR7 build manifest |
| `build_certificate` | T544 approval bindingのR7 build certificate |
| `dependency_manifest` | T544 approval bindingのchecked-in dependency manifest |
| `llama_common` | T544 approval済みmember bundle exact bytesの`artifacts.llama_common`かつcertificate `dll_sha256` |

T544/T547 approval binding自体のverdict、scope、対象file SHAを同descriptorで検証する。private locator、token、raw bytes、PIDは
bindingへ含めない。T548 review reportは上記hash、child 1/retry 0、module validator PASS、cleanup/reap、public/private境界を
独立判定し、最上位判定が`APPROVED`で各reviewed SHAが同値の場合だけbinding generatorがexclusiveに一回生成する。

qualification artifact pathは`scripts/native/phase6_pf3_member_abi_qualification.json`で、canonical LF終端の次の4 fieldだけである。

```text
schema_version = aiwolf.pf3-dump-member-abi-qualification.v1
build_certificate_sha256
actual_smoke_result_sha256
smoke_review_binding_sha256
```

`build_certificate_sha256`はR7 build manifestが指すcertificate exact file bytes、result SHAは2.3節、review binding SHAは
上記exact file bytesへ束縛する。qualification自身のSHAはLFを含むfile bytesから得る。生成はT548 APPROVED後一回だけ、
exclusive create→write→fsync→rereadで行う。既存file、partial、再生成、入力1-bit差は拒否する。

## 4. hash DAGとexecution bundle

### 4.1 member ABI bundle v2

T544の`phase6_pf3_member_abi_execution_bundle.json`を上書きせず、
`scripts/native/phase6_pf3_member_abi_qualified_execution_bundle.json`を新しいreview対象artifactとして作る。closed shapeは次である。

```text
schema_version = aiwolf.pf3-member-abi-smoke-execution.v2
design_sha256 = T543 stamped design SHA
state = ACTUAL_SMOKE_APPROVED
qualification_sha256
artifacts = {
  actual_smoke_source, actual_smoke_exe, build_manifest,
  build_certificate, llama_common, dependency_manifest
}
symbols = {json_parse,json_dump,json_destroy}
```

artifacts/symbolsはT544 APPROVAL bindingとexact一致する。qualificationは3節のexact file SHAである。旧v1、
`ACTUAL_SMOKE_NOT_RUN`、qualification null、unknown state、extra keyをfull proof許可へ使わない。T544 v1 bundleはsmoke実行時の
履歴証拠として不変保存する。

### 4.2 full proof execution bundle v2

旧`scripts/native/phase6_pf3_execution_bundle.json`と`aiwolf.pf3-reviewed-execution-bundle.v1`を実行許可へ拡張解釈せず、
新しい`scripts/native/phase6_pf3_qualified_execution_bundle.json`に次の18 keyだけを持つclosed v2を採用する。
旧v1は証拠として保存する。

```text
schema_version = aiwolf.pf3-reviewed-execution-bundle.v2
design_sha256
proof_child_sha256
child_source_sha256
build_manifest_sha256
sdk_manifest_sha256
compiler_sha256
linker_sha256
dumpbin_sha256
closure_manifest_sha256
source_archive_sha256
source_archive_size
product_certificate_sha256
symbols
abi
member_abi_execution_bundle_sha256
build_certificate_sha256
qualification_sha256
```

各値のauthorityは次である。

| field | authority |
|---|---|
| `design_sha256` | T532 stamped detailed design SHA |
| `proof_child_sha256` | T544 approval bindingのR7 `phase6_pf3_token_path.exe` |
| `child_source_sha256` | T544 approval bindingの`phase6_pf3_token_path.cpp` |
| `build_manifest_sha256` | T544 approval bindingのR7 build manifest |
| `sdk_manifest_sha256` | R7 build manifest fieldかつT544 approval bindingのR7 SDK manifest |
| `compiler_sha256`,`linker_sha256`,`dumpbin_sha256` | R7 build manifest fieldsかつR7 SDK manifest同値 |
| `closure_manifest_sha256` | R7 build manifest `closure.manifest_sha256`かつT532/T534 approved closure |
| `source_archive_sha256`,`source_archive_size` | R7 build manifest closureかつT532 approved archive定数 |
| `product_certificate_sha256` | T534 approved checked-in product certificate exact file |
| `symbols`,`abi` | R7 build manifest exact objects |
| `member_abi_execution_bundle_sha256` | 4.1節v2 exact file |
| `build_certificate_sha256` | T544 approval bindingのR7 certificateかつ4.1節artifact |
| `qualification_sha256` | 3節qualification exact fileかつ4.1節field |

configの`execution_bundle`はこのv2だけを指す。runner sourceに固定するSHA、config `hashes.execution_bundle`、
同descriptorから測るSHAの3つをexact一致させる。v1とv2のunion、optional field、null qualification、extra/missing、
複数候補からの選択は禁止する。v2全体を独立tool reviewした後だけrunner固定SHAを更新する。

hash DAGは次の一方向だけである。

```text
T544 build manifest -> compile record + build certificate + R7 child
T548 detail/manifest/seal/public -> validated child result -> parent ResultV2
T548 review binding -> T544 build approval + T547 smoke-tool approval + T548 evidence hashes + ResultV2
qualification -> build certificate + ResultV2 + T548 review binding
member ABI bundle v2 -> T544 artifacts + qualification
full proof bundle v2 -> R7 build/tool/product closure + member ABI bundle v2 + certificate + qualification
runner fixed SHA -> full proof bundle v2
```

build manifestからqualificationへの逆参照を作らない。これによりT543のhash循環回避を維持する。

## 5. full proof configと同一descriptor検査

既存config top-level schemaは変えず、`paths`/`hashes`のexact required setへ
`build_certificate`、`qualification`、`member_abi_execution_bundle`を追加する。各pathはabsolute、各SHAはlowercase 64 hexで、
別keyのalias pathを拒否する。config値だけをauthorityにしない。

`_pin_configured`は全artifactを既存share-deny read pinで保持し、hash、parse、相互比較、child終了後final hash/identity照合まで
同じdescriptorを使う。manifest、certificate、qualification、両execution bundleをpathで再openしない。validator順序は次である。

1. 全required pin取得とconfig SHA照合。
2. full proof bundle v2の固定SHA、closed shape、R7 child/source/build/toolchain/product closure照合。
3. member ABI bundle v2の固定SHA、closed shape、T544 approved artifact/symbol、qualification照合。
4. build manifest v2のclosed shape、`member_abi`、`implementation_dependencies`、R7 child/source/build helper照合。
5. embedded compile recordをclosed validatorへ通し、canonical SHAとmanifest fieldを照合。
6. pinned certificateをclosed validatorへ通し、compile record、manifest certificate SHA、bundle certificate SHAを照合。
7. pinned qualificationをclosed validatorへ通し、certificate SHA、member/full bundle qualification SHAを照合。
8. 既存SDK/product/sampler/module allowlist照合。

qualification内のresult/review binding bytesはruntime configへ再持込みしない。これらは4節bundleの独立reviewで確定済みであり、
private evidence locatorをfull proofへ漏らさない。いずれかのpin、schema、SHA、identityが不一致ならbackend構築前に
`UNKNOWN_ABI_IDENTITY`、child 0で終了する。

## 6. compile/runtime record伝播

certificate validatorの戻り値へ次のclosed projectionを追加する。

```text
member_abi_expected = {
  compile_record,
  compile_record_sha256,
  decorated_symbol
}
```

`run_backend`は必須keyword `expected_member_abi`としてこのexact projectionを受け、`validate_child_result`へそのまま渡す。
actual full proofでnull、省略、config自己申告、child自己申告からの補完を許さない。child resultのruntime recordはcompile-in SHA、
macro、PMF、symbol、export/module/roundtrip check全てを親projectionと比較する。比較成功後だけ既存accounting結果を
`FAIL_BUDGET`へ昇格できる。ABI比較失敗はPF3 FAILではなく`UNKNOWN_ABI_IDENTITY`である。

synthetic testだけは既存`{schema_version,synthetic=true}`と、test harnessが明示するsynthetic expected recordを許す。
actual CLI経路へsynthetic recordを渡した場合はchild 0または結果UNKNOWNであり、positiveにしない。

## 7. state、失敗、実行gate

qualification/bundle実装のstateは次だけである。

```text
UNQUALIFIED -> EVIDENCE_VERIFIED -> QUALIFICATION_WRITTEN
 -> MEMBER_BUNDLE_WRITTEN -> FULL_BUNDLE_WRITTEN -> TOOL_REVIEWED
```

各write前にcandidate bytesとSHAをmemoryで構築し、exclusive write/fsync/reread成功後だけ次stateへ進む。途中失敗では既存確定artifactを
変更せず、partialを実行authorityへしない。`TOOL_REVIEWED`は別ReviewerのAPPROVED bindingがある場合だけ成立する。

full proof preflightは全validatorを通して`READY, child_count=0, private_claim_count=0`を返す。full proof一回実行は、T546設計承認、
実装tool独立承認、fresh Tester task、fresh owner-private config/container、ユーザーの既存実行許可が全て成立した後だけである。
各smoke taskとfull proofはいずれも同run retry 0で、UNKNOWNを同task再実行で上書きしない。T545失敗証拠は不変保存し、
修正版sourceの独立tool承認後に別task T548・別run ID・fresh config/containerで一回だけ測定する。

## 8. acceptance test

positiveはmodel-free syntheticで次を確認する。

1. module validatorまでPASSしたAPPROVED T548 fixtureからchild/result v2 SHA、qualification、member/full bundle v2が一意になる。
2. full proof preflightが全pinを同一descriptorで照合し、child 0/private claim 0でREADYになる。
3. synthetic backendへ`member_abi_expected`が渡り、runtime record exact一致時だけ既存accountingへ進む。

negativeは各々独立に1-bit変更し、全てbackend start 0を要求する。

- detail fixed input/output/call count/runtime record/module ledger、manifest、seal、public、T548 review binding。
- qualificationのcertificate/result/review SHA、extra/missing key、noncanonical bytes、末尾LF差。
- member bundleのstate/null qualification/artifact/symbol、旧v1。
- full bundleのmember bundle/certificate/qualification SHA、R7 child/source/build SHA、旧v1、v1/v2混在。
- configの3追加pin欠測、alias、hash drift、descriptor identity drift。
- build manifest embedded record/SHA/certificate、certificate compile projection。

backend結果後はruntime recordのcompile SHA、macro、PMF、symbol、各booleanの1-bit差で
`UNKNOWN_ABI_IDENTITY`、`FAIL_BUDGET` 0を確認する。publication/seal失敗、deadline、cleanup不明は既存fail-closedを維持する。
focused test、関連T534/T544回帰、両Python、`check_docs`、diff-checkを通し、独立tool review後だけTesterへ渡す。
