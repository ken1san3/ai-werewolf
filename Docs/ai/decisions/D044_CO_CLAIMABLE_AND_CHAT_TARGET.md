# D044 CO の騙り可否宣言とチャット送信先

## Status

Accepted (2026-08-31) / ユーザー決定（Q38 クローズ）

## Context

Q38 は2点を未決にしていた。

1. `rules.co.allow_villager_claim` は「市民を騙れるか」を定めるが、
   どの役職が「市民」なのかを content から識別する手段が無かった。
   コアへ `"villager"` を直書きすれば D009 に反する。
2. `chat.send` の payload が送信先チャネルを含まず、Phase 2.4 は
   「利用可能な chat action がちょうど1件のときだけ受理する」という暫定処理で回避していた。

## Decision

### 1 騙り可否は role YAML が宣言する

各 Role に **必須キー** `claimable: true | false` を置く。
`allow_villager_claim: false` のとき、`claimable: false` の Role は
CO の騙り先の候補から外れる。コアは role ID を知らない。

```yaml
# content/roles/villager.yaml
id: villager
claimable: false
```

- **必須キーとする。** 宣言の無い Role は content load で落とす。
  `chat_channels.yaml` の `allows_co` / `public` と同じ strict 扱いにする
  （R-20260830-36 / R-20260831-55 で二度学んだ形）。
- `tags` は使わない。`tags` は勝利条件の分類軸
  （`eliminate_role_tag` / `fox` / `werewolf`）であり、CO 可否は別の軸である。
  混ぜると、勝利条件のためのタグ変更が騙り可否を巻き添えで変える。
- `rules.co` に role ID を列挙しない。content 側の宣言と設定側の列挙で
  二重管理になり、役職追加のたびにプリセットを直すことになる。

`allow_villager_claim: true` のときは `claimable` を見ない。
将来「常に騙れない役職」が要るなら、そのときルールキーを分ける。

#### 候補の母集合（2026-08-31 追記 / R-20260831-65）

**母集合は「その試合の配役に含まれる役職」である。** content に登録された
全役職ではない。`claimable` による除外は、この母集合に対して適用する。

- 配役は公開情報なので、母集合をここに限っても新たな秘匿は漏れない。
  逆に content 全体を母集合にすると、`ActionSpec` を通じて
  **そのサーバに導入済みの役職一覧がプレイヤーへ渡る**。Phase 8 の MOD 環境で顕著になる
- 配役に居ない役職の CO は、即座に嘘と分かる主張であり騙りの意味が変わる。
  候補に出さない
- 役職欠け（`role_missing`）で置換された役職の扱いは、置換後の配役に従う。
  置換前の役職は候補に出さない

### 2 `chat.send` は `channel_id` を必須にする

payload に `channel_id` を必須で載せる。core は `get_available_actions` に
その channel を持つ chat action があるかどうかだけで認可する。

- 利用可能なチャネルが1件でも省略を許さない。**特例を残さない。**
- Phase 2.4 の「ちょうど1件のときだけ受理する」暫定処理と
  `ambiguous_chat_channel` の拒否理由は廃止する。
- 未列挙のチャネルを指定した場合は `action_unavailable` で拒否する。
  「そのチャネルが存在しない」と「参加していない」を区別しない
  （存在の有無自体が秘匿情報になりうるため）。

## Consequences

- 既存13役職すべてに `claimable` の宣言を足す必要がある。
- `PLAYER_ACTION_REJECTION_REASONS` から `ambiguous_chat_channel` が消える。
  新たに騙り不可を伝える理由コードが要る（`claim_not_allowed` など。名称は実装時に決める）。
- protocol schema の `chat.send` payload に `channel_id` が必須で加わる。
  Phase 8 の複数チャネル導入時に schema を変えなくてよい。
- Q38 はこれでクローズ。Phase 2.4 の残りを実装できる。

## Verification

TEST_POLICY §12 に追加する。

- `claimable: false` の役職は `allow_villager_claim: false` のとき騙り先に現れず、
  指定しても拒否される。`true` のときは現れる
- `claimable` を宣言しない Role を content へ足すと load が落ちる
- `claimable` を持つ役職の ID をリネームしても Python 無変更で同じ挙動になる
- `chat.send` は利用可能チャネルが1件でも `channel_id` を要求する
- 列挙されていない `channel_id` は `action_unavailable` で拒否される
