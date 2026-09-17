# D077 実token計数と人工会話gate

Status: ACCEPTED（検証運用のみ。製品budget設計の承認ではない）
Date: 2026-09-18
Authority: 最新ユーザーのPhase6 PART A–F指示

## Decision

今後の品質修正はoffline focused→固定人工LLM suite→意味上の改善確認→製品採用時独立review→
許可された短い実gameの順とする。実gameで最初に問題を探す運用を避ける。
hard100%、重大秘密/状態矛盾0、長文exact copy0は必要条件であり、speech_act比率だけで合格にしない。
初回32caseでは小標本/戦略自由/未観測を明示し、恣意的なsemantic95%閾値は設定しない。

proxyとactual tokenを別単位として記録し、推定比率を安全式へ流用しない。
今回の8192はruntime slot設定、GGUF metadataは262144。T405棄却promptのactual1935を根拠に
context超過説を撤回する。製品proxy gateは設計独立承認・実装・採用reviewまで維持。

## Evidence and consequence

T406/T407: 32baseline＋28candidate新規生成、同bytes4再利用、transport/token不一致0。
完成例候補はNONE率改善と同時に26/28の例文全文コピーを生んだため不採用。
詳細は `Docs/ai/handoffs/tasks/T406_CONTEXT_AND_SYNTHETIC_SUITE.md`。
実game/長時間Master Run/Phase7を開始しない。モデル/concurrency/scale/private境界は不変。
