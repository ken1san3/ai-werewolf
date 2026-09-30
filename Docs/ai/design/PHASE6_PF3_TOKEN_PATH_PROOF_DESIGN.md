# Phase 6 PF3 合法token経路反例 詳細設計

Status: APPROVED

## 1. 目的と判定命題

本設計は、T529でcanonical encode countが512を超えた固定候補のうち1件について、凍結した
llama.cpp build、model vocabulary、actual JSON Schema grammar compiler、grammar sampler、decoderを用い、
**正常EOSまで到達できる一つのexact token経路**を有限offline proofとして検査する。

証明する命題は次である。

> 同じ凍結identityのprovider grammarが、初期状態から各token prefixを順に許可し、exact decoderが
> witness rawへbyte-for-byte到達し、正常EOGで終了する経路が存在し、そのprovider accounting上の
> generated token数が512を超える。

ここで「生成可能」はWP2 §11.1とT531が対象にする**schema grammarの合法raw domain**を意味する。model logits、
top-k/top-p/min-p等のrelative sampling policy、選択確率、特定promptでの到達可能性を含めない。これらを測らず、
actual provider chainで当該tokenが必ず選ばれる、またはfull-vocabulary candidate集合でfilter後も残るとは主張しない。
一方、`ignore_eos`、明示logit bias、別grammar、lazy triggerのようにmodel logitsと無関係に経路をhard excludeする設定は
grammar domainを変えるため§4.4で必ず閉じる。

この命題が成立した場合だけ、Harness §7.1の`LEGAL_COUNTEREXAMPLE`としてPF3を`FAIL`、reasonを
`FAIL_BUDGET`とする。WP2 §11.1の判定対象はschema grammarが許す合法raw全域であり、model logitsやrelative samplerが
正の選択確率を与える部分集合ではない。したがって、これは「全合法raw経路が512以内」という普遍上界に対する存在反例である。
独立reviewでこの正本対応を確認できない場合は、actual sampling可能性へ命題を拡張せず`UNKNOWN_SCOPE_BINDING`とする。
同じrawの短いtokenizationを排除する必要はなく、最短路を計算しない。

本toolはPF3 `PASS`を返さない。証明が一項目でも欠ける場合は`UNKNOWN`である。512 hard cap、本文bound、
schema、model/config、provider gateを変更せず、入力context 8192やPF4の時間成立性も判定しない。

## 2. 固定identityとwitness

### 2.1 native identity

build identityはT526/T529と同じ`10697/093adb242`とし、少なくとも次を開始前、native child READY時、
child終了後、runner終了時に再照合する。

| artifact | SHA-256 |
|---|---|
| game model | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| `llama.dll` | `2b84a13dc35361309a4bb9745853b0950c1e8b10a7c2d5e4c7e419b0ab9819a6` |
| `llama-common.dll` | `32079d938fe545c1d9801b0935cda8468c105cba27795f9ba0ffeddafd2d9ac9` |
| `ggml.dll` | `097c276b838facce28c3eb6fe3b9657c7ba1e0375e7578f5cb1d2a38da704228` |
| corrected `ggml-base.dll` | `091def1bb64cb6e8b50118d7e0219dbe79a220998ac3efc5de1a260b6d0cbefc` |
| `libomp.dll` | `a12116ba72d1d6820407cf30be23da04ce79d6bb8a71a5ee71759c5a1faa6f1c` |
| official archive | `1011cb18e52b2a8b0548eed8242299f85f57862fac237c0d258dadbb1ec4fdbc` |
| `include/llama.h` | `3d1b18eda626c1b9ecf5bda0798e65b974a8f70f30d56726610633a980cb4160` |
| schema converter source | `4d58b73438e97ac4e2d11dbb0309702d6899088d3c44a75a2dcb9c57697bb288` |
| chat source | `abab0d03a111bd198693a518c7cdd07a33326a6a6685cc182a88d4fdae5f61dc` |
| server request parser | `bd3d8ab38b62f0f1ba19afe7a00b93de48a17ce6e063432a1ce7a477d3fcf0b7` |
| server generation accounting | `c8e4d0095d5c87c17e0e1356574fcaf753f9c04d4a83f4df4778ab4a241f9134` |
| deployment server config | `43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab` |
| common sampler source | `f23f8d663932bfd73abac1bd5e99055fc6efb07e21ef27dd7e25af2f2da40bff` |

proof implementation source、C++ shim source、compiler/linker executable、Windows SDK headers/libs、build argv、
生成したproof childもmanifestへ追加し、実装taskでactual SHAをfreezeする。MSVC toolchain
`18.9.12105.275`は発見事実であり、設計時点ではcompiler binary SHA未確定なので`UNKNOWN`をPASSへ変えない。

保存source/headerの安全なrepo内locatorは`logs/t348-sampler/upstream/`である。これは静的API照合用の部分inventoryであり、
build-complete header treeではない。実際に`include/llama.h`がincludeする`gguf.h`と、`common__common.h`が要求する
`llama-cpp.h`は現inventoryに存在しないため、このlocatorだけからのbuildを許可しない。後続toolはpinned official archive
`1011cb18e52b2a8b0548eed8242299f85f57862fac237c0d258dadbb1ec4fdbc`から、build manifestが列挙する相対pathの
header/source closureをprivate build directoryへ展開し、archive memberの重複、absolute/親移動path、reparse、欠落を拒否する。
展開した各fileの相対path、size、SHAをbuild前後に照合し、保存partial inventoryの対応fileとも一致させる。archiveに必要closureが
ない、またはinclude closureを列挙できない場合は`UNKNOWN_ABI_IDENTITY`であり、network/package取得や別headerで補完しない。
absolute deployment pathはpublic evidenceへ保存しない。

### 2.2 witness

唯一のproduct witnessは`MESSAGE.CONTROL_NUL.COMPACT`とする。T529 public診断countは1005であり512から十分離れ、
CO固有fieldや最大whitespaceを必要としない。1005は期待値・回帰検知に使うが、証明authorityは新proof childが
同一区間で得るtoken列、prefix受理、decoder、EOG、accountingの結合結果である。

rawとschemaは旧private evidenceから読み出さない。承認済みT526 builderと現製品
`build_generation_v2_schema("message", catalog)`から一意に再構築し、actual backend request payload内のschema subtree、
companion subtree、factory出力をexact bytesで一致させる。rawはstrict JSON、Draft 2020-12 schema、
`parse_and_validate_generation_v2_candidate_structure`を全て通し、candidate ID、logical U+0000長200、property order、
compact serializationを再検査する。不一致はnative child 0で`UNKNOWN_INPUT_INVALID`とする。

## 3. component境界

### 3.1 Python runner

runnerはidentity pin、private container、witness/schema構築、child ownership、module observation、timeout、evidence seal、
public allowlistだけを担当する。Pythonからnative DLLをloadせず、`ctypes`でby-value structを再宣言しない。

### 3.2 native proof child

単一x64 C++ childをproof境界とする。承認後のtool buildは保存同版header/sourceと固定MSVC/SDKから
`/std:c++20 /EHsc /MD /Brepro`相当で行い、warningをerrorにする。build時にネットワーク、package取得、別header、
fallback compilerを使わない。proof run中にcompileしない。

childは次の順だけを実行する。

1. inherited private input handleとcontrol pipeを検査し、schema/raw/run nonceを読む。
2. absolute pinned DLL handleだけを`LoadLibraryEx`の限定search policyでloadし、必要exportを全て解決する。
3. `llama_model_default_params()`からparamsを得て`vocab_only=true`、GPU layer 0とし、vocabularyをloadする。
   proofで使う全DLL/dependency/model handleを保持し、この後は明示的なload/unload APIを呼ばない。
4. `READY_FOR_MODULE_SNAPSHOT`で停止し、parentのpre module snapshotとwhole-run pin照合が成功するまで待つ。
5. actual `llama-common.dll`のMSVC C++ export `json_schema_to_grammar(const common_json &, bool)`を、
   同版`common_json` header、同一x64 MSVC ABI、dynamic CRTで呼ぶshimへ渡す。`force_gbnf=false`はserver chat sourceと同じ。
6. §4.4のactual sampling closed recordとsaved-source certificateを検査する。
7. `llama_tokenize(vocab, raw, add_special=false, parse_special=false)`でexact token列を得る。
8. `llama_sampler_init_grammar(vocab, grammar, "root")`を作り、各tokenを§4のprefix検査へ通す。
9. 全tokenを`llama_detokenize(remove_special=false, unparse_special=false)`しraw bytesとexact一致させる。
10. 全vocabularyを`llama_vocab_is_eog`で走査し、§4の正常EOGを1つ選ぶ。
11. proof完了後、handleを保持したまま`READY_FOR_POST_MODULE_SNAPSHOT`で停止する。parentのpost snapshot成功後だけ
    private resultを確定し、sampler/model/DLL handleを逆順に解放してzero exitする。

### 3.3 C++ emitter shim ABI

C++例外、`std::string`、`common_json`、native pointerをprocess境界またはPythonへ出さない。child内部に次のPOD境界を置く。

```c
struct pf3_buffer_v1 { uint8_t * data; size_t capacity; size_t size; };
struct pf3_error_v1  { uint32_t code; uint32_t reserved; };
int32_t pf3_emit_grammar_v1(
    const uint8_t * schema_utf8, size_t schema_size,
    struct pf3_buffer_v1 * output,
    struct pf3_error_v1 * error);
```

shimはstrict UTF-8とduplicate-keyを拒否して`common_json`を構築し、actual exported emitterを呼び、結果をcaller所有bufferへ
copyする。必要長は正値、成功0、失敗は固定負値とし、例外を全てcatchする。native allocator所有objectをshim外へ返さない。
callerは§5.2の8 MiB grammar bufferを事前確保し、emitterを1回だけ呼ぶ。buffer不足は再実行せずUNKNOWNにする。
呼出し直前後にschema/input identityを再検査する。C++ mangled symbol名、header SHA、DLL SHA、compiler/CRT
identityが一つでも違えば呼ばず`UNKNOWN_ABI_IDENTITY`とする。

## 4. token prefix、decoder、EOG

### 4.1 prefix受理

各tokenごとに、新しい`llama_token_data{id, 0.0f, 0.0f}`一件だけを持つ
`llama_token_data_array{data, 1, -1, false}`を作る。

1. `llama_sampler_apply(grammar_sampler, &array)`を呼ぶ。
2. logitがfiniteかつ初期値0.0fのままであることを確認する。`-INFINITY`、NaN、別値は拒否。
3. 許可された同じtokenだけを`llama_sampler_accept`へ渡す。

apply前後のtoken ID、ordinal、許可boolをprivate evidenceへ記録する。拒否tokenをacceptへ渡さない。child crash/abort/SEHは
parentが`UNKNOWN_NATIVE_CHILD_FAILED`へ閉じ、同runで再試行しない。全rawのmembershipだけを別parserで確認してprefix証拠の代用にしない。

### 4.2 decoder

全token列を一度に`llama_detokenize`し、負の必要長を使うsize query後にcaller bufferへ再実行する。
返却length、埋込みNULを含むbytes、raw lengthを比較し、完全一致だけを`ROUNDTRIP_EXACT`とする。補助診断として
`llama_token_to_piece`は呼ばず、pieceを収集しない。token IDs、raw、それらのSHAはprivateに限定する。

### 4.3 EOGとprovider accounting

`llama_vocab_n_tokens`の全IDを一度だけ走査し、`llama_vocab_is_eog=true`のID集合をprivateに得る。prefix完了後の同じsamplerへ
EOG候補を一件ずつapplyする。保存実装のapplyはgrammar状態を変更しないため、finite logitとなる候補のうち最小IDを
証拠上の正常EOGとし、その1件だけをacceptする。applyの非変異性を保存source SHAとsynthetic nonmutation testで閉じられない場合は
即`UNKNOWN_EOG_OR_BUDGET`とする。replay fallbackは行わず、不許可EOGをacceptしない。

保存server sourceではOpenAI `max_tokens`が`n_predict`へ入り、各sampled tokenはEOG判定より前に`stats.n_gen += 1`され、
`n_gen == n_predict`で次tokenを生成せずlimit stopとなる。従ってaccounted countは
`path_token_count + 1 accepted EOG`であり、EOGも512へ含む。proofは次を全て要求する。

- `path_token_count`とaccepted EOGを整数として別記録。
- `accounted_generated_tokens = path_token_count + 1`。
- `accounted_generated_tokens > 512`。
- 選択した経路の先頭512 tokenが全てgrammar-accepted non-EOGである。

MESSAGE witnessはT529診断上1005 tokenなので境界から余裕があるが、actual proof countが512以下なら
`UNKNOWN_NO_COUNTEREXAMPLE`でありFAILにしない。stop string、context shift、speculative decoding、model logitsは本toolで動かさず、
文法上生成可能な経路とprovider hard limitの矛盾だけを証明する。

### 4.4 actual sampling closed record

bare grammar samplerの許可だけをactual provider chainの選択可能性へ昇格しない。runnerはactual backend request payload、
固定deployment config、server request parser、chat source、`common_sampler_init` sourceから次のeffective recordを一意に導出し、
private canonical bytesとhashへ束縛する。

| field | FAILへ進めるclosed value |
|---|---|
| response format | `type=json_schema`、`strict=true`、§2.2のexact schema |
| completion limit | `max_tokens=512`、override用`n_predict`なし |
| grammar | actual emitter出力、type `JSON_SCHEMA_GBNF`、root `root`、non-empty |
| `ignore_eos` | exact `false` |
| request/base logit bias | empty。全EOGへの追加bias 0 |
| `grammar_lazy` | exact `false` |
| grammar triggers | empty |
| preserved tokens | empty |
| stop strings | empty |
| reasoning/prefill grammar | witness生成開始前の追加prefix、lazy trigger、別grammar 0 |

payloadで省略されたfieldは「false/empty」と推定せず、hash固定したconfigとparser defaultを適用した結果を保存する。
config parse不能、field重複、未知option、base/request override、上表との差は`UNKNOWN_EFFECTIVE_SAMPLER`である。

さらにhash固定した`common_sampler_init` sourceから、actual ordered sampler chainと各sampler paramsのclosed certificateを作る。
certificateが証明する範囲は、model logitsと無関係なhard exclusionの有無だけである。`ignore_eos`、全EOGまたは当該tokenへの
explicit logit bias、別grammar、lazy trigger/preserved prefix、stopによる早期終了を列挙し、上表どおり全て不在とする。
top-k/top-p/min-p/temperature/penalty等のrelative samplerはinventoryとparamsを記録するが、singleton candidate testで
actual full-vocabulary非排除へ昇格せず、grammar合法domainの判定にも使わない。source branch未対応、chain順序差、未知hard mask、
EOG biasは`UNKNOWN_EFFECTIVE_SAMPLER`である。

prefix tokenとEOGについて、§4.1/4.3のgrammar受理とeffective hard-mask-clear recordが揃った時だけ
`GRAMMAR_PATH_HARD_MASK_CLEAR`とする。`ACTUAL_CHAIN_SELECTABLE`という状態・主張は設けない。

## 5. module、process、所有権

### 5.1 loaded module proof

parentはchildを`DEBUG_ONLY_THIS_PROCESS`相当のimage-load event監視下で開始し、CREATE_PROCESS、全LOAD_DLL、UNLOAD_DLL、exitを
process handle/creation identityと同じledgerへ記録する。各LOAD eventでfile handleとfinal identityを取得し、run終了までpinする。
READY前を含め、一度loadされたnon-system moduleのunload、identity取得不能、event欠落、debug detachは`UNKNOWN_MODULE_SET`である。

childはDLL/dependencyとvocab-only model loadを全て完了しhandleを保持した後、proof call前に
`READY_FOR_MODULE_SNAPSHOT`とnonceを書いて待機する。parentはOS event ledgerと現在の全loaded module snapshotを照合してpreを確定する。
child自己申告、PIDだけ、path文字列だけを使わない。

moduleごとにfile handleを開き、final path、volume/file identity、SHA-256を取得する。Windows system directory配下は
`SYSTEM`として名前・version・file identityをprivateに保存する。それ以外は`NON_SYSTEM`とし、proof child、固定native DLL群、
固定dynamic CRTの承認allowlistとexact一致を要求する。unknown、重複、unopenable、reparse、hash drift、列挙raceは
`UNKNOWN_MODULE_SET`である。pre成功後にchildをreleaseする。proof処理後・unload前の第2READYでpost snapshotを取り、pre集合、
LOAD/UNLOAD event ledger、post集合、全pinned identityが一致した後だけresult確定を許可する。load中だけ現れて消えたmoduleもevent ledgerで
検出されるため、snapshot間の一時load/unloadを見逃さない。

### 5.2 one-shotと有限上限

- runner invocation: 1、witness: 1、native proof child: 最大1。
- child REAL timeout: 90秒、全run REAL timeout: 180秒。
- compiler child、tokenizer CLI、server、HTTP、socket、GPU、provider、inference、game、Actions: 0。
- retry、fallback、別candidate、別model、別binary、同runでのchild再起動: 0。
- timeout時は所有process handleでterminate後waitし、reap確認できない場合は`UNKNOWN_RUNTIME_INVALID`。
- child外のprocessをPIDでkillしない。開始前・終了後にowned child 0を確認する。

入力値をallocationまたはloop boundへ変換する前に、符号、`size_t`/`int32_t`変換、加算、乗算overflowを検査する。
次を超えた時は該当allocation/native call前に`UNKNOWN_RESOURCE_BOUND`とする。

| resource | hard maximum |
|---|---:|
| schema input | 2 MiB |
| witness raw | 16 KiB |
| emitted grammar | 8 MiB |
| path tokens `T` | 4,096 |
| vocabulary tokens `V` | 262,144 |
| EOG tokens `E` | 256 |
| detokenized output | 64 KiB |
| loaded modules / module events | 512 / 1,024 |
| single runner/shim owned allocation | 16 MiB |
| all private evidence before seal | 32 MiB |
| child process committed memory | 2 GiB Job Object limit |

native call上限は、model/default/get/free等の定数callを最大32、`tokenize` 2、`detokenize` 2、
`vocab_is_eog`を`V`、prefix `apply+accept`を`2T`、EOG `apply`を`E`、EOG `accept`を1とし、
合計`V + 2T + E + 37 <= 270,629`とする。grammar emitterは1回だけで、size query再emitをしない。
`token_to_piece`はauthority経路0回とし、補助診断でも本runでは呼ばない。apply非変異性のsource certificateが閉じなければ
EOG replay fallbackを行わずUNKNOWNとするため、`E × T` loopは存在しない。90秒timeoutはこの決定的上限の代用ではない。

本proofはT529の同条件診断再実行ではない。witness数を24から1へ縮小し、canonical countだけでなくactual emitter、prefix sampler、
decoder、EOG/accounting、module集合を新たに同一childで結合する別measurementである。

### 5.3 whole-run identity pin

claim成功後、child作成前にparentが次をread handleでopenし、final absolute path、volume serial、file ID、size、SHA-256を記録する。
directory componentは順にopenしてreparseを拒否する。share modeはreadだけを許しwrite/delete/renameを拒否し、全handleを
child reap、post identity、private seal完了まで保持する。

- model、固定native DLL群、proof child executable、deployment config。
- tool/shim source、build manifest、固定header/source、compiler/linker/SDK manifest。
- product schema factory/backend/fixture builderの承認source manifest。

schema/rawはpathで渡さず、claim済みprivate container内へexclusive writeしたfile handleをparentがpinし、そのexact inherited read handleを
childへ渡す。childはhandle metadataとparentのnonce/identity recordを照合し、path reopenしない。model/DLLはWindows loader/APIがpathを
要求するため、parentがpinしたfinal pathだけをchildへ渡し、child open後のfile identity、loaded module event/snapshot identityを
parent pinへ結合する。同path別object、file ID/size/hash差、share deny不能、pin喪失、model遅延read中のhandle closeは
`UNKNOWN_ABI_IDENTITY`、module結合差は`UNKNOWN_MODULE_SET`とする。

開始前、pre READY、post READY、child reap後、seal直前に全pinのhandle-based identity/hashを再読する。前後のpath hashが同じでも、
途中のhandle/object同一性が証明できなければ成功としない。proof child自身はCreateProcess image handleとparent pinを結合する。
source/build manifestはchildが実行時に読まなくても、承認tool identityの根拠としてwhole-run pin対象から外さない。

## 6. private evidenceとpublication

private containerはabsolute local path、非reparse、owner-only ACL、開始時emptyを要求し、claim fileをstatic hashやchild開始より先に
exclusive createする。run ID、nonce、container file identity、runner/tool identitiesをclaimへ保存し、成功・失敗とも再利用しない。

private manifestは少なくとも次を保持する。

- schema/raw/grammar exact bytesとSHA、token IDs、EOG IDs、prefix ordinal結果。piece bytes/列は収集しない。
- shim/tool/compiler/SDK/source/DLL/model/moduleの全identityとbuild argv。
- child process handle由来identity、module snapshot前後、start/end/timeout/exit/cleanup。
- decoder bytes、accounting式の各項、全state transition、failure code。

各fileをexclusive writeしてhash manifestへ束縛し、manifestをsealへatomic renameする。seal完了前にpublic success/failure reportを出さない。
evidence failureは観測済みFAIL候補を無効化し`UNKNOWN_EVIDENCE_INVALID`とする。

public reportはallowlistで次だけを出す。

- task/run ID、candidate ID、build identity、tool/model/schema/native DLLの承認済み公開SHA。
- logical validation、emitter、prefix、roundtrip、EOG、module、accountingのenum status。
- path token count、EOG reserve 1、accounted count、hard cap 512。
- child count、provider/inference/server/GPU/game/Actions count、duration、aggregate/PF3。

raw、raw SHA、schema/grammar bytesまたはSHA、token/EOG IDs、stdout/stderr、argv、環境変数、module path、private locator、
process ID/creation identityは公開しない。

## 7. state machineとclosed failure matrix

```text
NEW -> CLAIMED -> INPUT_BOUND -> TOOL_BOUND -> CHILD_STARTED
    -> NATIVE_LOADED -> MODULE_PRE_VERIFIED -> GRAMMAR_EMITTED
    -> SAMPLER_CONFIG_VERIFIED -> PREFIX_VERIFIED
    -> ROUNDTRIP_VERIFIED -> EOG_VERIFIED -> ACCOUNTING_VERIFIED
    -> MODULE_POST_VERIFIED -> CHILD_REAPED -> PRIVATE_SEALED
    -> FAIL_BUDGET_PUBLISHED
```

`FAIL_BUDGET_PUBLISHED`へ入るのは全step成功かつaccounted count >512の場合だけである。他はcleanup後
`UNKNOWN_SEALED`へ入る。

| class | 例 | aggregate | PF3 |
|---|---|---|---|
| input | schema/backend subtree、witness、logical validation、static hash不一致 | `UNKNOWN_INPUT_INVALID` | `UNKNOWN` |
| ABI/compiler | export欠落、mangled名、header/toolchain/CRT不一致、shim exception | `UNKNOWN_ABI_IDENTITY` | `UNKNOWN` |
| emitter | parse/emit失敗、空grammar、actual binding不明 | `UNKNOWN_GRAMMAR_EMITTER` | `UNKNOWN` |
| sampler config | config/default/chain不明、EOG bias、lazy/trigger/preserved/stop、chain hard mask | `UNKNOWN_EFFECTIVE_SAMPLER` | `UNKNOWN` |
| prefix | 任意ordinal拒否、apply値不正、accept前検査不能 | `UNKNOWN_PREFIX_REJECTED` | `UNKNOWN` |
| decoder | detokenize失敗、byte/length不一致 | `UNKNOWN_DECODER_MISMATCH` | `UNKNOWN` |
| EOG/accounting | EOG 0、全EOG拒否、正常stop/count規則不明、count <=512 | `UNKNOWN_EOG_OR_BUDGET` | `UNKNOWN` |
| module | snapshot欠測、unknown non-system、前後drift | `UNKNOWN_MODULE_SET` | `UNKNOWN` |
| runtime | spawn/timeout/crash/SEH/kill/wait/ownership不明 | `UNKNOWN_RUNTIME_INVALID` | `UNKNOWN` |
| resource | bytes/count/call/module/memory上限、overflow、narrowing | `UNKNOWN_RESOURCE_BOUND` | `UNKNOWN` |
| evidence | claim/write/manifest/seal/publication不整合 | `UNKNOWN_EVIDENCE_INVALID` | `UNKNOWN` |
| all proof | 全identity/step成功、count >512、cleanup/seal成功 | `FAIL_BUDGET` | `FAIL` |

一度UNKNOWN classへ入ったrunを後続成功でFAILへ上書きしない。child crash後に別childを開始しない。公開report生成失敗時は
private sealを保持し、PF3 canonical stateはUNKNOWNのままとする。

## 8. test契約

### 8.1 pure/synthetic

1. fixed witnessがexact 1件で、MESSAGE/CONTROL_NUL/COMPACT、logical length 200、重複0。
2. product factory、backend actual payload、companion subtreeのschema bytesが一致する。
3. strict JSON/schema/product candidate validationのpositiveと、raw/order/length/schema 1-bit negative。
4. tiny synthetic grammar/vocabularyで、全prefix受理＋roundtrip＋EOG＋count 513を`FAIL_BUDGET`とする。
5. 同じrawへ短い別token列を用意しても、選んだ513経路が全stepを通ればFAILであることを固定する。
6. raw全体membershipだけ成功し途中prefixを拒否するcaseをUNKNOWNにする。
7. decoder 1 byte差、length差、embedded NUL欠落、special flag差をUNKNOWNにする。
8. EOG 0、未完了EOG、EOG前limit、accounting 512/513境界を固定する。
9. effective sampler recordの各fieldを1つずつ変更し、`ignore_eos=true`、EOG bias、non-empty logit bias、lazy、
   trigger、preserved token、stop、別root/grammar、未知chain branchを全てUNKNOWNにする。
10. relative sampler inventoryは記録してもsingleton候補をactual full-vocabulary選択可能性へ昇格せず、状態が
    `GRAMMAR_PATH_HARD_MASK_CLEAR`に留まることを固定する。WP2/Harnessのgrammar-domain bindingが不一致なら
    `UNKNOWN_SCOPE_BINDING`とする。
11. `llama_token_to_piece`呼出し0、piece evidence field不在、apply非変異certificate欠落時の即UNKNOWN、EOG replay 0を固定する。

### 8.2 ABI/process/evidence

12. C shimのsingle-call/copy、buffer不足、invalid UTF-8、duplicate key、C++ exception、allocator非越境。
13. exact export positiveと、各export欠落、header/DLL/compiler/CRT drift negative。partial saved inventoryだけでbuildせず、pinned archiveの
    exact include closure positiveと、`gguf.h`/`llama-cpp.h`欠落、unsafe archive member、member SHA driftをUNKNOWNにする。
14. model params by-value layoutをcompile-time `sizeof/offsetof` manifestとruntime shim self-reportで一致させる。
15. process開始からのmodule event ledger、pre/post一致、foreign non-system、unopenable、一時load/unload、late load、
    snapshot race、PID再利用をUNKNOWNにする。
16. child timeout/crash/SEH、kill/wait失敗、nonce違い、READY前exit、result後未reapをclosed matrixへ固定する。
17. model/DLL/tool/source/input各pinについてrename/write/delete、same-path replacement、handle close、file ID/hash driftをUNKNOWNにする。
18. 全resource boundの上限値・境界+1、負のsize query、`size_t`/`int32_t` narrowing、加算/乗算overflowをcall前拒否する。
19. claim/container reuse、partial manifest、seal/public report failureをUNKNOWNにし、private raw/IDs/pathをpublicへ出さない。
20. one-shot上限、child最大1、retry/fallback 0、provider/inference/server/HTTP/socket/GPU/game/Actions 0。

### 8.3 frozen-source accounting

21. saved server sourceの`max_tokens -> n_predict`、sample後`n_gen += 1`、budget check、EOG checkの順序をexact source SHAへ束縛する。
22. accepted path token数、EOG 1、accounted count、512比較を同一private recordからpublic集計する。
23. count >512でもsampler config/module/pin/evidence/cleanupがUNKNOWNならFAILを出さない。
24. count <=512はPASSでなく`UNKNOWN_NO_COUNTEREXAMPLE`。

## 9. 実装・review・実行gate

1. 本設計を独立ReviewerがAPPROVEDするまでtool source、shim、build scriptを実装しない。
2. tool実装者はproof source/test/build manifestだけを変更し、製品schema/backend/provider設定を変更しない。
3. 実装diffとbuild artifact identityを独立tool reviewする。synthetic testsだけでnative proof成功としない。
4. native proofは別Testerが、承認済みtool hash、empty private container、owner context、one-shot permissionを照合して1回だけ行う。
5. proof結果が`FAIL_BUDGET`でも512/schema/modelを自動変更しない。Mainが証拠を照合し、次の製品設計を別taskにする。
6. UNKNOWNはprovider開始許可ではない。PF3 `PASS`も本toolからは生じない。

## 10. acceptance対応

| requirement | 設計箇所 |
|---|---|
| actual C++ emitter binding | §2.1、§3.2/3.3 |
| token prefix grammar acceptance | §4.1 |
| exact decoder roundtrip | §4.2 |
| EOG/provider accounting >512 | §4.3 |
| actual sampler/server config | §4.4 |
| module identity | §5.1 |
| one-shot/resource bounds/identity pin/private | §5.2/5.3、§6 |
| closed UNKNOWN matrix | §7 |
| synthetic positive/negative | §8 |
| no shortest path / no provider | §1、§5.2、§9 |
