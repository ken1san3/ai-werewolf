# D008 Modifier（付与状態）を Role とは別概念にする

## Status

Accepted (2026-08-29) — ユーザー提案
「狐憑きや恋人などの通常の役職を配られた後に追加される追加役職はまた別の概念にした方がいいかも」

## Context

参照実装には、**通常の役職に重ねて付与されるもの**が複数ある。

| 例 | 付与タイミング | 付与元 | 効果 |
|---|---|---|---|
| 狐憑き | 配役時 | 配役 | 勝利条件が妖狐陣営へ変わる / 占われると呪殺 / 妖狐全滅で後追い |
| 恋人 | ゲーム中（初夜） | キューピット | 勝利条件が恋人陣営へ変わる / 相方の死亡で後追い / 相手を認識 |
| 手玉 | ゲーム中 | 悪女 | 悪女の死亡で後追い |
| 呪い | ゲーム中（一晩） | 背徳の呪術師 | その夜に占われると呪殺（**一時的**） |

これらは Role ではない。元の役職（占い師・人狼など）を**保持したまま**上に乗る。

公式の役職表で狐憑きの全属性が「役職に準ずる」になっているのは、
これが Role ではなく Role へのオーバーレイだからである
（`spec/JUDGMENT_REFERENCE.md` §1）。

Role として実装すると次が破綻する。

- 「占い師でありかつ恋人」を1つの Role で表現できない
- 恋人になった人狼の勝利条件が二重になる
- 呪いのような「一晩だけ」の状態を Role で表せない
- `by_role` 委譲（D003）が Role 同士の循環になる

## Decision

**Modifier** を Role / Team / Ability / Passive / Effect と並ぶ第一級の概念として追加する。

```
Player
├─ Role          （1つ。ゲーム中に置換されうる）
└─ Modifiers[]   （0個以上。重ねて付与される）
```

### Modifier の定義

```yaml
id: lover
name: 恋人
kind: modifier

# 付与
grant:
  timing: in_game          # assignment | in_game
  duration: permanent      # permanent | until_next_dawn | n_nights

# 勝利条件の扱い
win_condition:
  mode: override           # override | add | none
  value:
    type: survive_together
    with: modifier_holders(lover)

# 付与される受動効果
passives:
  - type: follow_death
    trigger: other_holder_died

# 知識とチャット
knowledge:
  knows: other_holders      # 恋人は互いを認識する
chat_channels: [lover]

# 属性の上書き（省略時は元役職のまま）
overrides: {}
```

```yaml
id: fox_possessed
name: 狐憑き
kind: modifier
grant:
  timing: assignment
  duration: permanent
win_condition:
  mode: override
  value: { type: fox_team }
passives:
  - type: die_when_inspected
  - type: follow_death
    trigger: team_eliminated
    team: fox
exclusions:                 # 付与できない役職
  - werewolf
  - thief
  - fanatic
  - whispering_madman
  - cupid
  - femme_fatale
  - zombie
  - tag: fox_team
```

### 適用規則

1. **Role の属性（D003の5軸）は Modifier で上書きできる。**
   上書きが無い項目は元役職の値をそのまま使う。
   これが「役職に準ずる」の実体である。
2. **勝利条件の優先順位を明示する。**
   `override` を持つ Modifier があれば、Role の勝利条件を置き換える。
   複数の `override` が競合した場合は `priority` の高い方を採用し、
   同値なら**設定エラーとして起動時に弾く**（暗黙の順序に依存しない）。
3. Modifier は Passive / Knowledge / ChatPermission を追加できる。
   Ability の追加も許すが、Phase 1 では使わない。
4. `duration` を持つ（呪いのような一時的な状態を表現するため）。
   期限切れは Phase Manager が解除する。
5. 付与できない役職を `exclusions` で宣言する。
   コアに「狐憑きになれない役職リスト」を書かない。
6. **Modifier は Role を置換しない。** 置換は別機構（下記 Non-goals）。

## Non-goals — Modifier と混同しないもの

以下は Modifier ではなく **Role Replacement**（役職そのものの差し替え）である。
マスター仕様 §8 の Effect 一覧では `ChangeTeam` / `SwapRole` / `CopyRole` が
並列に並んでいるが、実際には別の機構である。

| 機構 | 例 | 内容 |
|---|---|---|
| Modifier | 恋人、狐憑き、手玉、呪い | 元の Role を保持して上に乗る |
| Role Replacement | 光の使徒／闇の化身の変化、怪盗の交換 | Role そのものが別の Role になる |

Role Replacement は Phase 8 で別途設計する。本Decisionの対象外。

## Why

- 「占い師でありかつ恋人」のような直交する状態を素直に表現できる
- 狐憑きの「役職に準ずる」が特殊ケースではなく、
  Modifier の既定動作として自然に説明できる
- 呪いのような一時的状態を Role に持ち込まずに済む
- 恋人陣営・妖狐陣営など、勝利条件の上書きが1箇所に集約される
- Modifier を content として追加できるため、MOD拡張の幅が広がる

## Consequences

- Player の状態が `role` だけでなく `modifiers[]` を持つ
- 勝利判定・占い判定・襲撃判定は
  **必ず「Role + Modifiers を合成した実効属性」を経由する**。
  Role を直接読む実装はレビューで指摘対象とする
- 実効属性の解決関数が1つ増える（`resolve_effective_attributes(player)`）
- Phase 1 のスコープ:
  - **Modifier のデータモデルと適用機構だけ実装する**
  - 具体的な Modifier（恋人・狐憑き）は Phase 8。標準9人村には登場しない
  - ただし「Modifier が0個のとき Role の値がそのまま出る」ことはテストする

## Verification

- Modifier 0個のプレイヤーで、実効属性が Role の値と一致する
- テスト用 Modifier を付与すると、指定した属性だけが上書きされ、
  他は Role の値のままになる
- `override` を持つ Modifier が勝利条件を置き換える
- `override` が競合する2つの Modifier を同一プレイヤーへ付与する設定が
  起動時にエラーになる
- `duration: until_next_dawn` の Modifier が翌朝に解除される
