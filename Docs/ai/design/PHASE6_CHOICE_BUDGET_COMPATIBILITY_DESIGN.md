# Phase 6 choice予算適合性の限定詳細設計

Status: APPROVED

独立design reviewで承認済みである。実装はoffline範囲に限り、provider実行は未許可である。

## 1. 目的と観測事実

T506のGemma対照では、96/96のchoice callが`completion_tokens=32`かつ
`finish_reason=length`で終了し、output callは0だった。hash照合済みの2件は
choice JSONの`speech_act_kind`途中で切れていた。入力は2,142〜2,635 token、
contextは8,192であり、この観測を入力context不足とは扱わない。本文は未生成なので、
会話品質、意味安全、model間の優劣も判定しない。

本設計は、総生成予算512 tokenとcontext 8,192を維持したまま、choice/output間の
配分だけを一因子として変更する次の有限実験条件を定める。新しいprovider call、製品変更、
旧候補の再生成・再採点は本設計のscope外である。

## 2. 固定境界と選択する一因子

選択候補は`choice=64 / output=448`だけとする。現行の`32 / 480`から変える因子は
`max_tokens`の配分だけで、合計は各論理attemptについて512のままである。

次を固定し、同時に変更しない。

- choice schemaの必須fieldは`speech_act_kind`と`authoritative_fact_ids`の2つ、
  `additionalProperties=false`、factはcatalog内の0〜2件、重複不可とする。
- kind enum、catalog ID、参照解決、locked kind、choice/output instruction、message、
  sampling、seed対応、response schema、strict JSON、validator、closed filterを変えない。
- hostによるkind/refの推定、欠落fieldの補完、末尾追加、trim後の再解釈、部分JSONの修復、
  validator緩和を行わない。
- model file、template、offload、runtime、32 case、seed `4242027`、`K=3`、slot 1、
  privacy境界を変えない。
- outputは従来どおりlegacy full output全体を担う。責務分割や段階追加を行わない。

outputの`finish_reason=length`拒否は既存本文validatorやfilterを変更するものではなく、未完了と
providerが明示したtransport結果を候補へ渡さない完結性gateである。配分に依存しない実験安全前提として
両配分へ同じ意味で適用するが、旧Gemmaはoutput call 0なので旧結果の再採点は不要である。
新候補の安全条件を緩和せず、比較上の変更因子は引き続き`64 / 448`の配分だけとする。

`64 / 448`は成功を保証する値ではない。offline native-token fixtureで観測したGemma最大51へ、
CLI計数に含めないEOS reserve 1を足した52を、事前に固定した16-token境界で切り上げると64になる。
したがってfixtureで表現した完成JSONに対する余白は12 tokenである。再計数値が51と一致しない、
計数不能、tokenizer identity不一致のいずれかなら、この候補を自動拡張せずprovider gateを閉じる。
別の配分は新しい設計対象とする。

compact JSONを強制する案は別因子であるため選択しない。offline fixture内のcompact表現は
費用の分解にだけ用い、runtimeの整形制約を変えたことにはしない。

## 3. offline synthetic fixture契約

### 3.1 入力と行集合

fixtureはprovider/serverを起動せず、推論を行わない。既存Gemmaと既存Qwenそれぞれについて
84行を計数し、次をfreezeする。

- tokenizer実体のpath、SHA-256、build、model vocabulary fileのidentity。
- CPU vocabulary計数の完全なargv。既知の利用可能な計数器は
  `C:/AIagent/llama-tokenize.exe` build `10697/093adb242`、SHA-256
  `a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1`であり、
  `--stdin --ids --no-bos --no-escape --no-parse-special --offline -ngl 0 --device none`
  を使う。各model vocabulary identityを別途記録し、実行時に再照合する。child processは
  1行30 REAL秒でtimeoutし、networkを使わず、CPU device `none`、BOS/EOS追加なしとする。
- stdinは`raw.encode("utf-8")`のbytesを`subprocess`の`input`へ直接渡し、stdout/stderrも
  bytesで回収する。`text=True`、universal newline、shell pipeを使わない。CLIへ渡す直前の
  `wire_sha256`がfixture rowのUTF-8 bytes SHA-256と一致しなければ計数しない。Windowsの
  LF→CRLF変換をtoken費用へ混ぜない。
- 現行decision schemaから得たkind enumを`ANSWER`、`CLAIM`、`NONE`、`OPINION_CHANGE`、
  `QUESTION`、`REBUTTAL`、`RELATION_HYPOTHESIS`の順にfreezeする。
- token費用専用のpublic toy fact IDを`f000`と`f063`に固定する。cardinality 0は`[]`、1は
  `["f000"]`、2は`["f000","f063"]`とする。この2 IDだけをenumに持つtest-only schemaで
  field、kind、cardinality、unique、extra fieldを検査する。通常の実projectionに`f063`が存在するとは
  仮定せず、toy IDを実engine入力、許可pointer、新authorityへ追加しない。

各kindについて、fact cardinality 0/1/2、次の2 key順、次の2表現の直積を作る。

1. `kind-first`: `speech_act_kind`、`authoritative_fact_ids`の順。
2. `facts-first`: `authoritative_fact_ids`、`speech_act_kind`の順。

各key順について次の表現を作る。

1. `compact`: 同じescapingで空白を最小化したUTF-8。
2. `pretty`: 同じ値・key順・escapingを`indent=2`、末尾改行なしで表現したUTF-8。

したがって現行7 kindでは`7 × 3 × 2 × 2 = 84`行で有限である。key順は意味値を変えず、
compact/pretty間では値、key順、escapingを変えない。これによりkey順の費用とwhitespace費用を
別々に観測する。token-cost matrixは上記test-only schemaで形だけを検査する。これとは別に、
各対象caseの実projectionから現行choice schemaと実在catalog IDを取得し、0/1/2件の合法choiceが
現行`validate_choice`を通り、toy `f063`などcatalog外IDが拒否されることを検査する。この
real-projection legality testをtoken上限の根拠へ混ぜない。strict JSON、duplicate、nonfinite、
schema validityの判定結果とtoken費用も別fieldにし、validatorの成否をtoken数から推論しない。

### 3.2 記録と判定

各行に`model profile`、`kind`、fact cardinality、key order、format、UTF-8 bytes SHA-256、
byte数、token数、tokenizer/model identity、argv identity、`KNOWN/UNKNOWN`を記録する。token ID列そのものは
privateなfocused evidenceへ保存できるが、設計判定に必要なのは件数とhashである。

計数器欠落、identity不一致、非zero終了、stdout形式不正、token ID不正、入力hash不一致は
推測値へ置換せず`UNKNOWN`とする。proxy token数、別tokenizer、文字数からの換算をfallbackに
しない。全行`KNOWN`でなければprovider gateは閉じる。

出力はmodel別・key順別・format別のmin/maxとmodel別全体maxを集計する。preliminary native診断では、
Qwenはcompact 13〜27、indent 2は22〜46、Gemmaはcompact 17〜32、indent 2は26〜51 tokenで、
両modelとも84/84行が`KNOWN`だった。各modelの42個のindent 2行中28個が32 tokenを超えた。
また、
`{"authoritative_fact_ids":["f000","f063"],"speech_act_kind":"OPINION_CHANGE"}`
の`indent=2`表現は上記Gemma tokenizerで48 tokenだった。この値はfixture全体の上限、
任意whitespaceの上限、provider生成完了の保証とは扱わない。

preliminary証拠は`logs/t507-choice-budget/native-diagnostic.json`と
`logs/t507-choice-budget/identity-check.json`に保持する。Pythonのtext modeではWindows改行変換が
起こり得るため、これをformal fixture acceptanceには使わない。独立review承認後に実装するhelperが
上記binary stdin契約で84行/modelを新規計数し、51を再現した結果だけをformal gate証拠にする。
これはprovider再試行ではない。

CLIのtoken数は、与えた完成済みJSON bytesのvocabulary分割費用である。providerが返す
`completion_tokens`、EOS/stop費用、schema grammarの内部費用、生成中のwhitespace選択とは
別fieldに保存し、相互に同一視しない。特にfixtureが64未満でも、modelが64以内にstrict JSONを
完結するとは結論しない。

### 3.3 offline gate

次をすべて満たした場合だけ`OFFLINE_CAPACITY_CANDIDATE`とする。

- Gemmaの全84 token-cost行がtest-only schemaに適合し、token countが`KNOWN`。
- 対象32 caseのreal-projection legality testが、実在IDだけを現行validatorで受理し、catalog外IDを
  拒否する。
- frozen Gemma tokenizer/model identityと実測identityが一致する。
- Gemmaの全体maxが51と一致し、既知の48-token fixtureを同じ値として再現する。
- CLI token数51と別fieldのEOS reserve 1から必要量52を求め、16-token境界で一度だけ
  切り上げたchoice予算が64になる。追加reserveを重ねない。
- `64 + 448 = 512`を満たし、Mock境界値で
  `prompt_tokens_actual + stage max_tokens + 1 <= 8192`のときだけ送信可能になる。

Qwenの84行も同じ形式で保存するが、model固有tokenizer差のoffline診断controlであり、
Gemma provider適合やmodel品質比較の合否へ混ぜない。Qwen側のUNKNOWNはGemma gateの状態とは
別に`QWEN_DIAGNOSTIC_UNKNOWN`として残す。

これはprovider成功条件ではなく、`64 / 448`を一回の有限Gemma実験候補にできる条件である。
一項目でも不成立なら`OFFLINE_GATE_CLOSED`、判定不能なら`OFFLINE_GATE_UNKNOWN`とし、
providerを呼ばない。

## 4. 将来のprovider gateと有限実験

provider実行には、本設計artifactのSHA-256に対する独立Reviewerの`APPROVED`、offline gateの
evidence hash、source/config/model/runtime freeze、host所有権確認、およびT507とは別の実行許可を
すべて要求する。どれかが欠ければ開始しない。

許可後も試す配分は`64 / 448`の一つだけとし、配分grid、32-token同条件retry、compact整形arm、
model変更を追加しない。T506の`32 / 480`は保存済み互換性証拠として参照し、再生成しない。

対象modelは既存Gemma profileだけとし、model比較を同時に行わない。最初のcompatibility runは
既存32 case×seed `4242027`の32 rowだけとする。3 seedの品質比較へ拡張せず、必要なら結果保存後の
別scope・別freeze・別実行許可とする。各rowはchoiceを一度だけ呼び、strict JSONかつ現行schemaに
適合したときだけoutputへ進む。choiceが`length`、空、parse不能、schema不適合、保存不能、timeoutの
場合はoutput callを0にして終了する。部分値、途中まで読めたkind、閉じていないarrayを採用しない。

output stageの`K=3`は最大attempt数であり、成功時に早期終了する。choiceはKごとに再生成せず、
1回の受理済み値を全output attemptへexact hash bindingする。1論理attemptの生成上限は
`choice 64 + output 448 = 512`である。一方、K全体の物理call capは1 choice + 最大3 output =
4 call/row、32 rowで最大128 callである。choiceを一度だけ共有するため、全output attemptを使った
1 rowの物理的な生成上限合計は`64 + 3×448 = 1,408`であり、これを512と誤記しない。
retry、repair、fallback、format再試行を別枠で足さない。

各output attemptは、schema・validator・filterの結果にかかわらず、`finish_reason=length`なら
closed reject code `OUTPUT_LENGTH`として必ず不受理にする。偶然にstrict JSON、schema、filterを
すべて通る本文でも受理しない。`OUTPUT_LENGTH`は消費済みの1 provider callとして数え、残りattemptが
あれば、事前登録済みの次のoutput attemptへだけ進む。同じbodyと同じseedのrepair/retryは行わず、
既存契約の次の派生seedを使う。受理されたattemptがあれば即時終了し、残りattemptを開始しない。
K=3をすべて消費しても受理値がない場合は`OUTPUT_K_EXHAUSTED`としてrowを失敗にし、最後のrawを
採用しない。いずれの不受理rawも製品stateまたはsend経路へ渡さない。

各call前にnative prompt計数を行い、`prompt_tokens_actual + stage max_tokens + 1 > 8192`なら
送信せずrowを失敗にする。call marker、request/raw/usage/finish reason、strict判定、reject code、
hashをattempt単位でdurableに保存し、不明送信を未消費へ戻さない。rawはprivate境界を維持する。

choiceで最初の`CHOICE_LENGTH`を観測した時点で新しいrowを開始せず、残りを`CHOICE_NOT_RUN`として
32 case分母に残す。ほかのchoice不受理でも32/32 compatibilityは成立しないため同様に停止する。
停止済みrowを埋めるretryや別formatへの切替を行わない。

## 5. 状態、公開code、比較境界

choiceの公開状態は少なくとも次のclosed codeへ分ける。

- `CHOICE_ACCEPTED`: 完結したstrict JSONが現行schemaに適合。
- `CHOICE_LENGTH`: `finish_reason=length`。内容を問わず不受理。
- `CHOICE_EMPTY`: completion bytesが空。
- `CHOICE_JSON_INVALID`: 完結したstrict JSONとしてparse不能。
- `CHOICE_SCHEMA_INVALID`: parseできるが必須field、kind、fact、重複、extra field等が不適合。
- `CHOICE_NOT_RUN`: deadline/call cap/freeze不一致等で未送信。
- `CHOICE_UNKNOWN`: transportまたは保存結果が確定不能。

outputでは少なくとも`OUTPUT_LENGTH`と`OUTPUT_K_EXHAUSTED`を上記契約どおり別々に保存する。
`OUTPUT_LENGTH`はattempt単位の拒否理由、`OUTPUT_K_EXHAUSTED`はK終了後のrow状態であり、
後者で最後のattemptを受理済みへ書き換えない。

不受理choiceから本文を生成せず、本文内容は`UNKNOWN`、`semantic_outcome=0`、output attempt数0を
保持する。`violation_count=0`を安全と解釈しない。choice構造受理率とoutput本文評価を別集計にし、
choice改善で会話品質改善を主張しない。

この実験の第一判定は32/32の`CHOICE_ACCEPTED`、`CHOICE_LENGTH=0`、欠測/UNKNOWN=0である。
一件でも満たさなければ配分適合は`INCONCLUSIVE/NOT_COMPATIBLE`として停止し、別予算や整形へ
自動移行しない。満たした場合も、outputのstrict/HARD/semantic/style判定は既存rubricで別に行い、
製品採用を意味しない。448への縮小によるoutput truncationは独立して記録し、choice成功で相殺しない。

## 6. 整形制約案との交絡分離

compact整形のruntime強制は、同じ意味値でも生成grammar、whitespace、key配置に影響し得る別因子である。
本実験では現行response schemaと整形挙動をそのまま使う。hostが返却後にcompactへ再serializeして
受理させることも禁止する。

offline fixtureの`compact`対`pretty`差は、構造token費用を「値に必要な費用」と「既知の
whitespace表現差」に分解する診断に限る。任意のmodel出力形式をこの2つが包絡するとは主張しない。
将来compact制約を試す場合は、予算配分を同時に変えない別cycle、別設計、別review、別実行許可を
要求する。本実験の失敗後に同run内で切り替えない。

## 7. focused offline acceptance

実装前に独立Reviewerが本設計を承認し、実装後はproviderなしで次を検査する。

- 既存Gemma/Qwenそれぞれについて現行7 kindの84行が一意で、全kind、0/1/2 fact、2 key順、
  compact/prettyを過不足なく含む。
- token-cost matrixのtoy `f000/f063`をtest-only schema内に閉じ、実projectionのcatalogへ追加せず、
  real-projection legality testでは各caseの実在IDだけを受理する。
- 2 key順で意味値が同一であり、各key順のcompact/pretty間では値・key順・escapingが同一で、
  whitespaceだけが異なる。
- 同じ入力・tokenizer identityから同じwire bytes hash、token数、集計hashを再現し、
  binary stdinでLF→CRLF変換がないことを検査する。
- 既知のGemma 48-token fixtureを再現し、tokenizer欠落・SHA不一致・異常終了・timeout・不正token列を
  `UNKNOWN`としてgate閉鎖する。
- Gemma全体max 51、EOS reserve 1、16-token境界から64を一意に導き、再計数不一致、UNKNOWN、
  fixture欠落ではgateを閉じる。
- `64+448=512`、context式、1 row最大4 call、全32 row最大128 call、K全体1,408の
  accountingを検査する。
- choice `length`/empty/invalid/unknownでoutput 0、acceptedだけで最大K=3、early stop、
  同choice hash再利用、同body/seed再送拒否をMockTransportで検査する。
- MockTransportが「strict JSON、schema、filter上は合法なoutput」と`finish_reason=length`を同時に返す
  negative caseで、当該attemptを`OUTPUT_LENGTH`として拒否し、製品state/sendが0であることを検査する。
- `OUTPUT_LENGTH`後に残りKがあれば事前登録済みの次attemptだけへ進み、2回目で受理した場合は
  3回目を開始しない。3回すべて`OUTPUT_LENGTH`なら`OUTPUT_K_EXHAUSTED`、受理値なし、最後のraw不採用、
  output call 3、choiceを含むrow call 4となることを検査する。
- 全32 rowでもoutput callは96以下、choiceを含む総callは128以下であり、length reject、K枯渇、
  early stopのいずれでもaccounting上限を越えないことを検査する。
- 最初のchoice不受理で後続caseを`NOT_RUN`にし、32 case分母を維持する。
- 部分JSON、missing/extra field、unknown/duplicate fact、invalid kindを修復せず拒否する。
- fixtureとMockTransportがprovider/server/model inference、製品state/send、旧raw/annotationへ
  触れないことを検査する。

focused test、関連回帰、`python scripts/check_docs.py`、対象diffの確認後も、実装完了だけでは
provider gateを開かない。provider実測と製品採用はそれぞれ別gateに残す。
