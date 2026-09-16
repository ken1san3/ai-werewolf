# Phase 6 Stage B R16 開始identity・一回probe限定補遺

Task ID: T366（基礎設計T361）
Responsibility: Architect
Status: DRAFT — T364独立承認待ち

## 1. 適用範囲とauthority

本補遺は、T345が承認した `Docs/ai/design/PHASE6_STAGE_B_R15_FREEZE_ADDENDUM.md`（SHA-256 `7088de1b71b1e3a270c7adfaebc0af00ab1ce26e7c01e0b5a89a3a645ae74821`）を基礎に、次回の新Runについて次の事項だけを追加・置換する。

1. T351/T353/T354が限定修正として承認・検証した8 sourceのexact identity。
2. T357が文書scopeで承認したR16の4文書と、U1〜U3の観測手順。
3. `reasoning_format=deepseek` と厳密なboolean `enable_thinking=false` を含む修正後request profile。
4. 実gameと分離した、非private・一回限りの有限probe契約。

旧Stage B詳細設計、R15補遺、B01〜B11、会話合格式、mandatory式、privacy、clock、soft予算、whole-response上限、accepted population、停止・cleanup条件を変更しない。T307/T316/T330/T344のFAIL/BLOCKED/UNKNOWN、欠落原本、消費済み許可を新Runへ継承せず、履歴として保持する。P3は保留のまま、Q2診断欠測の受容も変更しない。

本補遺は新実装の設計ではない。製品、test、schema、runner、provider設定を変更しない。T364が本補遺のexact SHA-256を独立承認するまではDRAFTであり、probeまたはgameの起動authorityではない。承認後も、T365の現preflight、T364の独立照合、Mainの明示dispatchを別々に要する。

## 1.1 T366の限定改訂と責務mapping

T364-R1の指摘に対し、AGENTSの個別control照合と現在担当への置換だけを行う。旧T361候補のexact bytes（SHA-256 `a50f7853cf5fc788e1a10747ab0538fde3143a99645f465eb15ac15a0675f790`）は `logs/t366-freeze/PHASE6_STAGE_B_R16_FREEZE_ADDENDUM.before.md` に保存した。旧T358/T359/T360の中止、未成立approval、probe/game 0の履歴は変更せず、今回のcount・approvalへ遡及変換しない。

| 現担当 | 責務と独立性 |
|---|---|
| T363 Main | 最新dispatch、board/state、統合。設計/原本レビューの自己承認なし。 |
| T364 Reviewer | 既存独立Reviewerを新割当として再利用。T351実装、T361/T366設計に非関与の別sessionで、設計・preflight・probe・gameを段階別に独立審査。 |
| T365 Tester | 新preflight・新freeze・別operationと原本保全。明示dispatch後のみ単一probe、独立PASS後の別dispatchでgame一回。 |
| T366 Architect | 本限定改訂と公開static hash/diffのみ。設計を自己承認せず、保護原本読取・probe/game・provider操作を行わない。 |

再利用許可は独立性や保護原本へのアクセス許可を代替しない。T364の公開設計承認と保護原本照合は別gateとし、後者が未成立ならprobe/gameは起動しない。Content-Type検証、単一probeのschema・terminal・oracle、全game契約は変更しない。

## 2. 第一層: T351承認済み8 source置換

基礎source母集団は `logs/t344-stage-b/frozen.json` の191 pathをそのまま維持する。次の8 pathだけをT344値から置換する。承認対象manifestは `logs/t351-repair/implementation.json`、SHA-256 `4ba971f16b408d52225d143918de8fda0b73a00b2b4dfb631e4eff252d2d9b2b` である。

| path | 許容するSHA-256 | bytes |
|---|---|---:|
| `ai_client/llm/types.py` | `bd05afca73834bde5e83b8b7be5cae2176d8dbdf520f2288d2f020dea1f85ec0` | 55547 |
| `ai_client/llm/config.py` | `f699353c3b771ba309599e0a79e3aa5f4d40019be86bec632686e199db3248f8` | 4160 |
| `ai_client/llm/backend.py` | `97cc77fe27e0d7039d73ade5157320853a2c089ad2f4ebb23b6e621c3da50c6a` | 21954 |
| `scripts/run_phase5_local_smoke.py` | `f4256172793d5fff91e22b416528cbe1ab3f6703849b0bae0f7ed74b9966278a` | 216261 |
| `tests/test_phase4_llm_contracts.py` | `b5c28e137546c2ddbb75e820ef4ff082983c6560a84e1f0d42c904c20ece6dbb` | 22333 |
| `tests/test_phase4_llm_backend.py` | `fbf8c2a953e6f40f4aba52a9c890bb73af01e0d6a7916e75a11f76964b1ddc39` | 36141 |
| `tests/test_phase5_local_smoke.py` | `d30d3daa6cb553ec94bc0a0bbf0d290f87f1d119adf7e9da9e5b224962d2404c` | 140903 |
| `tests/test_phase6_semantic_completion.py` | `d5c869d68178fa019d9c60eb8bb2d272ba733c10bcd2fd508e90abcf45951fec` | 66386 |

T353の最終判定はこの8件をAPPROVEDとし、T354は同じ8件の前後一致を伴う7module回帰431 PASS＋175 subtests、およびsynthetic completion 1 PASSを実測した。この承認を実providerのHTTP 200、schema-valid、または実game成功へ拡張しない。

残る183 sourceのpath集合も維持する。このうち次のcoordination-control 1件と既存control log 1件を別々に照合し、それ以外181件はT344 freezeとexact hash一致を要求する。8製品/test置換のallowlistは拡張しない。未列挙の製品・test差分やその他source差分は一件でも起動停止条件であり、現在値を採っただけでは許容しない。

| control path | 今回許容するexact SHA-256 | authorityと範囲 |
|---|---|---|
| `AGENTS.md` | `b3258eaa8c4bd0c034b0cb862259687d9d846abe893274de218c441968b028b0` | 2026-09-16ユーザーの既存独立Reviewer再利用・thread上限時の継続指示をMainが反映。対象設計/実装に非関与のReviewer再利用、担当解放/直列化の手順だけ。Main自己承認・review省略を許さない。 |
| `EXTERNAL_REVIEW_LOG.md` | `fc60c69144912d22dfe403fef0a64a95a4d7e9893b353c4399d4e3504d6d89c8` | R15から続く非canonical監査履歴。R17採否はT362の記録に従い、監査log自体を製品変更や追加probeのauthorityにしない。 |

AGENTSはユーザーが直接編集したものとは主張しない。根拠は `Docs/ai/tasks/T363_REVIEWER_REUSE.md` とMain保存の `logs/t363-reuse/control-reconciliation.json`。T364が上記2件の個別snapshot・現hash・authorityを独立照合した場合だけ、191 sourcesの該当keyに現hashを保持しつつ別枠controlにも結合する。191 sourceから除去したり追加したりしない。上表と異なる後続hashは本補遺で自動許容せず、追加の差分/authority確認が必要。authority不明または未照合ならprobeを起動しない。

## 3. 第二層: T357承認済み4文書

T357は次のexact bytesを文書scopeだけでAPPROVEDとした。新freezeでは4件を個別に結合する。

| path | SHA-256 | freeze上の扱い |
|---|---|---|
| `Docs/ai/PHASE6_R16_REVIEW_ADOPTION.md` | `2c8640226a22cd3a55d247fde30be5ba5779ddfe7357cd41bb38abb0c80e9b83` | supplemental control |
| `Docs/ai/tasks/T328_STAGE_B_PREPARATION.md` | `fcd611d111ee9411aa419a63c0dba202a975497bf5b94fcee47b3a0940ecbb48` | supplemental control |
| `Docs/ai/PHASE6_STAGE_B_TEST_CASES.md` | `a98b9fff4e8975da7fb5abd9fbe48f5e38044d10550d6a023b2ebd18d8df8151` | frozen 6 documents内の置換 |
| `Docs/ai/PHASE6_STAGE_B_TEST_CASES.csv` | `57e65c10591eaae3c2af99f6347637748435593d1e53c0652fd8db647bd19ae0` | frozen 6 documents内の置換 |

T344の6 documentsはkey集合を維持し、上表のMD/CSV 2件だけを置換する。残る4件はT344値とexact一致を要する。R16 adoptionとT328は6 documentsへ混入せず、今回packet、T365/T364 packet、本補遺、独立承認record、最終launch条件とともにsupplemental controlへ列挙する。control更新はsource/product allowlistを広げない。

## 3.1 今回追加するsupplemental control

次の現revisionをT357の4文書と分けて結合する。OPERATIONSはAGENTSと同じ2026-09-16ユーザー指示をMainが反映した運用手順であり、191 source/6 documentsのkey集合へ追加しない。今回packetは担当境界とdispatch権限だけを定め、製品allowlist・probe数・oracleを広げない。

| control path | T366作成時SHA-256 |
|---|---|
| `Docs/ai/OPERATIONS.md` | `8096af26f860e3585b507f39338e41990f3d15be49a2b8db55601218254415e2` |
| `Docs/ai/tasks/T363_REVIEWER_REUSE.md` | `046647b7206cefe4aca42576cc1300fd0d448353df3fbc4ba46c53e566c57ae4` |
| `Docs/ai/tasks/T364_REUSED_REVIEWER.md` | `2645d637d5e49734704eb93ff913048baacffef3f2ee161497d147179198de03` |
| `Docs/ai/tasks/T365_RESUMED_STAGE_B_TEST.md` | `6a6cdad1f5a0702a85f4a20165d152cd4329fe80b973af1b45a13796a9d5ec50` |
| `Docs/ai/tasks/T366_FREEZE_CONTROL_REVISION.md` | `61f564604c8fcd1fbbef56f3072e39454063cebe72b77b5574e015eded1a897e` |

本補遺のexact hash、T364独立承認record、最終operation/launch条件のhashは別recordから新freezeへ結合する。自身hashを本文に埋める循環、approvalを旧T360から引き継ぐ循環は作らない。最終freeze前に上表packet/運用文書が変更された場合、Mainが差分とauthorityを提示し、T364が最終exact revisionを再照合する。単純な現hash採取だけを承認としない。実行開始後のfreezeは不変で、後続結果/dispatchは別recordへ残す。

## 4. 新Run freezeと現preflight

T365は旧freeze objectをコピーせず、新しいopaque run label、作成時刻、operation、freeze、公開status、owner-only新leafを作る。旧run ID、task ID、PID、count、status、approval、provider観測、fingerprint、結果を継承しない。最低限、次を同じ新Runへ結合する。

- 191 sourceの同一key集合、8置換、残るsourceの照合結果、control driftの有無とauthority。
- 6 documentsの同一key集合、T357のMD/CSV 2置換、残る4件の一致。
- T357の4文書、本補遺のexact hash、T364承認record、T363〜T366 packet、今回の一回許可とMainのdispatch record。
- 現在のprovider PID、owner、creation UTC、loopback `127.0.0.1:8080` listener、health、実CLI commandの安全なcanonical hash、environment observation。
- 同じPID/creationへ結合した実GGUF全shardのSHA-256/size/model identity、serving executable/build/file manifest、RTX 3070 Ti 8GB、context 8192、`ngl=99`、jinjaの現観測。
- endpoint `http://127.0.0.1:8080/v1/chat/completions`、model `Qwen3.5-9B-Q4_K_M.gguf`、共有concurrency 1、現backend/broker fingerprint。
- game契約値と別の `probe_contract`、`probe_operation`、`probe_launch_count=0`、`game_launch_count=0`。

U1/U3に従い、実command canonical hashをT344の `7d42b2161351cbeb9fd5316acdd91bf638fc4b035f0ad903c460ce54b1776281` と比較し、一致・不一致・比較不能を記録する。command一致だけで環境、template、GGUF、request全体の一致を認定しない。現GGUFやargvでT316の欠測を補完しない。必要な実値がまだ存在しない、T365 artifactが未作成、またはbinding不能なら `UNKNOWN` としてprobeを保留し、過去値を転記しない。

providerはユーザー所有である。preflight、probe、gameのいずれでも起動・停止・再設定しない。旧原本、ACL、TEMP、securityを変更しない。private argv、prompt、response、locatorはowner-only leafに置き、公開freeze/statusには安全なhash、count、enumだけを出す。

## 5. 修正後request profile

probeとgameは、T351で承認された現在の `OpenAICompatibleBackendConfig` / `StructuredGenerationRequest` と `OpenAICompatibleBackend._request_payload` のserializer経路を使用する。手書きHTTP body、curl、別serializer、別schema modeを使わない。実効profileは次のとおりである。

| field | 固定値 |
|---|---|
| `structured_mode` | `json_schema` |
| `response_format.json_schema.strict` | `true` |
| `reasoning_format` | `deepseek` |
| `chat_template_kwargs.enable_thinking` | boolean `false`（文字列や数値は禁止） |
| `generation.max_output_tokens` | `512` |
| `read_timeout_seconds` | `20.0` |
| `request_timeout_seconds` | `20.0` |
| `stream` | `false`（serializer既定） |
| transport retry | `0` |

probe/game双方のconfig fingerprintと、private serializer出力bytesのSHA-256を別々に記録する。probe成功をgameの大きなdecision schema全体の適合、品質、latency、予算、B01〜B11のPASSとはみなさない。

## 6. 非private probe契約

### 6.1 一回性と入力

probeはgameとは別の単一 `StructuredGenerationRequest` であり、provider POSTは最大1回、repair 0、retry 0である。probe failure、timeout、UNKNOWN、証拠欠測の後に自動再送しない。health/models等の非推論read-only観測はprobe countへ含めないが、同一内容の別推論で代替しない。

request IDは新Runにだけ属するopaque値とし、player/game/private識別子を含めない。messagesは次の非private固定文だけとする。

```text
system: Return only JSON that satisfies the supplied schema.
user: Set probe to ok.
```

`output_schema` は次のDraft 2020-12 JSON Schemaとする。

```json
{
  "type": "object",
  "properties": {
    "probe": {"type": "string", "const": "ok"}
  },
  "required": ["probe"],
  "additionalProperties": false
}
```

T365は既存serializerが生成したprivate payloadについて、`max_tokens=512`、strict schema、deepseek、boolean falseを機械確認してから一回だけ送る。payload本文とresponse本文は非privateであってもowner-only probe原本へ置き、公開側はhash、bytes、判定fieldだけを残す。

### 6.2 whole-responseとterminal観測

transportは自動retryなしで、HTTP status、response bodyの全chunk読了、stream closeを観測できる薄い一回限りの計測境界とする。payload生成とresponse envelope解析は既存backend経路を使う。`generate()` が返る前にresponse全体がdrainされる現backendの契約を維持し、独自の部分読取やstreaming parseをしない。

外側probe operationには30秒の有限上限を置き、backendのread/request各20秒と別に開始・終了・wall・owned helper identityを記録する。この30秒はprobe helperの回収余地だけであり、provider read/requestの20秒を延長しない。外側上限到達時は停止して `UNKNOWN` または観測済みerrorに分類し、同じprovider requestを再送しない。所有するprobe helperがある場合だけcleanupし、ユーザー所有providerは対象外とする。

terminal PASSは、同じ一回についてHTTP body全読了とstream closeが確認され、既存backendのparseが完了して `generate()` が正常returnした場合に限る。timeout、cancel、connect/request error、部分body、drain/close不明はterminal PASSにしない。exceptionの `provider_quiescence=PROVEN_TERMINAL` はfailure responseの終端証拠には使えるが、probe全体はFAILでありgame gateを開かない。

### 6.3 exact PASS/FAIL/UNKNOWN

probe PASSは、同一callについて次をすべて満たす場合だけである。

1. 観測したHTTP statusがexact `200`。
2. `Content-Type`、UTF-8 envelope、`choices[0].message.content` が既存backend検証を通り、`response.text` が非空。
3. `response.text` をJSONとしてparseした値が上記schemaに妥当で、exact `{"probe":"ok"}`。schema検証error 0。
4. `usage.prompt_tokens` と `usage.completion_tokens` がともに非nullの非負整数。欠測を0にしない。
5. `finish_reason` がexact `stop`。null、空、`length`、その他はPASSにしない。
6. whole body drain、stream close、正常returnが同一callへ相関し、terminal PASS。
7. probe call count `1`、retry count `0`、repair count `0`。payload hash、response hash、開始・終了・wall、status、schema error count、usage、finish、terminal判定が保存済み原本と一致する。

既知違反、backend exception、HTTPが200以外、schema不適合、usage欠測、finish不適合はprobe `FAIL` とする。status/body/usage/finish/terminal/call identityの必要証拠が取得不能または相関不能なら `UNKNOWN` とする。FAILとUNKNOWNのどちらでもgameを起動せず、追加probe、fallback、設定変更、provider再起動へ自動移行しない。公開記録には本文を載せず、`result`、HTTP status、schema valid、usage presenceと安全なtoken count、finish reason、terminal、call/retry/repair count、原本hashだけを記録する。probe usageはgame usage・B10集計へ加算しない。

## 7. game開始gate

Mainがgameを一回だけ明示dispatchできるのは、次をすべて満たす場合だけである。

1. T364が本補遺のexact revisionをAPPROVED。
2. T365の新freezeが第2〜5節を満たし、T364がsource/doc/control、現provider CLI、PID/creation、GGUF/serving-file binding、request profileを独立確認。
3. Mainがprobeだけを明示dispatchし、T365が一回実行し、T364が第6節のprobe PASSと原本相関を独立確認。
4. Mainがprobe approvalとは別に、同じfreezeへ結合したgame operationを明示dispatch。

probeがPASSでもgameは自動起動しない。simple schemaの成功はgame schema全体適合の証拠ではない。T364承認前、preflight不成立、probe未実行・FAIL・UNKNOWN、source/freeze変化、provider identity変化、未許可差分、private保全不能のいずれかではgame launch countを0に保つ。

## 8. 変更しないgame契約

gameは `standard_9`、seed `8625`、9 client、共有concurrency 1、day/vote/night `180/60/60`秒、read/request `20/20`秒、whole-response `512`、本文200文字/600 bytes、CHAT 2件、CO別枠、repair最大1回・同一leaseを維持する。game待ち1200秒、outer 1500秒、owned cleanup 120秒は別時計として記録する。

入力2,097,152、出力131,072、合計2,228,224 tokenはsoft監視でありhard capにしない。欠測を0補完しない。通常のB ID FAILだけでは中断せず、既存の危険停止条件だけを使う。accepted population 512以下は全件をT364が審査し、513以上は既存B11規則に従う。B01〜B11のoracle、mandatory式、clock、budget、privacy、processor一回、追加game/retry 0を一切変更しない。

## 9. 実行前後のfreeze検査と責任境界

probe直前、probe直後かつgame判断前、game直前、game終了後に同じsource/doc/control key集合とexact hashを照合する。変化があれば、それ以前に実行した事実を保持し、以後を起動しない。probe後のprovider identity変化もgame保留条件である。新freezeへ結果を書き戻して開始identityを変えず、approval、dispatch、probe result、game resultはappend-onlyの別recordで同じfreeze hashへ結合する。

T365は現preflight・probe/game実行・原本保全を担当し、T364は本設計・preflight・probe gate・B01〜B11を独立審査する。Mainだけがdispatchと統合を行う。T366は設計と静的identity照合だけを行い、現在のT365 artifactやprovider測定を捏造せず、自己承認しない。

本T366ではpytest、実provider呼出し、probe、game、processor、provider操作を0件とする。設計検証はhash、文書、scoped diffに限定する。
