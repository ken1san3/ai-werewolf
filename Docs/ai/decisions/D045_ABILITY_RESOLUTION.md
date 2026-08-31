# D045 Ability の解決単位を content で宣言する

## Status

Accepted (2026-08-31)

## Context

R-20260831-70 により、襲撃を集団解決するかどうかを role tag で判定していたことが判明した。
tag は勝利条件・対象選択にも使う content ID であり、集団解決の意味を兼ねると改名や MOD の追加で
コアの挙動が変わる。

## Decision

すべての Ability YAML に必須の `resolution: group | individual` を置く。

- `group` は同一 effect priority の group Ability をまとめて解決する。現在は attack effect がこの方式を使う
- `individual` は Ability ごとに対象へ effect を適用する
- `group` Ability は target count を 1 に制限する。複数対象の能力は `individual` を宣言する

標準 content では、通常の狼襲撃を `group`、強欲な人狼の二重襲撃とその他の能力を
`individual` とする。

## Consequences

- コアは role / tag / team によらず Ability の `resolution` だけを参照する
- 新しい集団攻撃 Ability は YAML だけで追加できる
- content ID の静的検査を維持し、core vocabulary の宣言箇所と protocol・rule vocabulary の
  同名値だけを例外として明示する
