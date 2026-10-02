# 再構築マニフェスト（何を残し、何を消すか）

作成: 2026-10-02 / 作成者: Claude（停滞分析セッション）
基準: `main` = `5a5635ff52d5f38cfcb08f80ddce69d18a290259`（2026-09-19）
全ファイルの機械可読な一覧: `rebuild_manifest_main_5a5635f.tsv`（path / disposition / lines / reason）
生成スクリプト: `make_manifest.py`（同じ規則で再生成・検証できる）

## 0. 前提

- 新しい作業ブランチは `main` から作る。ゲーム本体・サーバ・プロトコル・役職データは、mainとexperimentブランチで**完全に同一**（`git diff main experiment -- server protocol content` が空）。
- experimentブランチの102コミット（9/19〜10/2）は**新ブランチへ持ち込まない**。タグで保存する（§6）。
- 「消す」は新ブランチの作業ツリーから外すという意味。Gitの履歴とタグには残り、いつでも復元できる（§7）。
- 未追跡のローカルファイル（`C:\AIwolf\logs` など）はGitの対象外。**このマニフェストでは消さない**（§6）。

## 1. 集計

| 区分 | ファイル数 | 行数 | 意味 |
|---|---:|---:|---|
| KEEP（そのまま残す） | 120 | 17,798 | 変更しない |
| EDIT（残して書き換える） | 6 | 673 | §3の内容に書き換える |
| DELETE（消す） | 1342 | 229,375 | 新ブランチから `git rm`。タグに保存 |
| 合計 | 1468 | 247,846 | mainの追跡ファイル全部 |

**検証済み**: mainを書き出し、DELETEを適用したツリーで、残したテストを実行 → `204 passed, 661 subtests passed in 28.52s`（Windows / Python 3.13.3）。
残したファイルから、消したパッケージ（`ai_client` / `scripts`）へのimportは0件。

## 2. 残すもの（KEEP 120件）

### 2.1 ゲームサーバ（26件）
- `server/__init__.py`
- `server/aiwolf_core/__init__.py`
- `server/aiwolf_core/action_constraints.py`
- `server/aiwolf_core/actions.py`
- `server/aiwolf_core/available_actions.py`
- `server/aiwolf_core/capabilities.py`
- `server/aiwolf_core/clock.py`
- `server/aiwolf_core/content.py`
- `server/aiwolf_core/death.py`
- `server/aiwolf_core/events.py`
- `server/aiwolf_core/game.py`
- `server/aiwolf_core/interactions.py`
- `server/aiwolf_core/models.py`
- `server/aiwolf_core/phase.py`
- `server/aiwolf_core/rejections.py`
- `server/aiwolf_core/state.py`
- `server/aiwolf_core/sudden_death.py`
- `server/aiwolf_core/targets.py`
- `server/aiwolf_core/views.py`
- `server/aiwolf_core/voting.py`
- `server/aiwolf_core/wins.py`
- `server/network/__init__.py`
- `server/network/delivery.py`
- `server/network/protocol.py`
- `server/network/server.py`
- `server/network/session.py`

### 2.2 プロトコル（2件）
- `protocol/aiwolf-v1.1.schema.json`
- `protocol/aiwolf-v1.schema.json`

### 2.3 役職・プリセットのデータ（22件）
- `content/chat_channels.yaml`
- `content/death_causes.yaml`
- `content/effects.yaml`
- `content/passives.yaml`
- `content/presets/standard_9.yaml`
- `content/restriction_types.yaml`
- `content/roles/baker.yaml`
- `content/roles/fanatic.yaml`
- `content/roles/fox.yaml`
- `content/roles/greedy_werewolf.yaml`
- `content/roles/guard.yaml`
- `content/roles/madman.yaml`
- `content/roles/medium.yaml`
- `content/roles/nekomata.yaml`
- `content/roles/seer.yaml`
- `content/roles/villager.yaml`
- `content/roles/werewolf.yaml`
- `content/roles/whispering_madman.yaml`
- `content/roles/wise_werewolf.yaml`
- `content/selectors.yaml`
- `content/teams.yaml`
- `content/timings.yaml`

### 2.4 テスト（18件）— サーバ・プロトコル・ネットワークの検証
- `tests/__init__.py`
- `tests/fixtures/network_dummy_client.py`
- `tests/test_action_resolver.py`
- `tests/test_available_actions.py`
- `tests/test_content_models.py`
- `tests/test_game_state.py`
- `tests/test_network_review_regressions.py`
- `tests/test_network_sessions.py`
- `tests/test_phase1_completion.py`
- `tests/test_phase2_completion.py`
- `tests/test_phase_manager.py`
- `tests/test_player_interactions.py`
- `tests/test_protocol_schema.py`
- `tests/test_rejections.py`
- `tests/test_runtime_capabilities.py`
- `tests/test_state_delivery.py`
- `tests/test_voting.py`
- `tests/test_win_evaluator.py`

### 2.5 仕様書（2件）
- `Docs/ai/spec/DESIGN.md`
- `Docs/ai/spec/JUDGMENT_REFERENCE.md`

### 2.6 ゲームルールの決定記録（48件）— DESIGN.md が理由として参照している
- `Docs/ai/decisions/D001_SERVER_AUTHORITATIVE.md`
- `Docs/ai/decisions/D002_ROLE_ABILITY_EFFECT_MODEL.md`
- `Docs/ai/decisions/D003_ROLE_ATTRIBUTE_MODEL.md`
- `Docs/ai/decisions/D004_REFERENCE_IMPLEMENTATION.md`
- `Docs/ai/decisions/D005_WIN_CONDITIONS.md`
- `Docs/ai/decisions/D006_RULE_OPTIONS.md`
- `Docs/ai/decisions/D007_CO_AS_SYSTEM_ACTION.md`
- `Docs/ai/decisions/D008_ROLE_MODIFIER.md`
- `Docs/ai/decisions/D009_INITIAL_ROLE_SET.md`
- `Docs/ai/decisions/D010_WISE_WEREWOLF_INFO.md`
- `Docs/ai/decisions/D011_NEKOMATA_RETALIATION.md`
- `Docs/ai/decisions/D012_DEATH_CAUSE.md`
- `Docs/ai/decisions/D013_LOGGING_AND_REPLAY.md`
- `Docs/ai/decisions/D014_DRAW.md`
- `Docs/ai/decisions/D015_PHASE_MACHINE.md`
- `Docs/ai/decisions/D016_NIGHT_RESOLUTION_AND_NOTIFICATION.md`
- `Docs/ai/decisions/D017_ACTION_STATE_AND_PUSH.md`
- `Docs/ai/decisions/D018_FIRST_NIGHT_SEER_GLOBAL_RULE.md`
- `Docs/ai/decisions/D019_PASSIVE_EFFECT_PRIORITY.md`
- `Docs/ai/decisions/D020_EVENT_VISIBILITY_ROUTING.md`
- `Docs/ai/decisions/D021_SERVER_ONLY_EVENTS.md`
- `Docs/ai/decisions/D022_PHASE_TIMING_AND_TRANSITIONS.md`
- `Docs/ai/decisions/D023_VOTING_RESERVATION_AND_RESOLUTION.md`
- `Docs/ai/decisions/D024_ACTION_RESERVATION_AND_NIGHT_RESOLUTION.md`
- `Docs/ai/decisions/D025_CORE_SERVICE_BOUNDARIES_AND_RUNTIME_CAPABILITIES.md`
- `Docs/ai/decisions/D026_WIN_EVALUATION_AND_TERMINAL_RESULT.md`
- `Docs/ai/decisions/D027_PHASE1_5_RULE_DECISIONS.md`
- `Docs/ai/decisions/D028_DISCONNECTION_SUDDEN_DEATH_AND_GRAVEYARD.md`
- `Docs/ai/decisions/D029_NO_SELECTION_FALLBACK_ORDER.md`
- `Docs/ai/decisions/D030_AVAILABLE_ACTIONS_SERVICE.md`
- `Docs/ai/decisions/D031_PROTOCOL_VERSIONING_CHANNEL_SCOPE_AND_SINGLE_MODIFIER.md`
- `Docs/ai/decisions/D032_PROTOCOL_ENVELOPE_DIRECTION_AND_STRICT_HEADERS.md`
- `Docs/ai/decisions/D033_NETWORK_SESSION_BOUNDARY.md`
- `Docs/ai/decisions/D035_TICK_ISOLATION_AND_PLAYER_SEQUENCE.md`
- `Docs/ai/decisions/D036_GAME_ID_PROTOCOL_BOUNDARY.md`
- `Docs/ai/decisions/D037_DEADLINE_FREE_TICK_CONTRACT.md`
- `Docs/ai/decisions/D038_CORE_TICK_PROGRESS_DISPATCH.md`
- `Docs/ai/decisions/D039_EVENTBUS_WEBSOCKET_DELIVERY_BOUNDARY.md`
- `Docs/ai/decisions/D040_DECLARED_CHAT_CHANNEL_PUBLICITY.md`
- `Docs/ai/decisions/D041_PLAYER_ACTION_REJECTION_CODES.md`
- `Docs/ai/decisions/D042_PUBLIC_ACTIVITY_COUNTER.md`
- `Docs/ai/decisions/D044_CO_CLAIMABLE_AND_CHAT_TARGET.md`
- `Docs/ai/decisions/D045_ABILITY_RESOLUTION.md`
- `Docs/ai/decisions/D046_MEDIUM_TARGET_CAUSES.md`
- `Docs/ai/decisions/D047_PLAYER_STATE_DELIVERY.md`
- `Docs/ai/decisions/D048_SEAT_ENTRY_TOKEN.md`
- `Docs/ai/decisions/D049_NETWORK_SEND_AND_REPLAY_RETENTION.md`
- `Docs/ai/decisions/D050_PHASE2_SEPARATE_PROCESS_COMPLETION.md`

### 2.7 ルート設定（2件）
- `.editorconfig`
- `.gitattributes`

## 3. 残して書き換えるもの（EDIT 6件）

| ファイル | 書き換え内容 |
|---|---|
| `AGENTS.md` | 全面置換。新しい1ページの規則（`AGENTS_NEW.md`）にする。旧規則（6役割・Design Gate・独立レビュー連鎖・狭い読込み範囲）は使わない |
| `README.md` | 現状（再構築中）、起動方法（サーバ・llama-server・AIエージェント）、構成、製品要件（初期構想書から引き継ぐ項目）に書き直す |
| `pyproject.toml` | `packages.find.include` を `["server*", "ai_agent*"]` に。pytest marker は `completion` だけ残し `windows_private` を削除 |
| `tests/conftest.py` | completion marker の対象を `tests/test_phase2_completion.py` の1件だけにする |
| `.gitignore` | ゲーム出力先 `games/` を追加 |
| `Docs/ai/TEST_POLICY.md` | 冒頭の「Phase 6 テストアーキテクチャ」節だけ削除。サーバ側の節はそのまま |

## 4. 消すもの（DELETE 1342件）

### 4.1 ルートの文書・設定（5件）
- `CONTRIBUTING.md` — 旧6役割の運用説明
- `EXTERNAL_REVIEW_2026-09-14_PHASE6_HARNESS.md` — 旧運用の外部レビュー記録
- `EXTERNAL_REVIEW_LOG.md` — 旧運用の外部レビュー記録
- `PHASE6_DIAGNOSTIC_REPAIR_PROPOSAL.md` — 旧Phase6の方向転換・修正提案
- `PHASE6_REDIRECTION_INSTRUCTION.md` — 旧Phase6の方向転換・修正提案

### 4.2 GitHub設定（5件）— Actionsは停止中。必要になったら簡単なCIを新設
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/design_question.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/ci-cleanup-apply.yml`
- `.github/workflows/ci.yml`

### 4.3 AIクライアント全体（50件、29,551行）
AIの判断・発話・LLM呼び出し・GPU待ち行列・Phase 6の会話状態はすべて作り直す。
`ai_client/network` と `ai_client/world`（通信と受信状態の管理、約4,700行）は単体では健全だが、新しいAIは使わずに動く（試作で確認）。
再接続や別PC対応が必要になった時点で、タグから復元して再評価する。
- `ai_client/__init__.py`（231行）
- `ai_client/_compat.py`（87行）
- `ai_client/brain/__init__.py`（71行）
- `ai_client/brain/controller.py`（1975行）
- `ai_client/brain/coordinator.py`（100行）
- `ai_client/brain/dummy.py`（30行）
- `ai_client/brain/interface.py`（12行）
- `ai_client/brain/invocation.py`（1815行）
- `ai_client/brain/model.py`（405行）
- `ai_client/discussion/__init__.py`（169行）
- `ai_client/discussion/context.py`（1323行）
- `ai_client/discussion/model.py`（1532行）
- `ai_client/discussion/projection.py`（1087行）
- `ai_client/discussion/state.py`（1631行）
- `ai_client/discussion/transaction.py`（666行）
- `ai_client/game_time.py`（48行）
- `ai_client/llm/__init__.py`（138行）
- `ai_client/llm/admission_broker.py`（1708行）
- `ai_client/llm/admission_client.py`（1249行）
- `ai_client/llm/admission_metrics.py`（389行）
- `ai_client/llm/admission_types.py`（295行）
- `ai_client/llm/audit.py`（346行）
- `ai_client/llm/backend.py`（587行）
- `ai_client/llm/brain.py`（793行）
- `ai_client/llm/config.py`（101行）
- `ai_client/llm/decision.py`（642行）
- `ai_client/llm/prompt.py`（622行）
- `ai_client/llm/types.py`（1409行）
- `ai_client/network/__init__.py`（103行）
- `ai_client/network/client.py`（1308行）
- `ai_client/network/credentials.py`（82行）
- `ai_client/network/protocol.py`（275行）
- `ai_client/network/state.py`（178行）
- `ai_client/network/types.py`（601行）
- `ai_client/reaction_chat/__init__.py`（50行）
- `ai_client/reaction_chat/controller.py`（1931行）
- `ai_client/reaction_chat/frequency.py`（308行）
- `ai_client/reaction_chat/randomness.py`（81行）
- `ai_client/reaction_chat/types.py`（316行）
- `ai_client/runtime.py`（1196行）
- `ai_client/vote_ability/__init__.py`（27行）
- `ai_client/vote_ability/controller.py`（1009行）
- `ai_client/vote_ability/selection.py`（192行）
- `ai_client/vote_ability/types.py`（292行）
- `ai_client/world/__init__.py`（98行）
- `ai_client/world/memory.py`（190行）
- `ai_client/world/model.py`（638行）
- `ai_client/world/reducer.py`（634行）
- `ai_client/world/service.py`（516行）
- `ai_client/world/transport.py`（65行）

### 4.4 スクリプト（14件、9,290行）
- `scripts/ai_status.py`（581行）
- `scripts/check_docs.py`（796行）
- `scripts/ci_private_summary.py`（139行）
- `scripts/monitor_phase6_gpu.py`（169行）
- `scripts/phase6_context_probe.py`（163行）
- `scripts/phase6_conversation_suite.py`（255行）
- `scripts/phase6_model_comparison.py`（634行）
- `scripts/phase6_none_reason_probe.py`（92行）
- `scripts/phase6_private_review.py`（753行）
- `scripts/phase6_schema_order_probe.py`（33行）
- `scripts/phase6_suite_report.py`（87行）
- `scripts/run_long_regression.py`（270行）
- `scripts/run_phase4_local_smoke.py`（454行）
- `scripts/run_phase5_local_smoke.py`（4864行）

### 4.5 テストとfixture（79件、49,019行）— 消したAIクライアント・スクリプトを対象とするもの
- `tests/fixtures/ci_schema_contract_hashes.json`（18行）
- `tests/fixtures/completion_clock.py`（52行）
- `tests/fixtures/completion_diagnostics.py`（159行）
- `tests/fixtures/completion_evidence.py`（788行）
- `tests/fixtures/completion_process.py`（145行）
- `tests/fixtures/phase3_1_network_client_process.py`（453行）
- `tests/fixtures/phase3_2_world_client_process.py`（264行）
- `tests/fixtures/phase3_3_brain_client_process.py`（184行）
- `tests/fixtures/phase3_4_reaction_brain.py`（55行）
- `tests/fixtures/phase3_4_reaction_client_process.py`（331行）
- `tests/fixtures/phase3_5_brain.py`（36行）
- `tests/fixtures/phase3_5_client_process.py`（383行）
- `tests/fixtures/phase3_5_evidence.py`（59行）
- `tests/fixtures/phase4_client_process.py`（574行）
- `tests/fixtures/phase4_fake_backend.py`（133行）
- `tests/fixtures/phase5_broker_process.py`（177行）
- `tests/fixtures/phase5_client_process.py`（401行）
- `tests/fixtures/phase5_deterministic_backend.py`（166行）
- `tests/fixtures/phase5_server_process.py`（324行）
- `tests/fixtures/phase6_conversation_cases.py`（163行）
- `tests/fixtures/phase6_evidence.py`（94行）
- `tests/fixtures/phase6_private_review_vectors.json`（1行）
- `tests/fixtures/phase6_semantic_backend.py`（79行）
- `tests/fixtures/phase_attempt_barrier.py`（28行）
- `tests/fixtures/reaction_chat_evidence.py`（57行）
- `tests/fixtures/reaction_frontier.py`（124行）
- `tests/fixtures/reservation_probe.py`（126行）
- `tests/test_ai_status.py`（773行）
- `tests/test_asyncio_timeout_compat.py`（200行）
- `tests/test_ci_private_summary.py`（148行）
- `tests/test_ci_schema_sharing.py`（128行）
- `tests/test_ci_unstarted_stale.py`（251行）
- `tests/test_completion_barrier_clock.py`（35行）
- `tests/test_completion_diagnostics.py`（114行）
- `tests/test_logical_game_clock.py`（208行）
- `tests/test_long_regression.py`（37行）
- `tests/test_phase3_1_completion.py`（1211行）
- `tests/test_phase3_1_network_client.py`（2383行）
- `tests/test_phase3_2_completion.py`（375行）
- `tests/test_phase3_2_world_state.py`（1013行）
- `tests/test_phase3_3_brain_interface.py`（907行）
- `tests/test_phase3_3_completion.py`（310行）
- `tests/test_phase3_4_completion.py`（618行）
- `tests/test_phase3_4_reaction_chat.py`（1566行）
- `tests/test_phase3_5_completion.py`（856行）
- `tests/test_phase3_5_vote_ability_controller.py`（687行）
- `tests/test_phase4_ai_audit.py`（397行）
- `tests/test_phase4_completion.py`（504行）
- `tests/test_phase4_llm_backend.py`（912行）
- `tests/test_phase4_llm_brain.py`（1009行）
- `tests/test_phase4_llm_contracts.py`（593行）
- `tests/test_phase5_brain_admission.py`（2587行）
- `tests/test_phase5_completion.py`（1115行）
- `tests/test_phase5_generation_admission.py`（1883行）
- `tests/test_phase5_local_smoke.py`（3002行）
- `tests/test_phase5_short_chat.py`（859行）
- `tests/test_phase5_speaking_frequency.py`（1057行）
- `tests/test_phase6_audit_identity.py`（57行）
- `tests/test_phase6_conversation_suite.py`（211行）
- `tests/test_phase6_discussion_context.py`（1076行）
- `tests/test_phase6_discussion_state.py`（2414行）
- `tests/test_phase6_discussion_transaction.py`（3469行）
- `tests/test_phase6_evidence_retention.py`（226行）
- `tests/test_phase6_fixture_repetition.py`（38行）
- `tests/test_phase6_gpu_monitor.py`（96行）
- `tests/test_phase6_memory_projection.py`（779行）
- `tests/test_phase6_model_comparison.py`（245行）
- `tests/test_phase6_none_reason_probe.py`（225行）
- `tests/test_phase6_pre_vote_reassessment.py`（1320行）
- `tests/test_phase6_private_review.py`（867行）
- `tests/test_phase6_quality_grounding.py`（441行）
- `tests/test_phase6_reaction_semantics.py`（1221行）
- `tests/test_phase6_runtime.py`（2064行）
- `tests/test_phase6_schema_order_probe.py`（261行）
- `tests/test_phase6_semantic_completion.py`（1060行）
- `tests/test_phase6_semantic_output.py`（1514行）
- `tests/test_phase_attempt_barrier.py`（31行）
- `tests/test_reaction_frontier.py`（133行）
- `tests/test_reservation_probe.py`（159行）

### 4.6 `Docs/` 直下と `Docs/ai/` 直下の文書（37件）
- `Docs/PUBLISH_CHECKLIST.md`
- `Docs/ai/ARCHITECTURE.md`
- `Docs/ai/CURRENT_STATE.md`
- `Docs/ai/GPU_MONITOR.md`
- `Docs/ai/INDEX.md`
- `Docs/ai/LOGICAL_GAME_CLOCK.md`
- `Docs/ai/MAIN_INTEGRATOR_PROMPT.md`
- `Docs/ai/MODEL_ASSIGNMENTS.md`
- `Docs/ai/OPEN_QUESTIONS.md`
- `Docs/ai/OPERATIONS.md`
- `Docs/ai/PHASE6_ACTUAL_CONTEXT_BUDGET_DESIGN.md`
- `Docs/ai/PHASE6_AI_QUALITY_CAUSE_MAP.md`
- `Docs/ai/PHASE6_CODEX_QUALITY_REPAIR_PROPOSAL.md`
- `Docs/ai/PHASE6_CODE_REVIEW_20260917.md`
- `Docs/ai/PHASE6_COMPLETION_BUDGET_DESIGN.md`
- `Docs/ai/PHASE6_MASTER_TEST_BATCHES.csv`
- `Docs/ai/PHASE6_MASTER_TEST_CATALOG.csv`
- `Docs/ai/PHASE6_MASTER_TEST_PLAN.md`
- `Docs/ai/PHASE6_MODEL_COMPARISON_20260918.md`
- `Docs/ai/PHASE6_NONE_REASON_RESULT_20260919.md`
- `Docs/ai/PHASE6_QUALITY_ENFORCEMENT_ANALYSIS_20260918.md`
- `Docs/ai/PHASE6_QUALITY_GROUNDING_DESIGN.md`
- `Docs/ai/PHASE6_R13_TEST_REPAIR_PLAN.md`
- `Docs/ai/PHASE6_R16_REVIEW_ADOPTION.md`
- `Docs/ai/PHASE6_SPEECH_ACT_NONE_PROPOSAL_20260919.md`
- `Docs/ai/PHASE6_STAGE_B_TEST_CASES.csv`
- `Docs/ai/PHASE6_STAGE_B_TEST_CASES.md`
- `Docs/ai/PHASE6_SYNTHETIC_SUITE.md`
- `Docs/ai/PHASE6_TEST_OPERATION_RESET_2026-09-14.md`
- `Docs/ai/PROMPTS.md`
- `Docs/ai/REVIEW_INBOX.md`
- `Docs/ai/ROADMAP.md`
- `Docs/ai/RUNBOOK.md`
- `Docs/ai/SPEC_REVIEW.md`
- `Docs/ai/TASKS.md`
- `Docs/ai/TOKEN_SAVING_PROPOSAL_20260907.md`
- `Docs/ai/WORKFLOW.md`

### 4.7 仕様書フォルダのうち消すもの（3件）
- `Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` — 初期構想書。製品要件はREADMEへ移す（AI設計の節は旧前提）
- `Docs/ai/spec/CODEX_TOKEN_EFFICIENT_WORKFLOW.md` — 旧運用・開発補助環境の記録
- `Docs/ai/spec/LOCAL_LLM_SETUP.md` — 旧運用・開発補助環境の記録

`AI_WEREWOLF_CODEX_HANDOFF.md` は最初の構想書です。製品としての要件（ターン制にしない、全発言に反応しない、
古くなった発言は取り消す、人格、入力中表示、狼の秘密会話、人間参加・複数PC・異種LLM・MOD）は新しいREADMEへ移します。
AIの内部設計の節（§12〜§22、特に「LLMへ全履歴を丸投げしない」「思考と会話を分離する」）は、今回の停滞の出発点になった前提なので、
そのまま引き継がず、実ゲームで必要と分かった時点で採否を決めます。

### 4.8 決定記録のうち消すもの（21件）— 運用・AIクライアント・Phase 6の決定
- `Docs/ai/decisions/D034_LOCAL_LLM_DEV_ASSIST.md`
- `Docs/ai/decisions/D043_LOCAL_LLM_INPUT_LENGTH_GUARDS.md`
- `Docs/ai/decisions/D051_IMPLEMENTATION_DESIGN_GATE.md`
- `Docs/ai/decisions/D052_FAIL_CLOSED_DESIGN_GATE.md`
- `Docs/ai/decisions/D053_TWO_REVIEWERS.md`
- `Docs/ai/decisions/D054_DESIGN_REQUEST_LIFECYCLE.md`
- `Docs/ai/decisions/D055_TRANSPORT_OBSERVATION_VIA_WORLD.md`
- `Docs/ai/decisions/D065_AUTODEV_FREEZE_AND_REMOVAL.md`
- `Docs/ai/decisions/D066_ROLE_MODEL_SEPARATION.md`
- `Docs/ai/decisions/D067_STANDARD_MULTI_AGENT_ROLES.md`
- `Docs/ai/decisions/D068_EXTERNAL_PHASE5_REVIEW_DISPOSITION_AND_ACCEPTANCE_AUTHORITY.md`
- `Docs/ai/decisions/D069_Q8_PHASE6_BASELINE.md`
- `Docs/ai/decisions/D070_ASTRA_NATIVE_HARNESS.md`
- `Docs/ai/decisions/D071_PHASE6_EVIDENCE_RETENTION_RESUMPTION.md`
- `Docs/ai/decisions/D072_PHASE6_REBASELINE.md`
- `Docs/ai/decisions/D073_FROZEN_MASTER_TEST_PLAN_OPERATION.md`
- `Docs/ai/decisions/D074_PHASE6_STRUCTURED_OUTPUT_AND_FAILURE_COLLECTION.md`
- `Docs/ai/decisions/D075_RISK_BASED_DISPATCH_AND_CONTEXT.md`
- `Docs/ai/decisions/D076_LOGICAL_GAME_CLOCK.md`
- `Docs/ai/decisions/D077_SYNTHETIC_QUALITY_GATE.md`
- `Docs/ai/decisions/TEMPLATE.md`

### 4.9 運用の記録フォルダ（全件消す）
| フォルダ | ファイル数 | 行数 | 中身 |
|---|---:|---:|---|
| `Docs/ai/handoffs/` | 565 | 80,058 | 作業報告 |
| `Docs/ai/tasks/` | 444 | 18,121 | 作業指示（task packet） |
| `Docs/ai/design/` | 42 | 13,768 | Phase 3.1以降の詳細設計（AI側） |
| `Docs/ai/failures/` | 47 | 1,714 | 失敗記録 |
| `Docs/ai/history/` | 13 | 1,202 | 退避した運用記録 |
| `Docs/ai/prompts/` | 5 | 35 | 役割別プロンプト |
| `Docs/ai/review_archive/` | 4 | 8,884 | レビュー記録 |
| `Docs/ai/roadmap_archive/` | 2 | 363 | 旧ロードマップの退避 |
| `Docs/ai/roles/` | 6 | 44 | 役割定義 |

各ファイル名は TSV にすべて載っています。

## 5. 新しく作るもの（NEW）

| パス | 内容 |
|---|---|
| `AGENTS.md`（置換） | `AGENTS_NEW.md` の内容 |
| `ROADMAP.md` | 新しいロードマップ（Stage 0〜4）。短く保つ |
| `WORKLOG.md` | 1回の変更ごとに1〜3行：日付・変更・回したゲーム（seed）・観察・次の一手 |
| `ai_agent/` | 新しいAIエージェント。試作（`analysis/stagnation-2026-10-02/spike/spike_live_v2_improved_prompt.py`）を分割して正式化 |
| `tests/test_ai_agent_*.py` | プロンプト組立て・フィルタ・機械チェックの単体テスト、偽LLMで1ゲーム完走するスモークテスト |
| `Docs/analysis/2026-10-02/` | 今回の分析資料（`analysis/stagnation-2026-10-02/` 一式。private な実ゲーム本文は含まない）。参考資料で、コードからは参照しない |
| サーバの変更（Stage 1） | 人狼に相方を通知する処理とテスト。`DESIGN.md` の該当箇所も更新 |

## 6. Git以外とブランチ・タグの扱い

**タグ（消す前に必ず作る。origin へ push する：ユーザー決定済み）**

| タグ | 対象 | コミット |
|---|---|---|
| `archive/2026-10-02-main` | main | `5a5635f` |
| `archive/2026-10-02-experiment` | experiment/speech-act-kind-first-20260919 | `ee80895` |
| `archive/2026-10-02-codex-t575` | codex/context-audit-t575 | `4df84c6` |
| `archive/2026-10-02-capture-catchup` | fix/capture-catchup-20260921 | `b425cd1` |

既存の `autodev-freeze-2026-09-09` はそのまま。既存ブランチは消さない（タグ作成後に消すかどうかはユーザーが後で決める）。
新ブランチ `rebuild/simple-agent` も origin へ push する。main と他の既存ブランチは変更しない。

**未追跡のローカルファイル（`C:\AIwolf` 内、37項目）**

| 対象 | 扱い |
|---|---|
| `logs/`（実ゲーム・実験の生ログ、private原文を含む） | **消さない。** 必要ならユーザーがリポジトリ外へ移動して保管 |
| `logs/t448-quality-cycles/ci-capture-worktree`（Gitのworktree） | 消さない。不要になったらユーザーが `git worktree remove` |
| `.pytest-*`、`.review-*`、`.tmp/` などの一時フォルダ | AIは消さない。不要と判断したらユーザーが削除 |

**作業場所**: 既存の `C:\AIwolf` で、`main` から作った `rebuild/simple-agent` ブランチへ切り替える。
追跡ファイルに未コミットの変更が無いことは確認済み（2026-10-02）。未追跡ファイルは切り替え後もそのまま残る。
（別フォルダにworktreeを作る方法もあるが、Codexのサンドボックスが作業フォルダ外への書き込みを拒否する可能性があるため、既存フォルダでの切り替えを既定にした）

## 7. 復元方法

```bash
git checkout archive/2026-10-02-main -- ai_client/network ai_client/world
```

```bash
git show archive/2026-10-02-experiment:Docs/ai/CURRENT_STATE.md
```

ファイル単位・フォルダ単位で、いつでも取り出せます。履歴の書き換えはしないので、消したものはすべてGitに残ります。

## 8. ユーザーの決定（2026-10-02）

1. 初期構想書 `AI_WEREWOLF_CODEX_HANDOFF.md`: 外す。製品要件は新しい README に移す。
2. `ai_client/network`・`ai_client/world`: 外す。再接続などで必要になったらタグから復元する。
3. タグと新ブランチ `rebuild/simple-agent`: origin へ push する（通常の push のみ）。
4. 新ブランチへの commit: 許可。main と他の既存ブランチは変更しない。

## 9. 資料の保存場所

この一覧・プロンプト・分析資料の正本は `C:\AIwolf\logs\rebuild_materials_2026-10-02\`（Gitの対象外。ブランチを切り替えても消えない）。
T0 の後は `Docs/analysis/2026-10-02/` として新ブランチに入り、GitHub にも残る。
