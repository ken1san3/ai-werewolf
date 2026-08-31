# Open Questions

仕様が未確定で、ユーザーの決定が必要な事項。
決まったら `decisions/` へ移し、ここからは削除する。

参照実装は人狼ジャッジメント（`decisions/D004`）。
まず `spec/JUDGMENT_REFERENCE.md` を見ること。**同じ再調査をしない。**

## 解決済み

| # | 内容 | 決定 |
|---|---|---|
| Q1 | 判定陣営と勝利陣営の分離 | `decisions/D003`（5軸へ分離） |
| Q2 | 村の勝利条件の定義 | `decisions/D005`（役職タグの全滅） |
| Q4 | 勝敗判定のタイミング | `decisions/D005`（死亡処理完了直後に1回） |
| Q3 | ルール設定の既定値 | `decisions/D006`（全項目をオプション化。既定値に決め打ちしない） |
| Q9 | COをシステム機能にするか | `decisions/D007`（システム操作として実装） |
| Q10 | 投票ルールの実装範囲 | `decisions/D006`（Phase 1 で全設定に対応） |
| Q12 | 初期実装役職の範囲 | `decisions/D009`（13役職） |
| Q13 | 「教信者」の正体 | 狂信者の誤字と確認済み。`decisions/D009` のとおり |
| Q14 | 賢狼が情報を得る条件 | `decisions/D010`（襲撃成功時のみ） |
| Q15 | 猫又の道連れ条件 | `decisions/D011`（襲撃死=人狼から / 処刑死=生存者から。連鎖しない） |
| Q17 | 処刑時の道連れ対象 | 生存者全員でユーザー確認済み。`decisions/D011` のとおり |
| Q18 | 乱数の決定性 | `decisions/D013`（再現は目標にしない。イベントログ再生方式） |
| Q19 | フェーズ状態機械 | `decisions/D015`（初日は夜から / Night0 は襲撃不可） |
| Q20 | 夜の解決順 | `decisions/D016` |
| Q21 | 引き分けの扱い | `decisions/D014`（draw = 全員敗北） |
| Q22 | 能力結果の通知範囲 | `decisions/D016` |
| Q7  | ログの公開・秘匿分離 | `decisions/D013`（public / private / ai の3系統） |
| Q23 | available_actions の生成場所と送信 | `decisions/D017` |
| Q5  | 夜行動が未選択だった場合の既定挙動 | `decisions/D027`（能力ごとに `no_selection: random \| skip`） |
| Q16 | パン屋の通知の形式 | `decisions/D027`（`PUBLIC_NOTIFY { notify_id }` のみ。人数と player_id を含めない） |
| Q28 | 人狼襲撃の `designated` / `random` | `decisions/D027`（`random` は有効対象全員から。`designated` は語彙から除外） |
| Q29 | 死亡連鎖の深さ上限 | `decisions/D027`（上限を設けない。連鎖は人数で上界が決まる） |
| Q30 | `guard.self_guard` と target selector | `decisions/D027`（restriction `no_self_target` で表現） |
| Q31 | 突然死の発動条件 | `decisions/D028`（昼に一度も発言しない。既定は無効） |
| Q32 | 切断・タイムアウト時の扱い | `decisions/D028`（進行を続ける。原因を推測しない） |
| Q33 | 再接続時の本人確認 | `decisions/D028`（接続トークンで照合。自己申告を信用しない） |
| Q34 | 時間を進める主体と時刻源 | `decisions/D028`（サーバ単調時計・1秒 tick・締切は絶対時刻） |
| Q35 | 死亡プレイヤーの情報範囲 | `decisions/D028`（生存者以上の情報を渡さない） |
| Q36 | TEST_POLICY 全項目の完了時期 | 案1を採用。TEST_POLICY の各節に担当 Phase を明記し、ROADMAP の「全項目」はその Phase が担当する節を指す。§12 CO は Phase 2.4 |
| Q6  | プロトコルのバージョニングと順序保証 | `decisions/D031`（ハンドシェイクで検証し全メッセージにも保持。`seq` はサーバ→クライアントのみ） |
| Q11 | チャットチャネルの初期スコープ | `decisions/D031`（Phase 2 は public / wolf / fox / system。lover は Phase 8、graveyard / spectator は Phase 7） |
| Q24 | 複数 Modifier による同一属性の上書き優先順位 | `decisions/D031`（Modifier は1人1つまで。衝突が構造的に起きない） |
| Q26 | 処刑見送りの選択と消費規則 | `decisions/D023`（明示的棄権と game-wide 上限） |
| Q27 | 投票先の公開設定 | `decisions/D023`（`hidden` / `live` / `after`） |
| Q25 | 役職欠けの置換先の指定方法 | DESIGN.md §5（`role_missing` をオブジェクト化）。実装は R-20260830-06 |

---

## Q8 [Phase 5 前] 昼の議論時間とLLM推論量の見積り

参照実装の昼は1〜6分、さらに時短・延長で伸縮する。
8GB VRAM 共有LLMで9エージェントが1回の昼に何発言できるかを実測し、
議論時間・発言レート上限・サーバ側の発話制限を決める。

---

## Q37 [Phase 2.2 後] 初回 Join の席割当認可

接続トークンは Join 後の再接続を照合するが、最初に誰が事前登録済みの
`player_id` を Join できるかは未定義である。Phase 2.2 は未接続の席を1回だけ
Join できる境界に留める。リモート公開前に、ロビー作成者による招待・認証などの
初回席割当方式を決定する。

---

## Q38 [Phase 2.4] CO の市民騙り識別子とチャット送信先 — 解決済み（D044）

**ユーザー決定（2026-08-31）。D044 を参照。**

- 騙り可否は role YAML の必須キー `claimable: true | false` が宣言する。
  `allow_villager_claim: false` のとき `claimable: false` の役職は騙り先から外れる。
  コアは role ID を知らない。`tags` は勝利条件の分類軸なので使わない
- `chat.send` の payload に `channel_id` を必須で載せる。利用可能なチャネルが
  1件でも省略を許さない。Phase 2.4 の暫定処理と `ambiguous_chat_channel` は廃止する

---
