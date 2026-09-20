# Phase 6 Plan→Message 2-call 最小実験設計

Task: T452
Status: DRAFT

## 1. 目的と非変更境界

候補P2は、旧baseline出力をgrounded planと本文へ分け、planを先に厳密確定してから、必要なactionだけ本文を生成するtest-only実験である。S1は32件中構造合格2件で不採用済みであり、validatorを緩和せず再実行・再採点しない。I1、K1、C1、C2の変換も混ぜない。入力は保存baselineと同じ32case、model、sampling、projection、authority、秘密境界から作る。

製品schema/parser、game rule、admission、lifecycle、state、model、concurrency、contextを変更しない。probeはsend/state適用APIを持たない。最大32判断、最大64 provider call、retry0、repair0。通常game、Master Run、Phase7は行わない。

仮説は、行為・根拠・更新を旧strict制約で確定した後、そのplanだけに沿う本文を別callで生成すると、groundingを弱めず本文/act対応が改善する可能性がある、という限定的なものとする。2-call一般や内部思考を検証するものではない。

## 2. call1: Grounded Plan

call1はbaselineのsystem messageとuser canonical input messageをbyte-equivalentな先頭2要素として保持し、第三の固定user instructionを追加する。固定instructionは「今回は本文を生成せず、response schemaに従ってgrounded planだけを返す。fieldを補完・推測せず、既存のgroundingとoffered actionだけを使う」という範囲に限定し、例文、role/target/default、自由なrationaleを加えない。baseline 2 messages hash、stage1 instruction hash、完成stage1 messages hashを別々にfreezeする。response schemaのroot、`discussion`、`$defs`はbaselineをそのままcopyする。`decision.oneOf`だけを次のplan枝へ変換する。

| kind | plan decisionのrequired key | 変更 |
| --- | --- | --- |
| none | kind | baselineと同一 |
| chat | kind, option_id | `message`だけ除去 |
| vote | kind, option_id, target_player_id | baselineと同一 |
| ability | kind, option_id, target_player_ids | baselineと同一 |
| co_declare | kind, option_id, claimed_role_id | `comment`だけ除去 |
| co_report | なし | Phase6対象外のまま |

各枝はclosedで、元枝のkind/option/target/role制約を値・array順ごとcopyする。`discussion.speech_act`は元7枝oneOfのまま、reaction、CO、pre-vote、updatesもbaselineと同一である。未知shape、duplicate action signature、unknown keyword、remote ref、本文以外の差分は`SCHEMA_SHAPE_CHANGED`で停止する。

call1 rawをstrict JSONで読み、duplicate key、NaN/Infinity、float overflow、構文不正を拒否し、plan schema全体を検査する。その後、chatには固定内部値`.`を`message`、co_declareには`.`を`comment`としてdeep-copy上だけに挿入し、元baseline projectionの`parse_llm_output`へ渡す。placeholder入りcopyと`parse_llm_output`の返値は成功・拒否・例外の全経路で局所的に即時破棄する。call2 bindingとcanonical planは常にplaceholder挿入前のstrict元値からだけ作る。これはgrounded planの旧authority検査を再利用するためのvalidation-only placeholderであり、raw、公開row、call2入力、hash入力、最終出力、意味評価、stateへ保存・送信しない。none/vote/abilityは挿入なしで直接旧parserへ渡す。

旧parserがoption/target/role、EvidenceRef/visibility、actor/addressee/claim speaker、実prior/new cause、trigger/reaction、CO/pre-vote、更新重複・上限、distinct evidence、proposal bytesを拒否したplanは全体不合格とし、call2を実行しない。planの一部を補完・修正・再生成しない。

## 3. call2: Locked-plan Message

call2はcall1のdecision kindがchatまたはco_declareで、plan schemaと旧validatorの両方に合格した場合だけ実行する。none、vote、abilityはno-message actionとしてcall2を0回とし、合格したcall1値を最終候補にする。

call2 messagesは次の3要素を固定する。

1. baseline system messageをbyte-equivalentに保持する。
2. baseline user canonical inputをbyte-equivalentに保持する。
3. 固定user instructionと、strict検査済みplanのcanonical JSONを追加する。

固定instructionは「locked planの値を変更せず、そのspeech actと根拠に沿う公開本文だけをresponse schemaで返す」という内容に限定し、例文、追加事実、自由なrationale、秘密、role/target/defaultを加えない。plan JSONはcall1 rawではなくstrict parse後の値をcanonical化して同じ第三messageへ埋める。baseline 2 messages hash、stage2固定instruction template hash、canonical plan hash、完成stage2 messages hashを別々にfreezeする。original case/projection hash、call1 request wire hash、call1 raw hash、canonical plan hashをcall2 bodyへ束縛し、別case・別planの混線を送信前に拒否する。

call2 response schemaはchatならclosed `{"message": old_message_schema}`、co_declareならclosed `{"comment": old_comment_schema}`だけとし、旧枝のmin/maxLengthをcopyする。strict JSON、exact key、文字列型、schemaを検査する。call2はplanのaction、speech act、refs、updatesを出力できず、adapterもそれらを本文から推測しない。

合格した本文をcall1 planのdecisionへ値を変えず挿入し、完成した旧形式全体を元baseline projectionの`parse_llm_output`へ再度渡す。text scalar、UTF-8、truncation、proposal bytesを含む全体検査に合格した場合だけ候補を受理する。本文不合格、length finish、timeout、usage不一致、binding不一致では全体を破棄し、planだけを受理しない。

## 4. 可逆性と受理契約

plan抽出は合法な旧出力からchat.message/co_declare.commentだけを分離し、他の全値を保持する。結合は同じfieldへ本文を一度だけ戻す。merge前にkey交差とkind別exact key集合を検査し、型強制、trim、null除去、default、sort、deduplicate、ref/action導出を行わない。

focused helperは全5 actionについて次を満たす。

```text
join(split(old)) == old
split(join(plan, text)) == (plan, text)
```

no-message actionのtextは専用sentinel値ではなく「本文callなし」という制御状態であり、JSON fieldを追加しない。chat/co_declare以外に本文を結合する要求、本文field既存、wrong field、別kind planは拒否する。

候補の最終受理集合は、call1 planが旧validatorの非本文契約を満たし、call2本文を結合した完全旧出力が元`parse_llm_output`に受理されるものだけである。placeholderの一時検査で最終受理を代替しない。実在refが本文を意味的に支持するか、捏造・秘密・state/ability矛盾、act/text一致は独立意味評価へ残す。

## 5. 失敗原子性とcall accounting

1 caseを1 decision単位とし、状態は`PLAN_NOT_STARTED → PLAN_STARTED → PLAN_VALID → MESSAGE_STARTED → COMPLETE`の単調遷移とする。no-messageは`PLAN_VALID → COMPLETE_NO_MESSAGE`。各network dispatch前にcallを消費済みとしてdurable保存する。

- plan不適合: `PLAN_INVALID`、provider call 1、message call 0。
- plan timeout/error/length: `PLAN_ERROR`、call 1、message call 0。
- message不適合: `MESSAGE_INVALID`、call 2、最終候補なし。
- message timeout/error/length: `MESSAGE_ERROR`、call 2、最終候補なし。
- no-message成功: call 1。
- chat/co_declare成功: call 2。

いずれの失敗もretry、repair、fallback本文、別action、部分update、provider再起動による同case再実行をしない。plan確定中・call間・最終parse前後のstate/send副作用は0。runner再起動時は既存claim/resultがあれば再開せず、call消費記録を上書きしない。

最大は32 decisions / 64 calls。結果にはcaseごとの`plan_provider_calls`、`message_provider_calls`、合計、新規call上限、plan/message generation statusを保存する。32 decisionsを品質分母にし、call数を分母にしない。

## 6. token、REAL時間、runtime予算

旧baselineの`max_tokens=512`を2call合計上限として、call1 plan=`384`、call2 message=`128`に固定分割する。no-message actionでも未使用128をplanへ再配分しない。S1の安全集計では全field/nullを含む出力がcompletion p50 393、p95 512だった。P2 planはS1で増えた16 field/nullを持たず、さらに本文を除くため384を最初の有限仮説とし、残る128を旧最大200 scalarの短い本文へ割り当てる。この観測はP2の充足を保証しない。call1が384、call2が128で収まらない場合は実runの当該caseをlength/不適合として32件分母に残し、上限拡大や動的借用をしない。

schema/validator/roundtripの正しさとtoken適合は別gateにする。全合法shapeを384/128以内と証明する要件は置かない。旧512も最大合法全形の生成を保証していないため、最大合法fixtureがbudgetを超えることだけで全32生成を中止しない。実装前には全5 action、更新なし/代表的最大更新、ASCII/多byte本文境界の有限fixtureと、固定32入力のcall1/call2 promptを計数し、分布と超過診断だけを保存する。

offline計数には`C:/AIagent/llama-tokenize.exe` version `0.3.0-dev` build `10697` commit `093adb242`、SHA-256 `a0fbd34a8a3f25fc0f41cbac1ec67e8395a5ef940db33bd07307b5e3dc8cd6a1`を、同じcanonical GGUFに対してserverを起動せず使用する。引数は`--stdin --ids --no-bos --no-escape`に固定し、tool/model/argv hashをfreezeする。token ID列、stdout、stderr、入力本文は公開せず、fixture ID、固定count、pass/over-budget、tool/model hashだけを公開証拠へ保存する。このCLI診断と、実serverのapply-template→tokenizeによる各request context式・usage一致を混同しない。

各callについて`rendered_prompt_tokens_actual + assigned_max_tokens + 1 <= 8192`を送信前後に確認する。provider usageのprompt/completionをcall別に保存し、prompt actual一致、completionが割当上限以下、case合計completion `<=512`を検査する。plan/message proxy、schema bytes、rendered hash、usageを別単位で記録し、proxyをactual tokenとして扱わない。

HTTP request timeoutは各call60 REAL秒、load180秒、model全体1200秒（loadを含む）、所有cleanup各25秒、outer1320秒を据え置く。case開始時にplan call用`60 + 60`秒、本文が必要になった時点でmessage call用`60 + 60`秒を残す。残時間不足なら未送信callを`MODEL_TIME_BUDGET`として消費せず停止する。64callは同時1/slot1で逐次実行し、model全体deadlineを延長しない。

## 7. wire、binding、秘密

call1/call2 bodyを別々にcanonical化し、それぞれのschemaで宣言した新propertiesだけを指定順へ戻してimmutable wire bytesを作る。call1 planはbaseline root順、decision各枝の元順、discussion順を保持する。call2はmessage/comment一つだけである。各request bytesをprivateへcall種別付きで保存してから、同じbytesをapply-templateとgenerationへ渡し、MockTransportで三者一致を確認する。

plan raw、message raw、request、stdout/stderrはprivateに保持する。公開rowにはhash、bytes、token数、固定status/error code、kind、act、call数だけを出し、本文、EvidenceRef値、自由文、秘密、validation placeholderを出さない。`reasoning_content`は値がnullまたは空文字列なら不在として許可する。非空文字列、非空container、0/falseを含むその他の非null型は内容を保存せず`THINKING_NOT_DISABLED`で停止する。reasoning contentを利用・評価しない。

source identityはP2専用helper/test/runner/outerと現在の製品codeをexact allowlistでfreezeし、S1/I1 helperをsource deltaへ含めずimportもしない。model/runtime/argv/template/build、baseline artifact hash、32case順を固定する。call2 bindingはcase IDだけでなくprojection、plan、call1 wire/raw hashを全て照合する。

## 8. focused acceptance

- 全32caseでbaseline先頭2 messages/input/grounding/limits不変、stage1/stage2追加instructionと完成messagesのhash分離、call1 schema差分がdecision本文2fieldだけ、公式converter32/32。
- 全5 action、全7 speech act、全trigger、合法NONE、PRE_VOTE棄権、CO、ability 0/1 target、chat/co text、no-messageを両方向roundtrip。
- authority-closed positive matrix13件を旧出力とsplit/join後で完全一致。option/target/role、alive/dead、owner-only ability result、EvidenceRef/visibility、actor/addressee/claim speaker、prior/new cause、reaction/CO/pre-vote、updates、distinct evidence、proposal bytesを旧validatorと同じくaccept/reject。
- plan不適合・JSON不正・duplicate/nonfinite・length・timeoutではmessage endpoint call 0。message不適合時は最終出力/副作用0。
- call2 wrong case/plan/projection/raw/wire hash、古いplan、本文field違い、extra/missing keyを送信前またはschemaで拒否。
- schema/validator/roundtripをtoken gateから分離し、固定CLI identity/argvで有限fixture・32入力のcount診断を実行する。最大合法fixture超過だけでは全体停止せず、実caseのlengthを失敗として保持する。384/128、各context式、call別usage一致、合計completion<=512、最大64call、retry/repair0をMockTransportで検査。
- private save失敗、claim再利用、source/input/wire/runtime mismatch、apply-template/generation bytes差、provider HTTP error、即spawn exit、PID再利用、非所有process保護、cleanupを既存mockで回帰。
- raw不変、公開rowへの本文/plan値/placeholder漏えい0、通常game/state/send 0。placeholder入りcopyとparser返値が成功・拒否・例外の全経路で破棄され、call2 bindingがstrict元planだけから作られることを検査する。

独立Reviewerはplaceholderが最終出力へ混入しないこと、call2がplanを変更できないこと、plan invalid時のcall抑止、budget/usage/binding、旧validator同値、秘密境界を確認する。

## 9. 人工suiteと採否

独立設計承認→実装→独立tool審査→freeze→独立Testerの最大64call一回→独立意味評価の順を守る。32case全件を分母とし、plan/message未生成、schema不合格、旧validator不合格を除外しない。旧baseline/C1/C2/K1/I1/S1を再生成・再採点せず、保存baselineのhash付き判定だけを比較に使う。

相対条件はact/text mismatch `<23`、HARD fail `<=14`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`、各UNKNOWN非増加、合法NONE維持とする。これに加え、SEMANTIC passを保存baselineの7未満へ落とさず、固定18質問への本文回答を9未満へ落とさない。STYLE、decision kind、speech act、実message生成数、no-message数、call1/call2失敗数を別保存する。無発話でact/text mismatchが0になっても、それだけで会話改善と判定しない。

製品候補には別途D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0と独立reviewが必要である。1 seed/32caseの有限結果であり、2-call一般やmodel能力の結論にしない。

通常の候補不採用やfocused失敗は6時間/6cycle全体の停止条件ではない。同条件retryをせず証拠を保存し、残り上限内で独立承認済みの次の安全なcycleへ進む。重要な製品規則選択、権限衝突、破壊操作、外部認可欠落だけを停止条件とする。
