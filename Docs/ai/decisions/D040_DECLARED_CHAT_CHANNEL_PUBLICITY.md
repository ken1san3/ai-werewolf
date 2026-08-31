# D040 ChatChannel の公開性宣言

## Status

Accepted (2026-08-31) / R-20260831-55 の修正

## Context

D039 は public channel を「全 Role が宣言する channel」と推論した。しかし content に
public を持たない新 Role を加えるだけで、既存ゲームの公開 channel が private 扱いとなり、
死亡者の `rules.graveyard.view_public` も無関係に効かなくなる。Role の追加は YAML だけで
既存の channel 権限を変えない必要がある。

## Decision

- `content/chat_channels.yaml` の全 channel は `public: bool` を明示する。
- `ChatChannel.is_public` はその値を保持し、`GameState.chat_channel_recipient_ids` は
  Role 全体を走査せず、この値だけで public recipient を選ぶ。
- `public: true` の channel は、死亡者を含む recipient を
  `rules.graveyard.view_public` に従って選ぶ。`public: false` の channel は、従来どおり
  生存者の Role + Modifier channel 宣言で選ぶ。

## Why

channel ID の文字列を core に固定せず、かつ Role catalog の偶然の全称条件にも依存しない。
公開性は channel 自身の性質なので、既存の `allows_co` と同じ content registry に置く。

## Consequences

- D039 の「全 Role に宣言された channel を public とする」部分をこの Decision が置き換える。
- channel を追加・改名するときは `public` を明示する。Role を追加しても既存 channel の
  recipient は変化しない。
