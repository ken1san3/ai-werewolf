# Phase 3.1 完走テストの検証コントラクト — DESIGN REQUEST

Issued by: Reviewer / Claude（2026-09-02、D051）
For: Detailed Design / Sol
Status: REQUESTED — 成果物 `PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md` が
Reviewer の `DESIGN REVIEW: APPROVED` を得るまで実装へ渡らない（RUNBOOK §4.3）。
**承認は Claude が付ける**（D053。設計を書いた model と別の model が承認する）。

```
DESIGN: REQUIRED
```

Request status: CLOSED — 対応設計の承認、実装、両環境5回連続の検証が完了（2026-09-04、R-20260903-02）。

設計は APPROVED だが R-20260903-02 / 06 が未解決で、実装と検証は完了していないため
REQUEST を OPEN に戻した。R-20260903-07 の修正により、Addendum B の設計要求と
この検証コントラクト要求を同時に Gate へ提示できる。

対象は `tests/` のみ。`ai_client/` と `server/` は変更しない。

## Reason

**4回 `DESIGN: NOT REQUIRED` で回して、4回とも漂流したためである。**

R-94（拒否0を要求）→ R-100（chat が消えた）→ R-116 / R-119（対象を取り違え、不安定）
→ R-123（送信が9席中2席まで痩せた）。毎回 Implementer が「何を送り、何を assert するか」を
局所判断し、そのたびに ROADMAP §3.1 の完了条件から離れた。
D051 の判定条件「実装方法が複数あり、選択を誤ると手戻りが大きい」の実例そのものである。

対象そのものも REQUIRED 側に並ぶ。9個の別プロセス、asyncio、フェーズ境界の競合、
プロセス強制終了と Resume。局所修正で正しさが決まらない。

**根本原因は Reviewer にある。** `action_rejections` の合計0を assert せよと要求したのは
R-20260901-94 の Required（Reviewer / Claude）であり、これは canonical ではない。
`ROADMAP.md` §3.1 の完了条件は

```
  `action.rejected` を受け取ったことがテストから観測でき、握り潰されない
```

であって、**「拒否が0件であること」はどこにも無い。**
2秒フェーズの境界で `vote_unavailable` が返るのは、サーバが authoritative である以上
**正常な応答**である。間違っていたのは driver ではなく assert である。

したがってこの設計依頼は「テストを直す方法」ではない。
**「何をもって完了条件が満たされたと言えるか」という検証コントラクトを決めることである。**

## Design scope

`ROADMAP.md` §3.1 の完了条件7項目それぞれについて、
**「どの観測が、その条件の証拠になるか」を決める。**
成果物の節は `RUNBOOK.md` §4.2 の12項目に従う。ただし本件はテスト設計なので、
「Public interfaces」は driver が書き出す status ファイルのスキーマ、
「Data flow」は 観測 → status → assert の経路、と読み替えてよい。

現在の対象物:

| 何を | どこ |
|---|---|
| 完走テスト | `tests/test_phase3_1_completion.py` |
| 9席を動かす driver | `tests/fixtures/phase3_1_network_client_process.py` |
| 完了条件（**canonical**） | `Docs/ai/ROADMAP.md` §3.1「完了条件」 |
| 承認済み 3.1 設計 | `design/PHASE3_1_NETWORK_CLIENT_DESIGN.md` |
| 拒否理由の語彙（**実コード**） | `server/aiwolf_core/` の actions / voting / interactions / action_constraints、`server/network/session.py` |
| テスト方針 | `Docs/ai/TEST_POLICY.md` |
| 経緯 | `REVIEW_INBOX.md` R-20260902-123 |

## 現状の実測値（Reviewer が完走テストを複製して測った）

```
  ACTIONS_SENT: player-1: 4, player-2: 1, 残り7席は 0
  CHAT_SENT   : player-1: 1, player-2: 1, 残り7席は 0
  RESUMED     : player-0（再起動した席）→ actions_sent 0
  サーバ側     : CO_DECLARED 1 / VOTE_SUBMITTED 1 / ACTION_SUBMITTED 1
  3レビュー前 : CO_DECLARED 33 / VOTE_SUBMITTED 32 / ACTION_SUBMITTED 16
```

driver に積み上がった制限。**これらは設計判断の産物ではなく、
assert を通すための逐次的な削りである。**

- `_is_primary_action_sender` — vote / ability / CO を送るのは9席中1席だけ（index 1）
- `ability_action_sent` / `vote_action_sent` — その1席もゲーム全体で各1回
- `daytime_chat_sent` / `daytime_co_declared` — chat / CO もゲーム全体で1回
- `CHAT_SENDER_LIMIT = 3` — chat は先頭3席のみ
- `if resumed: continue` — **再接続した席は復帰後いっさい送らない。**
  `resumed` は一度立つとリセットされない

テストは 5/5 で安定している。安定しているが、証拠が無い。

## Questions Sol must resolve

**それぞれ、採らなかった案と、採らなかった理由を1〜2行で書くこと。**

1. **拒否理由の分類。**
   拒否は0件を要求しない。代わりに理由ごとに扱いを決める。
   核の語彙は閉じている（`action_unavailable` / `action_deadline_passed` /
   `action_closed` / `vote_unavailable` / `actor_unavailable` / `invalid_target` /
   `unknown_target` / `invalid_message` / `invalid_comment` / `co_limit_reached` /
   `claim_not_allowed` / `unknown_claimed_role` / `invalid_claimed_result` /
   `invalid_report_kind` / `self_vote_disabled` ほか。**実コードから確定すること**）。
   加えて `server/network/session.py` が返す `invalid_action` / `game_mismatch` /
   `unsupported_protocol_version` がある。
   - **フェーズ境界の競合として許容する理由**はどれか
   - **クライアントの欠陥として必ずテストを落とす理由**はどれか
   - 判断が割れるもの（`actor_unavailable`、`co_limit_reached`、`action_closed`）を
     どちらに置き、なぜか
   - 許容側でも**件数の上限**を置くか。置かないなら、際限なく拒否されても
     緑になる状態をどう防ぐか
   - この分類を**テストのどこに1箇所だけ**置き、driver 側に散らさない方法

2. **何席が、何を、何回送れば完了条件の証拠になるか。**
   「9個が `GAME_ENDED` へ到達する」が条件だが、到達だけなら黙っていても達成できる。
   - 投票フェーズで**生存者数と同オーダー**の `VOTE_SUBMITTED` をサーバ側で観測する、
     という形にするか。下限をどう表現するか（固定値か、生存者数からの導出か）
   - 能力 / CO / chat について同様の下限を置くか。置くならどの単位か
     （ゲーム全体で N 件か、日ごとか、席ごとか）
   - 「1件でも通る」assert を残さないこと。現状の
     `assertIn("VOTE_SUBMITTED", event_types)` は 1 件で通る

3. **再起動した席が「取りこぼした範囲を回復した」ことを何で示すか。**
   `if resumed: continue` は外す。そのうえで、
   - 復帰後にその席が送信できたことを示す観測は何か
   - `last_seq` からの回復が実際に起きたことを示す観測は何か
     （`session.resumed` の受理、欠番検出、`game.state_sync` による再基準化のどれを使うか）
   - 強制終了の**タイミング**を固定するか、乱数にするか。固定するなら
     どのフェーズで落とすのが回復の証拠として強いか。
     乱数にするなら再現性（seed）をどう担保するか

4. **フェーズ境界の競合を、送信を減らさずに減らす方法。**
   R-121 で締切の残り時間計算は正しくなった（`phase_ends_at - timestamp` から
   `monotonic` の経過を引く）。`DAYTIME_ACTION_SAFETY_SECONDS = 0.25` もある。
   - 安全マージンで送信を抑止するのは正しいか。抑止した場合、その席は
     「送らなかった」のか「送れなかった」のか、テストから区別できるか
   - 抑止と、1の許容分類は**どちらが主**か。両方使うなら重複しないか
   - 2秒フェーズという設定そのものを完走テストで変えてよいか。
     変えるなら何を犠牲にするか（現実の締切競合を踏まなくなる）

5. **不安定さ（R-119）を再発させないための、設計としての歯止め。**
   送信を増やせば競合は増える。5回連続緑は結果であって設計ではない。
   - 何が不安定の原因になりうるかを列挙し、それぞれについて
     **テストが「不安定」ではなく「明確な失敗」を出す形**にする方法
   - タイムアウト値（`game end` 45秒、`all client statuses` 15秒）は妥当か。
     妥当性の根拠を、実測のどの数字に置くか
   - 失敗時に原因が分かる診断出力の最小セット

6. **このコントラクトを 3.3〜3.5 / Phase 5 の完走テストへどう再利用するか。**
   同じ漂流を次のサブPhaseで繰り返さないために、
   - 1〜3で決めた分類・下限・回復証明のうち、**どこまでを共有部品にするか**
   - 共有するなら置き場所（`tests/fixtures/` の下か、別か）
   - 共有しないと決めるなら、その判断を明記する

追加で、設計上どちらでもよいと判断した点は**「決めない」と明示する。**

## Constraints

- **`ROADMAP.md` §3.1 を書き換えない。** すでに正しい。直すのはテストの契約である
- **`ai_client/` を変更しない。** driver の都合で本体を変えない。
  本体に問題があると判断したら**設計へ書かず Reviewer へ報告する**
- **`server/` / `protocol/` / ゲームコア / content を変更しない**
- テストがサーバの可否判定を再実装しない。
  行動選択肢は受信した `player.action_state` の列挙からのみ導く
- 役職名・チャネルID・死因IDを driver へ直書きしない
- LLM 無しで完走する
- Reviewer 環境ではテストをファイル単位で走らせる（`device_bash` に約120秒の上限）。
  1ファイルの実行時間がこれを大きく超えない範囲に収める

## Out of scope

- `ai_client/network/` および `ai_client/world/` の実装変更
- Phase 3.2 の完走テスト（World State は別の完了条件を持つ）
- Brain interface（3.3）、発言生成（3.4）、選択方針（3.5）
- LLM backend、プロンプト、structured output（Phase 4）
- CI の導入・実行環境の変更
- `check_docs.py` / `ai_status.py` の変更（Design Gate 側の制約は別途 Reviewer が起票済み）

## 粒度

**完成コードを書かない。関数内部を1行ずつ指定しない。**
決めるのは「何を観測し、何を証拠とし、何で落とすか」であって、
`assertEqual` の並べ方ではない。
判断基準は「Implementer が重要な設計判断をせずに書けるか」だけである。

## この設計が承認されない条件

- `ROADMAP.md` §3.1 の完了条件7項目のいずれかに、対応する観測が割り当てられていない
- 拒否理由の分類が「許容する / 落とす」の二値で確定していない
- 「1件でも通る」assert が残っている
- 再起動した席の回復が、送信0のままでも成立する形になっている
- `ai_client/` または `server/` の変更を前提にしている
- 関数内部まで書いてある（コードの二重管理）

---

## Addendum C — 開始タイミングの決定論化と、中断免除の一般化（2026-09-03、R-20260903-02）

Issued by: ユーザー（仕様・ルールの最終決定権者）。Reviewer / Claude が Design Gate として本 REQUEST へ収録した。
**この Addendum は本 REQUEST の一部である。** `PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md` は
`DRAFT` へ戻す。新しいファイルを作らず同じ `_DESIGN.md` を改訂し `Status: IN_REVIEW` にする。
承認は Claude が付ける（D053）。

### 方針（決定済み。設計で覆さないこと）

**R-02 は `tests/` だけで直す。`server/` は変更しない。**
**「timeout を伸ばす」「sleep を増やす」で直さない。** それをやると Reviewer 環境では通って
Windows では落ちる状態が続く。

### C1. 開始タイミングを決定論にする（P0）

現状は `server.start()` → ticker 開始 → player-0..8 を**逐次**起動、である。
プロセス生成が遅い環境では player-8 の接続前に Night → Day 1 まで進む。
Reviewer 環境で `test_separate_process_rejection_and_production_import_guard` が
5回中3回落ちたのはこれである（注入席が day1 の CO 機会をそもそも受け取らない）。

決めること:

- **`.ready` バリア。** Phase 3.2 完走テストと同じく、各クライアントが初回 `game.state_sync` を
  消費したら `.ready` を書き、親テストが9個そろうまで待つ
- **test-only gated clock。** `.ready` だけでは足りない。ticker は待機中も進むからである。
  `TickDriver` は `clock: Clock = monotonic_seconds` を受け取り
  `now = timestamp(self._clock())` → `game.advance_if_due(now)` とするので、
  テスト専用の gate 付き clock を注入すれば開始時刻を完全に握れる。
  `WebSocketGameServer(..., ticker=TickDriver(registry, clock=gate))` で注入できる
- **`GameState.started_at` も同じ clock から取ること。** 現在の完走テストは
  `started_at=monotonic_seconds()` を渡している。ticker だけ gate して `started_at` が実時計だと、
  RELEASE の瞬間に全 phase が期限切れになり意味が無い。**同一の clock を通すこと**
- **gate の形。** RELEASE 前は固定値 t0 を返し、RELEASE 後は t0 + (RELEASE からの実経過) を返す。
  RELEASE 後は実時間で進み、2秒 phase の締切競合はそのまま踏む
- **9プロセスは `asyncio.gather` 相当で並列起動する。** 逐次起動をやめる

`manual_advance.assert_not_called()`（server tick only）の契約は維持する。
gated clock は tick を止めるのではなく、tick が見る時刻を決めるだけである。

### C2. 中断免除を vote から全 action へ一般化する（P0）

現在は vote だけ `stop_seq < round_start_seq <= resume_sync_seq` で免除される。
ability / co_declare / chat には同じ仕組みが無いため、再起動直後に
`stale_before_send` になっただけで落ちる。**vote だけを特別扱いする設計をやめる。**

- **server opportunity ledger を作る。** 親テストは既に `session._reply` を差し替えて
  vote round の開始 seq を記録している（`round_replies`）。これを一般化し、
  停止対象 player へサーバが送った `game.state_sync` / `player.action_state` から
  `{seq, player_id, day, phase, kind[, ability_id]}` の**送信機会一覧**を作る
- **免除は seq で判定する。** `stop_seq < opportunity.seq <= resume_sync_seq` の機会だけを
  再起動席の送信義務から外す。例:

```text
  stop_seq = 30, resume_sync_seq = 35
    seq=31 ability|protect  -> 免除
    seq=35 co_declare       -> 免除
    seq=36 chat             -> 免除しない
```

### C3. eligible をクライアント証拠から作らない（P0）

現在 `eligible` は `action_evidence` から作られている。つまり
**クライアントが機会を観測できなかった場合、その機会は eligible にも入らない。**
「送るべきだったのに送っていない」を検出できない。今回の
「player-8 が day1 の CO / chat 機会を受け取っていない」もこれで隠れる。

- `expected` = **サーバが実際に player へ提示した機会**（C2 の ledger）
- `actual` = クライアントの `action_evidence`
- 判定: `required = expected - interrupted` として `required <= sent <= expected`
- **非再起動の8席は `sent == expected`**（厳密一致）

### C4. `stale_before_send` を成功扱いにしない（P0）

免除するのは **再起動席 かつ その機会の seq が中断 window 内** のときだけ。

```text
  player-8 / stop=30 / resume=35
    seq=33 stale_before_send -> OK（送信義務免除）
    seq=38 stale_before_send -> FAIL
```

window 外の `stale_before_send` は今までどおり失敗させる。実不具合を隠さない。

### C5. 「免除しすぎ」を禁じる（P1）

vote で既にやっている考え方を全 action へ広げる。最低限、次を FAIL とする。

- 免除された機会が **0件** — 再起動テストとして成立していない
- 免除された機会が **再起動席の全機会** — 再起動席を丸ごと検証対象外にしている

さらに「中断前に最低1回成功 → 中断区間あり → Resume 後に最低1回成功」を要求する。
`sent → interruption → resume → sent` の証拠が必ず残る形にする。

### C6. timeout 時の診断を強化する（P1）

現在の `_wait_for()` は `timed out waiting for ...` しか出さない。
`diagnostics=lambda: {...}` のような仕組みを持たせ、timeout 時に
elapsed / game の day・phase・result / 各クライアントの生死と return code /
`.ready` の数 / 各席の last_seq / `stop_seq`・`resume_sync_seq` / 直近の機会一覧
までを自動で出す。

### C7. 高速テストと完走テストを運用上分ける（P2）

`pytest -q -m "not completion"` で数秒、`-m completion` で重いものだけ、
`pytest -q` で全部、と分ける。**既定の全件実行から完走テストを外さない。**
分類するだけである。marker は `pyproject.toml` の
`[tool.pytest.ini_options]` に登録し、未登録 marker の警告を出さないこと。

### Acceptance（変更しない）

- Reviewer 環境で5回連続成功
- Windows で5回連続成功
- 各回120秒以内

### Out of scope

- `server/` / `protocol/` / ゲームコア / content の変更
- `ai_client/` の変更（本体に問題があると判断したら設計へ書かず Reviewer へ報告する）
- Addendum B で決めた wire 順序と終了境界の変更
- timeout を伸ばす、sleep を足す、phase を長くする類の回避

### この設計が承認されない条件

- gated clock を入れたのに `GameState.started_at` が別の時計から来ている
- 免除が seq ではなく day / phase の粗い単位のままである
- `expected` をクライアント証拠から作っている
- 中断 window 外の `stale_before_send` が成功扱いになっている
- 免除0件 / 全件を禁じる assert が無い
- `server/` を変更している
