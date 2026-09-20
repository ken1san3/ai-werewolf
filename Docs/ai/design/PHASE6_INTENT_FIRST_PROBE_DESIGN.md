# Intent-first 1call 最小実験設計

Task: T442
Status: DRAFT

今回の案A＝旧比較文書のB。設計のみ。製品実装、provider操作、実LLMは未実施・未承認。

## 1. 選択と事実

選択するのは **intent（行為・根拠・action）→realization（本文）→updates（任意更新の明示値）** の閉じた1call envelope。旧rootのproperty順を変えるだけではなく、本文と更新を別objectへ移し、intentに本文fieldを置けないschemaにする。K1の各speech_act枝kind-first、C1のproposal順、C2 reasonは組み合わせない。

K1は不一致23→18でもHARD fail14→23、捏造0→8で不採用。旧結果・旧承認は再審査しない。新形式なら行為と本文を合わせやすくなるという仮説であり、改善や生成時の因果は未検証。

現 `projection.py::_decision_schema` のPhase6枝はnone/chat/vote/ability/co_declare。co_reportはschema生成で除外され、`decision.py::_parse_decision_value(contextful=True)` でも拒否される。`parse_llm_output` は全schema→action→proposal→意味整合を検査する。新候補はtest-only adapter後にこの経路をそのまま使用する。

## 2. 出力interfaceと全action map

全objectをadditionalProperties=falseとする。rootのrequiredは `intent, realization, updates`。intentのrequiredは `discussion, decision`。intent.discussionは旧proposalから更新4fieldだけを除いた8fieldを全てrequiredで保持する。

```text
intent.discussion:
  speech_act, schema_version, base_revision, decision_kind, option_id,
  reaction, co_judgment, pre_vote_reassessment
intent.decision: 下表
realization: 下表
updates:
  assessment_updates, claim_updates, relation_updates, strategy_update
```

intentを「内部思考の説明」として扱わない。ここにあるのは従来から外形的に検証する行為・根拠・選択値だけ。自由文rationaleや思考過程を追加しない。decision/discussionという既存語彙と重複kind/optionを保持するため、既存promptの対応規則を読み替える補足promptは追加しない。

| 旧decision kind | intent.decisionの全required key | realizationの全required key | 制約・対応 |
| --- | --- | --- | --- |
| none | kind | なし、closed `{}` | 旧schemaにnone枝がある場合だけ。option_idをこのobjectへ追加しない。discussion側option_id=nullは明示値のまま。 |
| chat | kind, option_id | message | 旧messageのmin/maxLengthをコピー。本文をintentへ入れない。 |
| vote | kind, option_id, target_player_id | なし、closed `{}` | 旧offered target制約。contextful voteのtarget=nullは拒否。棄権は許可されたnone枝＋既存pre_voteのまま。 |
| ability | kind, option_id, target_player_ids | なし、closed `{}` | targetsの候補、個数、uniqueItemsをそのまま。0targetがofferedなら空配列もその既存制約どおり。 |
| co_declare | kind, option_id, claimed_role_id | comment | offered role enumと旧commentのmin/maxLengthをコピー。役職名はhardcodeしない。 |
| co_report | なし | なし | Phase6対象外を維持。context-free旧parserの対応をこの実験へ拡張せず、受信時は候補schemaで拒否。 |

schema_version/base_revision、reaction、CO判断、pre_voteはoptional updatesではない。PEER_CHATは元triggerのreaction必須、他triggerではnull。CO_OPPORTUNITYはCO判断必須、他ではnull。PRE_VOTEは再評価必須、none/棄権時のpreferred=null、候補・allow_abstainは元の枝を保持。他ではnull。これらのkind/option/claimed_role/target整合は旧validatorで再検査する。

## 3. 候補closed schemaの構築

入力は元baseline projectionのschemaとcapture。新たなgame stateや権限情報を取得しない。

1. 元schema全体をplain JSONとしてcopyし、既存 `$defs` を全て値・array順も含めて保持する。remote refは追加しない。
2. 旧 `decision.oneOf` の各枝Dを元の順で辿る。kindが上表の有効5種で、required/key集合が期待と一致することを検査する。未知形は `SCHEMA_SHAPE_CHANGED` で停止。本文keyだけをDから分離してintent.decisionとrealizationのclosed schemaを作る。本文のない枝はtype=object/properties={}/required=[]/additionalProperties=false。
3. 旧 `discussion.oneOf` からDとdecision_kindが一致し、option_idがDのoptionを許す枝Pを一意に選ぶ（noneはconst null）。ない／複数は停止。Pをintent.discussionの8fieldとupdatesの4fieldへ分割し、各propertyのschemaをそのままコピーする。matched branchのpre_vote/abstentionもそのまま移す。
4. Dごとにroot closed object（intent、realization、updates）を1枝作り、candidate rootのoneOfにする。各枝のaction option constを維持する。重複したaction signatureがあれば停止し、勝手にdeduplicateしない。元 `$defs` はcandidate rootに置き、既存absolute local refを有効にする。
5. Draft202012Validator.check_schemaで検査する。candidateは旧schemaとJSON値同一ではない。actionとdiscussionのkindが不一致な旧syntaxだけの組合せは生成候補に含まれないが、それは既存意味validatorも拒否する組合せであり、合法な旧出力を削らない。

意図する受理集合は「candidate厳密検査＋無損失adapter＋旧validator」と「旧validatorが受理する出力」の間で一対一。元schemaへ手書きのrole/target/defaultを再定義しない。schema共有化等の追加最適化は初回へ混ぜず、膨張やruntime非対応は測定前の問題として報告する。

## 4. optional updatesと無損失adapter

optionalは**変更する内容が任意**という意味。updates objectと4keyは全て必須。

| field | 更新なしの明示値 | 更新ありの制約 |
| --- | --- | --- |
| assessment_updates | [] | 旧max4、対象/score/confidence/evidence、同一対象重複禁止。 |
| claim_updates | [] | 旧max4、受信public Chat/COとspeaker整合、同一claim重複禁止。 |
| relation_updates | [] | 旧max2、異なる端点、PUBLIC_INFERENCE、public evidence、同一関係重複禁止。 |
| strategy_update | null | 旧closed scope/mode/focus/evidence。 |

配列null、strategy空object、field欠落、unused field、任意の追加keyは拒否。配列[]/strategy nullを勝手に補完しない。更新しない自由は残し、非NONEだから更新必須としない。

処理はraw保存→重複key/NaN/Infinity/構文不正を拒否する厳密JSON load→候補schema全体検査→copy上でadapter→元baseline projectionによる `parse_llm_output`。adapterは以下のfield移動だけを行う。

```text
old.decision   = copy(intent.decision) + exact realization fields
old.discussion = copy(intent.discussion) + exact updates fields
```

merge前にkeyの交差が空であることと各key集合を確認。型強制、trim、null除去、role/act/参照導出、欠落補完、sort/deduplicateによるarray値変更はしない。全候補値を一度ずつ旧形式へ移す。schema_version/option等も生成値をそのまま保持し、再計算しない。inverse mapping（旧actionから本文だけを分離、旧proposalから4更新だけを分離）をtest helperとして定義し、合法な旧出力について `decode(encode(old)) == old`、候補受理値について逆方向も検証する。原rawは変更しない。

候補schema不合格はadapter未実行、legacy_contract_pass=null。候補合格後の旧validator失敗はその別判定を保存する。両方合格しても意味品質は未判定。既存screen/evaluateはadapted JSONを元projectionで評価し、原本hash、adapted hash、候補/旧契約判定を別保存。独立Reviewerは元入力・候補raw・field mapを対象とし、adapterの成功を本文の正しさと混同しない。

## 5. grounding・strategy・atomic境界

旧validatorの保証を継承する: offered option/targets/roles、captureと投影memory双方に存在する同一EvidenceRef、current player ID、ANSWER/REBUTTALの受信speakerとaddressee、OPINION_CHANGEの実priorと新cause、claim speaker、trigger/action対応、CO/pre_vote整合、更新重複、distinct evidence上限8、proposal bytes上限等。

**参照が実在しても、本文の主張を支持するとは限らない。** 許可された履歴の取り違え、発言解釈の捏造、自由文の能力結果・役職・state矛盾、秘密漏えい、本文/act不一致は独立意味評価が必要。単純regexで合格にしない。地に足のついた反論・意見変更に必要な情報がないときは、別の支持可能な発言や合法NONEを選べる。真実のCO、任意の沈黙、戦略的な騙りまで禁止しない。

実験helperはschema構築とparse/変換だけで、send/state適用APIを持たない。全体不正なら有効なintentだけを部分受理しない。1callの途中のintentに副作用はない。offlineではstate更新0/送信0を固定する。

将来製品統合では既存capture/admission/request/terminal ownershipを維持し、adapter完了後も最新world/capture epoch/base revision/phase/deadlineを既存経路で確認してから一括受理する。parserのbase_revision一致は捕捉時との一致であり、await中に更新された最新stateへのfreshness保証ではない。新しい時点の値をadapterで上書きしてstaleを隠さない。stale/cancel/deadline/不正の場合は全proposalを破棄し、部分updateや暗黙retryを行わない。この将来統合は今回未実装・未許可。

## 6. 生成順・入力・予算

providerへ送るcandidate root各枝propertiesはintent→realization→updates。intent内はdiscussion→decision、intent.discussion内はspeech_actを先頭に置き、残り7fieldはalphabetical。これは新envelopeの生成段階の宣言であり、旧K1のspeech_act各枝内kind順は導入しない。他の既存 `$defs`/枝/required順は元のまま。

候補bodyを一度canonical化してから、上記新規wrapperのpropertiesだけを宣言順へ置き、sort_keys=falseでwire化する。messages・user canonical input・model/samplingは元baselineと完全一致を検査する。固定説明prompt/例文を足さない。candidate canonical schema/body hashはbaselineから変わるので、K1のcanonical同値条件を流用しない。元baseline body hash、messages hash、candidate schema/body hash、実wire hashを別にfreezeする。

具体的に、intentはdiscussion→decision、intent.discussionはspeech_act→base_revision→co_judgment→decision_kind→option_id→pre_vote_reassessment→reaction→schema_version。speech_act各oneOf枝内のpropertiesは元baselineのalphabeticalで、NONE枝順も元のまま。intent.decision、realization、updates内はalphabetical。この新しいfield配置以外に旧C1/C2/K1の変換関数を呼ばない。

prompt語彙の保持はschemaによる新配置との明白な衝突を減らすためで、モデルが三段の意味を理解する保証ではない。この対照で評価できるのは「説明promptを足さず、既存指示と新envelope/生成順を組み合わせたときの32case品質」。成功しても内部で意図を先に計画した証明ではなく、失敗してもIntent-first一般やモデル能力の否定にはならない。説明不足と能力不足を完全分離できない限界を報告し、同run中にprompt説明を追加しない。

実request.binをprivateへ保存し、同じimmutable bytesをapply-templateとgenerationのHTTP contentへ渡す。MockTransportで保存→送信一致を検査する。初回32件からrootの生成key順を固定ID/位置だけ記録し、順序が違う場合は `ORDER_NOT_REALIZED` として品質採用判定を保留する。余分なcallでやり直さない。完全応答の順序は内部思考や因果の証明ではない。対象runtime converterがroot oneOf内の順序を扱えるかは事前の静的確認と非LLM変換検査で確かめ、未知を既対応と宣言しない。

future実装は新 `scripts/phase6_intent_first_probe.py`（build_schema/body/wire、strict parse/adapter）とfocused test、既存comparison runnerの固定experiment `intent_first_v1` 接点だけ。旧projectionを改造してproduct parserへ新schemaを渡さず、candidate schemaと評価用baseline projectionを別に保持する。source_identityは本experimentだけ新helper/testを追加し、旧baseline/C1/C2/K1対象は変えない。既知差分pathをexact allowlistにし、全実行source/model/runtime/argvをfreezeする。

既存32case、qw9固定、context8192、slot1/同時1、max_tokens512、seed4242026、temperature0.2/top_k40/top_p0.95/min_p0.05、penalty/cache_prompt=false/thinking=false/ngl99/native templateを維持する。request60/load180/model1200（load含む）/所有cleanup各25/outer1320 REAL秒。新schemaが長くても出力上限を増やさず、length/truncationは欠測・不適合として保存する。

candidateの実rendered tokens＋512＋1<=8192、usage一致を送信前後に検査する。元projection.proxy値をcandidateの値と誤表示しない。candidate messages＋schemaのbyte/proxyと実tokenを別に記録し、製品proxy gateやbudget式を変更しない。token accounting一般化はT440へ分離。envelopeのschema膨張が既存制限で扱えない場合は停止し、根拠省略や予算拡大を即興で行わない。

## 7. focusedと将来32case判定

非LLM focused必須:

- 全32caseの候補schema検査、元projection/messages/grounding不変、wrapper以外のleaf制約コピー、wire凍結と実content一致。
- 合成の全5action＋許可/禁止none・PRE_VOTE棄権、全7act、各triggerについて両方向roundtripと旧parserの受理一致。co_report/context-freeは拒否。単一actionだけのテストで済ませない。
- extra/missing/duplicate JSON key/NaN/Infinity/誤型/unused null、chat comment・co message混用、no-text枝の本文、intent本文、updates欠落・null配列を拒否。unknown shapeや同一signature重複をprepareで拒否。
- option/target/role未提供、target個数/重複、参照visibility/actor/許可範囲違反、prior不一致、新cause欠落、trigger/reaction/CO/pre_vote不整合、更新重複/上限、全evidence上限、text/proposal bytes超過を旧validatorで拒否する対照。
- 合法NONE、明示no-change、正当CO・非開示の表現を保持。実在refなのに本文を支持しない対照はsyntax/旧validator合格があり得ると記録し、機械的なsemantic PASSを作らない。
- source/input/wire/runtime mismatch、claim再利用、private保存失敗、transport timeout、所有cleanupの既存mock regression。原raw不変、候補不合格時adapter未実行、全体不正時副作用0。

将来の実LLMは独立設計承認→実装→独立tool審査→新task/実行freeze→独立Testerの32call一回→独立意味判定。今回その実行taskは未割当。通常gameなし、baseline再生成/再採点なし、retry/repair0。T427のplan/results/annotationsを既存hashで束縛し、case/入力/出力/判定の32件対応を検査する。candidate schemaが変わるためbaseline同値はmessagesと元body復元の側で検査し、candidate全body同値を要求しない。

全32件を分母にし、未生成/構造不合格を除外しない。相対改善条件は不一致<23、HARD fail<=14、FABRICATED_EVIDENCE<=0、SECRET<=5、STATE_CONTRADICTION<=3、ABILITY_CONTRADICTION<=0をそれぞれ満たし、各UNKNOWNを増やさず、合法NONEを損なわないこと。非NONE増加や回答増で捏造等を相殺しない。32件1seedの有限結果とする。製品へ進むには別途D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0と独立reviewが必要。

provider起動前の既知blockerとして、outerのPID番号だけの所有追跡を修正・独立確認する必要がある。process objectまたはPID＋creation timeで今回の所有を照合し、非所有processを止めない。設計承認だけでこの運用gateを完了扱いしない。

## 8. 今回選ばない案

| 案 | scope/費用/null保証 | 判断 |
| --- | --- | --- |
| B 単一shape＋strict validator（旧比較C） | 1callだが全act共通fieldで出力増の可能性。kindごとの適用表、非該当nullだけの除去、必要field null拒否、旧unionへの厳密変換が必要。 | grammar非対称の軽減と検証負担を同時に変えるため今回は混ぜない。 |
| C 2call（旧比較D） | 32判断最大64call。intent検証と本文生成を別admissionにし、共有deadline・stale/cancel・attempt消費の再設計が必要。未完のintentは適用不可。 | 現32call枠外。追加費用・concurrency境界が大きく初手にしない。 |

案Aはcall数・既存validator・game authorityを維持し、field移動の可逆性をofflineで検証できるため最小候補として選ぶ。効果未確認であり、A失敗時に同runでB/Cへ切り替えない。
