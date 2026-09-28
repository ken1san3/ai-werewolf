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
| Q37 | 初回 Join の認可方式 | `decisions/D048`（席ごとの入室トークン） |
| Q38 | CO の市民騙り識別子とチャット送信先 | `decisions/D044`（`claimable` と明示 `channel_id`） |
| Q39 | Detailed Design が Reviewer を兼ねてよいか | 自己承認は禁止。旧割当は `decisions/D053` の歴史記録、現行原則は `decisions/D066` |
| Q40 | 役割責務とモデル割当の完全分離 | `decisions/D066`。責務・task・session・modelを分離し、現役割当は `MODEL_ASSIGNMENTS.md` に集約 |
| Q8 | Phase 6の議論時間・推論量baseline | `decisions/D069`。180秒、既存時短/延長、general server capなし、AI chat最大2回/phase（COは既存の別経路）。Phase 6実測までの暫定baseline |

## 未解決

T512 U1=A / U2=A は2026-09-28ユーザー承認、D086へ解決記録。providerとbaseline新規判定は未許可。
