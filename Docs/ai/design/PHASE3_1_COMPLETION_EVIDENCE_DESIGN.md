Status: APPROVED — Reviewer / Claude 承認（2026-09-04、Addendum C 反映後）。C1〜C7 すべてに答えており、ユーザー決定を覆していない。Reviewer が実コードで確認した点: `monotonic_seconds()` は `time.monotonic_ns() // 1_000_000_000` の整数秒で `timestamp()` は非 int を拒否するので、gate を整数にする判断は production と同じ粒度であり忠実度を落とさない。`SessionManager.__init__(..., clock: Clock = monotonic_seconds)` は実在し、**この3つ目の注入先は依頼書に無く Sol が見つけた。**`WebSocketGameServer(..., ticker=)` も実在する。免除は key と attempt の source_seq の**両方**が `(stop_seq, resume_sync_seq]` 内のときだけ、`0 < |interrupted| < |expected|`、停止前と Resume 後の実 sent を各1件以上要求しており、「送らないことで緑にする」経路は塞がれている。`sleep(0.2)` による checkpoint 保存推測を観測通知へ置き換えたのは依頼書より強い。C7 は既定の収集集合を変えず、対象7件を node 単位で列挙している。Acceptance（両環境5回連続・各120秒以内）は維持され、「失敗回を捨てて成功回だけ5件集めない」まで書かれている。
2026-09-04 実装時の確認事項: gate を3か所へ通したあと、`GameState.started_at` と envelope.timestamp と phase 期限が同じ時間軸に乗っていることを実測で示すこと（C1 の Acceptance 行に対応）。
2026-09-04: Detailed Design / Sol が REQUEST Addendum C（C1〜C7、R-20260903-02）の
ユーザー決定を反映。本文の vote 限定免除・client 由来 eligible を置換し、末尾に開始 barrier、
seq 証拠、診断、marker 運用を定義した。Claude の設計レビュー待ち。実装・検証は未実施。

# Phase 3.1 完走テスト検証コントラクト 詳細設計

## Purpose

Phase 3.1 の別プロセス完走テストが、単に9席を無言で `GAME_ENDED` まで待たせるのではなく、
受信した action handle だけを使って各席が実際に行動し、切断・Resume・欠番回復・拒否通知を
観測できることを証明する。フェーズ境界で起こり得る正当な拒否は許容する一方、送信を減らして
緑にすること、クライアント欠陥による拒否、回復後に沈黙したまま完走することは許容しない。

## Scope

- `ROADMAP.md` §3.1 の完了条件7項目に対する観測と合否基準を定める。
- 9プロセスの通常完走、保持内 replay、保持外 sync 回復、拒否観測、異常終了診断を対象にする。
- driver が action opportunity、送信結果、Resume、seq 回復、受信 chat を status に残す契約を定める。
- 親テストが driver の観測とサーバの authoritative event を突き合わせる契約を定める。
- 完走時間は現行と同じ2秒フェーズで検証し、LLM は起動も接続も行わない。
- Addendum C は開始時だけの test clock gate、全席の server opportunity ledger、全 action 共通の
  seq 中断免除を追加する。Network 設計 Addendum B の配送順・終了境界は変更しない。

## Files / Modules

| File | Change | Responsibility |
|---|---|---|
| `tests/fixtures/completion_evidence.py` | 変更 | 既存の共通語彙・拒否分類・quorum を維持し、提示機会の正規化、seq 証拠、全 action 共通の expected / interrupted / sent 検証を追加。`server` と `ai_client` を import しない。 |
| `tests/fixtures/phase3_1_network_client_process.py` | 変更 | 受信列挙から Dummy 行動を選び、action opportunity と結果、Resume / gap / chat の観測を status に書く。合否分類は行わない。 |
| `tests/test_phase3_1_completion.py` | 変更 | 9プロセスと実サーバを所有し、status と authoritative server event を共通検証部品へ渡して合否を決める。切断位置と replay 条件もここで制御する。 |
| `tests/test_phase3_1_network_client.py` | 原則変更なし | major version 不一致、`ActionRejected` 通知、seq 回復など Network Client 単体の既存証拠を引き続き担う。契約名の参照修正だけが必要な場合を除き変更しない。 |
| `tests/conftest.py` | 新規 | C7 の既存重いテスト7件に collection 時の `completion` marker を付けるだけ。skip / deselect はしない。 |
| `pyproject.toml` | 限定変更 | C7 が明示した `[tool.pytest.ini_options]` の marker 登録のみ。addopts・testpaths・依存は変えない。 |

実装修正は `tests/` 内。C7 の marker 登録だけが明示された例外である。
`ai_client/`、`server/`、`protocol/`、`content/`、canonical 文書は変更しない。

## Responsibilities

### Driver process

- `NetworkClient.events()` の consumer を1つだけ持ち、最新 snapshot の action handle 以外から
  action、target、ability、channel、claimed role を作らない。
- handle を見た事実、送信を試みた事実、送信 API の結果を分けて記録する。
- rejection は加工せず `action` / `reason` / `seq` を記録する。許容判定はしない。
- Resume 後も通常と同じ行動規則へ戻る。`resumed` を送信禁止条件に使わない。
- 役職名、チャネルID、死因ID、プレイヤー数を知らない。

### Parent completion test

- subprocess、credential、停止マーカー、status、サーバ、タイムアウト、cleanup を所有する。
- server event bus を authoritative な受理証拠として使う。サーバの可否判定は再実装しない。
- `VOTE_RESOLVED.tallies` の key 集合を、その vote / runoff round の authoritative な生存者集合として使う。
- 全9席向けの `game.state_sync` / `player.action_state` reply を受動記録し、
  サーバが提示した送信機会とその player-local seq を client status とは独立に保持する。
- driver の送信試行集合とサーバの受理集合を突き合わせ、動的下限を満たすか判定する。
- 停止境界から Resume sync 完了までに初めて提示された機会だけを、再起動席の送信義務から除外する。
  vote / ability / CO / chat を同じ式で扱い、他席の送信義務とサーバ受理クォーラムは除外しない。
- 意図的な拒否観測テストと、通常完走の拒否分類テストを混同しない。

### Shared evidence module

- 拒否理由を `boundary` / `defect` の二値に閉じ、未知理由は `defect` として fail closed にする。
- status schema の必須 field、型、非負値、seq の整合を検査する。
- `ceil(expected / 2)` の受理クォーラムと、action 種別ごとの coverage を計算する。
- 再起動席の停止 / Resume seq と server opportunity ledger から、送信義務を除外する機会集合を
  一意に導出する。除外規則を driver や親テストへ複製しない。
- Phase 固有の action 選択、サーバ起動、subprocess 起動は持たない。

## Public Interfaces

### Driver status schema

status は UTF-8 JSON object とし、`schema_version` は `phase-completion-evidence/v1` とする。
既存の診断 field に加え、少なくとも次を持つ。共通 v1 の既存利用者は変更せず、Phase 3.1 の
status / stop marker には `evidence_contract: "phase3.1/addendum-c-v1"` を追加する。
`validate_status` / `validate_stop_marker` に keyword-only `require_addendum_c: bool = False` を追加し、Phase 3.1 親は必ず
True を渡す。True はこの contract 値と以下の追加証拠を必須検証し、旧記録へ fallback しない。
既存の他 Phase の validator 呼出しは変更不要とする。

| Field | Shape / meaning |
|---|---|
| `schema_version` | 上記固定値。未知 version は親テストが失敗させる。 |
| `pid`, `player_id` | 実プロセスと、サーバから認証された席。token は含めない。 |
| `resumed` | `session.resumed` を実受信したか。credential の存在だけでは立てない。 |
| `resume_events` | `{seq, requested_last_seq}` の配列。payload の `last_seq` と checkpoint が一致した受理証拠。 |
| `state_sync_events` | `{seq, after_resume, after_gap}` の配列。Resume / gap 後の再基準化を識別する。 |
| `gap_events` | `{expected_seq, received_seq, recovered_seq}` の配列。未回復中は `recovered_seq: null`。 |
| `action_evidence` | 下記 Action Evidence record の配列。 |
| `action_rejections` | `{action, reason, seq, after_resume}` の配列。 |
| `chat_messages_received` | `{seq, sender_player_id}` の配列。message 本文や channel ID は証拠に不要なので保存しない。 |
| `game_end`, `last_seq`, `events_received` | 完走と受信進行の観測。 |
| `initial_sync_seq` | その incarnation が最初に消費した全量 sync の seq。未受理で例外終了した場合だけ null。成功時は .ready と一致する。 |
| `send_errors` | `{type, message, evidence_key}` の配列。空でなければ失敗。 |
| `server_imports`, `production_import_guard` | production client process が禁止 package を import していない証拠。 |
| `client_exit_reason`, `exception_type`, `exception_message` | 異常終了時を含む最終診断。 |

Action Evidence record は次の意味を持つ。

| Field | Shape / meaning |
|---|---|
| `key` | action の意味的な一意 key。下記の単位で作り、token や role ID は含めない。 |
| `kind` | `vote` / `ability` / `co_declare` / `chat`。 |
| `day`, `phase`, `action_generation` | handle から得た値。 |
| `source_seq` | 使用した current handle の根拠となる実際の `game.state_sync` / `player.action_state` の seq。snapshot.last_seq や親の推測値ではない。 |
| `after_resume` | この opportunity が `session.resumed` 受信後か。 |
| `outcome` | `sent` / `deadline_suppressed` / `stale_before_send` / `no_legal_target` / `send_error` のいずれか。 |
| `ability_id` | `ability` の場合だけ handle の値を記録する。分岐条件には使わない。 |

追加の seq は bool ではない正の整数とする。action.source_seq は対応 incarnation の最終 last_seq
以下、marker 内なら stop_seq 以下。欠落・文字列からの暗黙変換・未知 contract は許さない。
`initial_sync_seq: null` は未同期の例外診断だけで、正常完走 / stop marker の合否には使えない。

意味的 key と送信義務は次のとおり。

- vote: `(player_id, day, phase)` ごとに1回。`vote` と `runoff` は別 round とする。
- ability: `(player_id, day, phase, ability_id)` ごとに1回。`uses_remaining != 0` かつ
  `valid_targets` が `target_count` 以上の handle を opportunity とする。
- CO: `(player_id, day)` ごとに1回。最初に列挙された `CoDeclareAction` を使う。
- chat: `(player_id, day)` ごとに1回。最初に列挙された `ChatAction` を使う。

同じ意味的 key の action state が再送されても送信義務を増やさない。新プロセスで Resume した
席については、新しい process incarnation の key として再送信を許す。親は各 incarnation の
record を保存したまま、`sent` がある key の集合を取る。後の record で前の `sent` を上書きしない。
再提示の seq と key の最初の提示 seq を混同しない（Addendum C2〜C5）。

停止マーカーは status と同じ version / evidence_contract、`initial_sync_seq`、`pid`、`player_id`、`kind`、`last_seq`、その時点までの
`action_evidence` を持つ。再起動前プロセスを強制終了しても、停止点までの送信証拠が失われない。

### Parent-side interruption evidence

親は全席の reply から `{seq, player_id, type, day, phase, kind, key[, ability_id]}` を記録する。
構造上選択可能かも提示値だけから記録し、選択可能な同一 key の最小 seq を `opportunity.seq` とする。
sync の再提示は既存 key の最初の seq を置換しないが、初めて見た key は sync からでも登録する。
選択不能の診断記録も残す。送信義務を課す expected と raw 提示記録の区別は C2 / C3 に定義する。

共通検証部品が、client の観測の有無に関係なく次の集合を導出する。

```text
expected = {server ledger の選択可能な意味的 opportunity}
interrupted = {
  opportunity in expected |
  opportunity.player_id == restarted_player and
  stop_marker.last_seq < opportunity.seq <= resume_state_sync.seq
}
required = expected - interrupted
sent = {有効な action_evidence に outcome=sent がある expected key}
required <= sent <= expected
非再起動の各席: sent == expected
```

`stop_marker.last_seq` は controller が停止して送信不能になった論理境界、`resume_state_sync.seq` は
Resume 認証後の全量 sync を client が受理し、再び送信可能になった境界である。OS の `kill()` 呼出し
時刻ではなく、この player-local seq の半開区間を使う。停止マーカー後は receiver が checkpoint可能でも
controller は停止しているため、送信義務上は interruption 中として扱う。

共通検証部品は `0 < len(interrupted) < len(expected_for_restarted_player)` を要求する。
marker に stop_seq 以下の送信成功、新 PID に resume_sync_seq より後に初めて提示された
送信必須機会の成功が各1件以上必要。既存の前後 vote round の証拠もこの集合の投影として維持する。
day / phase 全体や status に載っていない機会を caller が免除指定する API は使わない。

### Rejection classification

分類表は `tests/fixtures/completion_evidence.py` の1箇所だけに置き、driver へ複製しない。

境界競合として許容する理由:

- `action_deadline_passed`: server deadline 到達後に処理された。
- `vote_unavailable`: vote / runoff handle 受信後、処理前に phase が進んだ。
- `action_closed`: night resolution が action 処理より先に確定した。
- `action_unavailable`: chat / CO / ability の列挙受信後、phase または availability が変わった。
- `actor_unavailable`: 列挙受信後、処理前に actor が死亡した。

クライアントまたは driver の欠陥として必ず落とす理由:

- `ability_uses_exhausted`
- `abstention_disabled`
- `abstention_limit_reached`
- `claim_not_allowed`
- `co_limit_reached`
- `invalid_claimed_result`
- `invalid_comment`
- `invalid_message`
- `invalid_report_kind`
- `invalid_target`
- `self_vote_disabled`
- `unknown_ability`
- `unknown_claimed_role`
- `unknown_target`
- `unsupported_action`
- `invalid_action`
- `game_mismatch`
- `unsupported_protocol_version`
- 上記いずれにもない未知理由

`actor_unavailable` は死亡境界を client が authoritative に予測できないため許容する。
`action_closed` も tick と request の直列化順で生じるため許容する。`co_limit_reached` は、同じ席・日で
1回しか送らない契約と最新 handle の列挙に従えば通常は生じないため欠陥側に置く。

許容拒否の単純なゲーム全体件数上限は置かない。代わりに、各意味的 key の送信は1回に有限化し、
全 opportunity の送信試行を要求し、さらに種別ごとの authoritative な受理クォーラムを要求する。
これにより拒否回数は opportunity 数を超えて増えず、全件または大半が拒否されたゲームは緑にならない。
rejection の `seq` 重複は status 不整合として失敗させる。

## Data Flow

1. 親が server reply ledger を全席分記録する。driver は同じ受信列を独立に消費し、
   current snapshot と消費済み seq の対応を確認して typed handle を列挙する（C3）。
2. 意味的 key が未処理なら Action Evidence を作り、受信列挙から target 等を決める。
3. typed send、stale、例外のいずれかを `outcome` に確定する。deadline safety margin は
   opportunity の送信可否には使わない。
4. `action.rejected`、`session.resumed`、gap notice、sync、chat message を各観測配列へ追記する。
5. driver 終了時に status を atomic に書き、親テストが全席分を schema 検証する。
6. 共通検証部品が server ledger と stop / Resume sync 境界から全 action の expected / interrupted を導出する。
7. 親テストが server event bus から `VOTE_SUBMITTED`、`VOTE_RESOLVED`、
   `ACTION_SUBMITTED`、`CO_DECLARED`、`GAME_ENDED` を抽出する。
8. server 由来 required と client の sent を照合し、server の受理集合を独立の quorum で検証する。
9. 拒否分類、coverage、Resume、gap、import guard、exit、cleanup の全条件が満たされた場合だけ成功とする。

投票 round の期待生存者は `VOTE_RESOLVED.payload.tallies.keys()` から取る。死亡イベントから生存状態を
再計算したり、vote rules をテストへ複製したりしない。

## State / Lifecycle

driver は選択可能な current handle にだけ evidence key を作る。選択不能な提示は診断に残し、
送信済み/処理済み key にしない。後で同じ key が選択可能になれば通常の送信義務になる。
evidence key は次の状態を取る（抑止・選択矛盾の2経路は欠陥診断用であり通常は通らない）。

```text
OBSERVED
  ├─ 抑止の誤導入     ─> DEADLINE_SUPPRESSED（FAIL）
  ├─ 選択可能性の矛盾 ─> NO_LEGAL_TARGET（FAIL）
  └─ send 開始
       ├─ 完了         ─> SENT
       ├─ stale        ─> STALE_BEFORE_SEND
       └─ exception    ─> SEND_ERROR
```

`SENT` は WebSocket send 完了であって server 受理ではない。受理は server event または
`chat.message` で別に証明する。終端 outcome は上書きしない。

再起動席は次の lifecycle 証拠をすべて満たす。

```text
first PID sends at fixed stop point
  -> marker/checkpoint seq fixed
  -> controller remains stopped while receiver persists a later vote-round seq
  -> test-only CredentialStore observer reports that save completed
  -> first PID is killed and reaped
  -> actual client-written checkpoint is verified beyond marker seq
  -> only checkpoint last_seq is intentionally restored to marker seq
  -> different PID starts with same credential file
  -> session.resumed accepted for same player_id and requested_last_seq
  -> replay, or SequenceGapDetected -> game.state_sync -> SequenceGapRecovered
  -> at least one SENT action after resume
  -> GAME_ENDED
```

保持内では `session.resumed`、checkpoint より大きい連続 seq の適用、最終 `last_seq` の前進を証拠とし、
gap notice は0件とする。保持外では `SequenceGapDetected` と `SequenceGapRecovered` を各1組要求し、
`received_seq > expected_seq`、`recovered_seq >= received_seq`、`after_gap` の state sync を要求する。

checkpoint の書き戻しは、停止マーカー後も receiver が保存を続ける現行テスト構造で、再起動時の
起点と欠落範囲を決定的に固定するために行う。書き戻すのは `last_seq` だけで、client が Join 時に保存した
connection token は変更しない。書き戻し前に、production の `FileCredentialStore` が保存した
`last_seq` が、親テストの受動記録で確認した次 round の action-state seq 以上、かつ marker seq より
大きいことを必ず assert する。

したがってこの完走シナリオは「client が自ら保存した最新 `last_seq` を無変更のまま crash 後に読み、
その厳密な位置から Resume すること」は証明しない。証明するのは、production が保存した token を使い、
テストが意図的に古くした有効 checkpoint から replay / sync 回復して完走できることまでである。
各 server event の checkpoint 保存は
`tests/test_phase3_1_network_client.py::test_join_sync_and_game_end_persist_every_server_event`、
atomic な file round-trip は `test_file_store_round_trips_and_replaces_atomically`、保存 checkpoint を使う
Resume と sync barrier は `test_resume_uses_checkpoint_and_accepts_sync_barrier_seq_jump` が担保する。

## Main Control Flow

通常完走 driver は全席で同じ規則を使う。vote は全生存席が各 round 1回、ability は利用可能な各
ability opportunity 1回、CO と chat は各席・各日1回を試す。主送信席、先頭N席、ゲーム全体1回の
flag は設けない。target は handle の列挙から決定論的に選び、必要数だけ使う。

再起動位置は乱数にしない。保持内ケースを通常9席完走ケースと統合し、再起動対象が最初の vote
送信を完了して停止マーカーを書いた位置を固定点とする。controller はそこで停止するが receiver は
動かし、親テストは再起動席向けの次の vote / runoff action-state と、それ以上の save 完了通知を
確認してから kill し、reap 後に実 checkpoint を検査する。保持外ケースは同じ barrier 制御を使い、replay history limit を
1にして replay floor が marker seq を越えたことも確認してから kill する。wall-clock sleep の長さで
欠落範囲を推測しない。

再起動席は、Resume 後に開始する少なくとも1つの completed vote / runoff round まで生存させる。
Dummy target 選択では、受信した `valid_targets` に代替候補がある間だけ再起動席を対象から外す。
これは親から渡した player ID と受信列挙だけで行い、role、team、固定 player ID は使わない。必要な
post-Resume round を完了した後は全席と同じ決定論的選択へ戻してよい。

通常完走における各 action 種別の合否は次のとおり。

| Kind | Send-attempt requirement | Authoritative acceptance requirement |
|---|---|---|
| vote | server ledger の expected から再起動席の seq 区間内機会だけを除いた required がすべて sent。completed vote / runoff の expected player 集合が authoritative tallies の集合と一致することも検査。 | 除外に関係なく、各 round で distinct `VOTE_SUBMITTED.voter_player_id` が `ceil(living / 2)` 以上。全 game の下限は各 round の下限の和。 |
| ability | server ledger の expected について `required <= sent <= expected`。 | game 全体で distinct `(day, phase, actor, ability)` が `max(2, ceil(len(expected_ability) / 2))` 以上。expected_ability 自体も2以上。 |
| CO | server ledger の席・日 key について `required <= sent <= expected`。 | day ごとに distinct `(day, player)` が `ceil(len(expected_CO_for_day) / 2)` 以上、game 全体で2件以上。 |
| chat | server ledger の席・日 key について `required <= sent <= expected`。 | status 群で受信した distinct sender が、server expected の distinct sender の半数切上げ以上かつ2送信者以上。 |

どの種別も非再起動の8席は `sent == expected`。受理集合が server expected の範囲外なら失敗し、
分母を sent や required に縮めない。中断免除は送信義務だけで、受理 quorum を引き下げない。

`deadline_suppressed`、`stale_before_send`、`no_legal_target`、`send_error` は `sent` の代わりに
ならない。C4 の区間内 stale は送信成功ではなく免除としてのみ扱う。通常 driver は安全マージンで
required opportunity を抑止せず直ちに送信し、サーバが返した
境界拒否を分類する。`deadline_suppressed` は、抑止処理が再導入された場合に沈黙を成功扱いせず
明確に失敗させるための診断 outcome として予約する。

意図的拒否テストは、同じ CO handle を上限超過まで使って実サーバの `co_limit_reached` を発生させ、
`ActionRejected` が status に残ることだけを検証する。このテストは通常完走 acceptance validator を
通さない。別の pure contract test で、同じ `co_limit_reached` または `invalid_target` を通常 status へ
注入すると validator が必ず失敗することを検証する。

## Failure Handling

- invalid status: version、必須 field、型、seq、outcome が不正なら即失敗し、対象 player / PID を出す。
- missing status: 15秒で失敗し、存在する停止マーカー、process return code、stdout、stderr、status の
  読取り結果をまとめて出す。
- timeout: game end は45秒を維持する。timeout 時は game phase/day、直近 server event、各 process の
  return code、各席の最新 evidence count を出す。
- disconnect: 計画した1プロセス以外の切断、Resume 失敗、違う席への Resume は失敗する。
- exception: `send_errors`、driver exception、`GAME_ENDED` 以外の client exit は失敗する。
- partial failure: 9席のうち1席でも status、game end、coverage、import guard を欠けば全体を失敗させる。
- interruption evidence: server ledger、client-written checkpoint 前進、Resume sync、interruption 前後の
  必須機会のいずれかが無い場合は、除外を推測せず scenario setup failure とする。
- stale work: stale outcome を記録し、同じ handle を再送しない。新 generation の同じ意味的 keyも
  game-wide の1回義務を既に満たしたなら再送しない。ただし再起動 incarnation の復帰後送信証明は別枠。
- cleanup: expected kill 以外の非0 return、未回収 PID、status 書込み失敗を失敗させる。全 subprocess を
  reap してから server を close する。

45秒の game-end timeout と15秒の status timeout は維持する。現在の記録はファイル単位で
約78〜85秒だが失敗回を含むため、安定性の証拠ではなく所要時間の参考だけとする。
通常完走と保持内 replay の統合は維持し、現行の拒否観測シナリオも全員の終了まで検証する。
開始 barrier は実時間15秒で区切り、各ゲームの45秒は RELEASE から測る（再起動後に再設定しない）。
実装後は両環境でファイル単位5回の全結果・所要時間・各 scenario の実測を残す。
1回でも失敗または120秒超過なら契約未達であり、phase / timeout / sleep の調整で回避しない。

失敗診断の最小セットは、test / scenario 名、elapsed、game phase/day、直近 server event type、
player_id、PID、process return code、client exit reason、last_seq、events received、各 outcome 件数、
rejection の action/reason/seq、gap tuple、exception type/message、stdout/stderr である。token、chat 本文、
private payload は出さない。

## Concurrency

- driver の event consumer だけが evidence ledger を更新する。action ごとの無制限 task は作らない。
- send は受信 loop 内で逐次 await するが、1 opportunity 1回に限定する。Network Client 自身の receiver /
  sender 分離は変更しない。
- 親テストが全 subprocess の唯一の owner であり、PID ごとに wait / kill / diagnostics を行う。
- 初回9席は並列生成し、作成済み PID を直ちに owner へ登録する。開始 gate の所有者も親だけとする。
- credential は席ごと、status と marker は process incarnation ごとに別 path とし、並行書込みを避ける。
- kill の時点は server の seq barrier で決め、OS scheduler の sleep 完了順に依存させない。
- `Random(0)` と固定 action 列挙順を維持する。kill timing に乱数を導入しない。
- 2秒 phase duration は維持する。0.25秒 safety 計算の helper を残すかは決めないが、通常完走の
  required opportunity を抑止する gate としては使わない。

## Resolved Questions and Rejected Alternatives

### 1. 拒否理由の分類

**Decision:** 上記5理由だけを boundary とし、それ以外と未知理由を defect にする。総件数上限ではなく、
有限 opportunity と動的受理クォーラムで際限ない拒否を防ぐ。

- 不採用: rejection 合計0件。canonical に無く、authoritative server の正当な境界応答を欠陥扱いする。
- 不採用: `actor_unavailable` / `action_closed` を defect にする。死亡・tick resolution と送信処理の順序は client が確定できない。
- 不採用: `co_limit_reached` を boundary にする。席・日1回と現行列挙を守れば通常経路では発生しない。
- 不採用: 全 boundary rejection を無制限に許し、進行だけ見る。全送信が拒否されても緑になる。

### 2. 何席が何を何回送るか

**Decision:** server ledger が expected を決め、4種すべてで required への送信を要求する。
vote は全生存席・全 round、ability は各機会、CO / chat は席・日が単位。再起動席の seq 区間だけを
免除し、受理は免除前の server-side 動的クォーラムを満たす。

- 不採用: 1件の event type 存在確認。1席だけでも通り、9 client の操作経路を証明しない。
- 不採用: 固定9件や固定N件。死亡と runoff により期待数が変わり、preset の規則をテストへ埋め込む。
- 不採用: 全送信が必ず受理されること。境界競合を再び rejection 0 の別表現にしてしまう。

### 3. 再起動席の回復証明

**Decision:** fixed vote-send barrier、別PID、同じ credential file と player_id、`session.resumed`、
replay または gap+sync、seq 前進、Resume 後 `sent`、全員 `GAME_ENDED` をすべて要求する。

- 不採用: `resumed: true` だけ。認証後に何も受信・送信しなくても成立する。
- 不採用: `game.state_sync` だけ。通常 Join の sync と区別できず、保持内 replay の証拠にもならない。
- 不採用: kill timing の乱数化。5回検証で失敗点が変わり、再現と診断を弱める。
- 不採用: credential 作成直後の即 kill。action と missed seq の双方を回復した証拠にならない。

### 4. フェーズ境界競合の低減

**Decision:** 送信機会を削らず、boundary rejection 分類を主とする。通常完走では0.25秒 margin による
required opportunity の抑止を行わない。抑止が生じたら status 上の明確な失敗にする。2秒 phase は維持する。

- 不採用: margin 内なら黙って opportunity を捨てる。送らなかった席を成功扱いする。
- 不採用: phase duration を延ばす。実際の短い締切競合を覆わず、既存安定性の比較条件も変える。
- 不採用: margin 抑止を成功扱いする。拒否を「送らない」に置換し、各席の送信証拠を再び失う。

### 5. R-119 再発防止

**Decision:** seq barrier による固定停止、意味的 key の有限送信、outcome の明示、incarnation 別 artifact、
全 PID reap、最小診断、45秒/15秒 timeout、ファイル単位5回を契約にする。正常実行時間には統合で余裕を作る。

- 不採用: 主テストだけを `-k` で5回。連続 subprocess 起動とテスト間 cleanup の競合を踏まない。
- 不採用: sleep で kill 時点を決める。scheduler 負荷により欠落範囲が変わる。
- 不採用: timeout の単純延長。不安定原因を隠し、Reviewer の約120秒上限を超える。
- 不採用: phase を1秒へ戻す。R-119 で観測された0.1〜0.24秒 callback 遅延に対する余裕を失う。

想定不安定要因と明確な失敗への変換は次のとおり。

| Cause | Explicit failure evidence |
|---|---|
| scheduler 遅延 / phase boundary | `deadline_suppressed`、`stale_before_send`、boundary rejection、round coverage 不足を席・round付きで出す。 |
| duplicate action-state | 意味的 key で重複を畳み、同一 key の複数終端 outcome は schema failure。 |
| subprocess resource / exit 255 | PID、return code、stdout/stderr、最終 status を必ず関連付ける。 |
| status path race / overwrite | incarnation 別 path と atomic final write。missing / malformed は対象 path 付きで失敗。 |
| replay timing | checkpoint と replay floor / retained seq の barrier を parent が確認する。 |
| cleanup leak | 各 test 終了時に開始した全 PID の return code を確認し reap する。 |
| deterministic game drift | fixed seed、2秒 duration、列挙順選択を維持し、server expected の fixture integrity を検査する。 |

### 6. 後続完走テストへの再利用

**Decision:** rejection 分類、status 共通 envelope、Action Evidence outcome、動的クォーラム、Resume / gap の
証拠検証だけを `tests/fixtures/completion_evidence.py` で共有する。3.2〜3.5 / Phase 5 の driver は共通
record を出せるが、各 Phase 固有の完了条件と action 選択は自身のテストに置く。

- 不採用: Phase 3.1 driver 全体を共通 driver にする。World State、Brain、LLM queue を通す後続 Phase の責務を迂回する。
- 不採用: 共有しない。同じ rejection 0、固定件数、Resume bool だけの漂流を繰り返す。
- 不採用: production package に evidence helper を置く。テスト契約を製品 API に混入させる。

### 7. R-20260902-128 / Addendum C — interruption 中の送信義務

**Decision:** C2 により vote 限定の式を全 action の `stop_seq < opportunity.seq <= resume_sync_seq`
へ一般化する。共通検証部品が server ledger から導出し、再起動席の全機会に対する0件・全件免除を
禁止する。非再起動席、サーバ受理クォーラム、interruption 外の再起動席には例外を設けない。

- 不採用: 再起動を含む scenario の全 round を coverage 対象外にする。切断を口実に送信証拠全体が消える。
- 不採用: kill が発生した day 全体を除外する。実際の送信不能区間より広く、Resume 後の欠落も隠す。
- 不採用: status に opportunity が無い round を自動除外する。driver の観測漏れと正当な切断を区別できない。
- 不採用: OS の kill / process start 時刻で比較する。server action-state と時計空間が異なり、境界が再現不能になる。

### 8. R-20260902-129 — checkpoint 書き戻しの証明範囲

**Decision:** token を保持したまま `last_seq` だけを marker へ戻し、決定的な stale checkpoint を作る。
書き戻し前に client-written `last_seq` が次 round seq 以上かつ marker より大きいことを必須 assert にする。
最新 checkpoint の無変更 crash-restart は、この完走シナリオの証明範囲外と明記する。

- 不採用: 書き戻しをせず receiver の最終 checkpoint を使う。欠落量が kill scheduling に依存し、保持内 /
  保持外 recovery を決定的に踏めない。
- 不採用: 書き戻し前の値を検査しない。production save が進んでいなくても、テストが作った値だけで Resume
  証拠を成立させてしまう。
- 不採用: token もテストで再生成・書換えする。保存済み connection token による同一席 Resume の証拠を失う。

## Explicitly Out of Scope

- `ai_client.network` / `ai_client.world` の実装変更
- server、protocol schema、ゲームコア、content、ROADMAP、TEST_POLICY の変更
- Phase 3.2 の World State 完走条件、3.3 Brain、3.4 発言生成、3.5 選択方針
- Phase 4 の LLM backend / prompt / structured output、Phase 5 の共有LLM queue 実装
- server の可否判定、role、team、channel semantics の test driver への複製
- action request と rejection の1対1 correlation ID、server acknowledgement の新設
- CI、実行環境、`check_docs.py`、`ai_status.py` の変更
- rejection を減らすための production client / server tuning

## Acceptance Criteria

ROADMAP §3.1 の7項目へ次の証拠を割り当てる。

| Completion condition | Required evidence |
|---|---|
| 9別プロセスが tick のみで完走 | 9つの異なる child PID、全 final status の `GAME_ENDED`、server `GAME_ENDED`、manual `advance_phase` / `resolve_votes` 未呼出し、4種 action coverage。 |
| 同じ席へ Resume し取りこぼし回復 | fixed stop marker、書き戻し前の client checkpoint 前進、別PID、同じ credential path / token / `player_id`、一致する `requested_last_seq`、保持内 replay または保持外 sync、seq 前進、Resume 後 action `sent`、全員完走。 |
| seq 欠番検出が観測可能 | 保持外 scenario の対応する `gap_events` 1組と `after_gap` sync。expected / received / recovered の大小関係も成立。 |
| major mismatch を拒否 | `test_protocol_major_mismatch_fails_before_state_or_checkpoint_update` が state / checkpoint 更新前の failure を固定。 |
| `action.rejected` を握り潰さない | 実サーバの意図的 `co_limit_reached` が status に action/reason/seq 付きで現れる。通常 validator では同理由が defect として失敗する。 |
| server package 非依存 | 全 child status で `production_import_guard == true` かつ `server_imports == []`。parent test の server import は対象外。 |
| LLM 無しで完走 | LLM fixture / process / endpoint / model 設定を一切用いず、ファイル単位テストが完走する。 |

加えて以下をすべて満たす。

- `VOTE_SUBMITTED` 1件だけでは、最初の9席 vote round の `ceil(9/2)` 下限を満たせず失敗する。
- 再起動席の Resume 後 `sent` action が0件なら失敗する。
- interruption により除外する機会が0件または再起動席の全 expected 機会なら失敗する。
- 再起動席以外は全 action 種別で server expected との厳密な送信集合一致を満たし、再起動席も
  interruption 外の必須機会で `sent` を満たす。status から機会を消しても expected は減らない。
- defect 理由または未知理由を通常 status に1件注入すると失敗する。
- boundary rejection が存在しても、全送信義務と受理クォーラムを満たせば成功する。
- status に token、role 固有分岐、固定 channel ID、内部死因、chat 本文を含めない。
- `tests/test_phase3_1_completion.py` が Reviewer 環境と Windows のそれぞれで、同一 command で
  5回連続成功し、各回120秒以内である。

## Required Tests

### Contract unit tests

- 分類表が実コードの `PLAYER_ACTION_REJECTION_REASONS` と session 固有3理由を過不足なく覆う。
- boundary 5理由は許容、defect 全理由と未知理由は拒否される。
- defect を1件混ぜた status、重複 rejection seq、不正 schema version、未知 outcome が失敗する。
- `ceil(expected / 2)` の境界、distinct player 集計、重複 server event が水増しにならないこと。
- 9席の round に `VOTE_SUBMITTED` 1件だけを与えると失敗する。
- server ledger と stop / resume seq から全 action の interrupted が一意に導出され、区間端の
  `opportunity.seq == stop_seq` は含まず、`opportunity.seq == resume_sync_seq` は含む。
- interrupted が0件、全件、または ledger に無い機会を caller が任意指定すると失敗する。

### Separate-process completion

- 9 child PID、全席 GAME_ENDED、server tick only、import guard、LLM 無し。
- 非再起動席は各 vote / runoff round、再起動席は interruption 外の各生存 round で `sent` を記録し、
  server accepted distinct voter は除外に関係なく全 round で動的下限以上。
- interrupted が全 action 集合で1件以上・全件未満で、interruption 前後に再起動席の必須機会が各1件ある。
  既存の前後 completed vote round の証拠も維持する。
- 再起動席が1 round をまたいでも成功し、同じ欠落を interruption 外へ移すと席・round 付きで失敗する。
- ability / CO / chat の server expected、required、sent、accepted が上記集合関係と動的下限を満たす。
- boundary rejection を含む synthetic completion evidence が coverage を満たす場合は成功する。
- `deadline_suppressed` または `stale_before_send` で送信義務が欠けると、席・day・phase を示して失敗する。

### Resume and recovery

- 保持内: fixed vote stop、次 vote round の reply と client checkpoint 前進、marker への `last_seq` 書き戻し、
  missed retained seq、別PID Resume、gap 0、seq 前進、Resume 後の必須 vote round 送信、完走。
- 保持外: 次 vote round と client checkpoint 前進、replay floor 超過、`last_seq` 書き戻し、GapDetected /
  sync / GapRecovered の1組、Resume 後の必須 vote round 送信、完走。
- `resumed == true` でも Resume 後送信0、requested checkpoint 不一致、別 player_id、未回復 gap は失敗する。
- 書き戻し前の credential が marker seq 以下、次 round seq 未満、または token が書き戻し前後で変われば失敗する。

### Rejection observability and diagnostics

- 実サーバで意図的 `co_limit_reached` を発生させ、ActionRejected の action / reason / seq が status に残る。
- 同じ rejection を通常 completion validator へ入れると失敗する。
- driver 例外注入で非0終了し、exception、last_seq、event / send counts が status と failure message に残る。
- status 書込み不能または欠落時、stderr と path を含む診断になる。

### Stability

- `python -m pytest -q tests/test_phase3_1_completion.py` を `-k` / `-m` なしで、Reviewer 環境と Windows
  のそれぞれで5回連続実行する。
- 各回の pass / fail、file elapsed、各 completion scenario の game-end elapsed を記録する。
- 各回成功、各回120秒以内、前回 test の PID / socket / artifact が次回へ残らないことを確認する。
- `python scripts/check_docs.py` を実行する。

## Deliberately Not Decided

- shared module の private helper 名、test method 名、内部 dataclass の採否は決めない。
- schema-valid な chat / CO の具体的文言は固定かつ非空であれば決めない。証拠には本文を保存しない。
- 有効 target が複数ある場合の先頭選択と seeded deterministic 選択のどちらを使うかは、受信列挙だけを
  使い全5回で再現可能なら決めない。module-level random は使わない。
- 診断メッセージの文章表現と field の表示順は、最小診断セットを失わない限り決めない。
- protocol reply の受動 recorder を mock、wrapper、test-only sink のどれで構成するかは、server の挙動を
  変えず全 reply を欠落なく記録できる限り決めない。除外式と記録 field は変更してはならない。

## Addendum C — 開始 barrier と server opportunity 契約

### C1. 開始時刻・readiness・並列起動

**Decision:** 親テストの clock gate は1ゲームに1つ、RELEASE は一度だけとする。
`tests/test_phase3_1_completion.py` の setup が次の3か所へ同じ callable を渡す。

- `GameState.create_from_preset(..., started_at=gate())`
- `TickDriver(registry, clock=gate)` を `WebSocketGameServer(..., ticker=...)` へ注入
- `SessionManager(registry, clock=gate)`

最後の注入も必要である。現行 `session.py` は envelope.timestamp と ability 要求の時刻を
自分の clock から取得するため、ticker だけを gate すると同じ deadline と比較できなくなる。
既存の注入 API を使うだけで、production コードの変更・monkeypatch は不要。

gate の論理値は、RELEASE 前は整数 `t0=0`、RELEASE 後は
`t0 + floor((monotonic_ns_now - release_monotonic_ns) / 1_000_000_000)` とする。
core の `timestamp()` が整数を要求するため float は返さない。release 時刻は親の実 monotonic clock
から記録し、clock 読出しのたびに起点を更新しない。timeout / diagnostics は gate ではなく実時計で測る。
tick interval 0.02秒、phase 2秒、silence 0秒、seed は維持する。RELEASE 後の再停止・巻戻し・加速は禁止。

開始フロー:

1. gate を未解放にして game / session / ticker を組み立て、passive recorder を設置してから client を起動する。
2. 全9席の subprocess を `asyncio.gather` 相当で並列生成する。seat ごとに作成完了した PID を即登録し、
   成功した8プロセスが9番目の失敗で owner 不明にならないようにする。
3. driver に `--ready <incarnation固有path>` を渡す。driver は初回の認証済み全量 sync を event stream から
   消費し、sync 記録と初期機会の bookkeeping を済ませた時点で `.ready` を atomic に一度だけ書く。
   socket 接続、credential の存在、ACK のみでは ready にしない。既存 `session.ready` request とも別物。
4. `.ready` は JSON `{schema_version, evidence_contract, pid, player_id, sync_seq}`。
   親は9件の内容、席と PID の対応、全 PID の生存、記録された初回 sync の ledger header との一致を確認する。
   `sync_seq` は driver の `initial_sync_seq` と同じ値。空ファイルや古い incarnation の ready は受理しない。
5. 全件確認後だけ RELEASE する。待機中に ticker は動いても gate が固定なので phase が進まない。
   初期 phase/day が維持され、game_result が無いことも確認する。ここから45秒の game-end deadline を開始する。

初回 readiness の15秒は並列生成開始から測り、プロセスごとの15秒を足し合わせない。
失敗・未達時は RELEASE せず diagnostics と cleanup へ進む。再起動 incarnation にも別 ready path を
与えるが、ゲームの gate を再び閉じることはしない。Resume の境界は ready 時刻ではなく実 sync.seq。
意図的拒否シナリオも同じ9席 barrier を通す。driver 例外注入の単独プロセステストは RELEASE 不要。

不採用: `.ready` だけ追加する案は待機中に ticker が進む。逐次起動・長い phase・待機 sleep・
tick 停止/手動 phase 進行では今回の決定を満たさない。`manual_advance.assert_not_called()` は維持する。

### C2. server ledger の単位と免除の導出

**Decision:** 現行 `round_replies` recorder を、全席・4種の機会を記録する受動 recorder へ拡張する。
`session._reply` の元処理を一度呼んで得た reply を変更せず返す。実際の接続に提示する reply だけを
観測し、テストが `get_state_sync` / `_reply` を追加呼出しして機会を生成してはならない。
生成後の schema / 配送失敗はシナリオ失敗であり、失敗した提示を後から ledger から消して救済しない。
これは「サーバが提示した」証拠であって「クライアントが読んだ」証拠ではない。

親は reply header に加え、提示ごとの occurrence と意味的な expected の2段を保持する。

| Record | Fields / meaning |
|---|---|
| Reply header | `{player_id, seq, type, day, phase}`。sync の actions が空でも記録する。day/phase の無い envelope は null。ACK / sync の照合用に秘密を含まない header も残す。 |
| Offer occurrence | `{player_id, seq, type, day, phase, kind, key, selectable[, ability_id]}`。同じ reply に複数種あれば別件。同一 seq の同じ意味的 key は1件に畳む。 |
| Expected opportunity | `(player_id, semantic key)` ごとに最初の selectable occurrence。`opportunity.seq` はこの最小 seq で固定する。 |

`game.state_sync.payload.action_state` と `player.action_state.payload` は同じ正規化を通す。
`selectable` は受信列挙の構造だけで決め、role / rule / current game state は読まない。

- vote: 有効対象が必要数ある、または列挙で棄権が許可されている。
- ability: `uses_remaining != 0` かつ `len(valid_targets) >= target_count`。
- co_declare: `claimed_role_ids` が空でない。
- chat: `ChatAction` が列挙されている。
- co_report は今回の4種に含めない。既存の意味的 key（vote/ability は phase 単位、CO/chat は席・日単位）を維持する。

選択不能の提示は診断 ledger に残すが expected に入れない。client が `no_legal_target` と書いた
ことを理由に expected を減らしてはならない。後の提示で選択可能になった key は、その最初の selectable seq
で expected に入る。channel / role / target の ID は正規化に必要な間だけ参照し、証拠やログに保存しない。

再送・replay は元 seq、Resume sync は新 seq で occurrence として照合するが、既存 expected の seq は
動かさない。sync に初めて出た key だけは sync.seq が opportunity.seq になる。
これにより、stop 前の未送信義務を Resume sync の seq へ付け替えて免除することを防ぐ。

`stop_seq < opportunity.seq <= resume_sync_seq` を再起動席だけに適用する。実際の source.seq が
異なる席の seq と偶然一致しても関係ない。vote の round 除外はこの結果の vote 投影であり、
別計算しない。旧 `derive_interrupted_rounds` 等を他の既存利用者のため残しても、3.1 の合否には使用しない。

不採用: day / phase ごとの一括免除、最後の再提示 seq の採用、client の受信漏れからの除外。
いずれも実際の中断より広い範囲の送信欠落を隠す。

### C3. expected と actual の独立性・source_seq の確定

**Decision:** 共通検証部品は server ledger、全 incarnation の client records、停止 / Resume 境界を
別入力として受け取り、expected / interrupted / required / sent と差集合を返す。expected と免除を
caller が恣意的に渡す API は作らない。正規化と集合演算は `completion_evidence.py` に一元化する。

driver は NetworkClient の snapshot が consumer より先行し得ることを前提にする。
受信した全 ServerEvent の消費済み seq と、最後に消費した action-state envelope の seq / payload を
保持する。送信試行は **CONNECTED かつ消費済み seq == snapshot.last_seq** の時だけ行い、
直近の消費済み action_state と snapshot.action_state の対応を確認する。
この確認と handle の取得の間には await を挟まず、`source_seq` はその action-state envelope の seq とする。
handle は常に production snapshot から取得し、raw payload から自作しない。

送信の契機を action-state event だけに限定しない。後続の非 action event や CONNECTED notice を
消費して追いついた場合にも未処理の current handle を試す。追いつく前に obsolete になった機会は
削除や架空の sent で埋めず、server expected との差として検出する。Resume replay を読む際も同じ条件を
用い、過去の handle を送らない。Network の受信 loop / state API は変更しない。

actual の各 record について、key の構成・kind/day/phase/ability と、同じ player / source_seq の
server occurrence の対応を確認する。存在しない source_seq、対応しない key、選択不能 occurrence に
対する sent は FAIL。先に expected と交差を取って余分な送信を隠してはならない。
同じ process の key は1 terminal outcome、再起動前後の同 key は別 record のまま検証する。
その後で、いずれかの有効 record が sent の key を集合にする。

必須判定は `required <= sent <= expected`。8席は個別に厳密一致。expected key を client から
丸ごと消しても FAIL になる。actual のない interrupted key は送信免除としてのみ扱う。
全4種について Main Control Flow 表の quorum 分母を server expected から作り、中断・stale・
no_legal_target・実 sent 件数で減らさない。vote の tallies 集合、CO の PHASE_STARTED による day 復元、
受信 chat の distinct sender を用いる既存受理証拠は維持する。

不採用: `eligible = action_evidence`、snapshot.last_seq を source_seq として保存する案。
前者は未観測機会が分母から消え、後者は無関係な後続イベントによって中断区間を取り違える。

### C4 / C5. stale と中断前後の証拠

**Decision:** `stale_before_send` は成功ではない。許容するのは再起動席の interrupted key で、
その attempt の source_seq も `(stop_seq, resume_sync_seq]` 内にある場合だけ。
key が免除でも source_seq が window 外の stale は FAIL。stop 前の required key は、window 内に
再提示されて stale になっただけでは免除にならない。別 incarnation の sent で stale 違反を隠さない。
`send_error` / driver exception / defect rejection は window 内でも FAIL。
`deadline_suppressed` は通常 driver に再導入せず FAIL。`no_legal_target` は選択可能な handle に対する
矛盾の診断であり FAIL。構造上選択不能な提示は ledger に残すが attempt record を作らず、
処理済み key にもしない。後から選択可能になった機会を失わせない。

共通 validator は次をすべて要求する。

- `stop_seq < resume_sync_seq`、同じ認証席、別 PID、要求 checkpoint と marker の一致。
- `0 < |interrupted| < |expected_for_restarted_player|`。action 種別ごとに免除を作る必要はないが、
  vote 以外でも同じ式が働くことを contract unit test で固定する。
- 停止 marker 内に、`source_seq <= stop_seq` の実 sent が1件以上。
- 新 PID の status 内に、最初の提示 seq と source_seq がともに resume_sync_seq より大きい
  required key の実 sent が1件以上、かつ `after_resume=True`。
- 既存の固定 vote 停止シナリオでは、停止前 / 中断内 / Resume 後の completed vote round の証拠を
  維持する。vote 以外の免除を追加してこの既存回復証明を弱めない。

`sent → interruption → Resume sync → sent` の鎖は、marker / ledger / 新 PID status を相互照合する。
単なる resumed bool、または Resume sync の中にある免除機会の sent だけでは最後の条件を満たさない。
再起動席を role で選ばず、現在の固定席・seed・列挙による停止位置を維持する。シナリオが必要な
前後機会を作れない場合は setup failure とし、免除拡大や停止タイミングの乱数化で救済しない。

固定 sleep を使わない checkpoint barrier:

- marker.last_seq は controller が送信成功後に停止した時点の**消費済み ServerEvent の seq**で固定する。
  send await 中に先行した Network snapshot.last_seq に付け替えない。未消費の機会が stop より前へ
  紛れ込むことを防ぐ論理 cursor であり、保存済み seq 以下であることを検査する。
- driver 内に test-only CredentialStore observer を置き、`FileCredentialStore` の load/save へそのまま委譲する。
  delegate.save が正常に返った後だけ `{kind: "checkpoint_saved", seq}` の観測を親へ出す。
  token・JSON内容は出さず、保存失敗を握り潰さない。protocol / production store は変更しない。
- 親は次 vote 機会の seq と、その seq 以上の保存完了通知を確認する。retention / replay floor の
  既存 barrier も確認してから kill / reap する。現行の `sleep(0.2)` による保存完了推測は除去する。
- reap 後だけ credential file を読み、実ファイルの last_seq が通知済み seq 以上かつ次 round seq 以上、
  stop_seq より大きいことを検査してから last_seq を rewind する。token はそのままで前後一致を検査。
  稼働中の credential を親が繰り返し開かず、Windows の atomic replace と競合させない。

この試験は依然として「意図的に古くした checkpoint からの回復」であり、無変更 crash checkpoint の
検証ではない。R-129 の証明範囲と production save / file round-trip の単体テストへの分担は変えない。

不採用: stale を sent と同等に数える、再起動席の全機会を除外する、古い sent を新 PID の復帰後証拠にする。
いずれも送信を失った client が緑になる。

### C6. 観測・timeout diagnostics・ownership

**Decision:** 親の `_wait_for` に副作用のない `diagnostics` callable を渡す境界を設ける。
全待機箇所が同じ診断 snapshot を利用し、timeout 以外の予期しない child exit も即座に同じ診断で失敗させる。
diagnostics は game を進めず、新規 sync や action を発生させず、長い I/O を待たない。

稼働中の情報は driver stdout の専用 JSON Lines から親の drain task が取得する。
各行は `{observation_version: "phase3.1-observation/v1", pid, player_id, kind, seq}`。
`kind` は `checkpoint_saved` または `progress`。progress.seq は消費済み ServerEvent の last_seq で、
sync / action-state / stop 境界で報告する。未認証時の player_id は null、seq は0を許す。
checkpoint_saved.seq と progress.seq を混同せず、各系列の単調性を検査する。
callback は同一 event loop で1行を分割せず出力し flush する。stdout に token を含む起動引数を出さない。

親は生成直後から stdout / stderr を継続 drain し、pipe 満杯で client が止まることを防ぐ。
stdout の構造化通知は PID ごとの最新値・件数を保持し、診断 tail は各 stream 64KiB まで。
全 action 証拠は final status と停止 marker に残し、診断 tail の切捨てで expected / sent を減らさない。
壊れた観測行、別 PID、seq 後退、save 通知の欠落は明示失敗。診断文字列も秘密を除去する。

timeout / early exit の最小診断:

- scenario 名、待機対象、当該 wait とゲーム RELEASE からの実 elapsed、limit。
- gate 状態、ready 有効数/期待数、未 ready 席、game day/phase/result、直近 server event type。
- 各 incarnation の player_id、PID、running/exited、return code、ready / status path と読取状態。
- 各席の最後に報告された consumed seq / saved seq（未観測は null と明記）、stop_seq、resume_sync_seq。
- 直近32件の sanitized offer occurrence、各 kind の expected / interrupted / required / sent 件数、
  missing / unexpected key。未確定の集合は未確定と表示し、0件に置き換えない。
- existing status の client exit reason、outcome 件数、拒否 reason/seq、gap、exception と bounded stderr/stdout tail。

diagnostics 自体の読取エラーは項目単位で記録し、元の timeout を上書きしない。cleanup の失敗も
一次原因に併記する。stdin/pipe の通知はゲームへの制御命令ではない。
PID、起動 task、pipe reader、server、passive recorder の復元は親が所有し、部分起動失敗・cancel 時も
生成済み全 PID を回収する。stdout reader と `communicate()` を同時に同じ pipe の reader にしない。
status / marker / ready は incarnation 別 path、credential だけが同じ席の再起動前後で共有される。

待機上限は startup/ready 15秒、RELEASE→game end 45秒、game end→status 15秒、既存 cleanup 5秒を
維持する。停止 marker / 次 round / retention の既存15秒または10秒の wait は残り game deadline で
切り詰め、wait ごとに game deadline を延ばさない。既存0.02秒の条件 polling は継続可だが、
条件の代用となる固定 sleep や backoff 増量を追加しない。

不採用: timeout 文言だけ、稼働中 credential の polling、失敗後に全 stdout を初めて読む案。
原因を失うか、Windows の書込競合・pipe の詰まりをテスト自体が作る。

### C7. completion marker と検証運用

**Decision:** marker は重い別プロセス試験の分類だけであり、既定の collection / 実行集合は変えない。
`pyproject.toml` の markers に `completion: separate-process completion and driver integration tests` を登録する。
`addopts = -m ...`、skip、環境依存の除外を入れない。

`tests/conftest.py` は次の既存7 test node（parameter variation があればその全件）へ marker を追加する。
collection 上の module と test 名で一致させ、単に `_completion.py` 全体を重い扱いにしない。

| Module | completion 対象 test |
|---|---|
| `tests/test_phase3_1_completion.py` | `test_nine_protocol_clients_complete_and_one_resumes`、`test_separate_process_action_stop_resumes_outside_retention_and_recovers_gap`、`test_separate_process_rejection_and_production_import_guard`、`test_driver_failure_writes_diagnostics` |
| `tests/test_phase2_completion.py` | `test_separate_process_clients_complete_game_using_only_protocol_actions_and_server_ticks` |
| `tests/test_phase3_2_completion.py` | `test_nine_world_clients_recover_from_in_retention_replay`、`test_nine_world_clients_recover_from_out_of_retention_sync` |

Phase 2 / 3.2 は実行分類だけを付け、テスト本体・完走条件・fixture は変えない。
3.1 の分類/quorum/ledger/clock/diagnostics の単体テスト、3.2 の semantic guard、Phase 1 の core-only
完走は未 marker とし高速側に残す。3.1 だけを分類すると3.2等の重い試験が高速側に残るため、この範囲を含める。

- `python -m pytest -q -m "not completion"`: 高速側（数秒級を目標に実測する）。
- `python -m pytest -q -m completion`: 重い別プロセス試験だけ。
- `python -m pytest -q`: 両者の和集合。従来全件と追加テストをすべて含む。

collection で両集合が交わらず和集合が default と等しいこと、上記7件が重い側に存在すること、
未登録 marker warning が無いことを確認する。時間短縮を理由にテスト削除・条件緩和・既定除外をしない。
高速側に重い試験が残る場合は計測結果を報告し、本件と無関係な最適化へ進まない。

不採用: ファイル丸ごとの marker と既定からの除外。前者は高速な契約単体テストも消し、後者は
通常の全件実行で回帰を見逃す。3.2 の設計・実装まで一緒に改訂することもしない。

### Acceptance / Required Tests（C1〜C7 の追加差分）

| Area | 最低限固定するテスト |
|---|---|
| C1 clock | HOLD 中に実時計を進めても gate / phase が不変、RELEASE が t0 で連続、以後整数秒で単調、二重 RELEASE 拒否。started_at / ticker / session が同じ clock を使い、初回 deadline と envelope.timestamp が同じ時間軸。 |
| C1 readiness | 最後の1席を制御して未 ready の間は RELEASE しない。初回 sync を消費しない、誤 PID/席、古い ready、部分生成失敗なら15秒以内に診断・全 child 回収。通常/拒否の両 scenario が同じ barrier を使う。 |
| C2 ledger | 全4種、sync-only 初提示、複数 action / channel、選択不能→可能、再提示/replay を検証。同 key の first seq が後の sync で動かない。各席の seq は独立。 |
| C2/C4 endpoints | stop=30/resume=35 として30は非免除、31/35は免除、36は非免除。vote/ability/CO/chat の各種で同じ結果。他席の31/35は非免除。 |
| C3 independence | 非再起動席の CO/chat/ability record を丸ごと削除して FAIL。expected に無い sent、偽 source_seq、key 不一致、選択可能なのに no_legal_target も FAIL。client 証拠を消して quorum 分母を縮められない。 |
| C3 consumer lag | Network snapshot が consumer より先行する fake source で、最新 handle を古い event.seq に結び付けない。非 action event を消費して追いついた後にも送信機会を処理し、replay の古い handle は送らない。 |
| C4 stale | 中断 key / source とも区間内だけ許容、source が36へ移れば FAIL。stop 前の key を Resume sync に再提示しても免除されない。旧 PID の sent と新 PID の stale を両方保存し、違反が隠れない。 |
| C5 bounds | 免除0件/全件、前の sent 無し、後の sent 無し、後の sent が免除 key のみなら FAIL。有限 proper subset と前後 required sent の正例。 |
| C5 storage | save 完了前は通知しない、失敗は通知しない。通知後の kill/reap と実 credential の前進・token 不変を照合。固定 sleep や稼働中 credential 読取り無し。 |
| C6 diagnostics | ready / marker / retention / game / status 待ちの timeout と early exit で最小項目が出る。malformed progress、壊れた status、diagnostics 自身の失敗、pipe 出力増大、部分起動 cancel でも一次原因と回収結果を残す。token/private payload を出さない。 |
| C7 collection | default = completion と not completion の互いに素な和、既存7件の重い分類、pure contract の高速分類、marker warning 0。 |

単体テストは clock / subprocess の fake と純粋な ledger を使い、安定化を証明するために実 sleep を
増やさない。実サーバの2種類の再起動完走・拒否観測では、ready9件、server tick only、全席の expected /
required / sent、前後証拠、受理 quorum、import guard、GAME_ENDED を一緒に検査する。

最終受入れは変更しない。Reviewer 環境と Windows でそれぞれ
`python -m pytest -q tests/test_phase3_1_completion.py` を5回連続、全件成功かつ各回120秒以内。
7という旧テスト数を固定しない。追加 contract tests を含む収集数と生の最終行、exit code、各回 elapsed、
環境/commit、各 scenario の startup と RELEASE→game-end 時間を記録する。失敗回を捨てて成功回だけ
5件集めない。marker 選択による部分実行をこの安定性検証の代わりにしない。
全 suite と `python scripts/check_docs.py` も実装後に実行する。

### 改訂範囲・引継ぎ

C1〜C7 はすべて本設計へ反映し、方針の未決定事項はない。private helper 名、内部 record の Python
表現、診断の整形は実装者に委ねる。既存の受理 quorum・拒否分類、R-129 の checkpoint 証明範囲、
承認済み Network Addendum B は変更しない。C7 が明示した marker 登録を除き `tests/` 外の実装変更はない。
本成果物はこの既存 `_DESIGN.md` 1枚のみ。Claude の承認前に実装へ渡さず、R-02 を解決済みにはしない。
