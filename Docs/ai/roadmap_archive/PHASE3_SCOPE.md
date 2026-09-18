# Phase 3 詳細スコープ（完了済み）

T429でROADMAPから全文を保全した。過去の仕様・判断を変更せず、必要な節だけ参照する。

---

# Phase 3 — AI Client Skeleton

Phase 3 全体の完了条件: RuleBased AI 9人でゲーム完走

3.3〜3.5 の境界は 3.2 完了後に確定した（R-20260901-82、2026-09-04）。
判断の差し替え境界を 3.3 で1つ作り、3.4 が「いつ喋るか」、3.5 が「誰に投票し誰に能力を使うか」を
受信した列挙だけから決める。**内容の質と推論は Phase 6、LLM は Phase 4。**

## 3.1 Network Client

サーバの WebSocket プロトコルだけで席に着き、切断をまたいで
本人視点の状態を保ち続ける単独プロセスのクライアント基盤を作る。
**このサブPhaseは「繋がり続けて、受け取って、送れる」までを担当し、
何を送るかは決めない。**

含む:
- 入室トークンによる Join と、private に受け取る接続トークンの保持
- 接続トークンと受信済み `last_seq` による Resume
- 受信ループと、封筒（`type` / `protocol_version` / `event_id` / `game_id` /
  `seq` / `timestamp` / `payload`）の検証
- `protocol_version` のメジャー不一致で接続を打ち切る
- プレイヤー単位 `seq` の欠番検出と、そこからの状態回復
- `game.state_sync` / `player.list` / `player.deaths` / `player.action_state` の
  受信と、本人視点の最新状態としての保持。**保持するだけで解釈しない**
- `phase_ends_at` からクライアント側の締切タイマーを起こす
- 上位（Brain / Controller）が使う送信 API。組み立ての入力は
  受信した `player.action_state` の列挙だけ
- `action.rejected` の受理
- 切断検知と再接続。諦めるときはプロセスとして明示的に終わる
- LLM を使わずに動く Dummy 操作での結合テスト

含まない:
- World State の構造化と記憶（3.2）
- Brain interface、Dummy Brain（3.3）
- 発言内容・反応制御（3.4）、投票や能力の選択方針（3.5）
- ゲームコアの import、および可否判定のクライアント側での再実装
- 役職名・チャネルIDのクライアントへの直書き
- 再接続 UI、観戦（将来候補）

完了条件:
- 別プロセスの Network Client 9個が、サーバ tick だけで `GAME_ENDED` へ到達する。
  行動の選択は Dummy でよいが、**選択肢は受信した列挙からのみ導く**
- 1個を任意のタイミングで落として再起動しても、保存済み接続トークンで
  同じ席へ Resume し、取りこぼした範囲を回復して完走する
- 受信列に欠番が生じたことをクライアントが検出したと、テストから観測できる
- `protocol_version` のメジャーが異なるサーバへは接続しない
- `action.rejected` を受け取ったことがテストから観測でき、握り潰されない
- `server.aiwolf_core` と `server.network` を import していない
- LLM 無しでテストが完走する

**Phase 3.1 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- `seq` 欠番時の回復方式
- 接続トークンの保存責務
- receive / send の asyncio 構造
- `action.rejected` の責務境界
- reconnect backoff と終了条件

参照: DESIGN.md §2 §9.1 §9.2 §9.3 §9.4
参照: D048 / D049 / D050 / D051、`handoffs/PHASE2_HANDOFF.md`
参照: TEST_POLICY「ネットワーク Phase の検証の所在」（節番号は持たない）

## 3.2 World State・Memory

Network Client が渡す検証済みイベントと本人視点 snapshot から、
**上位が読みやすい形の世界像と履歴**を組み立てる。
`ai_client.network` の生の payload を 3.3〜3.5 へ直接触らせない。

**このサブPhaseは「何が起きたかを覚えて、引ける形にする」までを担当し、
それが何を意味するかは判断しない。**

含む:
- 受信イベントの取り込みループ。`ClientEvent` を速やかに消費し、
  Network Client の受信を詰まらせない（bounded queue の consumer になる）
- 本人視点の現在像: 生存者 / 死亡者と公開死因 / 現在フェーズと日番号 /
  自分の役職・Modifier / 自分が受け取った能力結果 / 現在の行動選択肢
- 履歴: 発言、CO 宣言と CO 報告、投票結果、死亡、フェーズ遷移を
  **発生順に引ける形**で保持する
- `game.state_sync` を受けたときの全量再基準化。再接続・欠番回復をまたいで
  世界像が壊れないこと
- 記憶量の上限と、上限に当たったときの捨て方
- 3.3〜3.5 が使う読み取り API（読み取り専用。世界像を外から書き換えられない）

含まない:
- 推論。Belief / Suspicion / 信頼度 / ライン / 反応スコアは **Phase 6**
- 発言生成、投票先や能力対象の選択（3.4 / 3.5）
- Brain interface と Dummy Brain（3.3）
- LLM、プロンプト整形、structured output（Phase 4）
- サーバの可否判定の再実装。行動選択肢は受信した列挙をそのまま持つ
- 役職名・チャネルIDのクライアントへの直書き
- `server.aiwolf_core` / `server.network` の import

完了条件:
- `game.state_sync` だけを与えて世界像を1から構築でき、
  以後の増分イベントで同じ状態へ到達する（sync と増分の等価性）
- 切断 → Resume → 保持内 replay、および保持外の欠番 → 全量 sync 回復の
  どちらでも、回復後の世界像が「最初から接続していた場合」と一致する
- 履歴が発生順に引け、上限を超えたとき何が失われるかが観測できる
- 読み取り API から返る値を書き換えても内部状態が変わらない
- 9プロセスの完走テストが、World State を経由した状態でも通る
- `server.aiwolf_core` / `server.network` を import していない
- 全テストが LLM 無しで走る

**Phase 3.2 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- 世界像の内部表現と、読み取り API の形
- 履歴の保持単位と上限、上限到達時の捨て方
- 全量 sync と増分イベントの適用を1つの経路にするか分けるか
- イベント取り込みを Network Client と同じ task で回すか分けるか
- 未知イベント種別を受けたときの扱い

参照: ROADMAP §3.1、`design/PHASE3_1_NETWORK_CLIENT_DESIGN.md` の Public Interfaces
参照: DESIGN.md §9.3 §9.4（サーバが送る内容。クライアント側の構造は定めていない）
参照: TEST_POLICY「ネットワーク Phase の検証の所在」（節番号は持たない）

## 3.3 Brain Interface と Dummy Brain

World State の読み取りと Network Client の送信の間に、**判断を差し替えられる境界を1つ**作る。
LLM 無しで動く Dummy Brain を置き、Phase 4 の LLM Brain が同じ境界へ入れるようにする。

含む:
- Brain が受け取る入力の型。世界像は 3.2 の読み取り API から、行動の選択肢は
  `current_actions()` の handle から渡す。raw payload を Brain へ渡さない
- Brain が返す判断の型。**送信は Controller が行い、Brain は handle を自作しない**
- LLM を使わない Dummy Brain。seed から再現できる決定論
- Brain を呼ぶ単位と、応答が遅れた・返らないときの既定行動
- Brain を別実装へ差し替えられることを示すテスト

含まない:
- 発言内容の生成方針（3.4）、投票・能力の選択方針（3.5）
- LLM backend、プロンプト、structured output（Phase 4）
- Belief / Suspicion / 信頼度・ライン・反応スコア（Phase 6）
- ゲームコアの import、サーバの可否判定のクライアント側での再実装
- 役職名・チャネルID・死因IDのクライアントへの直書き

完了条件:
- Dummy Brain だけで9プロセスが完走する（LLM 無し）
- Brain を差し替えても World State と Controller を変更せずに動く
- Brain が列挙に無い行動を返したとき、Controller が送信せず、そのことを観測できる
- 同じ seed と同じ入力から同じ判断が出る
- `server.aiwolf_core` と `server.network` を import していない

**Phase 3.3 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- Brain の入出力の型と、`WorldSnapshot.version` の渡し方
- 同期か非同期か。応答の締切と、締切を過ぎたときの既定行動
- 1フェーズあたりの呼び出し回数
- 差し替えの単位（プロセス起動時か実行時か）

参照: ROADMAP §3.1 §3.2、`design/PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` の Public Interfaces

## 3.4 Reaction・Chat Controller

昼の会話で**いつ喋るか**を決める。何を喋るかは Brain が返し、その質は Phase 6 で扱う。

含む:
- 発話の間隔と1フェーズあたりの上限。締切前の打ち切り
- 他人の発言を受けて喋る起点。固定の発言順を作らない（Design invariant 3）
- CO 宣言を出す起点
- 送信失敗と `action.rejected` の扱い。握り潰さない

含まない:
- 文章そのものの生成（Phase 4）
- 説得力・整合性・議論品質の評価（Phase 6）
- 投票と能力の選択（3.5）

完了条件:
- 9人が同時に喋っても各フェーズの締切内に収まる
- 1人が黙っても他の席が進む
- 発話が特定の席に偏らないことを観測できる
- `action.rejected` を受けたことがテストから観測でき、握り潰されない
- LLM 無しでテストが完走する

**Phase 3.4 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- 発話間隔の決め方と、上限の単位（フェーズごとか日ごとか）
- 反応の起点をどのイベントから取るか
- 締切前どこで打ち切るか

参照: ROADMAP §3.3、DESIGN.md §9.3

## 3.5 Vote・Ability Controller

**受信した列挙だけ**から投票先と夜行動の対象を決めて送る。

含む:
- 投票・runoff・棄権の選択と送信
- 能力の対象選択と送信。対象数と使用回数は受信した handle から取る
- 締切前に送る。締切をまたいだ拒否の扱い
- seed から再現できる決定論

含まない:
- 誰が怪しいかの推論、占い結果の真偽判定（Phase 6）
- 役職固有の分岐（Design invariant 4）
- サーバの可否判定の再実装。対象の妥当性はサーバが決める

完了条件:
- 生存する全席が各 vote / runoff round で投票を送る
- 能力を持つ席が、その夜に使える能力を送る
- `standard_9`（9席・6種類の役職）で9プロセスが完走する。
  妖狐・猫又・賢狼等を強制する決定論的な別役職network completionは Phase 8
- 同じ seed から同じ選択が出る
- 列挙に無い対象を選ばない。拒否を受けたことを観測できる
- LLM 無しでテストが完走する

**Phase 3.5 の詳細設計で決定する事項。ROADMAP では決めない（D051）:**
- 複数の有効対象があるときの選び方
- 棄権を選ぶ条件
- 締切と送信の余裕をどう取るか

参照: ROADMAP §3.3 §3.4、DESIGN.md §9.4

<!-- 原文終端 -->
