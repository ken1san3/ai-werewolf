# D087 Phase6 LLMテスト到達までの継続許可

Status: ACCEPTED
Date: 2026-09-30
Authority: ユーザー「LLM実行テストまでできる限り進めてください」

## Decision

D086のoffline限定から、必要な製品v2接続・ローカル検証・独立承認を経て、有限なLLMテストへ進む作業を許可する。許可はpreflightの成功や実行済みを意味しない。現在はT524から既承認設計内の接続を順に進める。

## Retained gates

PF2候補/権限、PF3 native grammar/token/context予算、所有process、source/config/model/runtime/hash、一回freeze、private/public境界、独立tool reviewと必要な独立測定を維持する。stage budget UNSETのままproviderを呼ばない。新しい境界はDesign Gateへ渡し、上限拡大やvalidator緩和で回避しない。

通常game/Master Run/Phase7、model変更/DL、API導入、Actions、main変更、過去candidate再生成・再採点、同条件retryは引き続き禁止。v1既定と既承認証拠は保持する。製品既定切替え・品質改善の承認ではない。
