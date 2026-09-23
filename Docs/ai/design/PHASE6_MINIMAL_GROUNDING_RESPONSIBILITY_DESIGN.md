# Phase 6 minimal-output grounding責務分離 詳細設計

Status: APPROVED

## 1. 目的と境界

T499は不採用であり、既存rawの修復・再生成・再採点には使わない。本設計が検証する仮説は一つだけである。speech/trigger fieldでLLMが既に選んだ`EvidenceRef`を、同じ応答の`grounding[]`へpurpose付きで再記述させる責務を除き、hostがfield位置から決定的な検査viewを作れるかを確認する。

変更候補はtest-only schemaからtop-level `grounding` property/requiredを外すこと、専用`GROUNDING_INSTRUCTION` clauseだけを追加instructionから外すこと、validator内で検査用grounding viewを構築することに限定する。action、utterance、合法NONE、copy、legacy original system prefix、他prompt、model、sampling、context、token上限、ゲーム規則、private境界、subjective stateは変更しない。hostは本文意味、主張の支持、公開戦略、主観stateを推測せず、regex、source補完、欠落ref補完を行わない。

## 2. 現在の重複flowと提案ownership

### 2.1 現在

1. LLMが`speech_act`または`trigger_detail`のref fieldを選ぶ。
2. LLMが同じrefを`grounding[]`へpurpose付きで再生成する。
3. `expected_grounding(value)`がref field位置から同じpurpose/ref集合を作る。
4. `_bindings`がexpectedと生成mirrorをCounter一致させ、生成mirrorの各refをprojected/captured authorityと照合する。
5. 後段がANSWER/REBUTTAL actor、PEER_CHAT trigger/channel等を再検査する。

T499では24/32の最初の拒否位置がstep 4のmirror不一致だった。これは24件の安全性や意味品質が自動的にPASSすることを示さない。別のaction/text、copy、fabrication、質問回答、grounding support評価は残る。

### 2.2 提案

1. LLMは既存のspeech/trigger ref fieldだけを生成する。
2. strict schema/parse後、hostが`derive_grounding_view(parsed_candidate)`を一度だけ呼ぶ。
3. validator process内では、戻り値のimmutable `DerivedGroundingV1`一個をref authority検査、actor/channel/trigger検査、safe countとprivate snapshot作成へ渡す。各consumerが再導出しない。
4. `DerivedGroundingV1`は検査中間表現であり、candidate JSONへ書き戻さず、旧完全proposalを構築せず、公開発話へ追加せず、保存rawを補正しない。後続processの独立評価はPython object identityを継承せず、束縛済みprivate snapshotだけを読む。

情報所有者は、ref選択=LLM、field位置からpurposeへの写像=固定schema契約、存在・可視性・actor・authority・trigger・option/target合法性=host authority、本文の支持・公開妥当性=独立意味評価である。

## 3. 中間表現と決定的写像

```text
EvidenceRefKey = (record_kind, order, visibility)
DerivedGroundingItemV1 = (purpose, ref_key, canonical_ref_value)
DerivedGroundingV1 = tuple[DerivedGroundingItemV1, ...]
ValidationResultV1 = (probe_result: ProbeResult, derived_grounding: DerivedGroundingV1)

derive_grounding_view(candidate_without_grounding) -> DerivedGroundingV1
validate_without_grounding(raw, binding) -> ValidationResultV1
```

`derive_grounding_view`は本文を読まず、既存closed schemaとdataclass shapeを通過した明示refだけを次の位置から走査する。順序は表の順、各arrayはcandidate順とする。既存evidence arrayの`uniqueItems:true`とmodel shapeを維持し、同一array内の重複は導出前に`SHAPE_INVALID`とする。同じ`(purpose, EvidenceRefKey)`がcross-fieldで複数位置に現れた場合だけ、現行`expected_grounding`と同じく最初の一件を保持する。異なるpurposeの同じrefは別itemである。同じkeyのrefはschema上の三fieldが同じなのでcanonical bytes/valueも同一でなければならず、異なる場合は`BINDING_INVALID`とする。actor等のconsumerはJSON object identityではなく、このcanonical key/value identityで照合する。このcross-field/cross-purpose整理により、現行expected側が表現する情報をlosslessに保つ。

| source位置 | purpose | 損失と除外 |
|---|---|---|
| `speech_act.evidence[]` | `UTTERANCE` | CLAIM/ANSWER/REBUTTAL/RELATION_HYPOTHESIS等の明示refだけ。本文中の暗示は追加しない |
| `speech_act.source` | `UTTERANCE` | QUESTIONのnonnull sourceだけ。nullはitemを作らない |
| `speech_act.in_reply_to` | `UTTERANCE` | ANSWER/REBUTTALの明示ref。actor/addressee検査も同じcanonical key/valueを使う |
| `speech_act.causes[]` | `OPINION_CURRENT` | OPINION_CHANGEの現行causesを全件保持。priorとの差分・新規性検査は別に維持 |
| `trigger_detail.trigger` | `REACTION` | PEER_CHATの明示trigger。reaction source/channel検査も同じcanonical key/valueを使う |
| `trigger_detail.evidence[]` | `PRE_VOTE` | PRE_VOTE再評価の明示evidenceを全件保持。rank/option/target検査は別に維持 |

CO_OPPORTUNITYは現行trigger detailにEvidenceRef fieldがないためderived itemは0件であり、DECLARE/SILENCE/DEFER、option、claimed role検査を変更しない。INITIAL_CHAT/ABILITYもtrigger固有refを新設しない。NONEはspeech fieldにrefがなければ0件であり、合法NONEを拒否せず、本文や履歴からrefを作らない。

`EvidenceRef`の三fieldを変更・補完しない。異なるcross-field JSON位置の同keyは別objectでもよいが、canonical valueはexact一致し、全consumerは`(record_kind, order, visibility)`とcanonical valueで照合する。rawはcanonicalize・書換えず、合法なcross-field同keyを許す既存受理集合を狭めない。同一array内重複はこの規則の対象外である。

raw candidateとderived viewは別identityである。UTF-8 byte cap、raw SHA、strict JSON、unknown/duplicate key検査は受信したgroundingなしrawだけに適用する。derived viewはrawへserialize・連結せず、raw byte数/hashやprovider output identityを変更しない。

runnerは一個の`ValidationResultV1`からsafe countとprivate snapshotを一度だけ作る。snapshot canonical bytesは`case_id, raw_sha256, input_sha256, schema_sha256, probe_result, derived_grounding`を含み、private containerへcreate-new/fsync/readbackで保存し、そのcontent SHAもprivate locator metadataだけへ束縛する。後続評価processはこのsnapshotをhash検証してrehydrateするが、元processとのobject identityを主張せず、再導出もしない。snapshot/sink失敗時はsafe resultを成功扱いしない。

## 4. 検査対応表と受理契約

| 現行検査 | 新しい入力 | 維持条件 |
|---|---|---|
| strict JSON / closed schema | groundingなしcandidate | top-levelのgrounding以外のkey、branch、boundsはexact不変。未知keyと候補側groundingは拒否 |
| mirror Counter一致 | 廃止 | LLMによる二重記述だけを除去する。外部入力からpurposeを受けない |
| ref existence/visibility | `DerivedGroundingV1` | 各ref keyがprojectedとcapturedの双方に存在し、full descriptorが双方exact一致 |
| actor | shared reply ref | ANSWER/REBUTTAL source actorがexact 1、self以外、addresseeと一致 |
| trigger/channel | shared reaction ref | PEER_CHAT detail.trigger=`reaction_source`、captured channel=offered chat channel |
| OPINION_CHANGE | shared causes | subject prior存在、prior値一致、少なくとも一つがprior evidenceにないこと |
| PRE_VOTE | shared evidence + existing detail | optionはvote、ranked targets合法、preferred targetとdecision整合、abstain条件不変 |
| CO | existing detail/decision | DECLARE tupleまたはSILENCE/DEFERとnoneの整合、option/role authority不変 |
| option/target legality | existing decision/detail | offered option、vote/ability targets、CO roleの全検査不変 |
| text/action/copy | existing fields | 一切変更しない |

`projected∩captured`は単なるkey intersectionではない。identityが同じでもvisibility、actor list、channel等のdescriptor差は`BINDING_INVALID`である。refは両catalogにあるexact descriptorだけを許す。current state、本文、role知識、別sourceからEvidenceRefを合成しない。

新契約の受理は次で閉じる。

- grounding以外が同じ旧candidateについて、旧`grounding`が`expected_grounding`とexact一致し、旧validatorの全後段検査を通るなら、groundingを除いた新candidateも通る。
- grounding以外が同じ新candidateが通るなら、`DerivedGroundingV1`を現行canonical順で旧groundingへ機械投影した仮想値はmirror一致する。ただしこの投影は証明/test fixture専用で、candidate修復、保存、再採点、provider入力に使わない。
- 旧candidateのmissing/extra/wrong-purpose mirrorだけは比較対象責務から除く。ref field自体の欠落、unknown shape、不正visibility、catalog不一致、actor/channel/trigger不一致、option/target不正は引き続き拒否する。
- したがって「legacy full contract同値」や「24件PASS」は主張しない。同値範囲はmirror一致済み旧candidateに対する、grounding以外の機械受理条件だけである。

validation順はraw UTF-8 byte cap→strict JSON→closed schemaとdataclass shape→host shape→`_offered`相当のaction/target/OPINION prior/new-cause検査→binding bytes/hash→derived ref authorityとactor/trigger/channel→textとする。候補側grounding keyは`SHAPE_INVALID`、ref descriptor不一致は`BINDING_INVALID`とし、意味評価で機械拒否を上書きしない。

## 5. schema・promptの一因子delta

test-only response schemaからtop-level `grounding` propertyとrequired entryだけを除く。`$defs/evidence_ref`とspeech/trigger内ref schemaは保持する。追加instructionから独立定数`GROUNDING_INSTRUCTION`だけをexact除去し、legacy original system prefix、他のminimal instruction、2-message配置、user bytesを変更しない。schemaの他部分、body parameters、model/sampling/context/max tokensもbyte不変とする。

比較時は旧candidate schema/instruction SHAと新SHA、除去したJSON pointer 2件とclause SHAをfreezeする。token差は重複責務削減を含む記述値で、品質改善の原因証明にしない。prompt全面変更、警告追加、action/text修正を混ぜない。

## 6. 正例・負例

正例:

1. ANSWERが`in_reply_to=chat:7:PUBLIC`を持ち、projected/captured descriptorが一致し、actorがpeer、addresseeもpeer。derivedは`UTTERANCE`一件となる。
2. OPINION_CHANGEがcauses二件を持つ。derivedはcandidate順の`OPINION_CURRENT`二件となり、prior/new evidence検査も別途通る。
3. PEER_CHATでdetail.triggerがreaction sourceと一致し、captured channelがoffered chat channelと一致。derivedは`REACTION`一件となる。
4. PRE_VOTE evidence二件、合法option/rank/preferred target。derivedは`PRE_VOTE`二件となる。
5. CO DECLAREまたは合法NONEにref fieldがない。derived空でも既存CO/decision検査で判定する。
6. ANSWER/REBUTTALの`evidence[0]`と`in_reply_to`に同じkeyの別JSON objectがある。このcross-field重複は合法で、同じcanonical valueとして一件へ整理し、reply actor検査はそのkey/valueで行う。PEER triggerとspeech refが同じkeyでもpurposeが異なるため双方を保持する。

負例:

1. 本文が過去発言に触れるがref fieldが空。hostは本文から補完せず、grounding supportは独立意味評価でfalse/unknownになり得る。
2. ref keyがprojectedだけ、capturedだけ、または同identityでdescriptor差。`BINDING_INVALID`。
3. ANSWERのreply actorがself、複数、addressee不一致。`BINDING_INVALID`。
4. PEER_CHAT triggerがreaction sourceと違う、またはchannel不一致。`BINDING_INVALID`。
5. OPINION_CHANGE causesが全てprior evidence、PRE_VOTE targetが非合法、CO roleが非offer。既存固定errorで拒否。
6. candidateがtop-level groundingを送る、未知purposeを送る、旧full proposal fieldを送る。closed schemaで拒否し、adapter補正しない。
7. 重複に見えてもvisibility等の一fieldが違えば別keyでありdedupeしない。各keyをauthorityへ独立照合し、catalogにない方を`BINDING_INVALID`とする。private snapshotのraw/input/result binding違いも拒否し、再導出や修復をしない。
8. `speech_act.evidence[]`、`causes[]`、PRE_VOTE `evidence[]`など同一array内に同じrefを二回置く。既存`uniqueItems:true`/model shapeにより導出前に`SHAPE_INVALID`とし、dedupeして受理しない。

## 7. acceptanceとfocused test設計

実装前に独立reviewが次を承認する。

- schema diffがgrounding property/requiredだけ、instruction diffが`GROUNDING_INSTRUCTION`だけである。
- 全speech kind・全5 trigger・全action branch・合法NONEについてderive totalityと上表の写像が成立する。
- OPINION_CHANGE/PRE_VOTE/PEER_CHAT/COの情報損失が上記の通りで、後段検査が失われない。
- validator processでは一個の`ValidationResultV1`/derived viewを全consumerとsnapshot writerが使い、consumer別再導出がない。後続processはbinding検証済みprivate snapshotだけをrehydrateする。
- ANSWER/REBUTTALのevidenceとin_reply_toの合法cross-field重複、PEER triggerとのcross-purpose保持を正例にする。同一array内重複は既存shapeどおり`SHAPE_INVALID`となる負例にし、validatorを緩めない。
- raw byte cap/hashとderived view identityを分離し、projected/captured exact descriptor、visibility、actor、trigger/channel、option/target negative matrixが旧validatorと同じ固定errorになる。
- mirror一致済み旧fixture↔grounding除去新fixtureの受理同値を全branchで確認する。mirror不一致fixtureを新schemaで通す試験は責務削除の確認に限定し、意味PASSとは記録しない。
- candidate rawを修復・materialize・再採点する関数、regex、source/subjective補完が存在しない。
- derived canonical bytes/content hash、各ref、visibility、actor、channelはprivate containerだけに置く。public result/handoff/annotationは総item数、purpose別count、検査PASS/FAIL、candidate raw/schema/input等の既存safe hashだけを許し、derived content hashがないnegative testを持つ。

実装とtestの具体file/API、runner、provider測定は独立review後の別packetで確定する。本設計は新provider call、旧raw読取、T499再評価を許可しない。

## 8. 未解決・期待効果・交絡

期待効果は、同じref/purposeを二度生成する構造負荷とmirror mismatch機会の除去である。意味上のgrounding support、act/text一致、質問回答、copy、fabrication、能力矛盾が改善するとは予測しない。特にref fieldを選ばない出力は、重複責務削除後もsupportを失ったままである。

未解決は、モデルが各speech/trigger ref fieldを十分に選ぶか、選んだrefが本文主張を実際に支持するか、秘密を含め何を公開する戦略が妥当かである。visibility保証はprojected/captured descriptor一致だけを意味し、自由文の真偽、支持、秘密開示の妥当性を保証しない。これらはhostが機械導出できず、独立意味評価を要する。T499はlegacy instruction、NONE偏重、action/text、copy等の交絡を含むため、本設計の一因子効果の証拠ではない。

次gateは本SHAの独立design reviewである。APPROVED後もtest-only実装とfocused/tool reviewまでで、追加provider測定の許可にはならない。
