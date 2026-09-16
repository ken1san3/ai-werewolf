# Phase 6 rebaseline 最小実装設計

Status: DRAFT — T291 Architect。実装には新規独立 Reviewer の `APPROVED` が必要。

## 1. 権限・目的・停止境界

本設計は D072 と `PHASE6_REDIRECTION_INSTRUCTION.md` のユーザー決定を、既存 Phase 6 実装へ
反映するための最小契約である。確定値は Phase 6 専用の 200 Unicode code points、600 UTF-8
bytes、text token 誘導 20–120、whole-response 512 である。Phase 5/Q8 の 80/96/5–30/96、
day 180 秒、vote/night 60 秒は変更しない。

実装対象は本書の5修正と、その決定論的検証だけである。T288/T290のprovenance、T284/T286の
数値再審査、T285のplatform/CI/ACL修正、T288再生、新manifest、atomicity、FFI、ACL hardening、
新frameworkは含めない。実game、provider、GPU、modelは起動しない。実装後は独立Testerとfresh
Reviewerまで実施して停止し、調整実行・受入実行・G/H全体へ自動続行しない。

既存の未commit差分は継承し、巻戻し・上書きしない。Implementerは編集直前に対象diffを再確認する。

## 2. Phase 6 出力profileの型と伝播

### 2.1 型

`ai_client/llm/types.py` の `ShortChatConfig` は Phase 5 profileそのものとして維持する。
その既定値、`Literal[5]`、`Literal[30]`、`Literal[96]`、80/96の検証、例外文を変更しない。

同じfileへ frozen dataclass `DiscussionChatConfig` を追加し、次の初期値を持つ。

- `target_min_text_tokens: int = 20`
- `target_max_text_tokens: int = 120`
- `max_text_chars: int = 200`
- `max_text_utf8_bytes: int = 600`

全fieldはboolを拒否するexact intとし、tokenは各1–512、charsは1–240、UTF-8 bytesは1–960で検証する。
さらに `target_min_text_tokens <= target_max_text_tokens`、`max_text_chars <= max_text_utf8_bytes` を要求する。
token上端512は既存 `GenerationSettings` のwhole-response安全上端を超える誘導を許さないため、chars上端240は
既存 `LLMBrainConfig.max_generated_text_chars` の実応答hard boundを超える設定を作らないため、bytes上端960は
240 Unicode scalarがUTF-8で各最大4 bytesとなる実安全上端である。初期600はユーザー指定の200全角文字
（通常各3 bytes）を収容する。これらは初期値の再選択ではなく、調整可能なbounded intの型安全域である。

whole-response上限はprovider設定であり、このtext profileへ重複保持しない。同fileに
`ChatOutputProfile: TypeAlias = ShortChatConfig | DiscussionChatConfig` を置き、次の全利用点を
`ChatOutputProfile | None` とruntime `isinstance(value, (ShortChatConfig, DiscussionChatConfig))` へ揃える。

- `LLMBrainConfig.short_chat` と `LLMBrainConfig.__post_init__`
- `PromptProjection.short_chat` と `PromptProjection.__post_init__`
- `ai_client/llm/prompt.py::_system_message` の引数
- `ai_client/llm/prompt.py::_make_projection` のkeyword引数

`project_brain_input` と `ai_client/discussion/projection.py` は上記configから同じinstanceを渡し、属性duck
typingへ逃がさない。`DiscussionChatConfig` とaliasは `ai_client/llm/__init__.py`、既存top-level
`ai_client/__init__.py` にre-exportする。`ai_client/runtime.py` のPhase 5既定
`LLMBrainConfig(short_chat=ShortChatConfig())` はbyte-for-byte変更しない。semantic response schema、
decision/proposal型、audit schemaは変更しない。

### 2.2 runnerからproviderまで

`scripts/run_phase5_local_smoke.py` に非公開定数
`PHASE6_MAX_OUTPUT_TOKENS = 512` を一つだけ置く。CLI/envに調整fieldを追加しない。

`_prepare_run` は `--phase6` のときだけ `GenerationSettings(max_output_tokens=512)`、それ以外は96を
設定する。`_client_child` は Phase 6 のときだけ `DiscussionChatConfig()`、legacyでは従来どおり
`ShortChatConfig()` を使う。これによりpromptの20–120、parserの200/600とproviderの512が同じ
Phase 6 modeから選ばれる。

T247で確認済みの伝播漏れを次の閉じたbootstrapで塞ぐ。

- `_broker_bootstrap` は Phase 6 のときだけ `phase6: true` と
  `phase6_max_output_tokens: 512` を加える。親settingsが512でなければserialization前に失敗する。
- legacy mappingには両keyを一切加えず、同じlegacy入力に対するmappingをbyte-equivalentに保つ。
- `_phase5_broker_settings` はmarkerなしならcap keyの不在を要求して96を再構築する。markerありなら
  markerがboolの真、modelがcanonical 9B、capがboolでないintの512であることを要求する。
  missing、stray、false/null、string/float/bool、511/513はbackend構築前に失敗する。
- `_broker_bootstrap` 内で再構築したbackend config fingerprintと親fingerprintを比較する。
  real-provider Phase 6ではbroker ready直後・client spawn前に
  `backend_identity.config_fingerprint` と親fingerprintを比較する。fixtureは既存synthetic identityを維持する。

temperature、timeouts、request/response byte上限、structured mode、admission protocol、request型は不変。
initialと同一lease内の最大1回repairは同じ固定backendを使う。`finish_reason == "length"` は完全JSONでも
拒否し、CHATはplayer/phaseあたり最大2 start、COは別経路のままとする。

## 3. 閉じたdiagnostic stderr

`scripts/phase6_private_review.py` の `ReviewFailure` を `category` と `code` の2つの閉じた識別子へする。
stderrは成功時無出力、失敗時ちょうど1行
`phase6_private_review_failed category=<CATEGORY> code=<CODE>` とし、path、本文、ID、hash、例外文を
出さない。想定外例外も `category=internal code=unexpected_failure` に畳み、型名を出さない。

categoryは `input|evidence|linkage|review|platform|output|internal`、codeは各raise siteに対応する次の
集合だけを許す: `checklist_invalid`, `path_invalid`, `path_missing`, `path_not_private`,
`unsupported_platform`, `json_invalid`, `schema_closed`, `manifest_missing`, `manifest_hash_mismatch`,
`artifact_missing`, `artifact_hash_mismatch`, `visibility_mismatch`, `population_missing`,
`population_exceeded`, `correlation_missing`, `correlation_duplicate`, `correlation_mismatch`,
`checklist_population_mismatch`, `legacy_quality_dimension_failed`, `aggregate_write_failed`,
`unexpected_failure`。同じcodeへまとめられる箇所はまとめ、自由文字列を生成しない。

既存aggregate schemaとdimension keysは変更しない。ただしD072のclosureでは
`coherent`、`source_relevant`、`objective_consistent` を隠れた必須条件にしない。
`human_quality_pass` は構造・相関が成立し、population>0、`privacy_safe.fail == 0`、
`non_repetitive.fail == 0`、`normalization_duplicate_count == 0` のとき真とする。
他3dimensionのcountは診断用に保存する。条件3は§7の独立Reviewer記録だけで判定する。
これによりCLI成功はprivate原文を用いる条件4/5の検査成功を表し、旧追加dimensionをclosureへ戻さない。

## 4. game / synthetic 保存先の分離

`tests/fixtures/phase6_evidence.py` の既存helperだけを拡張する。新規task-local呼出しはkeyword-onlyの
`evidence_kind: Literal["game", "synthetic"]`、`task_id: str`、`created_at_utc: datetime` を全て渡す。
`task_id` は `^[A-Z][A-Z0-9_-]{1,31}$`、時刻はtz-awareでoffset 0のUTCだけを受理し、leafを
`<task_id>-<YYYYMMDDTHHMMSSffffffZ>` とする。秒未満6桁までcaller値に含め、既存leafとの衝突時は
`FileExistsError` でfail closedし、random suffixや自動retryをしない。kind directoryはhelperがprivate
modeで一度だけ作成・検証し、作成先をbase直下の `game/` または `synthetic/` に固定する。pytest
basetemp外、plain component、reparse拒否、base逸脱拒否を維持し、既存evidenceは移動・削除しない。

task-local callerは次のとおり閉じる。`tests/test_phase6_semantic_completion.py`、
`tests/test_phase6_private_review.py`、`tests/test_phase6_evidence_retention.py` は実装packetのtask ID定数と
各fixture開始時に一度取得した `datetime.now(timezone.utc)` を渡し、全て `synthetic` とする。Phase 6
実game runnerはhelperを呼ばず、caller指定 `--output-dir` が
`logs/phase6-private-evidence/game/<task>-<utc>` のexact直下で、同じtask/UTC文法かつ未存在であることを
`_prepare_run` で検証する。Phase 5/Q8のoutput path contractは変更しない。

変更禁止の既存 `.github/workflows/ci.yml` は旧
`create_private_evidence_container(base, pytest_basetemp=...)` を呼ぶため、helperはこの引数省略形だけを
legacy compatibility overloadとして残す。この経路は従来のbase直下random leafをbyte-equivalentに作り、
新しいgame/acceptance containerやD072の保存先適合証拠とは扱わない。新規production/task-local codeは
省略形を使用禁止とし、focused testで明示形とlegacy省略形を分けて固定する。CI jobの編集・再設計はしない。
rawはpytest管理下へ作らない。

## 5. private CHAT literal oracle

`tests/test_phase6_private_review.py::_terminal` に `visibility` の明示keywordを追加し、kindからvisibilityを
導出しない。`tests/fixtures/phase6_private_review_vectors.json` に、production分類関数から独立したliteral
としてprivate CHATの期待 `AUTHORIZED_PRIVATE` を1行追加する。

actual authorityは既存pure collector `scripts/run_phase5_local_smoke.py::_phase6_semantic_population` が所有する。
同fileにpure helper `_expected_chat_terminal_visibility(generation, terminal) -> str` を追加する。このhelperは
hash検証済み `generation["prompt_json"]` のcanonical inputから、terminalの `option_id` に一致するchat
actionのopaque channel IDを取得し、同じpromptに封印されたdiscussion contextのchannel descriptorsから
exactly one同ID descriptorを引く。そのdescriptorのvisibilityだけをexpected値とし、terminalの
`authoritative_evidence.record_kind == "chat"` とvisibilityが一致しなければ既存collectorを失敗させる。
descriptor欠落/重複、option非chat、channel不一致も失敗であり、kindやtest helperからvisibilityを推論しない。
processorは同collectorを再利用するため、`ReviewFailure(category="evidence", code="visibility_mismatch")`
へ閉じて変換する。protocol、audit/terminal schema、sealed descriptor型は変更しない。

新規test名を次に固定する。

- `test_private_chat_literal_visibility_matches_sealed_descriptor`
- `test_private_chat_public_terminal_visibility_is_rejected`
- `test_private_chat_missing_population_is_rejected_separately`

positiveはmanifest→shard→population→aggregateを通す。PUBLIC誤分類negativeは
`category=evidence code=visibility_mismatch`、人口欠落negativeは
`category=evidence code=population_missing` をliteralに確認する。

oracleはopaque synthetic text/channelだけを使い、production本文を固定文oracleにしない。CO、vote、ability
の既存matrix、closed fields、privacy/authorization/linkage検査は緩めない。

## 6. global 512件の記録付き停止

上限はaccepted-text population全体に対する512であり、shardごとではない。
`scripts/run_phase5_local_smoke.py` に閉じた例外
`Phase6PopulationExceeded(accepted_text_count: int, reason: Literal["ACCEPTED_TEXT_POPULATION_EXCEEDED"])`
を置く。`_phase6_semantic_population` はpureのまま、入力accepted receiptsが513件へ到達した時点で
本文/path/recordを保持せず、count=513と固定reasonだけを持つこの例外を送出する。511/512件は従来の
tupleを返す。切詰め、sampling、上限変更はしない。

`_write_phase6_evidence` は例外を握り潰さず、accepted ledgerとmanifestを一切publishしないまま
`_run_game` へ伝える。`_run_game` は専用exceptで `semantic` を
`{"accepted_text_count": 513, "failure_reason": "ACCEPTED_TEXT_POPULATION_EXCEEDED"}` にし、既存errorsへ
固定reasonを一度だけ加える。その後は既存finally/cleanupを完走し、return rowの
`machine_semantic_pass=false` と `semantic` に上記2値を残す。`_execute` は既存summary writerだけを使い、
rowを含む `config.output_dir/summary.json` をcleanup後にatomic writeして非zero終了する。新artifact、
新manifest、collector I/Oは追加しない。

既存 `pre_vote_reassessment_count` は診断値として保持するが、D072 closure外なので
`semantic_requirements_met` の必須項から外す。同fieldは `responsive_accepted_count > 0` と既存CHAT cap
成立だけを表す。pre-vote 0だけでrunner failureやmachine semantic failureにしない。schema/field名は
変えない。

## 7. D072の5条件への唯一の対応

1. runnerの `game_end=true` とowned cleanup 0。
2. `responsive_accepted_count >= 1`。既存のauthorization/linkage検査を通った行だけ数える。
3. fresh Reviewerが全accepted text原文と対応semantic actを読み、`QUESTION→ANSWER`、
   `claim→REBUTTAL`、`OPINION_CHANGE` のいずれかを1件以上確認する。handoffへは種別と件数だけを記し、
   本文・capture/player/channelを転載しない。aggregateへ新dimensionを追加しない。
4. machine側のschema/authorization/visibility/linkage違反0に加え、全原文を見たaggregateの
   `privacy_safe.fail == 0` を両方要求する。metadataだけで0としない。
5. `non_repetitive.fail == 0` かつ `normalization_duplicate_count == 0`。

`coherent`、`source_relevant`、`objective_consistent`、pre-vote件数は保存する診断値であり、Phase 6 closure
必須条件ではない。旧 `machine_semantic_pass` や `human_quality_pass` という総合名だけで判定せず、上記の
根拠fieldとReviewer記録をfresh Reviewerが明示的に照合する。今回はこのclosureを実gameで判定しない。

## 8. 実装所有と必要な決定論的検証

一人のImplementerが次だけを編集する。

- `ai_client/llm/types.py`, `ai_client/llm/prompt.py`, `ai_client/llm/__init__.py`, `ai_client/__init__.py`
- `scripts/run_phase5_local_smoke.py`
- `scripts/phase6_private_review.py`
- `tests/fixtures/phase6_evidence.py`
- `tests/fixtures/phase6_private_review_vectors.json`
- `tests/test_phase5_short_chat.py`, `tests/test_phase5_local_smoke.py`
- `tests/test_phase6_semantic_output.py`, `tests/test_phase6_semantic_completion.py`
- `tests/test_phase6_evidence_retention.py`, `tests/test_phase6_private_review.py`
- named handoff/logs

必要テストは、(a) Phase 6 profile初期値、bounded range/min-max相関/bool拒否、全annotation/runtime validation、
legacy profile known answer、prompt/parser 200/600、
(b) Phase 6 parent→bootstrap→broker→payload/fingerprintの512とclosed malformed matrix、legacy mapping/96、
(c) length-finish、repair1、CHAT2、CO別、closed semantic schemaの既存回帰、(d) stderr全codeのclosed one-line
かつsentinel非出力、(e) game/synthetic分離とpytest外保全、(f) private CHAT literalとPUBLIC化失敗、
(g) pure collector 511/512成功と513 closed例外、既存summaryへのcount/reason、manifest前停止、cleanup、
(h) D072 aggregateでprivacy/repetitionは必須、他3dimension
単独FAILは診断に残るがclosure passを落とさない、(i) pre-vote 0単独でsemantic条件を落とさない、である。

Implementerはfocused tests、関連Phase 4/5/6 regressions、`python scripts/check_docs.py`、compile、全scope diffを
実測してhandoffを書く。続いて別sessionの独立Testerが同じacceptanceを実測し、その後Implementer/Tester/
本Architectから独立したfresh Reviewerが最終bytesと証拠を審査する。いずれもpytest/provider/model/gameを
この設計sessionでは実行しない。独立Reviewerが本設計を `APPROVED` とする前はImplementerを開始しない。
