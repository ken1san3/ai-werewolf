# T510 段A: ローカル段階発話 probe 詳細設計

Status: APPROVED

対象は `experiment/speech-act-kind-first-20260919` の test-only L1＋L3。外部DRAFTの段Aを本artifactで置換する。独立Reviewerの承認前に実装・provider実測を開始しない。現行 `projection.py` / `decision.py` の契約に合わせる。L2・protocol v1.2・API・製品コード・製品model設定・通常game・Master Runは対象外。本文はPのLLM出力だけで作り、hostは主観stateや証拠refを推測しない。

## 1. 境界と保存する契約

1判断につき T（計画）→必要時だけ P（本文）→test-only 組立て→既存 `parse_llm_output` と既存text guard の順。`scripts/phase6_two_call_probe.py` のP2は既にTが「本文以外のlegacy完全出力」、Pが1 fieldである。今回はTの主観更新責務を明示的な `NO_CHANGE` に縮小し、P入力を許可リスト投影に変える。P2の strict JSON、lossless assembly、decision branch 抽出、最終validator 呼出しは、下記の契約を満たす範囲で再利用できる。P2の旧実験を再実行しない。

入力の正本は同一 `PromptProjection` の `canonical_input`、`decision_schema`、`discussion_capture`。`capture`/world/action の revision とhashを固定し、TとPの間に再取得・再投影を挟まない。`decision_schema` の offered branch、`grounding.allowed_decisions`、`_trigger_options`、`_no_decision_allowed` と一致させる。結果は実配信せず、製品transaction・server・AI clientの挙動を変更しない。

## 2. Tの入力と計画契約

Tは現行canonical inputと既存system指示を受ける。追加のclosed catalogは次の形とし、値は全て同じimmutable canonical input内のJSON Pointerから解決する。目録はsourceの権威を拡張しない。

```json
{"schema_version":"t510.catalog.v1","evidence":[{"id":"e000","pointer":"/memory/records/0/source","ref":{"record_kind":"CHAT","order":1,"visibility":"PUBLIC"},"actor_player_ids":["p1"],"channel_id":"public","day":1,"phase":"DAY"}],"public_facts":[{"id":"p000","pointer":"/grounding/current/day","actor_player_id":null,"visibility":"PUBLIC"}],"owner_ability":[{"id":"a000","pointer":"/grounding/ability_results/records/0","actor_player_id":"<owner>","visibility":"AUTHORIZED_PRIVATE"}]}
```

上の値はshape例であり、実値・enum・channelを固定するfixtureではない。`evidence` はcanonical `/memory/records` のindex昇順、最大24件。各entryの`pointer/ref/actor_player_ids/channel_id/day/phase`はそのrecordの同名field（refだけ`source`）と完全一致させ、`capture.evidence` の同一ref・actor・visibilityとも一致させる。`id=e{index:03}`。`reply` 候補はこの集合のうち、起点と同日・同channelの直近他者発言最大6件（起点を必ず含む）で、順はorder降順・同値ならrecord_kind昇順。本文解析による宛先推定やL2の構造宛先は作らない。起点が必要なのに目録から落ちたら失敗。

`public_facts` は次のpathだけを順に列挙し、nullは飛ばす: `/grounding/current/day`、`phase`、`players/<index>/alive`、`players/<index>/death/day`、`.../public_cause`、`alive_player_ids/<index>`、`vote_candidate_player_ids/<index>`、`/grounding/self_co/records/<index>`。player/array/recordはcanonical index昇順、上限256、IDは列挙順`p000`から、visibilityはPUBLIC。player下のfactには同じplayerの`player_id`をactor欄に束ね、self CO recordにはowner ID、日/phase/ID配列にはnullを束ねる。`owner_ability` は `/grounding/ability_results/records/<index>` に存在する本人に供給されたrecordだけをcanonical index昇順、最大8件、ID `a000`から列挙し、actorはowner ID、visibilityはAUTHORIZED_PRIVATE。能力recordの許可keyは`order/day/phase/event_type/target_player_id/result_id/revealed_role_id`だけで、record全体を選択単位とする。owner IDはobjectの`projection.discussion_capture.player_id`とcanonical `/context/player_id` の一致で確定する（canonical `/capture` に`player_id`は無い）。raw `role_id/team`、仲間identity、任意private memoryはcatalogに入れない。公開factに `EvidenceRef` を合成しない。目録にはpointerとbinding metadataを置き、値を二重コピーしない。解決不能・異なる値/actor/visibility・重複ID/ref・上限超過はcase失敗。GB1のpointer検査は再利用可だが能力結果をpublicへ流用しない。

Tの出力は現行legacyの `{decision, discussion}` と同じ閉じた構造から、`decision.message` / `decision.comment` および `discussion.assessment_updates` / `claim_updates` / `relation_updates` / `strategy_update` の**4項目だけ**を除き、トップレベルに必須 `updates_mode: "NO_CHANGE"`、`public_fact_ids`、`disclose_fact_ids` を加えた計画JSONとする。`public_fact_ids` は上記`p` IDから最大2件、`disclose_fact_ids` は上記`a` IDから最大1件。両者はrequired、string enum、uniqueItems、空配列可、余剰key不可。非本文actionでは両方空配列とする。Tが開示意思を示す唯一の場所は`disclose_fact_ids`であり、speech actの`evidence`からhostが開示意図を推定しない。4更新項目は `NO_CHANGE` 以外を提供しない。schemaを現行 `projection.decision_schema` から機械的に導出し、削除対象の存在・required・branch形状を検証する。その他のlegacy field、const、enum、長さ、array制約、`additionalProperties:false` は緩めない。`discussion.schema_version/base_revision/decision_kind/option_id`、speech act全分岐、reactionの`score/reason`、CO判断、PRE_VOTE再評価、全decision actionはT自身が出力する。固定 `reaction.trigger` もcanonical constに厳密一致するT出力とし、hostが欠落fieldを埋めない。`NONE` speech act、`none` action、`NO_CHANGE` は別概念であり連動させない。

動的catalog由来の各ID配列のschemaは件数`n=0`なら必ず `{"type":"array","minItems":0,"maxItems":0,"uniqueItems":true,"items":{"type":"string"}}` とする。`n>0`なら `items:{"type":"string","enum":[実IDをcatalog順で列挙]}`、`minItems:0`、`maxItems:min(n, 上記field上限)`、`uniqueItems:true`。0件でもfieldをrequiredから外さず、空enum・sentinel・偽IDを作らない。`disclose_fact_ids`の0/1/8件、`public_fact_ids`の0件、compact/prettyと公式provider grammar converterの受理をfocused testで確認する。

Tは `topic`、stance、subject/addressee、証拠refを既存schemaに沿って選ぶ。`ANSWER/REBUTTAL` の `in_reply_to` は候補の実refかつ他者actorに一致、`QUESTION` のsourceと宛先、`OPINION_CHANGE` の `dimension/prior/current/causes` は現行stateと新しい根拠に合致させる。`RELATION_HYPOTHESIS` も現行schemaにあるため残す。主観更新を止めてもspeech act上の意見変化を自動削除・自動生成しない。`CO_OPPORTUNITY` の `co_judgment`、`PRE_VOTE` の `pre_vote_reassessment`、`ABILITY` の target選択は現行optionと一致させる。`INITIAL_CHAT/PEER_CHAT/PRE_VOTE/CO_OPPORTUNITY/ABILITY` の各trigger、`none/chat/vote/co_declare/ability` の全actionを表現する。offered option以外は不可。合法 `none` は現行 `_no_decision_allowed` の条件下だけで、PRE_VOTEはabstain許可optionがなければ不可。合法 `none` でも該当triggerに必要なreaction/CO判断/投票再評価は省かない。

各refは目録にある**完全一致の既存ref**だけとし、selectionと解決後値を一致検査する。refのrecord_kindだけ、orderだけ、あるいは本文による人物推定は不可。`decision.py` の projected/captured一致、actor、同channel、prior、option、TEXT_BOUNDの意味検査を最終的に必ず通す。目録は選択肢の提示であり、既存validatorの代替ではない。

## 3. T検査・P起動・lossless assembly

strict parserは単一JSON objectのみ受け、duplicate key、NaN/Infinity、非有限値、非plain値、余剰keyを拒否する。T schema検査後、検査専用のdeep copyに限りトップレベルの `updates_mode/public_fact_ids/disclose_fact_ids` を除き、4更新項目を `[]/[]/[]/null`、chatの`message`またはco_declareの`comment`を検査用短文で戻し、`parse_llm_output` に渡す。検査用文字列を返却計画・保存結果・P入力へ流さない。挿入した値と削除した値の位置、schema導出前後の差分を検査する。Tから欠けた他fieldをhostが補わない。検査が通ったT計画を保持し、3つのprobe専用top-level field以外のT選択を変えない。

Tが合法な非本文action（`none/vote/ability`）を選んだ場合Pは起動しない。本文action（`chat/co_declare`）だけPを呼ぶ。Pは選択branchの本文field一つだけを返す。chatは `{"message":string}`、co_declareは `{"comment":string}` のclosed schemaとし、元branchの該当fieldの `minLength/maxLength/type` を一字も緩めず転写する。P本文を旧decisionの同名fieldへ一対一で挿入し、4更新項目を上記の空値へ戻し、probe専用top-level 3 fieldを除く。組立て後の全fieldはT由来・P由来・定数空値のいずれかに追跡でき、再serializationとstrict再parseでroundtrip一致を確認する。既存完全schemaの検査後、同じ元 `PromptProjection` で `parse_llm_output` を通す（内部でkind別 `_validate_generated_text` を実行）。その直後、fixture元の同一 `BrainInput` と返ったdecisionで `controller._invalid_or_repeated_self_text` を呼び、trueを固定 `TEXT_GUARD_REJECT` にする。元 `BrainInput` を保持できないならguardを省略せずcase失敗。`max_proposal_utf8_bytes`、生成文字数・UTF-8 bytes、wire bytes、context上限を超えれば不受理。本文の補修・切詰め・host作文はしない。

Tの不適合は `T_INVALID`、Pの不適合は `P_INVALID`、text guard、length、timeout、transport、stage別K枯渇、`NOT_RUN` をclosed attempt statusとterminal row statusで別保存し、失敗rowのfinal legacy outputは無し。`none` を成功として数えるのはTが合法に選び完全な組立て結果がvalidatorを通った場合だけ。拒否された先行attemptは後続受理時も消さない。96 rowを先に確保し、未実施も分母から消さない。比較専用viewは原status・全attempt診断を保持してterminal T枯渇→`CHOICE_INVALID`、terminal P枯渇→`FILTER_EXHAUSTED`、個別T不適合→`CHOICE_INVALID`、個別P不適合→`OUTPUT_INVALID`、stage別transport/timeout→`CHOICE_ERROR/OUTPUT_ERROR`、その他error→`ERROR` と写す。後続attemptが実際に受理されたrowだけ `ACCEPTED`。status写像でfinal hash・本文注釈・UNKNOWNを生成/補完しない。

## 4. Pの許可リスト投影とsystem

P bodyを既存baseline bodyからcopyして旧user全文やlocked raw planを追加する実装は禁止。新しいsystem文字列を独立作成し、現行 `_SYSTEM_MESSAGE`・`_DISCUSSION_INSTRUCTION` をPに連結しない。指示は「選択済み計画に沿う」「返信対象があればその内容に応答する」「短い1〜2文の英語」の3点に限定し、例文・role別指示・秘密を隠せという禁止語指示を置かない。response schemaは選択kindの本文1 field。Tは現行systemを使い、system分離も測定要因として記録する。

Pのuser入力は新規objectへ明示的に積み上げ、次表のfieldだけを許す。canonicalに無い値は省き、必要な制御値が欠けるならcase失敗。各値に内部provenance（path、owner visibility、channel、lane）を結び、公開safe evidenceには値を出さない。

| P field | 厳密な出典・写像 |
|---|---|
| `self_player_id`, `current_players`, `day`, `phase`, `vote_candidates` | `projection.discussion_capture.player_id` と `/context/player_id` の一致、および `grounding.current` の公開fieldだけ。display_nameは現行canonicalに存在する場合だけ |
| `public_co`, `public_chat` | `/grounding/self_co/records` の公開済みrecord、対象channelで共通知識と証明できる `/memory/records` の直近最大6件。返信refは6件内に優先確保し、他を古い順に落とす。本文は既存`text_excerpt`と`text_truncated`だけ |
| `action_kind`, `option_id`, `channel`, `claimed_role_id` | Tの`decision`から本文actionに必要なfieldだけ。channelは同じoption_idのoffered chat handleから厳密に解決。`claimed_role_id`はCOをTが選択した場合だけで、真のroleではない |
| `speech_act` | Tのkind別allowlist: `NONE`はkindのみ、`CLAIM`はsubject/topic/stance、`QUESTION`はaddressee/subject/topic/source、`ANSWER/REBUTTAL`はaddressee/in_reply_to/topic/stance、`OPINION_CHANGE`はsubject/dimension/prior/current、`RELATION_HYPOTHESIS`はsource_player_id/target_player_id/relation/confidence。evidence/source/causes/in_reply_toは本文全体でなく公開可能な解決済みrefと必要なexcerptだけ |
| `reaction` | PEER_CHATに限るTの`reason/score`と公開可能なtrigger excerpt。triggerのraw private ref objectは渡さない |
| `public_facts`, `intentional_disclosures` | Tの `public_fact_ids` と `disclose_fact_ids` を目録pointerで解決した値だけ。順と上限はT配列のまま。選択しないfactは渡さない |

`co_judgment`のうち`decision/selected_option_id/claimed_role_id`はCO本文でTが選んだ場合のみPへ渡す。`pre_vote_reassessment`、vote/ability target、主観更新は本文actionのPへは渡さない。planの完全object、proposal、stateは渡さない。

### 4.1 送信bodyの確定形

固定instructionのUTF-8 literalは次のとおり。改行はLF 1 byte、末尾改行なし。文言変更は設計revisionと独立再reviewを要する。

```text
T_CONTROL = Return a plan only. Do not write the message or comment text. Select every required control value from the offered schema and catalog. Set updates_mode to NO_CHANGE. Select public_fact_ids and disclose_fact_ids explicitly; an evidence reference alone does not select disclosure.
P_SYSTEM = Follow the selected action and speech act. If a reply excerpt is provided, respond to it. Write one or two short English sentences in the single text field required by the response schema.
```

Tの`messages`は既存baseline bodyの`[system,user]`のcontent bytesを保持し、3番目に `{"role":"user","content":T_CONTROL + "\n" + canonical_json_bytes(catalog).decode("utf-8")}` を追加する。元userのcanonical inputを改変せず、同じprojectionからcatalogを決定的に作る。Pの`messages`は新規の`[system,user]`のみで、system contentは上記`P_SYSTEM`そのもの、user contentは下記P objectの`canonical_json_bytes(...).decode("utf-8")`。`canonical_json_bytes`のUTF-8・辞書key昇順・空白なし・非ASCII非escape・非有限値拒否をT catalogとP objectに共通適用する。bodyは既存`wire_bytes`で確定し、`messages`/response schema/body/wireのSHA-256をfreezeする。native witnessと実generationは同一wire SHAのbytesを使い、差があれば送信前に失敗する。

P user objectのtop-level key集合は正確に `schema_version,self_player_id,current,public_co,public_chat,plan,selected_evidence,public_facts,intentional_disclosures`。`schema_version="t510.p-input.v1"`。`current`は正確に `day,phase,players,alive_player_ids,vote_candidate_player_ids`、各playerは `player_id,alive,death`、deathはnullまたは`{day,public_cause}`（元がnullならnull）。`public_co`はcanonical self COのrecordを順序維持して閉じたvariantでcopyする: declarationは`order/day/phase/record_kind/claimed_role_id/comment`、reportは`order/day/phase/record_kind/kind/target_player_id/claimed_result`。`public_chat`は最大6件で各要素が`{ref,actor_player_ids,channel_id,day,phase,text_excerpt,text_truncated}`、refは元recordのexact source、excerptは元のnullまたは文字列。各配列はcanonical記録のorder昇順（選択6件だけ抽出後）とする。

`plan`は正確に`{action_kind,option_id,channel,claimed_role_id,co_judgment,speech_act,reaction}`。channelはchat optionの値、それ以外はnull。claimed_role_idとco_judgmentはCO以外null。`speech_act`は§4表のkind別scalar fieldだけを元Tからcopyし、ref fieldを含めない。`reaction`はPEER_CHATなら`{reason,score}`、他はnull。`selected_evidence`はTのspeech act `source,in_reply_to,evidence[],causes[]` とreaction triggerをこの順に辿り、同一refを初出で一度だけ置く。各要素は`{use,ref,actor_player_ids,channel_id,text_excerpt,text_truncated}`で、`use`は上記field名、本文はcanonical recordの既存excerptだけ。A/C laneで許可できないrefはこの配列へ入れず、private payloadをrefだけから復元しない。`public_facts`/`intentional_disclosures`はTのID配列順に `{id,pointer,value}` を並べ、値はcatalog pointerの解決結果だけ。必要な制御値がない場合は失敗する。

全top-level fieldと上記object fieldはrequired、未知key不可。nullは指定した位置だけ保持し、空配列は`[]`、条件外branchのfieldはvariant規則に従い省く。actor等のmetadataを値から逆推定せずcatalog bindingと照合する。P objectへraw T plan、legacy proposal、canonical root objectを追加しない。各fieldのprovenanceは内部の同型path mapに保持し、P wireや公開safe証拠へは入れない。

Pのsourceは3 laneに分類する。(A) public channelで全員が知ってよい公開事実・同channel履歴、またはprivate channelでsealedな参加者・権限情報から全recipientの共通知識と証明したambient情報。(B) Tが `disclose_fact_ids` で選んだ、owner本人に供給されたauthoritative ability recordのintentional disclosure。これは同じclient内部でPに渡す意図的な初回報告であり、recipientが既に知ることを要件にしない。現行製品validatorが受理し得る本人の能力結果の公開発話を新規ルールで禁止しない。(C) private channelで全recipientの権限intersectionが証明された同channel発言等。Bで許すのは閉じた`owner_ability`目録だけで、秘密を自動送信することでも、Tの未選択事実を流すことでもない。recipient・visibility・権限を証明できないA/C、公開/私的値が混在するobject、Tが選択しても目録外のBはfail-closed。Pへ必要な情報が無ければcase失敗。生の`context.role_id/team/count_as/inspect_result/medium_result/attack_result/knows_teammates/authorized_known_player_ids/abilities/win_conditions`、仲間identity、未選択のprivate facts、raw T計画、raw canonical `context/state/memory`全体は渡さない。語彙禁止regexには依存しない。provenance edgeの構造検査で禁止source→Pが0、許可private source→PはBだけを通ることを検証する。これはraw fieldの非露出であって意味上の漏洩不可能性ではない。独立HARD評価は意図的な能力報告と未選択・禁止秘密の開示を分け、後者の安全PASSは0/96のみ。UNKNOWN・未生成を0へ補完しない。

## 5. 予算、offline gate、有限provider run

samplingはT506承認設定のQwen3.5-9B canonical、context 8192、`max_tokens`はT **384** / P **128** に固定し、1判断あたり合計512を超えない。途中配分変更・借用は0。全32 fixtureと追加ABILITY/私的channel fixtureで、各offered branchにつきcompact/prettyの有限な合法T/P出力witness、schema、text/UTF-8 bytesを事前測定する。これはschemaが許す全ての長文・全array組合せの普遍的収容証明ではない。元のT/P request bodyをそれぞれ既存native `/apply-template`＋`/tokenize` 経路へ通し、送信直前のexact rendered wireで `prompt_tokens_actual + max_tokens + 1 <= 8192`、witness出力token数 `<= max_tokens` を事前確認する。実call後にはprovider usageとcompletion予算の一致を別に検査する。実出力が上限に達して不完全ならlength failureとし補修しない。offline JSON token近似をnative rendered PASSへ読み替えない。384/128でwitnessが成立しない場合はrunせず、別design revisionと独立再reviewを要する。

focused testは全trigger/action、合法NONEと不合法NONE、全speech act（特にOPINION_CHANGE prior/current）、reaction.score、CO/PRE_VOTE、目録pointer/ref/actor/visibility、explicit disclosureの選択・拒否・除去、3 laneのprovenance、公開/私的channelの権限intersection、Pの禁止source edge不在、T/P stage分離、P skip、assembly lossless、strict JSONのduplicate/nonfinite/余剰/丸め、文字数/UTF-8 bytes/提案bytes/context境界を覆う。既存P2 adapter・projection・decisionの関連回帰と `python scripts/check_docs.py`、全diff検査を実施する。32 synthetic case全ての実際の選択channelでsource→P edgeを検査し、公開channel/ABILITY/private channelは追加fixtureでも測る。offlineでprivacy/authority・schema・witnessに失敗すれば実測しない。独立Reviewerが本設計を、実装後の別tool reviewが差分を承認する。L2はこのgateに含めない。

provider実測は新candidateだけの32 case×base seed `4242027/4242028/4242029`＝96 case。`K_T=3`、本文actionだけ`K_P=3`を各stageの**総attempt数**とし、成功時早期終了、最大6 call/row・192 call/seed・576 call/run。case indexは固定T506 case順の0〜31、stage indexはT=0/P=1、attempt indexは0〜2。各callの派生seedを `base_seed + 1009*(6*case_index + 3*stage_index + attempt_index + 1)` と事前固定する。stage内のbodyは同一でseedだけを変え、同じbody＋同じseedを二度送らない。transport retry 0、別repair 0、fallback 0、response内容による動的prompt変更なし。段別K使い切りは不受理を保存する。旧T506の保存baseline/GB1とP2は再生成・再採点しない。

送信直前のcall消費は既存 `scripts/phase6_recovery_runner.py::execute_call/reserve` と同等とする。単一lease下で、native gateとprivate request/renderedの保存が成功した後、`out/calls/<run>-<seed>-<case>-<stage>-<attempt>.json` をexclusive createし、`run identity, case_id, base_seed, stage, attempt, derived_seed, exact wire_sha256, started_at_utc`を書いてfsyncする。このdurable markerの作成時点でcallを1消費し、private consumed markerも送信前にexclusive/fsyncする。marker数でrow<=6、seed<=192、run<=576を毎回照合し、同一run/case/stage/attempt、同一body+seed、同一wire SHAの再送を拒否する。marker後にcrash・response不明・private consumed保存失敗なら消費を巻き戻さず、再送しない。native gateまたはprivate保存が**marker前**に失敗した時だけcall 0。中断後の暗黙resumeはせず、未処理rowはNOT_RUNで32分母に残す。terminal row status・attempt配列・durable marker数が一致しなければintegrity false。新たなschedulerや回復runtimeは作らない。

program REAL期限はfreezeしたplan作成時から21600秒。各seed blockはload込みで開始から1200秒、所有processのouter監督は1320秒（残余program期限が短ければその時点まで）。request 60秒、load上限180秒を固定。次callに60秒残らなければ新規callを行わず固定deadline failureと後続 `NOT_RUN` を含む32行を保存する。block開始前に不足なら当該blockはcall 0とする。期限到達後の補完・延長・暗黙再開はしない。開始前にsource/config/model/runtime/fixture/design/reviewのhash、所有process handle、GPU状態、private保存先、同一run未実施、freezeを確認する。hostで起動したprocessだけを所有handleで停止・回収し、旧processを勝手に継承しない。private raw response/promptは既存private evidence契約に保存し、公開safe evidenceにはhash、status、call数、時間、段別失敗code、分母だけを出す。latencyは(a)実provider call単位、(b)T初attemptからterminal P/失敗までのrow wall、(c)32 rowを含むseed block総時間に分ける。T506 baseline p95=5.95秒との探索比較は各rowの全generation call latency**合計**のp95で行い、call単位p95を混ぜない。欠測rowがあれば性能判定はUNKNOWN。所有権不明・freeze不一致・漏洩可能な保存先・事前gate未了ならblockを開始しない。

## 6. 評価と解釈

96行の全分母と固定質問18 case×3＝54行を保持し、未実施・構造拒否・HARD/SEMANTIC/STYLEの `UNKNOWN` を区別する。構造受理率と本文意味品質を混ぜず、独立Reviewerが新candidateの意味注釈を付ける。比較元は保存済み `Docs/ai/handoffs/tasks/T506_SAFE_RESULTS.json` SHA-256 `b765b77776f7a968ec0532d4aa7c90a9e4ef42042e5a1352e134548a0ff1411e` に束縛する。主要比較はそのQwen baseline（構造93/96、機械的HARD失敗27、SEMANTIC本文PASS50、semantic outcome48、STYLE59、質問29/54）、補助比較はGB1（構造92、機械的HARD失敗10、SEMANTIC本文PASS45、semantic outcome45、STYLE55、質問16/54、状態矛盾2）。旧保存値を再採点しない。

paired比較の旧row原本は `logs/t506-quality-recovery/program-v1/qw9-baseline-4242027`、`...-4242028`、`...-4242029` の各`result.json`と`annotations.json`だけを読む。6 fileそれぞれのSHA-256を上記SAFE_RESULTSの`groups.qw9-baseline.bindings`の同名keyと照合し、欠落・不一致なら比較はINCONCLUSIVE、別原本へのfallbackなし。response/raw本文は再読・再採点しない。新candidateもT506と同じく、構造拒否でも安全かつ一意に取得できた本文はcontent-only注釈として残し、取得不能・曖昧・未生成だけUNKNOWNにする。structural outcomeは常に0のまま。現行`phase6_recovery_statistics.compare`はlowerだけなので、新test-only統計adapterで同一case-cluster resample列から第5000/95000値を計算し、既存lower結果との一致をfixtureで確認する。既存関数を未変更のままupper判定が可能と扱わない。

主指標 `semantic_outcome` はstrict structural受理かつSEMANTIC本文PASSだけを1、他を0とし、内容UNKNOWNを別fieldで保持する。`phase6_recovery_statistics.py` のcase index昇順で32 caseをcluster、case内3 seed率差を作る。質問だけ固定18 caseのclusterであり54独立標本と扱わない。paired bootstrapはseed 20260924、100000 replicate、marginはsemantic `-2/32`、質問`-1/18`、STYLE`-2/32`、HARD PASS`-1/32`。各同一resample列を昇順に並べ、片側95%下限を第5000値、片側95%上限を第95000値（1始まり）と固定する。`lower_95 >= margin` なら非劣性、`upper_95 < margin` なら劣性、他は `INCONCLUSIVE`。欠測・UNKNOWN・比較環境不一致なら境界を表示しても結論はINCONCLUSIVE。観測後にmargin/seed/replicateを変えない。

質問回答、捏造、秘密開示、状態矛盾、HARD fail、SEMANTIC、STYLE、act/text一致、合法NONE、段別latency/callを別表で示す。構造rejectを含む機械的HARD failureと、受理本文だけのcontent-only HARD注釈を別に出し、非NONE率だけを成功基準にしない。旧baseline/GB1の数値は保存済みT506 SAFE_RESULTSの値だけを照合して引用し、再採点しない。探索上の相対改善と製品安全gateを分離する。完全な32×3、権限/秘密/規則のHARD、UNKNOWNなし、本文整合、将来の実game検証を経ない限り製品統合を正当化しない。特にL1＋L3の束、Tの `NO_CHANGE`、Pのsystem短縮、入力許可リスト、候補目録、予算配分、2 call化が同時に変わるため、単一要因の因果効果を主張しない。能力・長期信念更新・9人1slot待ち行列は今回の人工単発suiteでは受け入れられず、段A結果から別scopeで判断する。
