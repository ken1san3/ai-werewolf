# Phase 6 実Context budget限定設計

## 1. 設計対象と実測根拠

Phase 6 structured generationのcontext安全判定に、同じprovider build・model・chat templateで数えた
実token数を追加する。モデル、schema、output上限、repair回数、private境界、validationは変更しない。

T406の生成なし実測は次を示す。

- runtime build: `b10697-093adb242`
- 1 slotの実効context: **8192 tokens**
- model metadata `qwen35.context_length=262144` はslot設定より大きく、今回の実効上限には使わない。
- template SHA-256: `7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67`
- T405同bytes再構築: proxy 8192、generation用template適用後のactual prompt 1935 tokens。
- 現buildではrequestのstructured-output schemaを受け取るが、chat template本文のtoken列には入らず、
  schema有無でrendered promptは変わらなかった。
- 同build sourceの`apply-template`はgenerationと同じrequest parse経路を使う。

したがってproxy 1 unitを実token 1個と扱わない。また262144をslot上限として扱わない。schema overhead 0も
build固有の観測であり、将来buildへ一般化しない。

## 2. Context安全式

providerへ送る各HTTP requestを独立に数える。base requestとrepair requestは同時にslotへ載らないため合算しない。

```text
C = verified context_per_slot = 8192
O = request max_tokens = 512
M = 1 token
T(R) = generationと同じparse/template/tokenizerで得たrequest Rのrendered prompt token数

safe(R) := T(R) + O + M <= C
```

`M=1` は境界をstrictに空ける有限marginであり、`T+O < C`と同値である。今回の実測artifactも
`safety_margin=1`で計算している。根拠のない割合余白やproxy/actual比の最小値は安全式へ使わない。
`max_tokens=512`はcompletionの最大予約であり、実際の短いcompletionを見込んで減らさない。

baseは `safe(base_request)`、repairは `safe(repair_request)` をそれぞれ送信直前に満たす必要がある。
base+repairのtoken数を足さず、baseが短いことをrepairの余裕へ振り替えない。

## 3. 既存budgetとの関係

次を維持する。

- canonical prompt 32768 bytes、context/state/memory/proposal各section上限。
- token proxy 8192はprovider非依存の既存projection選択heuristic・telemetry境界であり、provider context安全の根拠ではない。
- mandatory trigger、最新ability result、repair用empty datumのbyte予約。
- 200 code points/600 UTF-8 bytes、schema/semantic validator、repair最大1回・同一lease。
- prompt/schema/private audit hashとimmutable projection。

最小案では既存projection heuristicとしてproxy上限そのものを拡張しない。変更するのは、baseとrepairを一つのproxy envelopeとして加算する
判定だけである。base projection単体を `proxy<=8192` に保ち、実provider contextはbaseとrepairを別requestとして
actual countする。これによりproxy 8192のbaseへproxy repair予約を加えたことだけを理由に拒否せずに済む一方、
proxy 8192超の新しいprompt母集団は今回許可しない。このheuristicはactual安全式から導いた値ではなく、将来の
別設計で再検討できる。内部`fits()`試行中のproxy値を最終採用projectionや採用上限の根拠に使わない。

byte上限はbase/repairとも各requestのcanonical serializationへ従来どおり適用する。actual tokenが安全でも
byte、section、validation、privacyのいずれかが不適合なら拒否する。

## 4. 計数地点と同期projectionの分離

同期関数 `project_discussion_brain_input` にnetwork、subprocess、model tokenizerを入れない。projectionは従来どおり
immutable prompt/schema/hash/proxy/bytesを作る。

actual計数はasync backendのrequest payload確定後、HTTP generation送信前に行う。backendへ注入する小さい
`ContextTokenCounter`相当のcallableは、generationと同じmessages、template指定、add-generation-prompt設定を使い、
同buildの`apply-template` parse結果を同model tokenizerで数す。provider request JSON全体や
`canonical_prompt_json`のJSON token数を代用しない。今回schemaはrendered promptへ入らないが、schemaも計数入力の
requestに保持し、同build parserが実際に無視した結果として0を得る。

計数結果にはprovider identity、model file hash、context_per_slot、template hash、rendered token count、request hashを
結び付ける。generation送信直前に同じbindingを再確認する。計数不能、型不正、identity変化、request hash変化は
`PROMPT_CONTEXT_UNVERIFIED`としてfail closedし、generationを送らない。

counterが構成されていない通常経路は従来のproxy＋repair予約判定を維持する。actual計数を使って従来判定を緩める
modeは明示的なverified runtime bindingがあるPhase 6構成だけとし、fallbackでproxy予約を外さない。

## 5. Baseとrepair

### Base admission

projection完成後、実際のbase requestを生成なしで数え、安全式、byte上限、identity bindingを満たす場合だけ送る。

全validation code・truncation flag、`sys.maxsize`長、固定 `"f" * 64` SHA、empty excerptを組み合わせた
T406のrepair計数は**保存標本の最大**である。可変SHA文字列や実際の長さ表現を含む全tokenizationの数学的上界では
ないため、repair送信保証やbase admissionの根拠にしない。既存empty repairのbyte予約は維持するが、actual contextで
最低限repairが必ず送れるとは主張しない。repairの安全根拠は、invalid response後に確定した具体payloadの直前再計数だけである。

### Repair admission

invalid response後、実際に確定したvalidation code、original scalars/bytes、SHA、truncation flag、excerptを含む
repair requestを改めてcountする。
最長excerptがsafeならそのまま送る。unsafeなら、既存上限内の長さ候補
`1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1, 0` code points（設定上限より大きい候補は飛ばす）を
それぞれ独立に再serialize・再countし、**実測でsafeだった最長候補**だけを使う。token数の単調性を仮定した
binary searchや、一度数えた差分の使い回しはしない。0もunsafe、再計数不能、binding変化ならrepairを送らず
従来のbounded failureを返す。

選択後のrepair payloadを最後にもう一度serializeし、request hashとtoken countを照合してから送る。
excerptを縮めてもoriginal scalars/bytes、truncated flag、SHA、validation codeは維持する。

## 6. Identityと設定変更

actual計数を有効にするbindingは少なくとも次の完全一致を要求する。

- provider implementation/build/commit (`b10697-093adb242`)
- model file SHA-256とmodel identity
- context_per_slot 8192およびslot構成
- chat template SHA-256
- apply-template/tokenizerの計数方式とadd-generation-prompt設定
- max output tokens 512

model metadataのcontext長は記録するがadmission値にしない。build、model、template、slot context、output上限の
どれかが変われば既存計数cacheと承認を無効化する。新bindingで同じ測定suiteと独立reviewを行うまで、
actual modeは使わず従来proxy予約へ戻す。実requestの途中で変化を検出した場合はfallback送信せずfail closedする。

cacheする場合はidentity binding＋request SHAの完全一致だけをkeyとし、baseとrepair、excerpt候補を混同しない。
token countや余白を異なるrequestへ外挿しない。

## 7. Failure・audit境界

- context preflight失敗はoutput validation失敗やprovider generation失敗と区別する。
- private prompt、repair excerpt、schema本文をpublic logへ出さず、hash、token count、上限、reasonだけを記録する。
- counter requestにもgenerationを要求せず、計数がgeneration quotaやsemantic attemptに数えられないことを固定する。
- preflight後のserver側context rejectionは既知不一致として保存し、安全式をPASSへ読み替えない。
- actual count導入でschema/semantic validator、repair lease、dispatch/stage、server authorizationを変更しない。

## 8. 必要focused test

1. 保存7 fixtureでproxyとactualを別fieldとして再計数し、T405例がproxy8192/actual1935であることを固定する。
2. `safe`境界を `T+512+1=8192` PASS、8193 FAILで確認する。metadata262144を使わない。
3. baseと具体的repairを独立requestとして数え、両者を加算しない。固定SHAのempty repair標本を全payload上界や
   repair送信保証に使わず、確定payloadがunsafeならrepairを送らない。
4. schema有無が現bindingのrendered promptを変えないことを確認する。ただしschemaをrequestから除去しない。
5. actual verified modeだけがbase proxy8192をrepair proxy加算なしで候補にできること、8192がcontext安全値ではなく
   既存selection heuristicであること、内部fits試行値を最終projectionとして扱わないことを確認する。
6. repair excerpt候補を個別再serialize/countし、unsafe候補を飛ばして実測safe最長を選ぶ。0 unsafeはno-send。
7. base/repair payloadまたはrequest hashが計数後に変わるとno-send。再計数後payloadだけを送る。
8. build/model/template/context/output変更、counter error、malformed countをfail closedにし、通常modeは従来proxy予約を維持する。
9. 32768 bytes、section、200/600、validator、private hash、repair1回・同一leaseが不変であることを関連回帰で確認する。
10. mock async counter/backendで計数呼出しはgeneration前、projection内network/subprocess 0、計数だけではgeneration 0を確認する。
11. 同build保存sourceに対しapply-template parse条件とgeneration parse条件の一致をoffline assertionし、identity evidenceへ結ぶ。
12. context preflight reason、actual input/output/margin/remaining、identity hashだけがauditされ、private本文が出ないことを確認する。

製品採用には本設計の独立承認、実装diffの別review、focused/regression/check_docsが必要である。
T406人工suite作成や今回の設計だけでは製品設定を変更しない。
