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
| Q26 | 処刑見送りの選択と消費規則 | `decisions/D023`（明示的棄権と game-wide 上限） |
| Q27 | 投票先の公開設定 | `decisions/D023`（`hidden` / `live` / `after`） |
| Q25 | 役職欠けの置換先の指定方法 | DESIGN.md §5（`role_missing` をオブジェクト化）。実装は R-20260830-06 |

---

## Q11 [Phase 2] チャットチャネルの初期スコープ

参照実装には「墓場の発言」「観戦者の発言」の設定がある。
マスター仕様 §22 のチャネル案は `public / wolf / lover / spectator / system / private`。

Phase 2 でどこまで実装するか。

Reviewer 推奨: `public` / `wolf` / `system` / `private:<player_id>` の4つ。
`graveyard`（墓場）と `spectator` はチャネル定義だけ用意し、送信は Phase 7 で。

---

## Q6 [Phase 2 前] プロトコルのバージョニングと順序保証

- 全メッセージに `protocol_version` を入れるか、ハンドシェイクのみか
- 再接続時の欠落検出用に `seq`（連番）を入れるか

後から足すのは高コストなので Phase 2 の最初に決める。

Reviewer 推奨: ハンドシェイクで `protocol_version`、全イベントに `seq`。

---

## Q8 [Phase 5 前] 昼の議論時間とLLM推論量の見積り

参照実装の昼は1〜6分、さらに時短・延長で伸縮する。
8GB VRAM 共有LLMで9エージェントが1回の昼に何発言できるかを実測し、
議論時間・発言レート上限・サーバ側の発話制限を決める。

---

## Q24 [Phase 8] 複数 Modifier による同一属性の上書き優先順位

複数の Modifier が同じ実効属性（`team` / `count_as` / `attack_result` /
`inspect_result` / `medium_result`）を異なる値で上書きするときの優先順位が未定義。

Phase 1.1 は曖昧な状態を許可せず、同じ属性を二重に上書きする付与をエラーにする。
Phase 8 で具体的な Modifier を追加する前に、priority・付与順・相互排他のいずれで
解決するかを決める必要がある。

---
