# D046 霊能の対象死因を Ability の宣言に統一する

## Status

Accepted (2026-08-31、R-20260831-71 の Reviewer 推奨に従う)

## Context

霊能の対象死因は、preset の medium ルールと役職 Ability の `target.causes` に
重複して宣言されていた。実際に selector が読むのは後者だけで、preset を変えても
対象が変わらず、設定変更が黙って無視されていた。

## Decision

対象死因は Ability の `target.causes` のみから導く。`MediumRules` と preset から
未使用の `targets` を削除し、通知時期の `notify_timing` は維持する。
旧キーが残った preset は未知のキーとしてロード時に拒否する。

## Consequences

- 標準の対象死因と通知動作は変わらない。対象を変える場合は役職 YAML を編集する
- 死因の参照検証は既存の content registry 検証に委ね、medium ルール内の死因直書きを廃止する
- YAML の死因変更で対象候補・行動受付・private 結果が変わることを回帰テストする
- DESIGN §5 の古いキーと §6.1 の突然死の霊視説明は Reviewer が更新する
