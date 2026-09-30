# Phase 6 v2 品質probe限定改訂設計

Status: DRAFT

## 1. 目的と適用範囲

この文書はT550 WP1の唯一の追加設計である。最初の実LLM測定に限り、生成契約v2の11節と
測定器v2の7節にある普遍的なtoken上界証明を、fixture実測による予算決定へ置き換える。また、
製品capture/leaseを通らないtest-only経路と、その経路で使うprovider所有権検査を定める。

製品の `build_generation_v2_schema` と `parse_and_validate_generation_v2_candidate_structure` は再利用する。
`GenerationCatalogV2` は2.1節のprior validationだけを変更する。製品既定はv1のままとし、
game、action送信、通常brain、capture/lease、PF3 native proof、APIは呼ばない。TとPは品質測定用の候補を返すだけで、
製品stateへcommitしない。

本改訂の「最大」は、ここで定義する有限fixture集合を同一native tokenizerで数えた観測最大である。
任意の合法raw、任意のUnicode本文、grammar全体の上界を意味しない。予算内に収まらない実出力は安全違反ではなく、
当該行の検出済み失敗である。

## 2. D089で確定した入力境界

### 2.1 G04-1の既存priorを保持する限定製品変更

G04-1の保存入力 `suspicion=80, credibility=20` は丸めず保持する。製品 `OpinionBasisV2.prior` は
`type(prior) is int` かつ0以上100以下を受理し、bool、範囲外、他の数値型を拒否する。
モデルが選ぶ `opinion_current` の語彙は `0,25,50,75,100` のまま変えない。
`allowed_current` の正規値は、この5値からpriorと同値の値だけを除いた順序付きtupleとする。
従ってprior 80/20では5値全部、prior 75では75以外の4値になる。fixture、baseline、schema上のcurrent語彙を
変更せず、製品変更はこのvalidationと対応testに限る。

### 2.2 非chatの4ケースは元triggerで測る

28 chatケースはT→Pの2段、G13-1/2は製品 `pre_vote` schemaの1段、G15-1/2は製品
`co_opportunity` schemaの1段とする。元trigger、入力、32 case×3 seedの96行分母を維持し、chatへの変換、除外、
未実施扱いをしない。非chatはP入力adapterを通さず、製品schemaとcatalog束縛、製品structure validatorで検査する。

## 3. fixture実測による予算

### 3.1 固定identityと測定単位

prepareは32 case、seed 4242027〜4242029の96行について、canonical input bytes、projection、catalog、schema、
messages、request bodyを一度だけ構築し、SHA-256とsource SHAをcacheする。次も同じclosed recordへ含める。

- runtime executable、model file、model path、build、chat template、tokenizer/model identity
- 製品schema builderとstructure validator、test-only builder、serializerのsource SHA
- case ID、seed、stage、catalog/schema/messages/body/wire SHA、budget witness SHA
- native apply-template結果のprivate SHA、入力token数、output fixture token数、余裕、固定budget

同じlogical valueでもraw bytesが違えば別witnessである。token ID、rendered prompt、fixture本文、private pathは
private evidenceだけへ保存し、公開結果はhash、件数、token数、status、reasonだけにする。identity欠測、cache不一致、
生成前後のsource変化はUNKNOWNとして生成call 0で停止する。

### 3.2 chat plan予算

84 chat行の各実catalogから製品 `build_generation_v2_schema("chat_plan", catalog)` を作る。schemaのenum、const、nullable、
array boundを辿り、oneOfの各branchについて配列を最大件数まで満たし、各scalar enum値を少なくとも一度その位置へ置く
決定論的な最大形candidate集合を作る。Cartesian積や全raw表現の列挙はしない。object keyは製品schemaの挿入順、配列はcatalog順を用い、
UTF-8、`ensure_ascii=false`、空白なしの一意serializerでraw witnessを作る。各witnessを製品structure validatorへ戻し、
合格したbytesだけをnative `/tokenize` で数える。手書きschemaや旧T510 plan schemaは使わない。

84 chat行のcandidate集合の観測最大を `plan_fixture_max_tokens` とし、余裕は固定32 tokenとする。

```text
PLAN_BUDGET = plan_fixture_max_tokens + 32
```

`PLAN_BUDGET > 512`、列挙欠落、validator不合格、native count欠測ならPF3実測はFAILである。512へ丸めない。
この値は全raw表現の上界ではない。

### 3.3 speech予算

message schemaは製品builderから作る。次の200文字logical textを固定stress corpusとし、各々を
`{"message": text}` の製品key順で、planと同じ一意serializerに通す。

- fixtureの公開chat本文から得た文字列を循環し、ちょうど200文字にしたもの
- `a`、`あ`、`漢`、引用符、reverse solidus、改行、tabを各々または周期列として200文字にしたもの
- SHA-256のlowercase hexを循環した200文字列
- 4-byte Unicode scalarとASCIIを組み合わせ、200文字かつUTF-8 600 bytes以下にしたもの

全候補は製品structure validatorを通し、200文字・600 UTF-8 bytes以下を再確認する。予算witnessは3.2節と同じ
`ensure_ascii=false` のcompact serializerだけで作る。観測最大を
`speech_fixture_max_tokens`、余裕を固定32 tokenとする。

```text
SPEECH_BUDGET = speech_fixture_max_tokens + 32
```

`SPEECH_BUDGET > 512` または候補/count欠測ならPF3実測はFAILである。これはstress corpus内最大であり、
任意200文字rawの上界ではない。providerが異なるescapeや空白を生成して予算を使い切った場合は、実行時のlengthとして
検出して失敗集計し、事前に代替raw表現を列挙しない。

### 3.4 PRE_VOTEとCO_OPPORTUNITYの予算

G13の各実catalogから `pre_vote` schema、G15から `co_opportunity` schemaを作る。PRE_VOTEは各branchの配列を最大件数で
満たし、各scalar enum値を少なくとも一度その位置へ置く3.2節と同じ決定論的candidate集合を使う。COのSILENCE/DEFERは各1件、DECLAREの自由commentには
3.3節の200文字stress corpusを適用する。各witnessを対応する製品structure validatorへ戻してからnative countする。

```text
PRE_VOTE_BUDGET = pre_vote_fixture_max_tokens + 32
CO_BUDGET       = co_fixture_max_tokens + 32
```

いずれかが512を超える、列挙・validation・countが欠ける場合はPF3実測FAILとし、512へ丸めない。

### 3.5 context測定と許可endpoint

prepareのtoken測定は、承認済みローカルruntimeを起動してmodel/tokenizerをloadし、既存runtimeの
`/apply-template` と `/tokenize` だけを使う。これはHTTP utility callであり、runtime loadとHTTP callは0ではない。
`/v1/chat/completions`、sampling、model inference、GPU generationは0である。旧native DLL proof toolは使わない。

実行時と同じrequest bodyから実schemaを含むmessagesを `/apply-template` へ渡し、その返却文字列を
`add_special=true, parse_special=true` の `/tokenize` で数える。84 chat行では3.2節の最大形plan candidateごとに
発話段allowlist入力を構築して数える。G13/G15の各6行は元triggerの実入力だけを数える。

各callで次を満たさなければPF3実測はFAILである。EOS/special reserveは既存経路と同じ1 tokenを別加算する。

```text
native_input_tokens + stage_budget + 1 <= 8192
```

prepareのruntime起動にも4節の完全所有権検査を適用する。測定cacheの生成後にruntimeを終了してよいが、実LLM runは
同じidentity、同じcache、同じbudgetを再照合する。prepare countを生成callとは数えない一方、utility call数、load回数、
durationは明示して隠さない。

### 3.6 実行時の判定

各stageは固定budgetを `max_tokens` に設定する。`finish_reason=length`、completion token超過、応答欠測はparseせず、
当該attemptを失敗にする。同じimmutable input/schemaで最大2 sample、異なる導出seedを使う。2回とも不受理なら
その行は沈黙となる。length本文を途中まで利用せず、repair promptへ入れない。公開集計はstage別にcall、length、
structure invalid、guard reject、sample exhausted、silenceを分ける。

## 4. test-only生成経路

### 4.1 prepareとcatalog

入力は保存fixture projectionだけであり、callerが任意dictをcatalogへ昇格させる入口を作らない。test-only builderは
projectionをexact pointerで読み、短いIDへ写し、製品 `GenerationCatalogV2` を構築する。reply、player、public fact、
disclosure、claim、opinion basisの各entryはprojection SHA、source pointer、source kind、actor、channel、authorityを
private binding表へ保持する。pointer不達、重複、別projection、private値のpublic fact化、未証明disclosureはFAILである。

schemaは必ず製品builderから生成し、request serializerまでdict insertion orderを保持する。PF1は実送信bytesをstrictに読み、
chat planのroot `oneOf` 全branchについて先頭 `reply_to`、後続 `act`、reply有無ごとのact partitionを検査する。
Python上のschemaだけの検査は代用にならない。

PF2はcaseごとの期待selectorをfixtureへ固定し、reply/fact/basis/claim/optionが同じprojectionとactor/channelに一意に
束縛されることだけを検査する。モデルが選ぶ確率や意味的十分性は主張しない。priorは2.1節どおりexactに保持し、
triggerは2.2節どおり分岐する。fallbackや既定値で補わない。

### 4.2 v2 planからspeech入力への薄いadapter

旧T510 `presenter_input` と `validate_final` は旧planの
`decision/discussion/public_fact_ids/disclose_fact_ids` を先に検査するため、v2 planを直渡ししない。
test-only adapterは製品validatorが返したimmutable v2 planから、選択済みsurfaceだけを解決し、次のP入力を新規に組む。

- self/current表示名、day、phase、生死、公開死因、自分の公開CO
- 同一channelの直近6発言、選択replyの本文とactor
- planのact、subject、topic、stance、opinion prior/current
- 選択したpublic fact、disclosure、utterance claimのsurfaceとprovenance

旧structured output全体へ変換せず、欠けた意味を推測しない。旧部品から再利用できるのは、同一channel recent-chat選択、
pointer/value provenance照合、自己反復・長文copy・TEXT_BOUNDの最終guardである。旧plan validatorと旧plan schemaは呼ばない。
最終acceptには、messageの製品structure validator合格、adapter provenance全leaf一致、選択済みID以外のsurface不存在、
再利用guard合格を全て要求する。

P入力のclosed top-level keyは
`schema_version,self_player,current,public_co,recent_chat,plan,selected_public_facts,selected_disclosures,selected_claims`
とし、unknown keyを拒否する。offline testは全leaf provenanceを辿り、role、team、count_as、能力定義、win condition、
未選択能力結果、主観state全体、別channel履歴が構造上存在しないことをassertする。private channelは今回のfixtureで許可せず、
出現したら `PRIVATE_RECIPIENT_UNPROVEN` で生成call 0とする。

### 4.3 run境界

prepare cacheはrun前に一度作り、run中はread-onlyで再利用する。HTTP clientも一個だけ作る。caseごとにschemaやfixtureを
再projectしない。chat行はplan最大2 call、そのaccepted後だけspeech最大2 call、非chat行は対応stage最大2 callとする。
通常game/action/state commitは0。
通信timeoutまたは受理不明では同じattemptを再送しない。

### 4.4 2行probeと96行見積り

probeは順序も含めて `G01-1/seed 4242027`、`G15-1/seed 4242027` の2行に固定する。前者でT/P、後者で
CO_OPPORTUNITYを観測する。各stageは最大2 sampleだが、length、structure/guard拒否、sample exhausted、timeout、
transport/ownership異常、integrity falseのいずれかを一度でも観測した時点でprobeを停止し、96行を開始しない。
特にTのlengthは即時停止である。attempt clockはbody/schema準備の直前に開始し、provider call、structure/guard検査、
outcomeのdurable記録が完了した直後に終了する。各attemptについてこのfull elapsed、内包するprovider latency、
prompt/completion token、finish reasonを保存する。

成功した各stageの最大full attempt elapsedを `A_T,A_P,A_CO` とする。観測済みattemptごとの
`full attempt elapsed - provider latency` の非負値の最大を `H_ATTEMPT` とし、未観測PRE_VOTEには
`A_PRE = 60 + H_ATTEMPT` を使う。2 probe行の各row elapsedから、そのrowのfull attempt elapsed合計を引いた
非負値の最大を `H_ROW` とする。runtime起動、cache load、開始・終了identity照合、cleanupのうちrow/attempt clock外を
実測した合計を `H_FIXED` とし、各区間は重複計上しない。96行の保守見積り秒は次で固定する。

```text
ESTIMATE_96 = H_FIXED + 96 * H_ROW
            + 2 * (84 * A_T + 84 * A_P + 6 * A_PRE + 6 * A_CO)
```

probeで1 sampleだけが実行されたstageも係数2を掛ける。係数2は各stage最大2 sample、84は28 chat case×3 seed、
各6はG13/G15の2 case×3 seedである。
PはT accepted時だけだが、見積りでは全chat行がPへ進む。測定値欠測、負のoverhead、clock不整合、
`ESTIMATE_96 > 3600` のいずれかなら停止する。2行からp95は算出せず、この値を `SMOKE_ESTIMATE_ONLY` と記録する。

## 5. 軽量な所有権確認

### 5.1 Windowsで同一portを共有できる条件

Microsoft Learnは、`SO_REUSEADDR` をbind前に有効にしたsocketが使用中portへ強制bindでき、同じportのTCP socketへ
届く接続は非決定的になり得ると説明している。また `SO_EXCLUSIVEADDRUSE` はbind前に設定すれば強制的な再bindを防ぐ。
したがって「開始時に一度listenerを見た」だけではrun中のport乗取りを否定できない。

- [Using SO_REUSEADDR and SO_EXCLUSIVEADDRUSE](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse)
- [SO_EXCLUSIVEADDRUSE socket option](https://learn.microsoft.com/en-us/windows/win32/winsock/so-exclusiveaddruse)

Windowsの `GetExtendedTcpTable` はowner PID付きTCP endpoint表を返せるため、毎callの軽量照合にはPowerShellを起動せず、
同APIの `TCP_TABLE_OWNER_PID_LISTENER` をin-processで使う。

- [GetExtendedTcpTable](https://learn.microsoft.com/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)
- [MIB_TCPTABLE_OWNER_PID](https://learn.microsoft.com/en-us/windows/win32/api/tcpmib/ns-tcpmib-mib_tcptable_owner_pid)

### 5.2 full check、light check、transport世代

開始時のfull checkは、保持中process handleからPID、creation time、alive、executable SHAを取り、runtime/model identityと、
loopbackのexact address/portに対するlistener owner集合がそのPID一件だけであることを確認する。その後にHTTP clientを一個だけ
作り、`transport_generation=1` とする。full checkはrun開始、run終了、通信異常、接続再確立の直前と直後に行う。

HTTP adapterは接続生成を観測する必須transport recorderを持つ。full check後、generation前のutility requestで一度だけ
接続を確立してimmutableなconnection identityと `transport_generation=1` を固定する。recorderは新規connect開始を
request body送信前に通知し、generation固定後の二度目のconnectを例外で止める。接続identity/open/reuse/close eventを
観測できないadapterはpreflight UNKNOWN、generation request 0で停止し、full checkで代用しない。

毎callのlight checkは次をno-subprocessで行う。

1. 保持中process handleがsignalされておらず、PIDとcreation timeが開始時snapshotと一致する。
2. `GetExtendedTcpTable` のIPv4/IPv6 listener行を読み、exact loopback endpointのowner PID集合が開始時PID一件だけである。
3. recorderのconnection identityとgenerationが固定値で、前callからdisconnect、新規connect、reconnect eventがない。

keep-alive中でも1と2を省略しない。disconnect、connection ID変化、reconnect attempt、owner表の複数/欠測、API access errorは
通信異常として現在attemptを受理せずrunを終了する。同じrun内で新clientを作らず、attemptを再送しない。

### 5.3 脅威別の検出理由

| 脅威 | 観測 | 検出できる理由 | 異常時 |
|---|---|---|---|
| provider停止 | 毎callのprocess handle alive | PID文字列でなく開始時に開いたprocess objectを照合するためPID再利用と分離できる | 即停止 |
| 別processのport共有・乗取り | 毎callのowner PID表、異常/reconnect時full check | Windowsは共有bindが可能なのでowner集合の変化を直接検出する | request送信前停止 |
| 暗黙reconnect | transport connection/generation観測 | client object同一性を接続同一性の代用にしない | clientを捨てfull check。現在attemptは再送しない |
| runtime差替え | 開始・終了のbuild/template/runtime照合 | run境界のidentity変化を検出する | integrity false |
| model差替え | 開始・終了のmodel path、file identity、runtime報告照合 | path文字列だけでなく固定file identityとruntime選択を照合する | integrity false |
| cache/source差替え | prepare前後とrun終了のhash照合 | 96入力とschemaを再生成せず同じbytesへ束縛する | integrity false |

いずれかの観測が欠ける場合も安全側にUNKNOWNとして停止し、`run_integrity=false` とする。ownedでないprocessをkillしない。
cleanupは保持handleのprocessだけを対象とし、process終了とlistener消失の両方が確認できなければ完了扱いしない。

## 6. offline testと受入条件

WP2はprovider generation 0で次を両Python環境で検査する。3.5節のutility endpointを使う統合preflightは、
runtime/model loadとHTTP utility callを別counterで記録し、completion endpoint call 0をassertする。

1. 96入力cacheの一意性、source/hash freeze、同入力を1回だけ構築すること。
2. product catalog/schema/structure validatorを実際に呼び、mock同名関数で代用しないこと。
3. plan最大形candidate集合の全witnessがschema-validで、送信bytesのkey順とPF1 partitionが一致すること。
4. PF2の必須候補が同じprojection/sourceへ一意に束縛されること。prior 80/20、current 5値、prior 75の同値除外、
   prior範囲・exact int拒否境界を検査すること。
5. speech/CO stress corpus、固定32 token余裕、T/P/PRE_VOTE/COの各budget 512以下、全96実入力のcontext式が
   成立し、G13/G15が元triggerの製品schemaを使うこと。
6. forbidden P input各fieldの注入、別channel record、未選択disclosure、旧planを拒否すること。
7. invalid raw、length、guard reject、2 sample使い切りが行失敗・沈黙へ一意に集計されること。
8. full/light ownershipの正常系、process終了、PID再利用、第二listener、listener欠測、暗黙reconnect、runtime/model drift、
   cache driftで、異常後request callが0かつintegrity falseになること。
9. probe対象と順序が固定され、attempt準備または検査・記録だけを遅らせるclock fixtureもfull elapsedと
   `H_ATTEMPT`へ反映されること。PRE_VOTEが `60+H_ATTEMPT`、全stageが2 sample、row/fixed overheadが非重複で、
   96行stage数から同じ見積りが再現されること。provider latencyだけ、1 sampleだけ、row elapsed全体の二重加算では
   小さくならないこと、および各停止事象と3600秒超過が96行開始を拒否すること。

独立Reviewerが本書を承認し、WP2 toolの独立reviewとoffline preflightが全PASSになるまで、2行probeを開始しない。
同じ成果物が3回目のreviewへ入る場合、または別設計文書が必要になった場合は停止してユーザーへ選択肢を示す。
