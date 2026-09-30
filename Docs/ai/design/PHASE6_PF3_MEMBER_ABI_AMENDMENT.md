# Phase 6 PF3 member ABI 限定追補

Status: APPROVED
Task: T543

## 1. 目的と境界

本追補はT532 3.3節の`common_json::dump(int) const` bindingだけを置換する。対象はtest-only
proof child、builder、ABI smoke、model-free test、build/execution evidenceである。製品、schema、grammar、
witness、512 budget、model、runtime DLL、provider gateを変更しない。compile、smoke、actual proofは本設計の
独立承認後に別責任で行う。

旧`std::string (__cdecl *)(const common_json *, int)`型、同型へのcast、free-function fallbackを禁止する。
`json_parse`はstatic、`json_schema_to_grammar`はfree functionなので対象外である。void-return destructor edgeも
今回のhidden-return不一致ではなく変更しない。

## 2. closed ABI certificate

builderはcanonical JSONの`Pf3DumpMemberAbiBuildCertificateV1`をbuild manifestへ保存する。

```text
schema_version, target_arch=x86_64-pc-windows-msvc
compiler_sha256, linker_sha256, sdk_manifest_sha256
msvc_version, _MSC_VER, _MSC_FULL_VER, _MSVC_LANG
dynamic_crt=true, iterator_debug_level=0
header_relative_path, header_sha256, source_archive_sha256
class_declaration=common_json, no_base_clause=true
dll_sha256, decorated_symbol, export_kind=DIRECT_EXECUTABLE
pmf_mode=MSVC_DEFAULT_BEST_CASE_NO_BASE, pmf_size, pmf_alignment
farproc_size, pointer_size, pmf_trivially_copyable=true
binding_method=MEMCPY_FARPROC_BYTES_TO_PMF_V1
mock_dll_source_sha256, mock_dll_sha256, mock_dll_argv_sha256
mock_caller_source_sha256, mock_caller_exe_sha256, mock_caller_argv_sha256
mock_result_sha256
actual_smoke_source_sha256, actual_smoke_exe_sha256, actual_smoke_argv_sha256
```

smoke後に作る別のcanonical `Pf3DumpMemberAbiQualificationV1`は
`build_certificate_sha256`、`actual_smoke_result_sha256`、`smoke_review_binding_sha256`だけを持つ。この分離で、
smoke実行前に確定するbuild artifactと実行結果のhash循環を作らない。

`header_sha256`はT532のpinned source archive内`common/json.h` exact bytesへ束縛する。そのbytesの
`class common_json {`にbase-clauseが無いことをreviewed certificateへ固定する。traitや`sizeof`から継承なしを
推論しない。別header、forward declarationだけ、preprocessorによるclass-head置換は拒否する。

装飾名はdumpbinの全exportからexact一件だけを選び、承認済み
`?dump@common_json@@QEBA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@H@Z`
とbyte-equalにする。PE export entryはforwarderでなく、RVAがpinned DLLのexecutable section内にあることを
builderが証明する。prefix一致、overload複数、undecorated aliasは拒否する。

child、mock DLL/caller、smokeのcompile/link argvはclosed listとし、`/vmg`、`/vmb`、`/vmm`、`/vms`、
`/vmv`をいずれも許さない。既定のbest-case表現、no-base header、PMF sizeを一組としてcertificateへ束縛する。

## 3. typed bindingとpre-call gate

唯一の型は次とする。

```cpp
using pf3_json_dump_member_fn = std::string (common_json::*)(int) const;
```

各translation unitはx64/MSVC、64-bit pointer、`std::is_trivially_copyable_v`、
`sizeof(pf3_json_dump_member_fn) == sizeof(FARPROC)`を`static_assert`する。不成立ならbuild失敗であり、別castへ
fallbackしない。

runtimeはpinned `llama-common.dll`からexact symbolを一回解決する。forwarded exportを拒否し、PE section range、
`GetModuleHandleEx(FROM_ADDRESS)`、`VirtualQuery.AllocationBase`がいずれもexact loaded moduleを示すことを検査する。
addressはexecutable section内でなければならない。address値はprivateにも永続化せず、public evidenceへ出さない。

zero-initialized PMFへ`memcpy`でFARPROC object bytesを一度copyし、別FARPROCへreverse-copyして元objectと
byte-equalかつpointer-equalであることを検査する。PMFを整数化せず、union、`reinterpret_cast`、C-style cast、
free-function trampolineを使わない。全検査後だけbindingを有効化し、`(parsed.*dump_member)(-1)`という
compiler-generated member callを一回行う。`sizeof`とroundtripだけでは意味的ABI proofにしない。

## 4. compile recordのauthorityと輸送

新しいCLI/config/input fieldは加えない。builderは2節からactual-smoke結果を除いたclosed
`Pf3DumpMemberAbiCompileRecordV1`とそのcanonical SHAをgenerated headerとしてchildへcompile-inする。build
manifestはcompile record bytes/SHA、child exe SHA、build certificate SHAを一意に結ぶ。smoke承認後に更新する
execution bundleだけがbuild manifest SHAとqualification SHAを期待値として持つ。

childは起動後、`common_json`生成前に次のclosed `Pf3DumpMemberAbiRuntimeRecordV1`を構築する。

```text
schema_version=aiwolf.pf3-dump-member-abi-runtime.v1
compile_record_sha256
_MSC_VER, _MSC_FULL_VER, _MSVC_LANG, dynamic_crt, iterator_debug_level
pmf_mode, pmf_size, pmf_alignment, farproc_size, pointer_size
pmf_trivially_copyable, decorated_symbol, export_kind
export_nonforwarded, address_in_pinned_module, address_in_executable_section
getmodule_owner_matches, virtualquery_allocation_base_matches
pmf_roundtrip_bytes_match, pmf_roundtrip_pointer_match
binding_enabled
```

SHAはlowercase 64 hex、macro/size/alignmentはnonnegative integer、残るcheckはboolean、enum/stringは上記literal
だけを許す。single-inheritance x64 positiveでは`pmf_size=farproc_size=pointer_size=8`を要求する。

child内のpre-call比較はcompile-in recordに対する全field exact一致、全check `true`、`binding_enabled=true`を要求する。
失敗時はcanonical `Pf3DumpMemberAbiRuntimeFailureV1`の
`{schema_version,status=UNKNOWN_ABI_IDENTITY,failing_check,parse_calls=0,dump_calls=0,emitter_calls=0}`
だけを出す。`failing_check`は`COMPILE_RECORD`、`COMPILER_MACROS`、`PMF_MODE`、`PMF_SHAPE`、`SYMBOL`、
`EXPORT_DIRECT`、`ADDRESS_MODULE`、`ADDRESS_SECTION`、`GETMODULE_OWNER`、`VIRTUALQUERY_BASE`、
`ROUNDTRIP_BYTES`、`ROUNDTRIP_POINTER`だけで、自由文を出さない。出力不能なcrashもparentがruntime failureとしてsealする。

順序は、compile/runtime record構築→child内exact比較→成功時だけparse/dump→runtime recordとcall countをresultへ
emit→parent exact比較、である。parentはchild exe pin、build manifest、execution bundle、runtime recordを比較する。
extra/missing/duplicate、noncanonical、1-bit差は拒否する。pre-call gate前のJSON parse、dump、emitter、
binding有効化は0回である。T532既定どおり先行し得るvocab-only model loadの順序は変更しない。自己申告config、
任意path、symbol文字列だけをauthorityにしない。

## 5. model-free mock

同じcompiler executable、closed argv、x64、`/MD`、SDKでmock DLLと別mock callerを作る。DLLはbase-clause無し
classの`std::string dump(int) const`をdecorated memberとしてexportする。receiver sentinelは`51`、indentは`-1`、
expected UTF-8 bytesは`{"receiver":51,"indent":-1}`、dump/destructor countは各1とする。callerは3節と同じ
typed PMF、module-owner検査、memcpy bindingだけを使う。

callerはcanonical `Pf3DumpMemberAbiMockResultV1`だけを出す。

```text
schema_version, status
call_count, receiver_expected, receiver_observed
indent_expected, indent_observed
returned_utf8, returned_size, returned_sha256, returned_exact_match
destructor_count, cpp_exception=false, seh=false
timed_out=false, exit_code=0
mock_dll_sha256, mock_caller_exe_sha256
```

builderはclosed key/type、canonical bytes、全expected値を検証した後だけresult SHAとcertificateを発行する。DLL/caller
各source/binary/argvを別々にhash束縛する。mock throw/SEH/timeout、symbol/owner/PMF差、same-value別schema、
extra/missing/duplicateはcertificate 0である。旧free-function typedef/call断片がsourceにあればstatic test FAILとする。

## 6. actual DLL ABI smokeとgate順

mockはactual DLLのbuild provenanceを証明しないため、full proof前にpinned actual DLLのmodel-free smokeを必須とする。
smoke childは固定UTF-8 `{"pf3":1}`だけをactual static `common_json::parse`で一回parseし、3節のtyped PMFで
`dump(-1)`を一回呼び、exact同bytesを確認してactual destructorを一回正常returnさせる。grammar emitter、model、
tokenizer、sampler、provider、server、GPUは0回である。

smokeはactual DLLと全dependencyをT532と同じread handle pin、safe load policy、module event ledgerで保持する。
owned child 1、deadline 30秒、retry 0、stdout/stderr bound、必須reapとする。結果
`Pf3DumpActualAbiSmokeResultV1`はstatus、parse/dump/destroy call count各1、固定input/output UTF-8、exact-match、output size/SHA、
compile record SHA、actual DLL/dependency/module-ledger SHA、exception/SEH/timeout、exit/cleanupだけをclosedに持つ。
address、path、PID、raw loader logは出さない。failure、crash、module drift、evidence/seal失敗は
`UNKNOWN_ABI_IDENTITY`、full proof child 0である。

### 6.1 既存owner・module・evidence契約の再利用

smoke runnerはT541の`TokenPathPrivateEvidence`と`_claim_private_v2`を変更せず再利用する。fresh owner-private
container、owner-only ACL、non-reparse、開始時empty、exclusive `claim.json`、一回run ID/nonce、descriptor
reread、fsync、handle rename、32 MiB全体上限をT532/T538/T541と同じ順で適用する。保存memberは
`claim.json`、`detail.json`、任意の`child-stderr.bin`、`manifest.json`、`seal.json`だけとし、duplicate/extraを拒否する。

moduleはT532実装の`ModuleIdentity`と`ModuleEvent`、`validate_module_sets`をそのまま使う。private ledger projectionは
`module_pre`、`module_post`、`module_events`の3 keyだけで、各identityは
`final_path,volume_serial,file_id,size,sha256,classification,version`、各eventは`ordinal,kind,module`だけを持つ。
pre/post listはsnapshot順を保持し、eventは`ordinal=0..N-1`順を要求する。mappingは既存`canonical_bytes`
（UTF-8、sorted keys、compact）でbytes化してSHAを得る。pathを含むprojectionとSHAはprivateに限り、publicへ出さない。

smoke固有`Pf3DumpAbiSmokeStateV1`は次のclosed sequenceだけとし、汎用frameworkへしない。T532の同名stateは
同じ意味で再利用し、実行しないgrammar/sampler/accounting stateを成功扱いで通過させない。

```text
NEW -> CLAIMED -> INPUT_BOUND -> TOOL_BOUND -> CHILD_STARTED -> NATIVE_LOADED
 -> MODULE_PRE_VERIFIED -> DUMP_VERIFIED -> MODULE_POST_VERIFIED
 -> CHILD_REAPED -> PRIVATE_SEALED -> SMOKE_PUBLISHED
```

任意の失敗は追加childなしでcleanupし、証拠を保存できれば`UNKNOWN_SEALED`、保存不能ならpublic 0で終了する。
`detail.json`は`aiwolf.pf3-dump-abi-smoke-private.v1`としてstate history、runtime recordまたはfailure、fixed
input/output、call counts、module projection、child lifecycle、closed failureだけを保持する。manifest/sealは既存
`aiwolf.pf3-token-path-manifest.v1`のsorted file `{name,sha256,size}`と既存seal
`{manifest_sha256,aggregate,publication_candidate,canonical_state_at_seal}`をexact再利用する。

public作成は既存`_publish_public`のexclusive partial、fsync、atomic rename、rereadを再利用し、closed
`aiwolf.pf3-dump-abi-smoke-public.v1`の`task,run_id,status,child_count,duration_seconds,provider_count,
inference_count,server_count,gpu_count,game_count,actions_count,approved_hashes`だけを許す。`status`は
`ABI_COMPATIBLE|UNKNOWN_ABI_IDENTITY`、countはchild開始前0/開始後1および他全0、durationはfinite nonnegativeとする。
`approved_hashes`のexact keyは`runner_source`、`windows_source`、`build_helper_source`、`actual_smoke_source`、
`actual_smoke_exe`、`build_manifest`、`build_certificate`、`llama_common`、`dependency_manifest`である。固定JSON、output、
runtime record、module projection/SHA、stderr、path、PIDはprivateである。seal前publication、extra key、public
write/reread失敗はqualification発行0、`UNKNOWN_ABI_IDENTITY`、full proof 0とする。

循環を避けるgate順は固定する。

1. 本設計を独立design reviewする。
2. builder、mock、smoke、proof修正を実装し、model-free syntheticを実行する。
3. 独立tool reviewはsource/build/static/mockを判定し、actual smokeは`NOT_RUN`のまま実行可能性だけ承認する。
4. 別Testerがfresh private containerでactual DLL smokeを一回だけ実行し、別Reviewerがresult/seal/bindingを判定する。
5. APPROVED smoke resultからqualificationを作りexecution bundleへ固定し、再度tool/binding照合後だけ別taskのfull proofを許可する。

smoke成功は同じDLL/dependency/header/child/toolchain/certificate SHAに限り再利用できる。一項目でも変われば再利用せず、
新task・新承認なしにsmokeまたはproofを走らせない。

## 7. fail-closedとacceptance

identity、compile/runtime record、mock、smokeの一項目でも欠ければ`UNKNOWN_ABI_IDENTITY`、PF3 `UNKNOWN`、budget
`UNSET`、provider gate `CLOSED`とする。dump call後のC++ exception、SEH、fast-fail、canonical mismatchは既存child
failure/cleanup契約で`UNKNOWN`へ閉じ、同runでretryしない。strict canonical比較、duplicate-key拒否、emitter一回、
resource上限、private/public境界は維持する。

acceptanceは次の全てである。

1. 旧free-function binding/fallbackがsourceとstatic testで0件。
2. build certificate、qualification、compile/runtime record、mock result、actual smoke resultがclosedで全SHAへ一意に結合する。
3. direct executable export、module owner、PMF mode、no-base、byte bindingをcall前に検査する。
4. mock正負testが有限終了し、独立Testerのactual smokeが一回でPASSするまでfull proof 0。
5. focused synthetic、既存回帰、`check_docs`、diff-checkがPASSする。

成立しない場合はPMF近似を追加せず、T542の正規link bridgeを別design gateへ戻す。
