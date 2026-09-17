# Phase 6 英語会話品質・grounding限定設計

## 1. 目的と境界

T394で確定した生成前段の不足とschema・局所検証の差を、現在のimmutable `BrainInput` だけで修正する。
新しいserver情報、秘密情報、永続state sourceは追加しない。入力は同一requestの `snapshot`、
`ability_results`、`co`、既存bound context、action options、最終投影memoryに限る。

モデル、concurrency、scale、時間予算、phase期間、ゲームルール、200 Unicode code points、
600 UTF-8 bytes、repair最大1回・同一lease、server authorization、局所validatorは変更しない。

## 2. `grounding` 投影

prompt user payloadへ `grounding` を追加する。別stateを作らず、projection開始時に受け取った単一requestから
allowlistで派生させる。snapshot/version/seq/phaseは既存capture整合検査を通過したものだけを使う。

### 2.1 必須の現在値

```json
{
  "grounding": {
    "current": {
      "day": 4,
      "phase": "vote",
      "players": [
        {"player_id": "...", "alive": false,
         "death": {"day": 3, "public_cause": "..."}}
      ],
      "alive_player_ids": ["..."],
      "vote_candidate_player_ids": ["..."]
    },
    "self_co": {"records": [], "complete": true, "omitted_count": 0},
    "ability_results": {"records": [], "complete": true, "omitted_count": 0},
    "allowed_evidence_refs": [],
    "allowed_decisions": []
  }
}
```

- `players` は `snapshot.players` の `player_id/alive/death.day/death.public_cause` のみ。内部死因を追加しない。
- `alive_player_ids` は同じsnapshotの値と `players[].alive` が一致しない場合 `PROMPT_INVALID` とする。
- `vote_candidate_player_ids` はcurrent action optionsのvote `valid_targets` の和集合。候補を推測しない。
- selfのrole/team/win condition/abilitiesは既存bound contextを正本とし、`grounding`へ重複コピーしない。

### 2.2 本人のCOと能力結果

- `self_co.records` は `request.co.records` のうち `player_id == capture.player_id` の宣言・報告だけ。
  order順を維持し、宣言はclaimed role/comment、報告はkind/target/claimed resultを既存typed fieldから投影する。
- `ability_results.records` は本人へ既に配送された `request.ability_results.records` だけ。
  `order/day/phase/event_type/target_player_id/result_id/revealed_role_id` を値の改名や推測なしで保持する。
- role名やresult値をPythonで列挙しない。受信済みopaque IDをそのまま扱う。

`ImportantEvent` のschema、永続形、state reducerは変更しない。能力結果の正本は既存
`request.ability_results` であり、groundingへ直接投影する。retentionで既に失われた結果は復元・推測しない。

固定上限は `players/alive/vote candidates=32`、`self_co.records=8`、`ability_results.records=8`、
`allowed_evidence_refs=24`、`allowed_decisions=64` とする。opaque IDは128 code points/512 UTF-8 bytes、
CO commentは200 code points/600 UTF-8 bytesを最大field長とし、途中sliceしない。typed入力が上限を超える場合は
`PROMPT_INVALID`、section全体がprompt予算へ収まらない場合はrecord単位で縮約する。

各viewの `complete` とretention情報を正本にする。優先順位は (1) current players/phase/options、
(2) 最新ability result 1件、(3) 最新self CO 1件、(4) 残りability results、(5) 残りself CO、
(6) optional cognitive state、(7) trigger以外のmemory とする。存在する最新ability result 1件は必須で、
これとrepair予約が収まらなければ `PROMPT_TOO_LARGE` とする。追加recordsは新しいorderを優先し、入らない古い
recordsを `omitted_count/omitted_through_order` へ記録する。`complete=false`、`dropped_count>0`、または
`omitted_count>0` を空集合・結果なし・COなしという事実に読み替えない。文字列の途中切断や秘密境界の拡張をしない。

## 3. 最終memoryとallowed一覧

schemaを最終memory選択前に構築する現在の順序は維持し、schemaサイズを候補選択へ循環させない。
memory確定後、次の小さい一覧をuser payloadへ入れ、system instructionで「一覧にない値を生成しない」と明示する。

- `allowed_evidence_refs`: 最終 `memory.records[].source` と完全一致する重複なしの一覧。
- `allowed_decisions`: `none/null` とcurrent optionsから導いた `{decision_kind, option_id}` の組。
- vote optionごとの `valid_targets`、CO optionごとの `claimed_role_ids` は既存 `action_context.options` を正本とする。

`allowed_evidence_refs` 自体も最終memoryから機械的に派生するため追加budget探索を要しない。memory 1 recordにつき
同じ小さなrefを1回増やす固定差を `fits()` に含めて選択する。最終materialでbyte/proxy/repair予約を再検査する。

## 4. schemaの限定強化

providerで既に使用中の `oneOf/anyOf/const/enum/$ref` だけを使う。未対応の `allOf`、`if/then`、
sibling properties付き`oneOf`は使わない。

1. 各triggerで合法なkindは `none` と単一action kindだけなので、`discussion` proposalを最大2つの完全な
   closed `oneOf` branchにする。none branchは `decision_kind=none` / `option_id=null`、action branchは
   `decision_kind=<triggerの単一kind>` / `option_id=<同kindのoffered option ID enum>` とする。optionごとには
   branchを増やさない。両branchは全required fieldを持つ完全objectとし、既存`$defs`のspeech act、assessment、
   claim、relation、strategy等を参照して重複を抑える。object合成用の新しいgrammar機能は導入しない。
   cross-objectのtop-level `decision` と `discussion` のkind/option一致、vote target一致等は
   `allowed_decisions` のpair指示と既存semantic validatorを最終権威として維持する。
2. CO opportunityの `co_judgment` は小さい完全branchとする。
   `DECLARE` branchはoffered CO option/claimed roleの組だけ、`SILENCE` と `DEFER` branchは両fieldをnullにする。
3. PRE_VOTEはoffered vote option IDをconst/enum、ranked/preferred targetをそのoptionの`valid_targets`へ限定する。
   abstain時preferred null、vote選択時preferredはdecision target一致というcross-object相関はallowed一覧＋局所検証に残す。
4. evidence refは型schemaを維持し、実値集合は `allowed_evidence_refs` に置く。全refをschemaへ複製しない。
5. relation endpoints相違、claim actor一致、speech act source解釈など動的意味契約は既存validatorを緩めず維持する。

この構造でT394のnone＋非null option、CO null不一致、unoffered pre-vote targetをgrammar段階で減らし、
未投影claim参照はpromptのallowed一覧で抑える。全triggerと最大option数でbackend grammar変換、canonical bytes、
proxy増分を測定する。8192 proxy/32768 bytesとrepair予約は据え置く。最大2 branchでも境界を超える場合は
schemaを緩めて続行せず `PROMPT_TOO_LARGE` でfail closedとし、設計へ戻す。

## 5. 英語戦略指示

Phase 6 system messageへ次の意味を短い英語で追加する。role固有名は含めない。

- Use your authorized context, current role/team/win condition/abilities, current strategy, living state, prior self CO,
  and personal ability results when deciding what is true and what to reveal.
- `context.inspect_result` and `context.medium_result` describe attributes of your effective role; they are not results
  you observed. Only `grounding.ability_results.records` may be treated as your observed ability results.
- Do not reveal private role or results merely to introduce yourself. Claim, conceal, or deceive only when it serves the
  chosen strategy and an offered action permits it. Do not repeat an introduction already present in `self_co`.
- For `PEER_CHAT`, respond to the supplied source when its content supports a real answer or rebuttal. Keep
  `speech_act` consistent with the text; do not label a non-answer as `ANSWER` or invent an opinion change.
- Use only `allowed_evidence_refs`, `allowed_decisions`, offered targets, and offered claimed roles. Leave unsupported
  updates empty. Treat omitted/incomplete input as unknown.
- Write one complete, concise utterance in your own words.

騙り、他者へのrole言及、疑い先、投票先、CO時機は固定しない。出力本文からrole文字列を一律禁止しない。

speech actの意味は次で固定する。`QUESTION` は相手へ実際に問いを発する本文、`ANSWER` は指定された質問sourceへ
内容上直接答える本文、`REBUTTAL` は指定されたclaim sourceの内容・理由・結論を否定または弱める本文、
`OPINION_CHANGE` は新しいevidenceによりprior値からcurrent値へ実際に判断が変わった本文である。`NONE` は
これらや既存`CLAIM`/`RELATION_HYPOTHESIS`のどれも本文に当てはまらない場合だけに使い、便利なdefaultにしない。
`CLAIM` はsubject/topic/stanceについて実際に主張する本文、`RELATION_HYPOTHESIS` は二者間関係を実際に述べる
本文という既存意味を維持する。質問でないsourceをANSWER、単なる不同意をREBUTTAL、結論不変を
OPINION_CHANGEへ分類しない。

## 6. 公開長文コピーguard

送信候補がchatまたはCO commentの場合、stage前に副作用なしで検査する。比較sourceは同じimmutable requestの
`history.records` と `co.declarations` に保持された本人受信済み全文のうち、既存bound-contextの
visibility変換で `PUBLIC`、actorが一意でself以外のChat/COだけとする。capture evidenceや最終memoryから
省略された受信済み公開文も比較してよい。
private channel、visibility lost、別client原本、新共有stateは参照しない。

正規化はNFKC、casefold、Unicode whitespaceの単一space化、前後space除去とする。candidateとsourceがともに
**50 code points以上かつ空白区切り8 words以上**で、正規化全文が完全一致する場合だけ
`VALUE_NOT_OFFERED` とし、既存controllerの `STAGE_FAILED` / no-send経路で終了する。substring、編集距離、
`SequenceMatcher`、高類似は今回のguardに使わない。

比較sourceは既存retention上限内を一回走査し、candidateは200chars/600bytesなので有限である。50文字未満、
8 words未満、短い同意、引用を含む新規文章、自己の過去発言、語彙だけ共有する推論、高類似だが非一致の文は
cross-player guardで拒否しない。既存の直前self完全一致guardは変更しない。retention不完全で失われたsourceへの
全game重複0は保証せず、既存B09の事後全population判定を維持する。

## 7. 完結短文

生成本文をhard slice、trim、書換えしない。既存200chars/600bytes検証を先に行う。さらに上限近傍
（190 code points以上、または570 UTF-8 bytes以上）のchat/commentだけ、末尾の空白を除いた最後が
`. ! ?` またはそれに続く閉じquote/bracketでなければ `TEXT_BOUND` とする。末尾が `, ; : - /` の場合も
上限近傍では同codeとする。短い自然な発話へ句点を強制しない。既存の1回repairへ同じprojection/schemaと
`TEXT_BOUND`を渡し、repair後も不完結なら送信しない。

## 8. 変更対象

- `ai_client/discussion/projection.py`: grounding、allowed一覧、限定schema、budget計算。
- `ai_client/llm/prompt.py`: Phase 6英語指示。
- `ai_client/llm/decision.py`: copy guardと上限近傍完結検査。既存意味validatorは維持。
- `ai_client/brain/controller.py`: 既存stage前検証経路への結果反映だけ。新state sourceは作らない。
- 既存Phase 6 focused test files。新frameworkや実LLM fixtureは作らない。

## 9. focused 10項目

1. 本人向けAbilityResultの `event_type/target/result_id/revealed_role_id` がrequestからgroundingへ一致して入り、他人向け結果やImportantEvent新detailは入らない。
2. snapshotのday/phase、alive/dead、public death causeが一致し、内部死因はなく、不整合snapshotを拒否する。
3. self COだけをorder順に投影し、complete/omitted境界を保持する。他player COをself historyへ混ぜない。
4. vote candidates、allowed decisions、allowed evidence refsがcurrent optionsと最終memoryに完全一致し、未offered値を拒否する。
5. 全triggerでproposalの最大2 closed branchesを変換し、none＋null/action＋offered optionをschemaで許容、
   none＋非null/action＋null/unoffered optionをschemaで拒否する。CO DECLARE/SILENCE/DEFER、PRE_VOTE target、
   cross-object identityは既存局所validatorでも確認し、最大option時のbytes/proxy/repair予約を測定する。
6. system instructionにrole名literalがなく、context/strategy/self CO/result grounding、speech act整合、秘密開示非強制を
   指示する。`context.inspect_result/medium_result` をrole属性、観測済み結果をgrounding recordsだけと明記する。
7. 50文字・8words未満の短い同意とself再言及、高類似の新規文を許容し、条件以上の他者PUBLIC全文完全一致だけをstage前に拒否する。
8. private・visibility lost・別client原本へguardが依存せず、retention不完全でも失われた文を推測拒否しない。
9. 189charsの無句点文を許容し、190chars/570bytes近傍の不完結末尾を`TEXT_BOUND`→同一lease repairへ送り、完結repairだけ送信する。
10. canonical prompt bytes/proxy、repair予約、8192上限、保存converter grammar、既存terminal/revision/no-sendを関連回帰で確認する。

設計を担当していない独立Reviewerが承認するまで実装しない。実装後も同Reviewerによる別の製品diff review、
focused/regression/check_docsが完了するまで短いquality smokeを起動しない。長時間runと時間拡張は対象外である。

## Mainによる閾値の具体化

ユーザーが示したguard自己紹介コピー例は58chars/12wordsのため、完全一致の最小文字数を50とする。
8words条件、短い同意除外、PUBLIC/他者限定、全文一致のみという設計は維持する。独立承認前の明確化。
