# Phase 6 v2 PF3 512 token反例候補 — 限定詳細設計

Status: APPROVED

Task: T526

## 1. 結論と境界

本設計は、固定した24件のschema-valid rawを、現行game modelと`llama-tokenize`でprovider 0・
inference 0のまま再encodeし、canonical token数を診断値として保存するtest-only単位である。

現有のexact toolだけでは、次の3点を証明できない。

1. actual response-format compilerが生成したgrammarへのcandidate membership。
2. childが実際にロードした全非system native moduleの完全な集合と解決元。
3. 同じrawを生成できるgrammar-accepted token列の最短長、またはcanonical encode長がその下界であること。

したがって、観測値が513以上でもPF3は`UNKNOWN`のままである。本設計はPF3 `PASS`も`FAIL`も
生成しない。513以上は後続の証明対象を絞る診断witnessであり、512 hard cap不足の反例とは呼ばない。
stage別`max_output_tokens`は`UNSET`、provider gateは閉じたままとする。これはF009 K3の
「不明をPASSにしない」とK7の「測定成立性と品質判断を分ける」を満たす。

次は権限外である。

- provider/server起動、HTTP、socket、model inference、GPU、game、Master、Actions。
- 製品code、schema、本文200文字bound、候補数、512 hard cap、tokenizer/model/configの変更。
- 旧raw、旧annotation、T507の168行の再採点または普遍上限への読み替え。
- actual grammar emitter/matcher、native module observer、decoder、token graph探索器の新設。
- budget freeze、PF3 `PASS`/`FAIL`、RESERVED以降の接続。

## 2. 固定identity

### 2.1 入力artifact

実装は次のSHA-256を定数として持ち、実行前と終了前に照合する。絶対pathはprivate設定から解決し、
public reportへ出さない。

| artifact | SHA-256 | 用途 |
|---|---|---|
| `PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json` | `8d3c4be2e2c3c1565afcdbaa73c93a65252f39919a05ae59307b4dbda2b206d5` | schema companion |
| game model Qwen3.5-9B | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` | vocabulary/model |
| `llama-tokenize.exe` | `a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1` | canonical encode |
| `llama-common.dll` | `32079d938fe545c1d9801b0935cda8468c105cba27795f9ba0ffeddafd2d9ac9` | static native identity |
| `llama.dll` | `2b84a13dc35361309a4bb9745853b0950c1e8b10a7c2d5e4c7e419b0ab9819a6` | static native identity |
| `ggml.dll` | `097c276b838facce28c3eb6fe3b9657c7ba1e0375e7578f5cb1d2a38da704228` | static native identity |
| `ggml-base.dll` | `091def1bb64cb6e8b50118d7e0219dbe79a220998ac3efc5de1a260b6d0cbefc` | static native identity |
| `libomp.dll` | `a12116ba72d1d6820407cf30be23da04ce79d6bb8a71a5ee71759c5a1faa6f1c` | static native identity |
| llama.cpp config | `43e509956d96492cace8393bdc3fd598ab2418315ccdfd82e144b876241f3bab` | build設定 |
| saved converter source | `4d58b73438e97ac4e2d11dbb0309702d6899088d3c44a75a2dcb9c57697bb288` | provenanceのみ |
| official b10697 archive | `1011cb18e52b2a8b0548eed8242299f85f57862fac237c0d258dadbb1ec4fdbc` | provenanceのみ |

build identityは`10697/093adb242`に固定する。`llama-server.exe`や
`llama-server-impl.dll`はこのchildで使用するidentityではないため、canonical countの採用根拠にしない。

上表はdisk上の静的allowlistである。Windows loaderが解決・ロードした全非system module集合を
証明しないため、全行に`runtime_loaded_modules_status=UNPROVEN`を付ける。allowlist不一致は診断値も
採用せずsystemic `UNKNOWN_STATIC_IDENTITY_MISMATCH`とする。一致してもPF3 evidenceへ昇格しない。

### 2.2 schemaとprovider wireの束縛

各stageについて、次を同じrun内で行う。

1. `ai_client.discussion.generation_v2.build_generation_v2_schema`から製品schemaを生成する。
2. companionの対応stage subtreeと、JSON value、property insertion order、canonical compact bytesを比較する。
3. `OpenAICompatibleBackend._request_payload`をofflineで構築し、strict JSON scannerで
   `response_format.json_schema.schema`のvalue spanを抽出する。duplicate key、path重複、型違いを拒否する。
4. wire subtreeをstrict parseし、製品factoryのJSON valueとproperty orderを比較する。

bodyは保存も送信もしない。HTTP/socket/server/provider/inferenceは0である。比較不一致はsystemic
`UNKNOWN_SCHEMA_WIRE_IDENTITY`とし、残候補を`NOT_RUN`にする。

## 3. 固定candidate集合

candidate数は事前に`2 stages × 6 payload classes × 2 serializations = 24`へ固定する。
adaptive追加、random、成功時early stop、旧fixture混入は禁止する。

### 3.1 stage

| stage ID | logical object |
|---|---|
| `MESSAGE` | `{"message": <200 logical chars>}` |
| `CO_DECLARE` | `decision=DECLARE`, `co_option_id=o100`, `claimed_role_option_id=q000`, `comment=<200 logical chars>`, `fact_ids=[f000,f001]` |

IDは`tests/test_phase6_generation_v2.py::catalog`と同じ固定synthetic catalogから取り、companion schemaで
列挙された値と一致させる。catalogはpublic synthetic dataだけを含む。実ゲームstateやprivate dataは使わない。

### 3.2 payload class

各payloadはlogical lengthを正確に200とする。

| class | logical char | JSON表現 |
|---|---|---|
| `CONTROL_NUL` | U+0000 | `\u0000` |
| `QUOTE` | `"` | `\"` |
| `REVERSE_SOLIDUS` | `\` | `\\` |
| `NON_ASCII_RAW` | `漢` | UTF-8 raw |
| `NON_ASCII_ESC_LOWER` | U+FFFF | `\uffff` |
| `NON_ASCII_ESC_UPPER` | U+FFFF | `\uFFFF` |

### 3.3 serialization

- `COMPACT`: separatorは`,`と`:`。property orderはschema orderと一致させる。
- `MAX_SPACE_RULE`: saved converter sourceの`SPACE_RULE = | " " | "\\n"{1,2} [ \\t]{0,20}`で
  `space` nonterminalが挿入される全位置へ、exact bytes `0a 0a`（LF 2個）に続けて`09`（TAB）20個、
  すなわち`b"\n\n" + b"\t" * 20`を置く。位置ごとの別選択やASCII spaceへの置換はしない。

両serializationのexact byte builderはpure functionとし、同じIDから同じbytesを返す。fixture manifestは
candidate ID、stage、payload class、serialization、logical length、raw byte countを持つ。raw本文はprivate
container内だけに置く。

## 4. 合法性とgrammarの境界

各candidateは次のlogical検査を全て通す。

1. UTF-8 strict decode。
2. duplicate-key拒否のstrict JSON parse。
3. 対応する製品schemaに対するDraft 2020-12 validation。
4. `parse_and_validate_generation_v2_candidate_structure`による製品構造検査。
5. parse後valueをcandidate IDから再構築したlogical objectと比較。

この結果は`logical_schema_status=VALID`だけを意味する。actual response-format compilerが生成したgrammar bytes、
rule graph、candidate membership matcherは現有scopeにない。saved converter source hashや手作業の部分導出を
membership certificateとして使わない。全行を次に固定する。

- `actual_grammar_sha256=null`
- `grammar_membership_status=UNPROVEN`
- `provider_legal_raw_status=UNPROVEN`

文書・実装・reportはcandidateを「schema-valid raw」「診断candidate」と呼び、
「grammar-accepted raw」「provider合法raw」「PF3反例」と呼ばない。

## 5. native canonical encode

### 5.1 commandと有限性

各candidate raw bytesをstdinへ1回だけ渡す。

```text
llama-tokenize -m <exact-game-model> --stdin --ids --no-bos --no-escape --no-parse-special --offline -ngl 0 --device none
```

- child最大24、各30秒、run全体15分。
- retry、fallback、別binary、別model、GPU、server起動なし。
- owned processだけを扱い、timeout時はkill後waitする。PIDだけでなくprocess handleとcreation identityを保持する。
- stdoutはstrictな整数ID列だけを許す。stderr、token IDs、raw本文はpublicへ出さない。
- static artifact hashを全child前後とrun終了時に再照合する。

### 5.2 countの意味

成功した行へ`canonical_encode_token_count`を記録する。これは指定CLIが返した一つのencode列の長さであり、
生成時の最短token列でも、grammar samplerが選べる全列の下界でもない。EOS/BOS/special tokenはcommandで
数えず、providerのgeneration accountingとの同値性も証明しない。

現有scopeにはexact detokenize/decoderによるbyte-exact roundtripもない。全成功行を次に固定する。

- `roundtrip_status=NOT_AVAILABLE`
- `minimum_generation_token_count=null`
- `token_lower_bound_status=UNPROVEN`
- `pf3_evidence_status=UNPROVEN`

`canonical_encode_token_count >= 513`でもreasonは
`CANONICAL_ENCODE_EXCEEDS_512_DIAGNOSTIC`であり、PF3 resultは`UNKNOWN`である。512以下は
`CANONICAL_ENCODE_WITHIN_512_DIAGNOSTIC`であり、全raw domain上界を示さない。

## 6. state、failure、集計

### 6.1 run state

```text
NEW -> INPUT_BOUND -> CANDIDATES_BUILT -> LOGICAL_VALIDATED
    -> DIAGNOSTIC_RUNNING -> DIAGNOSTIC_COMPLETE -> UNKNOWN_SEALED
```

systemic failureは任意の途中stateから`UNKNOWN_SEALED`へ移る。candidate-local failureは当該行を
`UNKNOWN`にし、owned cleanup成功後だけ次行へ進む。どの経路も`PASS`または`FAIL`へ移らない。

### 6.2 closed failure matrix

| class | codes | remaining rows | post identity | aggregate |
|---|---|---|---|---|
| candidate-local | `TOKENIZE_TIMEOUT_REAPED`, `TOKENIZE_NONZERO_REAPED`, `TOKENIZE_STDOUT_INVALID`, `TOKEN_COUNT_SHAPE_INVALID` | cleanup成功後に継続 | 実施 | `UNKNOWN_DIAGNOSTIC_INCOMPLETE` |
| systemic input | identity/schema/wire/fixture/private container不一致 | `NOT_RUN` | 可能なら実施 | `UNKNOWN_INPUT_INVALID` |
| systemic runtime | spawn ownership不明、kill/wait不能、child残存不明、post identity drift | `NOT_RUN` | 必須。不能も同class | `UNKNOWN_RUNTIME_INVALID` |
| systemic evidence | private write失敗、manifest finalize失敗、atomic rename失敗、seal失敗 | `NOT_RUN` | 可能なら実施 | `UNKNOWN_EVIDENCE_INVALID` |

candidate-local failure後に別行が513以上でも、aggregateはなお`UNKNOWN_DIAGNOSTIC_INCOMPLETE`である。
本設計は存在反例を確定しないため、候補間の成功でfailureを上書きしない。systemic failure後は既観測countも
PF3 evidenceとして失効する。private evidenceがsealできなければpublic success reportを出さない。

### 6.3 aggregate truth table

| 観測 | public status | PF3 |
|---|---|---|
| 全24行成功、最大513以上 | `UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND` | `UNKNOWN` |
| 全24行成功、最大512以下 | `UNKNOWN_NO_FIXED_CANDIDATE` | `UNKNOWN` |
| candidate-local failureあり | `UNKNOWN_DIAGNOSTIC_INCOMPLETE` | `UNKNOWN` |
| systemic input/runtime/evidence failure | 対応する`UNKNOWN_*_INVALID` | `UNKNOWN` |

## 7. evidenceとprivacy

private evidenceはraw bytes、token IDs、stdout/stderr、absolute paths、process identity、全hash再照合、
行別状態、fixture manifestを保持する。manifest hashはprivate detail全体を束縛する。

public reportはallowlist方式とし、次だけを含める。

- task/run ID、tool/schema/modelの固定公開SHA-256、provider/inference/server count 0。
- candidate ID、logical validation status、grammar membership status、runtime loaded-module status。
- raw byte count、canonical encode token countまたはnull、row status/reason。
- aggregate status、PF3 `UNKNOWN`、最大24、各30秒、全体15分、開始/終了時刻。

public rowにraw SHA-256を含めない。raw本文、token IDs、stdout/stderr、private locator、absolute path、
process command line、環境変数も含めない。syntheticで再構築可能でも例外を設けない。

## 8. test契約

offline unit/integration testは少なくとも次を固定する。

1. candidate集合がexact 24で、ID重複0、logical length 200、adaptive追加0。
2. 製品factory、companion、offline request schemaのvalue/orderが一致する。
3. 24件がstrict JSON、製品schema、製品parserを通る。
4. raw escapingの1-bit変異、property order変異、ID変異、length 199/201を拒否する。
5. native command argv、stdin byte equality、child上限、timeout、kill/wait、retry 0を検査する。
6. canonical count 513以上を注入してもaggregateは`UNKNOWN_DIAGNOSTIC_CANDIDATE_FOUND`でPF3 `UNKNOWN`。
7. 全行512以下でも`UNKNOWN_NO_FIXED_CANDIDATE`でPF3 `UNKNOWN`。
8. failure matrixの各classでremaining row、post identity、public aggregateが表どおりになる。
9. loaded-module proof、roundtrip、shortest-path certificateがnull/UNPROVENであることを検査する。
10. public serializerにraw SHA、raw、IDs、stderr、path、locatorが出ないことを検査する。
11. provider、inference、server、HTTP、socket、GPU、game、Actionsが0であることを検査する。

設計承認後もnative CLI実測はTesterの別責任であり、実行結果からPF3を`FAIL`へ変更しない。

## 9. 現診断の前提と後続gate

T526診断を実行する現在のprerequisiteは、2.1節のstatic artifact、2.2節のschema/wire束縛、private
evidence container、5.1節の既存`llama-tokenize`入口だけである。いずれかが欠ける場合は6.2節の対応する
systemic input/runtime/evidence failureで`UNKNOWN`へ閉じる。以下の将来proof toolはT526診断の
prerequisiteではなく、存在確認も起動も行わない。

PF3 `FAIL`へ昇格するには、少なくとも次を別設計・独立reviewで閉じる必要がある。

1. actual provider wire schemaからexact generated grammar bytes/rule graphを作る同版emitterと、candidate bytesの
   完全なmembership matcher。
2. childのPID/creation identityへ束縛した、全非system loaded moduleと解決元の完全なobserver。
3. exact model/tokenizer/decoderでのbyte-exact roundtripと、grammar-accepted token graph上のshortest path、
   またはcanonical encode数が全生成可能列の下界である機械検査可能なcertificate。

これらをT526実装へ暗黙追加しない。不在は現診断のfailureではなく、PF3 `FAIL`昇格が未実装である
既知の境界を意味するだけである。
将来、束縛済み最短列が513以上と証明された場合にだけ、512 hard capや本文boundをどう扱うかが製品判断となる。
現段階では予算・本文bound・schemaを変更するユーザー判断は不要である。

## 10. acceptance

1. exact 24件を有限生成し、logical schema-validityとcanonical encode診断を分離する。
2. actual grammar membership、loaded module集合、roundtrip、生成最短token長を未証明と明示する。
3. canonical encodeが513以上でもPF3 `UNKNOWN`を維持し、`PASS`/`FAIL`を生成しない。
4. R4の全failureをclosed matrixどおり一意に集計する。
5. public reportからraw SHAを含むprivate/reconstructable detailを除外する。
6. provider/inference/server/game/Actionsを0に保つ。
7. focused test、関連offline regression、`python scripts/check_docs.py`、diff検査がPASSする。
