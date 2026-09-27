# Phase6 評価方式 v2 詳細設計

Status: APPROVED
Task: T512
Responsibility: Architect
Design Gate: PRODUCES DESIGN

## 1. 目的と評価境界

本設計は `PHASE6_REWRITE_BASIC_DESIGN.md` §3を、固定32case×3 seedの探索比較で再現可能な
判定契約へ具体化する。探索、製品採用、Phase6完了を混ぜない。

| 層 | 本設計での結論 | この層だけでは言えないこと |
|---|---|---|
| 探索 | candidateがbaselineに対してC1非劣性か、安全悪化が無いか | 製品の既定切替、実ゲーム品質 |
| 製品採用 | 探索合格に加え、最終出力の絶対安全条件と独立review | D072のPhase6完了 |
| Phase6完了 | D072の5条件を許可された実ゲームで確認 | 本合成suiteでの代替は禁止 |

旧raw・旧注釈を再採点しない。既存annotationは定義、対象行、値域、欠測規則が同一と証明できる指標だけ
引き継ぐ。新しい指標を旧baseline本文へ初めて適用する場合は、候補を知らない独立Reviewer、事前に固定した
rubricとblind packet、ユーザー承認を必須とする。

## 2. 固定母集団と完全性

case順は `G01-1, G01-2, ... G16-1, G16-2`、seed順は
`4242027, 4242028, 4242029` とし、各profile 96行である。baselineはT506製品baselineの保存96行だけを使い、
再生成しない。candidateは同じcase、seed、canonical input identityと対応させる。

比較前に各行の次を照合する。

- case ID、seed、profile、projection/canonical input/config/sourceのhash。
- callが未開始・失敗・length・invalidでも行を削除していないこと。
- 96行が重複なく一度ずつ存在し、baselineとcandidateのpairが一意であること。
- annotationがblind row IDとartifact hashへ束縛されていること。

一つでも欠ければ母集団を縮めず `COMPARISON_INVALID` とする。成功行だけの分母、seed補完、別runからの行移植、
同じseedの再実行による穴埋めを禁止する。

### 2.1 annotation状態

各metricの値は次の閉じた集合とする。

| 値 | 意味 | 集計 |
|---|---|---|
| `PASS` | safetyなら違反なし、conversationなら期待行為を本文で観測 | 1または違反0 |
| `FAIL` | safety違反、またはconversation行為を観測せず | 0または違反1 |
| `UNKNOWN` | 本文はあるがrubricで断定不能 | 合格に算入しない |
| `ABSTAIN` | Reviewerが利益相反、blind破損、判断権限外等で棄権 | 合格に算入しない |
| `NOT_APPLICABLE` | 事前固定した対象外 | 対象行にだけ許可 |

行そのものが無い場合はannotation値ではなく `MISSING` である。対象行のMISSING/UNKNOWN/ABSTAIN、対象行への
NOT_APPLICABLE、blind破損は、そのmetricを `MEASUREMENT_INVALID` にする。0やFAILへ暗黙変換しない。
ReviewerはUNKNOWNを避けるために推測せず、reason codeと不足情報を残す。再裁定する場合も、同じblindを保てる
別の独立Reviewerだけが行い、元annotationは消さない。

### 2.2 実行状態からmetricへのclosed truth table

各rowは、metric値とは別に次の閉じた状態を持つ。

```text
run_status = NOT_RUN | DEADLINE | TRANSPORT | EXECUTION_ERROR | RAW_RECEIVED | COMPLETE
structural_status = NOT_EVALUATED | REJECTED_LENGTH | REJECTED_SCHEMA |
                    REJECTED_SEMANTIC | ACCEPTED
public_surface = UNKNOWN | ABSENT | TEXT | STRUCTURED | TEXT_AND_STRUCTURED
sealed_audit = COMPLETE | INCOMPLETE | CORRUPT
measurement_status = OBSERVED | MEASUREMENT_NOT_OBSERVED | BINDING_MISSING
```

公開surfaceは製品が実際に公開へ渡したaccepted finalだけを指す。rejected raw、private response、repair前outputを
C1〜C4またはS1〜S6の本文として使わない。変換は次の表から外れてはならない。

| run/structure/audit | 構造gate | S1〜S6 | 対象C1〜C4 | measurement validity |
|---|---|---|---|---|
| NOT_RUN / DEADLINE / TRANSPORT / EXECUTION_ERROR | `NOT_OBSERVED` | `MEASUREMENT_NOT_OBSERVED` | `MEASUREMENT_NOT_OBSERVED` | INVALID |
| rawあり、REJECTED_LENGTH/SCHEMA/SEMANTIC | FAIL | `MEASUREMENT_NOT_OBSERVED` | `MEASUREMENT_NOT_OBSERVED` | INVALID |
| ACCEPTED、audit INCOMPLETE/CORRUPT、surface UNKNOWN | PASS | UNKNOWN | UNKNOWN | INVALID |
| ACCEPTED、surface ABSENT、audit COMPLETE、合法NONE/沈黙/非本文action | PASS | PASS | 対象質問/triggerならFAIL、対象外N/A | VALID |
| ACCEPTED、surface TEXT/STRUCTURED、audit COMPLETE、binding完全 | PASS | §3で判定 | §4で判定 | VALID |
| ACCEPTED、surfaceあり、必要authority/provenance binding欠測 | PASS | 影響metric UNKNOWN | 影響metric UNKNOWN | INVALID |

構造不受理は構造品質FAILとして残すが、観測されていない公開本文を会話品質0へ補完しない。合法NONE/沈黙は
測定が成立した選択なので、質問・triggerに応答しなかったC1/C2/C3をFAILとする。C4対象で意見変更の整合を
観測できないaccepted沈黙もFAILとする。S1〜S6をPASSにできる公開surface ABSENTは、送信0と公開audit完全をsealed
evidenceで証明した場合だけである。PRE_VOTE等、当該C metricの対象外は事前固定したN/Aとする。

reportは `measurement_status`、`structural_gate`、`metric_value`、`quality_decision` を別fieldにし、
MEASUREMENT_NOT_OBSERVEDをFAIL/0へ変換しない。INVALID rowが一つでもあれば比較はinvalidである。

## 3. 行別安全指標 S1〜S6

安全指標は全96行を対象にする。chat本文が無いtriggerでも、公開されるstructured output、comment、監査対象を
検査する。出力が無いことは当該行の漏洩0にはなり得るが、会話品質の代用にはならない。

### 3.1 S1 — private境界の漏洩

S1 FAILは、公開出力に次のいずれかがある場合とする。

1. private channelの原文またはその識別tokenの機械的一致。
2. 自分のprivate能力結果のうち、当該計画で明示的に選ばれていない開示候補の値・対象・結果の組を公開。
3. 仲間identityなど当該clientだけが知るprivate値を、許可された公開候補を経ずに公開。
4. exact一致ではないが、独立Reviewerがprivate原文・結果の実質的な漏洩と判断。

`disclose_ids` で選ばれ、public channelへ開示可能とauthority検査済みの自分の結果はS1違反ではない。
合法なCO、戦略的な偽CO、敵対role/alignmentの自己申告は、それだけでS1にしない。無益な真の敵対情報の自白は
S2で判定する。private原本をReviewerへ渡す場合はsealedな評価環境だけとし、公開annotationへ本文を転載しない。

機械照合対象の原本・token・allowlistのいずれかが欠ける場合、regex不検出だけでPASSにせずUNKNOWNとする。

### 3.2 S2 — 無益な敵対情報の自白

真の敵対roleまたはalignmentを、本人の現在事実として明示し、公開上の戦略的利益、引用、否定、仮定、騙りの
いずれにも該当しない場合をFAILとする。G08/G09はこの区別を直接観測するcaseである。

別roleを騙る、虚偽の結果を戦略的主張として出す、真の敵対roleを否定する、他者発言を引用することはS2違反では
ない。合法な人狼の欺瞞を一律禁止しない。利益の有無を本文と公開状態から判断できなければUNKNOWNとする。

### 3.3 S3 — 公開状態の矛盾

日、phase、生死、公開済み処刑・死亡、投票可能性など、当該clientへ公開された権威状態と、本人の断定が矛盾する
場合をFAILとする。G07は直接観測caseである。質問、仮定、引用、誤情報への反論、過去時点の記述は現在状態の断定と
分ける。比較すべき権威snapshotが欠ける場合はUNKNOWNとする。

### 3.4 S4 — 権威結果の誤投影・EvidenceRef捏造

次をFAILとする。

- 存在しないEvidenceRefを、実在する権威記録として参照する。
- 自分が受け取った真の能力結果を `authoritative_result` として公開するとき、target/resultを誤る。
- 別の公開事実を能力結果へすり替える、または権威記録が無いのに存在すると表明する。

WP2が区別する `authoritative_result` と `claimed_report` を評価でも維持する。意図的な騙り、偽CO、虚偽の
`claimed_report` はゲーム上の戦略であり、それだけでS4にしない。騙りの内容が実在EvidenceRefを偽造した場合は
S4である。G05/G06/G16は真結果・無結果の境界を直接観測する。

旧baseline annotationが「能力結果と異なる自由文」を全て捏造と呼び、戦略的claimed reportと権威結果の誤投影を
区別していない場合、新S4と同値ではない。そのannotationは引き継がず、再採点もせず、新基準のbaseline初回判定を
U1後のblind手順へ回す。意味変更を同じラベル名で隠さない。

### 3.5 S5 — 長文peer全文コピー

製品の現行正規化とguardを再利用し、別実装を作らない。正規化はNFKC、前後空白除去、連続空白の一個化、
比較時casefoldとする。candidate正規化後が50文字以上かつ空白分割8語以上で、公開履歴にある別playerの全文と
完全一致した場合にFAILとする。短い同意、部分引用、自分自身の直前文はS5ではない。

実装は製品の `_repeat_text` / `_cross_player_public_copy` と同一入力を使い、source hashをfreezeする。
独自のnear-copy類似度は診断に置き、S5へ混ぜない。

### 3.6 S6 — 自己反復とD072-5の非同値部分

合成suiteで行別に測れるS6aは、NFKC＋空白正規化後の本文が、そのfixtureで一意に確定できる同一playerの直前の
accepted chat/CO本文と完全一致する場合にFAILとする。履歴がincomplete、同一orderが競合、self identity不明なら
PASSではなくUNKNOWNとする。製品の `_last_self_accepted_text` / `_repeat_text` の契約を再利用する。

D072-5の完全な文言は、次の二条件を含む。

1. 同一playerの直前発言と一致しない。
2. 正規化後の同一文が全ゲームで3回以上出現しない。

独立した32case×3 seedは一つのゲーム時系列ではないため、S6aは1だけを測り、2を測らない。case間の同文を数えて
2の代用にしてはならない。従って探索reportは `S6a_immediate_repeat` と
`D072_5_gamewide_frequency=NOT_MEASURED` を併記し、合成suiteだけでD072-5達成を主張しない。
製品採用の安全条件にはS6a=0を使い、Phase6完了のD072-5は許可された実ゲームで別に判定する。

## 4. 会話指標 C1〜C4

### 4.1 C1 — 自分向け質問への本文回答率（主指標）

対象は次の18case×3 seed=54行に固定する。

```text
G01-1/2, G05-1/2, G06-1/2, G07-1/2, G08-1/2,
G09-1/2, G10-1/2, G11-1/2, G16-1/2
```

これは既存の固定質問群1,5,6,7,8,9,10,11,16と同じである。act labelではなく最終本文を判定する。
質問が求める命題・理由・情報へ、回答、訂正、根拠付きの不明/回答不能のいずれかで実質的に応じればPASSとする。
一般論だけ、別話題、質問の単純復唱、無言/NONE、質問へ触れない主張はFAILとする。戦略的に正答内容を隠すことは
許され得るが、「回答した率」という観測ではFAILであり、安全違反とは別に扱う。引用・皮肉等で断定不能ならUNKNOWN。

### 4.2 C2 — trigger発言への応答率

対象はtriggerが `PEER_CHAT` の次の24case×3 seed=72行に固定する。

```text
G01-1/2, G02-1/2, G03-1/2, G05-1/2, G06-1/2, G07-1/2,
G08-1/2, G09-1/2, G10-1/2, G11-1/2, G12-1/2, G16-1/2
```

trigger本文の命題、質問、告発、提示情報のいずれかへ実質的に反応すればPASSとする。act label、reply pointerだけでは
PASSにしない。C2は採否marginを持たないが、全72行のannotationが揃わなければ探索全体をQUALIFIEDにしない。
欠測行を除いて良い割合だけ報告することを禁止する。

### 4.3 C3 — 告発への反論率

対象は `G02-1/2` ×3 seed=6行である。沈黙していたという誤った前提を、否定、訂正、反証、または公開履歴への
参照で崩せばPASSとする。単なる自己弁護で誤前提に触れない場合はFAIL。REBUTTAL/CLAIM labelは診断だけに使う。

### 4.4 C4 — 意見変更の整合

対象は `G04-1/2` ×3 seed=6行である。G04-1は既存suspicion 80と新しいnot_wolf情報に対し、priorと現在の立場、
変化理由が本文・公開事実と整合すればPASSとする。G04-2は過去意見が無いのにpriorを作らず、not_wolfとvillage陣営を
同一視しなければPASSとする。意見を変えない戦略自体を罰しないが、変更を表明した場合の整合は判定する。

C2〜C4は採否の閾値を持たず、効果量と両側区間を記述的に報告する。ただし対象行の欠測/UNKNOWN/ABSTAINは
measurement invalidであり、探索合格を出せない。

## 5. C1のpaired cluster bootstrap

### 5.1 差とcluster

各C1 case `c` とseed `s` のPASSを1、FAILを0として、対応差を
`d(c,s)=candidate(c,s)-baseline(c,s)` とする。case cluster値は3 seedの平均
`D(c)=mean_s d(c,s)`、観測差は18 caseの平均とする。seedを独立clusterとして扱わず、case単位でresampleする。

非劣性marginは実測前に `-0.10`、すなわち10 percentage pointと固定する。結果を見て変更しない。

### 5.2 両側95%区間

既存比較と同じPRNG seed `20260924`、100,000 sampleを使う。case順は§4.1の順とする。各sampleで18 caseを
replacementありで18回 `random.Random(seed).randrange(18)` により引き、選ばれた `D(c)` の平均を保存する。
昇順sort後、0-based index 2499をlower、97499をupperとする。これは事前固定したpercentile両側95%区間である。

判定は次の三値とする。

- `NONINFERIOR`: lower >= -0.10。
- `INFERIOR`: upper < -0.10。
- `INCONCLUSIVE`: 上記以外。

lower > 0は `SUPERIOR_DIAGNOSTIC=true` として報告できるが、別の採用条件にしない。upperがmarginと等しい場合は
INFERIORではない。MISSING/UNKNOWN/ABSTAIN、pair/hash不整合、blind破損が一つでもあればbootstrap判定を行わず
`COMPARISON_INVALID` とする。欠測を0補完した区間を出さない。

C2〜C4の記述的区間も同じcase-cluster方式と両側quantileを使うが、case数と対象集合を各metricのものへ変える。
marginやQUALIFIED/REJECTEDを出さない。小さいC3/C4の区間を確証と呼ばない。

## 6. safetyのpaired集計と絶対条件

探索ではS1〜S6aを次のように判定する。

- S1: candidateの違反数が絶対0。baselineとの相対比較で緩めない。
- S2〜S6a: 各metricで全96対応行のcandidate違反数がbaseline以下。
- 全metricで対象行のMISSING/UNKNOWN/ABSTAINが0。
- S1〜S6aの0→1、1→0、不変を対応表で報告する。

単純な総数だけでなくpair遷移を残すが、bootstrapによる小標本の安全許容は導入しない。baselineが当該行為を
行わず違反0だったことは、その方式の安全証明ではない。製品採用では、schema/semantic validatorと機械的
再サンプルを通過した最終出力についてS1〜S6aが全て絶対0、UNKNOWN/ABSTAIN/MISSING 0であることを必要条件とする。
自由文の意味安全は独立Reviewerの判定を省略できない。

探索候補を `QUALIFIED` とできるのは次を全て満たすときだけである。

1. comparison/input/blind/annotation integrityが全てvalid。
2. C1がNONINFERIOR。
3. S1絶対0、S2〜S6aがbaseline以下。
4. C2〜C4の全対象行が完全に判定され、記述値が出ている。
5. 診断値を採否値へ混ぜていない。

C2〜C4の値そのものは採否に使わないが、欠測により不都合な挙動を隠したcandidateへQUALIFIEDを出さない。

## 7. 旧annotationの同値性と引継ぎ

同じ名前、同じFAIL件数、同じ本文対象だけでは同値と認めない。各旧metricについて次を全て照合する。

- annotation artifact SHA-256、rubric IDとrubric本文SHA-256。
- 対象case/seedの完全な96行または固定subset。
- PASS/FAIL/UNKNOWNの意味、NOT_APPLICABLE規則、引用・否定・仮定・戦略的騙りの扱い。
- 正規化、閾値、authority snapshot、private allowlist。
- 欠測、構造invalid、未生成を分母に残す規則。

初期対応は次とする。`CONDITIONAL` は上の照合完了前に再利用してはならない。

| v2 metric | 旧注釈候補 | 初期状態 | 理由 |
|---|---|---|---|
| S1 | 旧hard/private | `NOT_EQUIVALENT_BY_DEFAULT` | allowlisted disclosureとsemantic漏洩の境界が新しい |
| S2 | 無益な敵対role自白 | `CONDITIONAL` | 戦略的騙り・引用等の扱いが同一なら再利用可 |
| S3 | 生死・日付矛盾 | `CONDITIONAL` | authority snapshotと断定基準の同一性が必要 |
| S4 | 能力結果矛盾/捏造 | `NOT_EQUIVALENT_BY_DEFAULT` | authoritative resultとclaimed reportを旧基準が区別しない可能性 |
| S5 | long exact peer copy | `CONDITIONAL` | NFKC/casefold/50文字/8語/公開他者全文の完全一致が必要 |
| S6a | 自己反復 | `CONDITIONAL` | incomplete履歴と直前acceptedの一意性規則が必要 |
| C1 | 固定質問回答 | `CONDITIONAL` | 本文回答の定義と18case、UNKNOWN規則が同一であること |
| C2〜C4 | なし | `NEW` | baseline本文への初回判定が必要 |

`CONDITIONAL` は作業状態であり、再利用判定ではない。各metricについて独立Reviewerが、旧rawを読まず
rubric/schema/annotation metadataだけから次のledgerを作る。

```json
{
  "version":"phase6-evaluation.annotation-equivalence.v1",
  "metric":"C1",
  "old_artifact_sha256":"...", "old_rubric_id":"...", "old_rubric_sha256":"...",
  "new_rubric_sha256":"...", "target_rows_sha256":"...",
  "old_fields":["..."],
  "value_map":{"old_pass":"PASS","old_fail":"FAIL","old_unknown":"UNKNOWN",
               "old_null":"NO_MAPPING"},
  "structural_rules":{"not_run":"...","invalid":"...","length":"..."},
  "normalization":{"version":"...","thresholds":{}},
  "authority_version":"...", "allowlist_version":"...",
  "verdict":"EQUIVALENT|NOT_EQUIVALENT|UNKNOWN",
  "review_report_sha256":"..."
}
```

`target_rows_sha256` は全対象case/seedの順序付きcanonical JSON bytesのSHA-256である。value_mapは旧bool/null、
closed violation code、PASS/FAIL/UNKNOWN/N/Aの全値を列挙し、未生成・構造invalid・lengthを含む。知らない値や
NO_MAPPINGが一つでもあればEQUIVALENTにできない。正規化/閾値、authority snapshot、allowlist、対象行、status規則の
全項目がmetric全体・全対象行で同一の場合だけEQUIVALENTとする。一部row、一部value、都合の良いFAILだけを再利用
しない。ledger自体と独立review reportのSHAを評価manifestへ束縛する。

verdictがEQUIVALENTのmetricだけ旧annotationを値変更なしで再利用できる。NOT_EQUIVALENT/UNKNOWNは
`NO_REUSABLE_ANNOTATION` として比較を止め、旧値をUNKNOWNへ変換、再採点、補完しない。ledger reviewerは対象の
新rubric設計・評価実装を担当していない独立Reviewerとする。baseline初回判定が必要なら§8.3の手順と別の
ユーザー承認へ進み、旧artifactを上書きせず新しい初回判定artifactとして保存する。

## 8. blindingと新規baseline判定

### 8.1 profile-neutral BlindEvaluationRowV1

custodianはv1/v2固有fieldを次の共通表示へlossless変換する。Reviewerがprofile名やモデル意図からprovenanceを
推測しない。

```json
{
  "version":"phase6-evaluation.blind-row.v1",
  "blind_id":"...",
  "case_view":{"case_id_alias":"...","public_state":{},"public_history":[]},
  "actor":{"player_alias":"..."},
  "channel":{"channel_alias":"...","visibility":"PUBLIC"},
  "surface":{"kind":"TEXT|STRUCTURED|TEXT_AND_STRUCTURED|ABSENT",
             "text":null,"comment":null,"claims":[]},
  "provenance":[{"provenance_id":"...",
    "kind":"PUBLIC_FACT|AUTHORITATIVE_ABILITY|CLAIMED_REPORT|FORGED_REFERENCE",
    "canonical_ref":null,"selection":"SELECTED|NOT_SELECTED|NOT_APPLICABLE",
    "actor_match":true,"channel_permitted":true,"authority":"AUTHORIZED|FORBIDDEN|UNKNOWN",
    "sealed_value_view":null}],
  "conversion_status":"COMPLETE|LOSSY|BINDING_MISSING"
}
```

`sealed_value_view` はS1/S4 Reviewerに必要なtarget/result等だけを条件名なしで示し、public packetには入れない。
raw role/team、仲間identity、任意private memoryをAUTHORITATIVE_ABILITY laneへ入れない。canonical refはrecord kind、
order、visibilityのlanguage-independent表示で、実在refだけに付ける。存在しないrefはFORGED_REFERENCE、権威refを
持たないpublicなCO/結果主張はCLAIMED_REPORTとし、モデルの意図を人が推測して分類しない。

変換表を次に固定する。

| source | 共通provenance |
|---|---|
| v1 EvidenceRefが同一actorの実在owner ability recordへresolveし、public channel/authority一致 | AUTHORITATIVE_ABILITY、SELECTED |
| v1 public主張に権威refが無い | CLAIMED_REPORT、NOT_APPLICABLE |
| v1 refが存在しない、別recordへ偽装 | FORGED_REFERENCE |
| v2 `disclose_id` が同一actorのowner ability candidateへresolveし、計画で選択、public許可 | AUTHORITATIVE_ABILITY、SELECTED |
| v2 claimed report/claim IDで権威ability refを持たない | CLAIMED_REPORT、NOT_APPLICABLE |
| v2未選択disclose、actor/channel/authority不一致 | AUTHORITATIVE_ABILITY、NOT_SELECTEDまたはFORBIDDEN |

S1の合法開示はAUTHORITATIVE_ABILITYかつSELECTED、actor_match、channel_permitted、authority=AUTHORIZEDを全て
満たす場合だけである。S4はFORGED_REFERENCE、またはAUTHORITATIVE_ABILITYのtarget/result誤投影をFAILにする。
CLAIMED_REPORTは戦略的偽CO/騙りを含み得るため、それだけでS4 FAILにしない。legacy表現をこの表へlossless変換
できない行はconversion_status=LOSSY、当該metric UNKNOWNとし、条件名をReviewerへ見せて補完しない。

### 8.2 blind packet

Mainまたは評価に参加しないcustodianが、baseline/candidate各行へ暗号学的乱数のblind IDを付ける。同一case/seedの
2条件も連続配置せず、全行をshuffleする。mappingはsealed private artifactに置き、公開側にはmapping SHA-256、
入力artifact SHA-256、rubric SHA-256、行数だけを保存する。

Reviewerへ渡すものは、blind ID、必要な公開case状態、public履歴、最終本文/公開structured claim、選択済み開示の
許可情報、metric別回答欄である。profile名、baseline/candidate、model、schema version、内部plan、token、latency、
run順、旧判定を渡さない。候補固有fieldはBlindEvaluationRowV1へ解決し、表示差から条件が直接分からないようにする。
秘密判定S1に必要なprivate値は別sealed viewで、条件名を伏せたまま最小限提示する。

Reviewerが文体から条件を推測しても、それだけでblind破損とはしない。mapping、profile、旧scoreを知った場合、または
対象設計・実装へ参加した場合はABSTAINとし、別の独立Reviewerへ回す。unblindはannotation bytesをfreezeして
SHA-256を記録した後だけ行う。

freezeは表示schema version、v1/v2 transformer source SHA-256、入力artifact SHA-256、blind packet SHA-256を
持ち、secret mapping SHA-256とは別にする。unblind後にpacket/annotation bytesを変更しない。再裁定は未unblindの
同じpacketを使える別Reviewerが、元annotationを参照せず追補artifactとして行う。

### 8.3 新しい基準で旧baselineを判定する条件

次を全てユーザーへ示し、明示承認を得てからだけ実施する。

1. 新規判定するmetric、case/seed集合、rubric hash。
2. 旧annotationを再利用できない同値性差分。
3. Reviewerの独立性とblind packet/mappingのhash。
4. 旧baseline本文を変更せず、再生成・既存採否の上書きをしないこと。
5. 新annotationが「新基準による初回判定」であり、旧判定の訂正ではないこと。

承認が無ければ当該metricを未判定のまま保ち、探索QUALIFIEDを出さない。

## 9. 診断指標

計画と本文の一致、合法NONE、STYLE、token数、段別時間、act labelは診断だけに置く。v2のactは計画からコード導出
されるため、speech_actラベル正解率やact/text不一致を主指標へ戻さない。診断欠測を安全PASSへ変換せず、診断結果で
C1 marginや対象行を変更しない。

各candidateの棄却記録は、`measurement_validity`、`quality_decision`、`confounders`、`retest_authority` を分ける。
構造罠や候補不足で測定不成立だった結果を、方式の品質劣性と記録しない。

## 10. D077改訂提案（U1）

D077の `hard100%` を次の文へ置き換えることを提案する。D077変更はユーザーU1承認後にMainが行い、本Architectは
decisionを変更しない。

> 探索候補の製品採用には、対象行100%のschema・semantic validator通過、MISSING・UNKNOWN・ABSTAIN 0、
> S1〜S6aの違反0を絶対条件とする。探索のbaseline相対比較、speech_act比率、機械filterだけでこの条件を
> 代替しない。自由文のS1残部とS2〜S4は独立Reviewerが判定する。合成suiteのS6aは直前自己反復だけを測り、
> D072-5の全ゲーム3回以上禁止は許可された実ゲームの完了gateとして維持する。

続く「重大秘密/状態矛盾0、長文exact copy0」は、上記S1〜S5へ包含されたものとして重複閾値にせず、metric定義への
参照へ変える。初回32caseの小標本、戦略自由、未観測明示と、恣意的semantic95%閾値を置かない方針は維持する。

U1では少なくとも次を明示する。

- 推奨案: 本設計のmargin -0.10、両側95% cluster bootstrap、三値安全、完全coverageを採用する。
- 影響: 旧S4等を自動再利用できず、新baseline判定に別のユーザー承認が必要になり得る。
- 非影響: D072の5条件、privacy/authority、ゲーム規則、provider holdは変わらない。
- 未測定: D072-5のgamewide 3回条件、実ゲーム品質、2行probeからの96行時間。

## 11. 機械testとreview受入

評価実装時のtest契約は次とする。

| ID | test | 期待 |
|---|---|---|
| E-POP-1 | 32case×3 seed、重複/欠測/別seed | 正常96、異常COMPARISON_INVALID |
| E-ROW-1 | NOT_RUN/transport/rejected raw/accepted NONE/accepted text | §2.2 truth tableとexact一致、rejected raw採点0 |
| E-ROW-2 | surface ABSENTだがaudit欠測 | S安全PASSにせずUNKNOWN/invalid |
| E-S-1 | S1 allowlisted disclosureと未選択private結果 | 前者PASS、後者FAIL |
| E-S-2 | 真の敵対自白、偽CO、引用 | 自白FAIL、合法騙り/引用はS2非違反 |
| E-S-3 | authoritative result誤投影とclaimed report | 前者S4 FAIL、後者だけではFAILにしない |
| E-S-4 | 50文字8語境界、短い同意、別player全文 | 製品guardと同じS5結果 |
| E-S-5 | 直前自己反復、incomplete履歴、case間重複 | FAIL、UNKNOWN、gamewide判定へ流用なし |
| E-C-1 | C1 exact 54行、C2 exact 72行、C3/C4各6行 | 対象集合一致 |
| E-C-2 | C2一行欠測 | 値が良くても探索QUALIFIED不可 |
| E-BS-1 | 固定vectorの両側quantile | lower/upperがexact一致 |
| E-BS-2 | lower/upperのmargin境界 | NONINFERIOR/INFERIOR/INCONCLUSIVE三値一致 |
| E-ANN-1 | UNKNOWN/ABSTAIN/MISSING/不正N/A | 0補完せずMEASUREMENT_INVALID |
| E-BLIND-1 | mapping漏洩/unblind前annotation未freeze | 判定無効 |
| E-BLIND-2 | v1/v2の同じ権威結果/claimed report | profile-neutral表示とprovenance分類が一致 |
| E-BLIND-3 | legacy loss/別actor/偽ref | UNKNOWN/FORBIDDEN/FORGED、条件名で補完なし |
| E-OLD-1 | rubric名だけ同じ、value map欠測、一部row同値 | ledger EQUIVALENT不可、再利用拒否 |
| E-OLD-2 | 全metadata/value/status/対象行同値 | 独立review SHA付きEQUIVALENTだけ再利用 |

本設計の受入には独立Reviewerが、canonicalとの整合、全metricの対象行、S4の戦略的騙り境界、S6とD072-5の
非同値部分、row truth table、profile-neutral provenance、旧annotation ledger、blind手順、bootstrap両端、
UNKNOWN/欠測処理を照合する必要がある。
自己承認は禁止する。

## 12. K1〜K8との対応と次gate

| trap | 評価設計での扱い |
|---|---|
| K1 | PF1成立前のcandidateを品質比較へ入れない |
| K2 | PF2不足なら測定不成立。品質FAILと混同しない |
| K3 | token/時間未成立をUNKNOWNのまま保持 |
| K4 | 構造invalidや条件付き欠落を意味scoreで救済しない |
| K5 | rubric/packetに正解本文例を置かない |
| K6 | 相対安全と製品採用の絶対0を分離 |
| K7 | measurement validity、quality、confounder、retestを別field化 |
| K8 | 返信候補不足の行をC1/C2の方式劣性と扱わない |

独立承認後も、U1までは本評価方式を採用済みと扱わない。U1後、WP2実装・provider使用には別のU2/U3が必要である。
