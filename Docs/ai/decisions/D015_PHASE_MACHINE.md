# D015 フェーズ状態機械 — 初日は夜から始まる

Status: Accepted (2026-08-29) / ユーザー決定
「初日は夜から始まり、襲撃は不可能（もしくは役職無しのNPCを強制選択）で
最初の動きを人狼チャットで相談する時間とします」

## Decision

Night0 から始まる状態機械とし、条件つきフェーズ（Night0 / Runoff）を持てる構造にする。
遷移図と Night0 の挙動は `spec/DESIGN.md` §6。

### 襲撃を封じる方法 — 能力の無効化を採用

ユーザーは「襲撃不可」と「役職無しのNPCを強制選択」の2案を挙げた。
**能力の無効化（`available_from_night: 1`）を採用する。**

NPC を `players` に混ぜると、勝利条件のカウント（`count_as`）、
全ターゲットセレクタ、猫又の道連れ抽選（`alive_all`）、
公開プレイヤー一覧のすべてに NPC 除外の例外が必要になる。
1箇所の設定で済むものを、全体に例外を撒く形にしない。

`available_from_night` は汎用の制約とし、「3日目から使える」役職にも使える形にする。

## Why

- 初日夜を相談時間にすることで、初日から人狼陣営が連携でき、
  初日の議論に情報の非対称性が生まれる
- 初日占いの3設定（none / free / random_white）がすべて Night0 で表現できる
- 条件つきフェーズを許す構造にしたため、決選投票と Night0 を同じ仕組みで扱える

## Consequences

日番号は Night0 → Dawn 1 → Day 1 の順に振る。
Night0 では人狼の `player.action_state` に襲撃が出ない。

## Verification

`Docs/ai/TEST_POLICY.md` を参照。
