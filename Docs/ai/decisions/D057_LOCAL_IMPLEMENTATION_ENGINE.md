# D057 Qwen中心のローカル実装基盤

## Status

Accepted — D056と2026-09-07のユーザー実行指示に基づく移行。詳細設計はClaude+GPT-6 Astra、独立承認はReviewer/Solの実記録に対応。

## Context

D034の用途制限では、評価済みのQwen実装能力を日常開発に使えない。
ユーザーはQwenを基本とし、GPT/Claudeは重要判断、Geminiは例外補助という構成で基盤開発を指示した。

## Decision

Researcher / Implementer / Reviewer / Fixer / テスト失敗分析 / 文書草稿をQwenへ許可する。
仕様の最終判断、Design Gate承認、Hard Red単独実装、自己申告による機械検証合格は禁止する。
役割とモデルを分離し、通常のImplementerはQwen、進行は決定的なrunnerが担う。

- Green: 契約 → 実装 → 機械検証。失敗時だけ修正。
- Yellow: 必要な調査 → 実装 → 機械検証 → freshレビュー。具体的違反だけ修正。
- Red: 上記に加えGPT/Claudeによる最終確認。Qwenだけでは適用しない。
- Hard Red: 上位モデルへ引継ぎ。ローカル実装ループを開始しない。
- Gemini: Qwen/GPT/Claudeへ任せる合理性が低い具体的理由がある場合のみ。通常フローの依存にしない。
- 修正は最大2回。契約不足・範囲外変更・設計判断の発生は上位へ戻す。

契約は版、入力、許可パス、保護パス、API、不変条件、受入条件、必須検証、証拠を明示する。
Qwenは構造化whole-file候補を返し、runnerが検証・隔離適用する。モデル生成コマンドは実行しない。
最終的な元リポジトリへの適用は別操作とし、元入力との競合を拒否する。
隔離コピーはOSセキュリティsandboxではない。テストはローカル権限で動く開発コードである。
ゲームコア・通常CIはローカルLLMへ依存しない。

## Why

上位モデルを工程管理や反復修正から外し、重要な設計と検証に消費を集中する。
LLMの誤回答、古い入力、編集形式の不正、テスト収集漏れを機械的に検出する。

## Consequences

- D034の用途制限をsupersedeする。履歴本文は保持する。
- D034の生ログによる成否判定、対象を勝手に減らさない規則、およびD043の入力長保護を継続する。
- D051/D053の設計承認と役割別署名を維持する。
- 実装の詳細と検証結果は `design/INFRA_QWEN_RUNNER_DESIGN.md` および専用handoffに記録する。
- 2026-09-07の変更前検証: Local Windows / Python 3.13.3。AIagent 7 passed、AIwolf 305 passed / 612 subtests passed。AIwolfは192.82秒。生ログはC:/AIagent/agent/baseline-tests.logとC:/AIwolf/.infra-baseline.log。
- 初回AIagentテストはsandboxの一時フォルダ権限で3 failed / 4 passed。通常ローカル権限で同じテストが7 passed。コードを直して隠していない。
