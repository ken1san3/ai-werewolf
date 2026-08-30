# D024 夜行動の予約と解決の実装境界

## Status

Accepted (2026-08-30) / Phase 1.5 implementation shape

## Context

DESIGN.md §6.3 は、夜の能力を締切まで置換可能な予約とし、解決時だけ状態と使用回数を
変える。§7.1 は effect 単位の priority と DeathResolve の順序を定める。

## Decision

- `GameState.submit_action(now, actor, ability, targets)` は生存者・フェーズ・締切・
  target selector・restriction・uses を server 側で検証し、actor ごとに最後の予約を
  保持する。予約履歴は `ACTION_SUBMITTED` の server event に残す。
- `resolve_pending_actions(now)` は夜の締切以後に一度だけ予約を解決する。通常の
  `advance_phase` も Night0 / Night の終了時にこれを呼ぶため、呼び出し側が解決を
  忘れてもフェーズを通過しない。
- effect は content の effect reference priority で並べる。Protect / Inspect /
  MediumInspect / Attack は DeathResolve（70）の前に状態候補を作り、死亡を確定してから
  InspectDeadRole（75）と Retaliation（78）を実行する。
- Role 名では分岐しない。target selector、passive、effect ID と Role + Modifier の
  実効属性から処理する。内部死因を持つ死亡は既存の単一入口へ渡す。
- 標準設定の複数人狼襲撃は `majority` で集計し、同数の乱数選択は server event に記録する。
  `designated` / `random` は Q28 の決定まで解決を拒否する。

## Deferred

Q5（未選択時フォールバック）、Q16（パン屋通知形式）、Q28（人狼襲撃の未定義方式）、
Q29（死亡連鎖上限）、Q30（自己護衛と selector 対応）は、設計決定後に実装する。

## Verification

`tests/test_action_resolver.py` で予約置換、締切、占い / 呪殺 / 襲撃、護衛、
強欲な人狼、賢狼、霊能、猫又、初日占い、公開死因のマスクを検証する。
