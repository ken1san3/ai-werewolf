# D012 死因（DeathCause）を第一級の概念とする

## Status

Accepted (2026-08-29) — ユーザー決定
「処刑、噛む、道連れというのを別の死因として用意するのが丸い」

## Context

マスター仕様のイベント一覧（§6）には `PLAYER_DIED` があるが、
**死因を持たない**。しかし死因で挙動が変わる役職が既に3つある。

- 猫又: 襲撃death と 処刑death で道連れの対象プールが違う（`decisions/D011`）
- 猫又: 道連れdeath では発動しない（連鎖の停止条件）
- 霊能者: 判定対象が「処刑・突然死した人」に限られる

死因を bool や文字列の場当たり的な扱いにすると、
役職を足すたびにゲームコアへ分岐が増える（マスター仕様 §37.2 で禁止されている）。

## Decision

### 1. DeathCause を列挙型として定義する

```
lynched        処刑（昼の投票による死亡）
attacked       襲撃（人狼に噛まれた死亡）
retaliation    道連れ（猫又など、他者の死亡に誘発された死亡）
cursed         呪殺（占いによる妖狐の死亡）
follow_death   後追い（背徳者・恋人など、対象の死亡に連動した死亡）
sudden_death   突然死（時間切れ・退出など、ゲーム外要因）
ability        その他の能力による死亡（聖騎士・九尾など）
```

- 拡張可能にする。**未知の死因を content が追加できること。**
  コアは列挙を固定せず、`content/death_causes.yaml` から読む。
- `PLAYER_DIED` イベントは死因を**必ず**含める。省略不可。
- 死因は1つの死亡につき1つ。
  複数要因が同時に成立した場合は、解決順（priority）で先に確定した方を採用する。

### 2. 死因は Passive / Ability の条件として参照できる

```yaml
passives:
  - type: retaliate_on_death
    rules:
      - when: { death_cause: attacked }
        target: { selector: alive_by_tag, tag: werewolf, count: 1, pick: random }
        death_cause: retaliation
```

`when.death_cause` は単一値でもリストでも受け付ける
（霊能者の `[lynched, sudden_death]` のように）。

### 3. **内部死因と公開死因を分離する** ← 重要

死因は**そのままクライアントへ送ってはならない**。

参照実装では、呪殺された妖狐は朝に「無残な死体」として表示され、
**襲撃死と区別がつかない**。区別できてしまうと、
村側は「呪殺が起きた＝占い師が本物で妖狐を引いた」と即座に確定でき、
妖狐と占い師の駆け引きが成立しなくなる。

したがって死因は2層で持つ。

```
DeathRecord
├─ cause          内部死因（サーバのみが保持。ロジック判定に使う）
└─ public_cause   公開死因（クライアントへ送る。マスクされた値）
```

マスキングは content 側の表で定義する。

```yaml
# content/death_cause_visibility.yaml
attacked:      { public: found_dead }
cursed:        { public: found_dead }   # 呪殺を襲撃と区別させない
ability:       { public: found_dead }
lynched:       { public: lynched }      # 処刑は公開
retaliation:   { public: retaliation }  # 道連れは公開（同時死亡で明らかなため）
follow_death:  { public: follow_death }
sudden_death:  { public: sudden_death }
```

**マスキングをコアにハードコードしない。**
ルール設定（`decisions/D006`）で変更可能にすること。
「呪殺を公開する」モードも設定だけで作れること。

## Why

- 死因で分岐する役職（猫又・霊能者）を、
  ゲームコアの分岐なしに content だけで表現できる
- 道連れを独立した死因にしたことで、**死亡連鎖が構造的に止まる**
  （`decisions/D011`）。ループ検出機構が不要になる
- 公開死因を分離することで、
  マスター仕様 §27「AIへ知り得ない情報を渡さない」を
  死亡通知の経路でも守れる。
  **これはネットワーク層で分離すべき情報であり、
  プロンプトで隠す方式では絶対に守れない**

## Consequences

- `PLAYER_DIED` のペイロードに `cause` が入る（サーバ内部）
- クライアントへ送る死亡イベントには `public_cause` のみを入れる。
  **内部死因を含んだイベントをブロードキャストしたらレビューで指摘対象**
- 霊能者の判定対象条件が `death_cause in [lynched, sudden_death]` で書ける
- 死因の追加が content の変更だけで済む
- Phase 2 のプロトコル設計時、死亡イベントの型は2種類になる
  （サーバ内部イベント / クライアント向けイベント）

## Verification

- 猫又が襲撃されたときと処刑されたときで、道連れの対象プールが変わる
- 霊能者が処刑死と突然死のみを判定対象にする
- 呪殺された妖狐の死亡通知が、クライアント側では襲撃死と区別できない
- 設定で「呪殺を公開する」に切り替えると区別できるようになる
- 内部死因 `cursed` が、いずれのクライアント向けイベントにも出現しない
  （情報漏洩テストとして自動化する）
- content に新しい死因を追加してもコアの変更が不要
