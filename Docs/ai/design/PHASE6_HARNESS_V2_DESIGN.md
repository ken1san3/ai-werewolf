# Phase6 測定器 v2・事前ゲート詳細設計

Status: APPROVED
Task: T512
Responsibility: Architect
Design Gate: PRODUCES DESIGN

## 1. 目的と権限境界

本設計は、`PHASE6_REWRITE_BASIC_DESIGN.md` §4を、providerを使わず実装・検証できる有限の
CLIと純関数へ具体化する。対象は入力cache、source freeze、PF1〜PF3、run所有権とHTTP client再利用の
offline検査である。T512で実装してよいのは `scripts/` と `tests/` のtest-only fileだけとする。

次は本設計の権限外である。

- provider・LLM server・token生成、`probe`、`run`、通常game、Master Run。
- `ai_client/`、`server/`、`protocol/` の変更と、製品v2の実装。
- 保存済みraw・注釈の再採点、旧runの再開・補完。
- validator、privacy、authority、所有判定の緩和。

CLIは `probe` と `run` という名前を予約するが、T512実装では必ず
`COMMAND_DISABLED_BY_AUTHORITY` を返す。無効判定は出力directory作成、port確認、process確認、
HTTP client構築、tokenizer起動より前に行う。環境変数や隠しoptionで解除できる経路を作らない。
PF4は `NOT_RUN` と記録し、preflight全体のPASSへ算入しない。

本測定器が証明するのは、与えられたbytes・schema・候補・token証拠に対する有限の構造的事実だけである。
PF1はtokenizer内部のgrammar変換、モデルの選択確率、品質を証明しない。PF2はモデルが候補を選ぶことを
証明しない。PF3の合法反例は予算不足を証明できるが、任意stringを含むschemaの最大token数を証明しない。

## 2. 実装単位と公開入口

新規fileを次に限定する。

- `scripts/phase6_harness_v2.py`: CLI、純関数、immutable report型。
- `tests/test_phase6_harness_v2.py`: provider 0のfocused test。
- `tests/fixtures/phase6_harness_v2_contract.json`: 非秘密のclosed spec、PF1 target、PF2 expectation。

既存の製品schemaと投影は `ai_client.discussion.projection.discussion_output_schema`、既存のsuite入力は
既存fixture/projection builderを直接呼ぶ。製品処理の複製を作らない。過去候補の再現には、その候補の
既存pure helperを直接使う。旧moduleのtransitive importは、§8.3のside-effect 0 testを通るpure helperに限る。
CLIは旧runnerの `main`、dispatch、process/port observer、HTTP request/client factoryを取得も呼出しもしない。

公開する純関数は次の形に固定する。引数と戻り値はJSON化できる値か、test用protocolだけにする。

```text
analyze_pf1(request_bytes, target) -> Pf1Result
check_pf2(expectation, inventory) -> Pf2Result
check_pf3(contract, schema, evidence) -> Pf3Result
prepare_cache(spec_bytes, task_id, private_factory, private_path_guard) -> PrepareResult
verify_cache(cache_dir, expected_spec_sha256) -> VerifyResult
aggregate_preflight(pf1, pf2, pf3) -> PreflightResult
```

`target` は `schema_pointer`、`reachable_routes`、`leading_selector`、`act_field`、期待partitionを持つ。
JSON PointerはRFC 6901形式で、schema本体へのpointerだけを許す。`expectation` はcase ID、profile、
expected act、必要候補selectorを持つ。
`contract` はstage ID、model/tokenizer identity、`max_tokens`、context、判定方式を持つ。

CLIは次だけを公開する。

```text
python scripts/phase6_harness_v2.py prepare --spec tests/fixtures/phase6_harness_v2_contract.json --task-id T512HARNESS
python scripts/phase6_harness_v2.py preflight --locator LOCATOR_ID --out REPORT
python scripts/phase6_harness_v2.py report --preflight REPORT
python scripts/phase6_harness_v2.py probe ...   # 常に無効
python scripts/phase6_harness_v2.py run ...     # 常に無効
```

exit codeは `0=PASS`、`2=FAIL`、`3=UNKNOWN/NOT_RUN`、`4=COMMAND_DISABLED_BY_AUTHORITY`、
`5=INPUT_OR_INTEGRITY_ERROR` とする。例外をPASSへ変換しない。

## 3. 共通の三値契約

各gateは `PASS`、`FAIL`、`UNKNOWN` のいずれかを返す。未実行は結果の欠如ではなく `NOT_RUN` として
保存し、集計ではUNKNOWNと同じく合格に数えない。集計順は `FAIL > UNKNOWN/NOT_RUN > PASS` とする。
一つでもFAILなら全体FAIL、FAILが無く一つでもUNKNOWN/NOT_RUNなら全体UNKNOWN、全てPASSだけがPASSである。

共通reportの必須fieldは次とする。

| field | 契約 |
|---|---|
| `version` | const `phase6-harness-v2.preflight.v1` |
| `prepared_manifest_sha256` | 検証済みmanifestのSHA-256 |
| `status` | `PASS` / `FAIL` / `UNKNOWN` |
| `provider_calls` / `inference_calls` | T512ではともに整数0でなければFAIL |
| `pf1` / `pf2` / `pf3` / `pf4` | 各gateの結果。PF4は `NOT_RUN` |
| `source_freeze_sha256` | source freezeのcanonical JSON SHA-256 |
| `started_at_utc` / `ended_at_utc` | 計測時刻。品質値や決定乱数に使わない |
| `errors` | 閉じたerror code配列。秘密本文・prompt・token IDを含めない |

欠測、fixture不一致、未対応schema、identity不一致、hash不一致はUNKNOWNまたはintegrity FAILであり、
既定値の補完、行の除外、予算拡大で隠さない。

## 4. prepare cacheとsource freeze

### 4.1 closed PrepareSpecV1

入力specは `tests/fixtures/phase6_harness_v2_contract.json` のみを受理し、repository rootからresolveしたpathと
実装に固定したSHA-256が一致しなければUNKNOWNとする。JSONはduplicate keyを拒否し、次のtop-level field以外を
拒否する。path、module、callable、class名をspecから受け取らない。

```json
{
  "version": "phase6-harness-v2.prepare-spec.v1",
  "suite_id": "phase6-synthetic-32-v1",
  "case_ids": ["G01-1", "G01-2", "...", "G16-2"],
  "seeds": [4242027, 4242028, 4242029],
  "profiles": ["PRODUCT_V1", "I1", "P2_PLAN", "MINIMAL", "T510_PLAN",
               "K1_KIND_FIRST", "WP2_CHAT_PLAN_FIXTURE", "GB1", "T506_CHOICE32"],
  "pf1_targets": [{"profile": "PRODUCT_V1", "registry_key": "PF1_PRODUCT_V1_G01"}],
  "pf2_expectations": [{"case_id": "G01-1", "profile": "GB1",
                         "registry_key": "PF2_G01_TRIGGER_REPLY"}]
}
```

`case_ids` はG01〜G16の各1/2を表示順で32件、seedは上記順と完全一致させる。profile、PF1 target、PF2
expectationは内部のclosed registry keyだけを使い、重複、未知key、順序差、case/profileの組合せ違反を拒否する。
registryはbuilder、serializer、schema route、expectation builderをコード側で固定し、spec値からimportしない。

profileの生成domainもregistryで固定する。

| profile | domain |
|---|---|
| PRODUCT_V1 / GB1 | 32case×3 seed |
| I1 / P2_PLAN / MINIMAL / T510_PLAN / K1_KIND_FIRST | G01-1×seed 4242027だけ |
| WP2_CHAT_PLAN_FIXTURE | case/seedを持たないschema fixture 1件 |
| T506_CHOICE32 | G01-1×seed 4242027の保存counterexample 1件 |

domain外tuple、同tuple重複、必要tuple欠測を拒否する。case×seed×profileという表現は、このeligible domainの
直積要素だけを指し、全profileを無条件に96行へ広げない。

全32caseについてPF2 entryを一つ持つ。既存fixtureの `expected_acts` が空のcaseは
`registry_key=PF2_NO_EXPECTED_ACT`、G14は `PF2_NONE_NO_CANDIDATE`、返信を必要とする
G01/G02/G07/G16はcaseごとの `PF2_TRIGGER_REPLY_<case_id>`、G03は `PF2_QUESTION_SUBJECT_<case_id>`、
G04-1は `PF2_OPINION_BASIS_G04_1`、G04-2は `PF2_ALTERNATIVES_G04_2` とする。自然文や
`expected_acts` から実行時に必要IDを推測しない。fixture作成時にこの対応と既存case metadataのhashを固定する。

### 4.2 owner-private cache

`prepare` は `tests.fixtures.phase6_evidence.create_private_evidence_container` を固定base
`logs/phase6-private-evidence`、`evidence_kind=synthetic`、検証済みtask IDで一回だけ呼び、その新規container内に
cacheを作る。任意 `--out` は廃止する。既存DIR再利用、workspace内の別DIR、pytest temp、絶対path指定を拒否する。
helperのWindows/Python/専用base/reparse検査に加え、作成前後に全path componentがsymlink/reparseでないこと、
resolve後parentが固定 `logs/phase6-private-evidence/synthetic` であること、synthetic親とcontainerがowner-private ACL
条件を各々満たすことを検証する。ACLを観測できない場合はUNKNOWNで、cacheを作らない。partial作成時もpublic
reportへpathを出さない。

ACL判定は既存 `scripts.phase6_private_review._windows_private_path` をsource hash固定して再利用し、DACL parserを
再実装しない。このguardはownerがprocess user/owner SID、DACL present/non-NULL、allow SIDがowner/OWNER RIGHTS/
SYSTEM/Administratorsだけであることをread-onlyで検査する。helper呼出し前にsynthetic親が存在すれば検査し、
存在しなければhelper作成直後に検査する。container作成直後とmanifest確定前にもcontainer自身を検査する。
一回でもfalse、例外、観測不能ならUNKNOWN/拒否とする。親子DACLのhash同一性やinheritance flag同一性は要求せず、
ACLを変更・修復しない。import source SHAとguard結果だけをprivate freezeへ記録する。

同じcaseのprojection、canonical input、request bytes、schema、inventoryを一回のbuilder結果から派生し、private
artifactとしてexclusive作成する。後段はbuilderを再呼出しせずcacheを読む。cache locatorは専用baseからのopaqueな
container名とmanifest SHAだけを別のpublic locatorへ保存し、絶対pathを保存しない。
`task_id` はhelper既存regexの部分集合として、先頭英大文字、英大文字・数字・underscore・hyphenだけの9〜32文字を
作成前に要求する。helperの許容範囲自体は変えない。helperのUTC basename suffixはhyphenを含め23文字なので、
`LOCATOR_ID` は作成helperが返したcontainer basenameを登録した32〜55文字のASCII識別子で、slash、backslash、
dot segment、colonを禁止する。preflightはpublic locator registryの完全一致から固定synthetic親直下へだけresolveし、
CLI値をpathとして連結しない。public `--out` はworkspace内の新規JSON fileだけを許し、symlink/reparse/既存fileを
拒否する。

### 4.3 非循環hash DAG

hashは次の一方向だけとする。

```text
PrepareSpecV1 bytes
  -> input_spec_sha256
  -> source_freeze（input_spec_sha256 + path/size/source SHA一覧）
  -> source_freeze_sha256
  -> private artifacts（input/request/schema/inventory）各SHA
  -> output manifest（上記3種のSHAとartifact index）
  -> manifest_sha256（manifest自身には入れない）
  -> public locator/report
```

`case manifest` という曖昧な名前は使わない。入力はPrepareSpecV1、出力はoutput manifestと呼ぶ。source freezeは
output manifestやartifact SHAを参照せず、output manifestは自身のSHAを含まない。Git commitだけを同一性証明に
使わない。利用時にsource freezeとprivate artifactを全照合し、差があれば `FROZEN_SOURCE_CHANGED` または
`CACHE_ARTIFACT_DRIFT` で停止する。

fileは一時名へexclusive作成し、flush後に同一volume内でrenameする。既存containerへの上書き、追記、部分再利用、
自動修復を禁止する。同じspecの再prepareも新containerを要求し、既存cacheはread-only検証だけ許す。

### 4.4 public report allowlist

公開してよいfieldを次に限定する。

```text
version, status, locator_id, manifest_sha256, source_freeze_sha256,
provider_calls, inference_calls,
pf1[{fixture_id,status,reason,branch_count,leading_selector,act_field}],
pf2[{case_id,profile,status,reason,required_count,present_count}],
pf3[{stage_id,status,mode,max_tokens,observed_tokens,evidence_sha256}],
pf4[{status,reason}], errors, started_at_utc, ended_at_utc
```

canonical input、request/schema bytes、prompt、token ID、private valueとその単独hash、低entropy能力結果のhash、
source pointer/ref、actor/channel/authority detail、artifact file名、絶対pathを公開しない。PF1のbranch path/value partition、
PF2のbinding detail、個別artifact SHAはprivate reportだけに保存する。report writerは汎用dict dumpを使わず、この
allowlistから新dictを作る。未知fieldが公開値へ来たら削除ではなく `PUBLIC_REPORT_FIELD` で停止する。

## 5. PF1 — 送信bytesの生成空間

### 5.1 入力と解析単位

PF1はPython上のschema dictではなく、実際に送るrequest bytesを入力にする。UTF-8 strict JSONとして読み、
object pair順を保持する。duplicate key、非有限number、UTF-8不正はFAILである。JSON parserでdict化した後に
並べ直したbytesや、schema生成直後のdictだけで代用しない。

`target.schema_pointer` でrequest内schema rootを取り、`target.reachable_routes` の全routeをrootから辿る。
routeはinline JSON Pointer segmentとlocal `$ref` 解決の列で、対象 `oneOf` までを表す。`$defs` に定義が存在する
だけでは到達とみなさない。registryに無いreachable route、同じtargetへのalias、多重参照数の違い、未解決route、
想定外target数はUNKNOWNとする。全reachable routeを検査し、一つだけ選んで残りを無視しない。

各branchについて、local `$ref` を循環検出付きで解決し、次を別概念として求める。

- raw bytes上の `properties` 先頭キーと、そのキーの `const` / `enum` / nullを含む値集合。
- profileが意図する `leading_selector` のkey、branch内位置、値集合。
- `act_field` のkey、branch内位置、到達act集合。
- `leading selector value -> reachable acts` のpartition。

出力行は必ず次を持つ。

```json
{"route":".../oneOf", "branch_path":".../oneOf/0",
 "leading_key":"kind", "leading_values":["NONE"],
 "leading_selector":"kind", "act_field":"kind",
 "reachable_acts":["NONE"], "support":"SUPPORTED"}
```

branchごとの結果に加え、key/value別partitionを出す。rootから未参照のbranchや `$defs` は結果へ混ぜない。
PASS/FAILは「全branch同じ先頭key」の一般則ではなく、profile registryに固定したroute数、先頭key/value、selector、
act field、partitionの完全一致で決める。WP2 chat planでは先頭 `reply_to` と後続 `act` が別fieldであり、
reply有無がact集合を意図どおり分けるためPASSにできる。任意の共通先頭keyを無条件PASSにしない。
これは生成確率やtokenizer内部grammarのPASSではない。

### 5.2 対応するschema部分集合

対応するのはlocal `$ref`、`$defs`、closed object、`properties`、`required`、`const`、`enum`、`oneOf`、
単純なnullable `anyOf` だけとする。外部ref、dynamic ref、循環ref、`allOf`、`if/then/else`、
`patternProperties`、`dependentSchemas`、discriminatorを複数の方法で制約するbranch、または解析結果へ影響する
未知keywordがあれば、そのtarget全体を `UNKNOWN_UNSUPPORTED_SCHEMA` とする。一部branchだけを除外してPASSに
しない。act集合が空または算出不能でもUNKNOWNである。

### 5.3 fixture別actual bytes契約

全historical fixtureはG01-1、seed 4242027を使う。`p` は既存recovery entryのG01-1 projection、`c` はcase、
`b = phase6_recovery_runner.baseline_body(p, "qw9", 4242027)` とする。最終bytesと現在のSHAは次である。

| fixture | builder | actual serializer | schema root / reachable route | leading / act | bytes SHA-256 | 期待 |
|---|---|---|---|---|---|---|
| PRODUCT_V1 | `b` | `phase6_context_probe.wire_bytes` | `/response_format/json_schema/schema`; `/properties/discussion/oneOf/{0,1}/properties/speech_act/$ref -> /$defs/speech_act/oneOf` | `kind` / `kind` | `f91e9b53902f3be4a972f01f329b1064d80e98146583cbd2b43bd11a510ae45e` | FAIL |
| I1 | `intent_first_probe.candidate_body(b)` | `intent_first_probe.intent_first_wire_bytes` | 同schema root; `/oneOf/{0,1}/properties/intent/properties/discussion/properties/speech_act/$ref -> 各branchの/$defs/speech_act/oneOf` | `kind` / `kind` | `10c77f8b52a471eeae814fed47a71c58cd7a8d16d0618ae177ad3fa7e7f52b11` | FAIL |
| P2_PLAN | `two_call_probe.plan_body(b)` | `phase6_context_probe.wire_bytes` | PRODUCT_V1と同じ2 route | `kind` / `kind` | `0589603f8d927a6bd43a9f01897d464cc2dd10cf9ee6ffc67ac0fc1fcc7dad68` | FAIL |
| MINIMAL | `minimal_suite_adapter.candidate_body(minimal_suite_adapter.bind_case(c,p), "Qwen3.5-9B-Q4_K_M.gguf")` | `minimal_suite_adapter.candidate_wire` | 同schema root; `/properties/speech_act/$ref -> /$defs/speech_act/oneOf` | `kind` / `kind` | `88c1cc6c46c6b9fefbbad8e1915e1864db93b6372e60712bb4403b890df7bff5` | FAIL |
| T510_PLAN | `local_staged_probe.plan_body(b,p)` | `phase6_context_probe.wire_bytes` | PRODUCT_V1と同じ2 route | `kind` / `kind` | `c4d5537f9d8c47abcfa339b6bc22026c902422d34fae609e578e392e711b2ff0` | FAIL |
| K1_KIND_FIRST | `b` | `kind_first_probe.kind_first_wire_bytes` | PRODUCT_V1と同じ2 route | `kind` / `kind` | `fe09db0d2459f0a7ffd83b54229b7caa5c1d89cf5081f91ca560ea4b6e508819` | PASS |

`{0,1}` は両方を列挙する記法で、wildcard実装を許さない。I1の各root branchはそれぞれのlocal `$defs` へ解決し、
targetが計2個であることを固定する。その他は2 routeが同一 `$defs/speech_act/oneOf` targetへ到達することを固定する。
各targetは7 branchでなければUNKNOWNである。

PRODUCT_V1/I1/P2/MINIMAL/T510の観測partitionは、先頭kindがNONEだけ、他actが
evidence/addressee_player_id/causes/confidence等の別先頭keyへ分割されるためFAILとする。K1は全branchでkindが先頭、
kind値とactが1対1なのでPASSとする。単にerror codeだけでなくroute、branch、leading key/value、act集合をassertする。
source/helper/spec hashが変わった場合は上表を自動更新せずUNKNOWNとする。

### 5.4 WP2 reply-first design fixture

`PHASE6_GENERATION_CONTRACT_V2_SCHEMAS.json` SHA-256
`8d3c4be2e2c3c1565afcdbaa73c93a65252f39919a05ae59307b4dbda2b206d5` は
`status=DESIGN_FIXTURE` であり、製品request bytesではない。PF1はraw fixture bytesのschema root
`/schemas/chat_plan`、route `/oneOf`、8 branch、`leading_selector=reply_to`、`act_field=act` を検査する。

期待partitionは次のexact値である。

- reply_to が `r000` または `r001`: ANSWER、REBUTTAL、QUESTION、CLAIM、OPINION_CHANGE。
- reply_to がnull: QUESTION、CLAIM、OPINION_CHANGE、NONE。

全branchの先頭keyはreply_to、2番目はactでなければならない。このexact relationなら
`PASS_DESIGN_FIXTURE`、差があればFAIL、unsupportedならUNKNOWNとする。これはreply-first設計の静的成立だけを示し、
将来製品のactual request serializerについては `NOT_MEASURED` のままにする。製品実装後は同じ期待partitionを
actual request bytesへ再適用し、別のrequest SHAを固定しなければprovider gateを開けない。

## 6. PF2 — 返信・事実候補の充足

PF2は意味を推測せず、contract fixtureに固定した必要selectorがinventoryに存在し、同じprojection、actor、channel、
authorityへ束縛されるかを調べる。`expected_acts` や自然文ruleからrequired IDを実行時に導出しない。

### 6.1 ExpectationV1

各32case×profileのentryは次のclosed shapeを持つ。未知field、重複case/profileを拒否する。

```json
{"version":"phase6-harness-v2.expectation.v1", "case_id":"G01-1", "profile":"GB1",
 "expected_acts":["ANSWER"],
 "alternatives":[{"act":"ANSWER",
   "required":[{"inventory_kind":"REPLY", "selector":"TRIGGER_SOURCE"}]}]}
```

`alternatives` は許されるactごとの候補要件で、少なくとも一つのalternativeが完全ならcase PASSとする。
空の `expected_acts` は `PF2_NO_EXPECTED_ACT` としてNOT_APPLICABLE、NONEは候補不要でPASSとする。
G01/G02/G07/G16のANSWER/REBUTTALはTRIGGER_SOURCE reply、G03 QUESTIONはcanonical player候補、
G04-1 OPINION_CHANGEはcanonical opinion basis、G04-2はCLAIM/NONE/QUESTION各alternativeを固定する。
この表のbytesとSHAはPrepareSpec/source freezeへ含める。

### 6.2 InventoryItemV1とbinding

inventory builderはcache済みprojectionだけを入力とし、次のclosed itemを返す。

```json
{"id":"r000", "inventory_kind":"REPLY", "projection_sha256":"...",
 "actor_player_id":"player-6", "output_channel_id":"public-day-1",
 "source_ref":{"record_kind":"chat","order":3,"visibility":"PUBLIC"},
 "source_pointer":"/canonical_input/...", "source_kind":"TRIGGER_CHAT",
 "read_authority":"AUTHORIZED", "disclosure_eligibility":"PUBLIC",
 "binding_sha256":"..."}
```

`binding_sha256` は同fieldからbinding_sha自身を除いたcanonical JSON bytesのSHA-256である。pointerを同じcached
canonical inputへ実際にresolveし、resolved valueのprivate hashとprojection SHAをprivate reportだけに保存する。
別projection、actor不一致、channel不一致、重複ID、同一pointerの異なるitem、pointer不達、未知authority/visibilityは
UNKNOWNとする。明示的に当該actor/channelへ禁止されたitemはFORBIDDENでFAILする。既定値で補完しない。

reply builderはprojectionのtrigger sourceと同日・同channelの許可されたrecent chatだけを使う。fact builderは既存
`phase6_grounding_basis_probe.fact_catalog` のID/pointerを同じprojectionへ再束縛する。disclose builderは製品projectionが
当該actorへ明示したowner ability candidateだけを使う。actorが知るauthoritative ability recordの明示選択は
DISCLOSEになり得るが、raw role/team、仲間ID、任意private memoryは候補にしない。canonical metadataだけで
read authority、output channel、明示開示laneを証明できない場合はUNKNOWNとし、測定器で新しい製品ruleを作らない。

### 6.3 G01 positive/negative

G01-1/2のTRIGGER_SOURCEは、同じprojectionのpublic CHAT EvidenceRef
`record_kind=chat, order=3, visibility=PUBLIC` にexact resolveする。GB1 inventoryはfactだけでreply collectionが無いため、
negativeは `MISSING_REPLY_CANDIDATE` となる。positive fixtureは同一cached projection SHA、actor、public channel、
上記trigger refを持つREPLY itemを一件だけ追加する。別projectionの同形ref、private ref、actor違い、2件の重複追加は
PASSにしない。

PF2結果は `candidate_presence_authority_only=true` を保持する。証明するのはID存在、同一projection、actorのread
authority、channel/disclosure eligibilityだけで、候補の意味的十分性やモデルが選ぶ確率ではない。public reportは
reason/countだけを出し、ref/pointer/binding detailを出さない。

## 7. PF3 — token予算

### 7.1 判定方式

PF3はstageごとに次のいずれか一つを使う。

1. `FINITE_EXHAUSTIVE`: schema上の論理値だけでなく、providerが生成しvalidatorが受理し得るraw wire bytesの全domainを
   有限列挙したことを、grammar/serializer binding、列挙器version、入力domain hash、件数、各bytes hashで証明できる
   場合だけ最大値を返す。JSON値が有限でも、任意whitespace、key順、escape表記をvalidatorが受理するならraw wire
   domainはその事実だけでは有限・完全と証明できない。canonical serializerが一表現を作れるだけでは不十分である。
   全wire件のnative token計数がKNOWNなら、最大値＋EOS reserveが `max_tokens` 以下でPASS、超えればFAIL。
   欠測または全wire完全性証明の欠如はUNKNOWN。
2. `LEGAL_COUNTEREXAMPLE`: 厳密schema validatorを通る一つのexact bytesと、それに束縛されたnative token証拠が
   `max_tokens` を超えればFAIL。この方式はPASSも最大値も返さない。超えない証拠しか無い場合はUNKNOWN。
3. `UNBOUNDED_OR_UNPROVEN`: 任意string、列挙不能なbounded string、未対応tokenizerではUNKNOWN。

`maxLength` があるだけでnative token最大を軽いwitnessから推定しない。入力側contextも、最大入力bytesと
native token証拠が有限に束縛されない限りUNKNOWNとする。WP2の本文段は任意stringを含むため、T512時点では
`UNBOUNDED_OR_UNPROVEN` でprovider gateを閉じたままにする。

T512のtest-only実装は全wire domain列挙器とgrammar完全性callbackを実装しない。`FINITE_EXHAUSTIVE` が指定されても
callbackが無い、または `accepted_wire_domain_complete=true` とbinding hashを証明できなければ
`UNKNOWN_EXHAUSTIVE_WIRE_ENUMERATOR_UNAVAILABLE` を返す。現実測する方式は `LEGAL_COUNTEREXAMPLE` と
`UNBOUNDED_OR_UNPROVEN` だけである。これにより有限な論理schemaを根拠に予算PASSを誤って出さない。

### 7.2 T506 choice 32 tokenの保存証拠

既存証拠を次のSHA-256で束縛する。

- `logs/t507-choice-budget/formal-binary-v2/result.json`:
  `79e8bb4aca7e48a1cd523258f0ced6b7c48b043464a4b1f6b2452a69ba11c511`
- `logs/t507-choice-budget/formal-binary-v2/freeze.json`:
  `1279d36e20954deadf3d7e0996024e0162f0effbbe178aa4dd52a6526ead9bc5`

このresultは168行、`provider_calls=0`、`inference_calls=0`、全行KNOWNで、gm12の観測最大51 tokenを含む。
51はこのcost-only fixture集合内の最大であり、任意stringや全schemaの普遍最大とは呼ばない。

32 token不足の回帰には、`phase6_choice_budget_probe.fixtures()` で再構築できるgm12の
ANSWER、facts=1、kind-first、prettyのbytesを使う。raw SHA-256は
`f3a1627db619a9e3c36c5da4fc6aaa5dbe5ea9c3c054a2613f5bfe0e41234b4d`、保存native countは37である。
G01-1の実projectionから `fact_catalog` と `choice_schema` を作り、`f000` が実際のenumに含まれること、
再構築bytesがそのschemaに合法であること、bytes hash・model/tokenizer identity・freeze hashが一致することを
全て検査する。これにより32を超える合法反例としてFAILを確定する。toy fixtureの `f063` や最大51だけを根拠に
実case合法性を主張しない。

保存証拠が一致しない場合はUNKNOWNとする。必要なら、独立承認後のoffline実装で、同じexact bytesを既存
native tokenizerのvocab-only/offline modeへ与えるcallbackを別途注入できる。CLI既定経路は外部binaryを
自動起動しない。tokenizer callbackはHTTP、model inference、GPU generationを許さず、argv、binary/model hash、
stdin hash、token count、exit、timeoutだけを記録し、token IDsと本文を公開しない。

## 8. 所有権、PID再利用、HTTP再利用

T512ではprocess/port/HTTP APIを一切呼ばず、immutable snapshotと純粋な状態遷移だけを実装する。将来
`probe/run` を有効にする別設計は実observer/transport adapterと本state machineの互換性を別に承認する。

### 8.1 snapshot型

```text
LeaseIdentity(host_id, pid, creation_time_ns, executable_sha256, nonce128,
              manifest_sha256)
ProcessObservation(status, host_id, pid, creation_time_ns, executable_sha256, alive)
ListenerObservation(status, endpoint, listener_count, owner_pid,
                    owner_creation_time_ns, owner_executable_sha256)
TransportObservation(event, connection_id, reconnect_count, owner_pid,
                     owner_creation_time_ns, response_complete)
```

全型はfrozen dataclass相当で、型・範囲・閉じたenumを検査する。`status` はOBSERVED、MISSING、ACCESS_DENIED、
AMBIGUOUSだけ、transport eventはOPEN、RESPONSE、DISCONNECT、RECONNECT_ATTEMPT、CLOSEだけとする。PID、creation
time、executable、host、nonce、manifest、endpoint owner、connection identity、reconnect countの欠測をfalse/0で
補完せずUNKNOWNにする。nonceは16 bytes、SHAは32-byte lowercase hex、PID/creation timeは正整数に限る。

公開純関数を次に固定する。

```text
evaluate_owner(lease, process, listener) -> OwnershipDecision
transition_transport(state, lease, process, listener, event) -> TransportState
evaluate_cleanup(lease, process_after, listener_after) -> CleanupDecision
```

stateはINIT、READY、CONNECTED、STOP_REQUIRED、CLOSED、REJECTED、UNKNOWN。lease解放は同じhost/PID/creation/
executable/nonce/manifestが一致するownerだけが行う。stale leaseは自動削除せず、非所有processをkillしない。

### 8.2 truth table

| 観測 | 判定/遷移 |
|---|---|
| process/listenerがlease完全一致、listener_count=1 | INIT→READY |
| 同PID・異creation time、異executable/host | REJECTED `PID_REUSE_OR_OWNER_DRIFT` |
| access denied、creation time欠測、owner欠測 | UNKNOWN |
| listener 0または複数、別owner | REJECTED。killしない |
| OPEN、reconnect_count=0、完全owner一致 | READY→CONNECTED。connection IDを固定 |
| RESPONSE、同connection、response complete、owner一致 | CONNECTED維持 |
| connection ID変化またはreconnect_count>0 | REJECTED `IMPLICIT_RECONNECT` |
| DISCONNECT/server exit/listener drift | STOP_REQUIRED。再OPEN禁止 |
| RECONNECT_ATTEMPT | 常にREJECTED |
| CLOSE後process不在かつlistener不在を両方観測 | CLOSED |
| cleanup観測欠測、process/listener残存 | UNKNOWNまたはREJECTED。完了にしない |

同じclient objectを使っただけではconnection同一性を証明しない。T512 fake testが証明するのはこのstate machineだけで、
実HTTP libraryのkeepalive/reconnect挙動は `RUNTIME_TRANSPORT_COMPATIBILITY=NOT_RUN` とする。

### 8.3 importとside-effect境界

T512実装は上記snapshotを引数で受けるだけで、process列挙、port照合、socket、HTTP client生成/request、spawn、killを
行わない。historical fixtureのpure helperをtransitive importすることは許すが、testでimport時と全CLI offline実行時に
`subprocess.Popen/run`、`httpx.Client/request`、socket connect/bind、process/port observerをsentinelへ置換し、呼出し0を
assertする。native tokenizer callbackを明示注入するPF3 testだけは別sentinelとし、保存証拠再利用testでは呼ばない。

harnessは旧runnerの `main`、`execute_call`、`run_block`、dispatch、request、process/client factoryへの参照を保持しない。
pure helper moduleがimport時side effectを起こす場合、そのhelper/profileはUNKNOWNとし、import許可listへ追加しない。

T510 timingの保存値 `aligned_non_generation_fraction=0.6858082458786764` は、生成外時間が大きいという設計入力で
あり、厳密な関数別帰属ではない。入力cacheとclient再利用による改善量をこの値から断定しない。

## 9. PF4と時間推定の扱い

基本設計の2行probeは、疎通と段別clockの観測にだけ使える。2行のp95から96判断のp95、queue tail、完了時刻を
証明してはならない。T512ではPF4をNOT_RUNとする。将来の別承認では、少なくともstage別latency、queue wait、
非生成時間、sample数、sampling方法、信頼区間を分け、deadlineに対する保守的上限を事前固定する。
2行だけなら結果名を `SMOKE_ESTIMATE_ONLY` とし、run開始可否をPASSにしない。

## 10. positive/negative test契約

全testはprovider/inference/process spawnを0に保ち、monkeypatchした禁止入口が呼ばれたら即FAILする。

| ID | 検査 | 期待 |
|---|---|---|
| H-PF1-1 | v1/I1/P2/minimal/T510の送信bytes | 5件すべてFAIL、path/先頭キー/act集合あり |
| H-PF1-2 | K1 kind-first bytes | PASS、全branch先頭kind |
| H-PF1-3 | WP2 chat plan design fixture | reply_to→actのexact partitionでPASS_DESIGN_FIXTURE |
| H-PF1-4 | alias/route数違い/unsupported combinator/cyclic ref | UNKNOWN、branch除外によるPASSなし |
| H-PF2-1 | GB1 G01 | MISSING_REPLY_CANDIDATE |
| H-PF2-2 | v2相当の完全inventory | PASS |
| H-PF2-3 | 別projection/actor/channel、private/重複/未知authority | FAILまたはUNKNOWN、PASSなし |
| H-PF3-1 | 保存T507の37-token合法反例対32 | FAIL_BUDGET |
| H-PF3-2 | hash/model/tokenizer identity drift | UNKNOWN |
| H-PF3-3 | 任意stringを軽いwitnessだけで評価 | UNKNOWN |
| H-PF3-4 | finiteな論理値だが全wire列挙callback/grammar証明なし | UNKNOWN_EXHAUSTIVE_WIRE_ENUMERATOR_UNAVAILABLE |
| H-CACHE-1 | builder call数 | registryのeligible tupleごとに一回、後段0回 |
| H-CACHE-2 | spec未知field/path/callable/profile、hash DAG循環 | 入力拒否、出力manifest自己参照なし |
| H-CACHE-3 | reparse/ACL/source/config/artifact drift | container作成拒否またはFROZEN_SOURCE_CHANGED |
| H-REPORT-1 | private dictへ秘密fieldを混入 | public allowlist writerが停止、秘密/hash/path出力0 |
| H-OWN-1 | 同PID・異creation time | 所有拒否 |
| H-OWN-2 | access denied/複数listener/cleanup欠測 | UNKNOWN/REJECTED、完了なし |
| H-OWN-3 | keepalive切断後の暗黙reconnect | STOP_REQUIRED/REJECTED |
| H-IMPORT-1 | importとoffline全CLI | spawn/process/port/socket/HTTP callable呼出し0 |
| H-AUTH-1 | probe/run CLI | side effect前にCOMMAND_DISABLED_BY_AUTHORITY |
| H-AGG-1 | FAIL/UNKNOWN/欠測の集計 | FAIL優先、欠測はPASSにしない |

## 11. K1〜K8との対応

| trap | 本設計の対策 |
|---|---|
| K1 | PF1は送信bytes順とact集合をbranchごとに出す。dictだけを見ない |
| K2 | PF2で必要返信・事実・開示候補とvisibilityを検査する |
| K3 | PF3三値とPF4 NOT_RUN。UNKNOWNを開始許可にしない |
| K4 | 測定器は条件付き必須を補完しない。schema解析不能はUNKNOWN |
| K5 | test fixtureに生成本文例を追加しない |
| K6 | 生成しない候補の違反0を安全PASSにしない。本測定器は品質採否を出さない |
| K7 | reportに `measurement_validity` と `quality_decision` を分ける。WP3は前者だけ |
| K8 | PF2で返信候補の存在をact選択前に検査できるinventory契約にする |

## 12. 受入条件と次gate

実装着手には本設計bytesへの独立Reviewer承認が必要である。実装後は別の独立Reviewerが、上記test、関連する
既存pure helper回帰、`python scripts/check_docs.py`、diff全体を確認する。WP3完了の必要条件は次である。

1. H-PF1〜H-AGGがprovider 0、process/port/socket/HTTP 0で全件PASSする。
2. v1/I1/P2/minimal/T510をPF1が検出し、K1だけを当該罠についてPASSにする。
3. GB1 G01をPF2が不足と判定する。
4. T506の32 tokenを、保存native証拠と実G01 schemaで合法な37-token反例によりFAILにする。
5. 任意string最大、モデル確率、2行probeからの96行p95を証明したと報告しない。
6. closed spec、非循環hash DAG、owner-private cache、public allowlistをpositive/negative両側で検査する。
7. snapshot state machineで所有identity、PID再利用、listener drift、disconnect/reconnect、cleanup欠測を検査し、
   runtime transport compatibilityはNOT_RUNと報告する。
8. `probe` と `run` は明示的に無効で、provider・process・HTTP callは0である。

この承認と実装reviewは、WP2製品実装、provider使用、probe/run有効化、製品採用を許可しない。
