Status: APPROVED — Reviewer / Claude 承認（2026-09-02、R-20260902-128 / 129 反映後）。R-128 の除外は `stop_marker.last_seq < round_start_seq <= resume_state_sync.seq` の player-local seq 半開区間として機械的に定義され、0件・全件を禁じる下限と、非再起動席の厳密一致・受理クォーラムの維持まで書かれている。R-129 は書き戻しの理由と**証明しなくなるもの**を明記し、書き戻し前に client-written `last_seq` を assert する判断まで決めた。参照された4つのテスト名は `tests/test_phase3_1_network_client.py` に実在することを確認した（`test_join_sync_and_game_end_persist_every_server_event` / `test_file_store_round_trips_and_replaces_atomically` / `test_resume_uses_checkpoint_and_accepts_sync_barrier_seq_jump` / `test_protocol_major_mismatch_fails_before_state_or_checkpoint_update`）。R-128 / 129 以外の変更は無い。実装レビューでの確認事項: 再起動席が3 round 以上生存する席であること（下限3条件を満たせない席を選ぶと scenario setup failure になる）。

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

## Files / Modules

| File | Change | Responsibility |
|---|---|---|
| `tests/fixtures/completion_evidence.py` | 新規 | status の共通語彙、拒否理由の唯一の分類表、status 検証、動的クォーラム計算を提供する。`server` と `ai_client` を import しない。 |
| `tests/fixtures/phase3_1_network_client_process.py` | 変更 | 受信列挙から Dummy 行動を選び、action opportunity と結果、Resume / gap / chat の観測を status に書く。合否分類は行わない。 |
| `tests/test_phase3_1_completion.py` | 変更 | 9プロセスと実サーバを所有し、status と authoritative server event を共通検証部品へ渡して合否を決める。切断位置と replay 条件もここで制御する。 |
| `tests/test_phase3_1_network_client.py` | 原則変更なし | major version 不一致、`ActionRejected` 通知、seq 回復など Network Client 単体の既存証拠を引き続き担う。契約名の参照修正だけが必要な場合を除き変更しない。 |

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
- 再起動席向けに生成された protocol reply を受動記録し、各 vote / runoff round で最初の
  `player.action_state` が持つ player-local `seq` を round 開始境界として保持する。
- driver の送信試行集合とサーバの受理集合を突き合わせ、動的下限を満たすか判定する。
- 停止境界から Resume sync 完了までに開始した round だけを、再起動席の送信義務から除外する。
  他席の送信義務とサーバ受理クォーラムは除外しない。
- 意図的な拒否観測テストと、通常完走の拒否分類テストを混同しない。

### Shared evidence module

- 拒否理由を `boundary` / `defect` の二値に閉じ、未知理由は `defect` として fail closed にする。
- status schema の必須 field、型、非負値、seq の整合を検査する。
- `ceil(expected / 2)` の受理クォーラムと、action 種別ごとの coverage を計算する。
- 再起動席の停止 / Resume seq と protocol reply ledger から、送信義務を除外する round 集合を
  一意に導出する。除外規則を driver や親テストへ複製しない。
- Phase 固有の action 選択、サーバ起動、subprocess 起動は持たない。

## Public Interfaces

### Driver status schema

status は UTF-8 JSON object とし、`schema_version` は `phase-completion-evidence/v1` とする。
既存の診断 field に加え、少なくとも次を持つ。

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
| `send_errors` | `{type, message, evidence_key}` の配列。空でなければ失敗。 |
| `server_imports`, `production_import_guard` | production client process が禁止 package を import していない証拠。 |
| `client_exit_reason`, `exception_type`, `exception_message` | 異常終了時を含む最終診断。 |

Action Evidence record は次の意味を持つ。

| Field | Shape / meaning |
|---|---|
| `key` | action の意味的な一意 key。下記の単位で作り、token や role ID は含めない。 |
| `kind` | `vote` / `ability` / `co_declare` / `chat`。 |
| `day`, `phase`, `action_generation` | handle から得た値。 |
| `after_resume` | この opportunity が `session.resumed` 受信後か。 |
| `outcome` | `sent` / `deadline_suppressed` / `stale_before_send` / `no_legal_target` / `send_error` のいずれか。 |
| `ability_id` | `ability` の場合だけ handle の値を記録する。分岐条件には使わない。 |

意味的 key と送信義務は次のとおり。

- vote: `(player_id, day, phase)` ごとに1回。`vote` と `runoff` は別 round とする。
- ability: `(player_id, day, phase, ability_id)` ごとに1回。`uses_remaining != 0` かつ
  `valid_targets` が `target_count` 以上の handle を opportunity とする。
- CO: `(player_id, day)` ごとに1回。最初に列挙された `CoDeclareAction` を使う。
- chat: `(player_id, day)` ごとに1回。最初に列挙された `ChatAction` を使う。

同じ意味的 key の action state が再送されても opportunity を増やさない。新プロセスで Resume した
席については、新しい process incarnation の key として再送信を許すが、coverage 集計時は席・日・
phase の意味的 key へ畳み込む。

停止マーカーは status と同じ version、`pid`、`player_id`、`kind`、`last_seq`、その時点までの
`action_evidence` を持つ。再起動前プロセスを強制終了しても、停止点までの送信証拠が失われない。

### Parent-side interruption evidence

親テストは再起動席宛てに生成された protocol reply を、少なくとも
`{seq, type, action_state.day, action_state.phase}` の形で受動記録する。秘密 payload と token は
記録しない。`player.action_state` が vote / runoff を列挙したとき、同じ `(day, phase)` の最小 `seq` を
その round の `round_start_seq` とする。`game.state_sync` は既存 round の途中にも届くため、round 開始の
代用にはしない。

再起動席の送信義務から除外する round は、次の式で一意に定める。

```text
interrupted_rounds = {
  round |
  stop_marker.last_seq < round.round_start_seq <= resume_state_sync.seq
}
```

`stop_marker.last_seq` は controller が停止して送信不能になった論理境界、`resume_state_sync.seq` は
Resume 認証後の全量 sync を client が受理し、再び送信可能になった境界である。OS の `kill()` 呼出し
時刻ではなく、この player-local seq の半開区間を使う。停止マーカー後は receiver が checkpoint可能でも
controller は停止しているため、送信義務上は interruption 中として扱う。

共通検証部品は `interrupted_rounds` が1件以上で、かつ再起動席が生存していた completed round の
全件未満であることを要求する。さらに `round_start_seq <= stop_marker.last_seq` の送信済み round と、
`round_start_seq > resume_state_sync.seq` の送信必須 round が各1件以上必要である。これにより除外集合が
0件または全件となって coverage を無効化することを防ぐ。

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

1. driver が `game.state_sync` / `player.action_state` を受け、snapshot の typed handle を列挙する。
2. 意味的 key が未処理なら Action Evidence を作り、受信列挙から target 等を決める。
3. typed send、stale、例外のいずれかを `outcome` に確定する。deadline safety margin は
   opportunity の送信可否には使わない。
4. `action.rejected`、`session.resumed`、gap notice、sync、chat message を各観測配列へ追記する。
5. driver 終了時に status を atomic に書き、親テストが全席分を schema 検証する。
6. 親テストの passive recorder が再起動席向け protocol reply の seq と action-state round を保持し、
   stop / Resume sync 境界から `interrupted_rounds` を共通検証部品で導出する。
7. 親テストが server event bus から `VOTE_SUBMITTED`、`VOTE_RESOLVED`、
   `ACTION_SUBMITTED`、`CO_DECLARED`、`GAME_ENDED` を抽出する。
8. status の opportunity / attempt と server の受理を action 種別・day・phase・player で照合する。
9. 拒否分類、coverage、Resume、gap、import guard、exit、cleanup の全条件が満たされた場合だけ成功とする。

投票 round の期待生存者は `VOTE_RESOLVED.payload.tallies.keys()` から取る。死亡イベントから生存状態を
再計算したり、vote rules をテストへ複製したりしない。

## State / Lifecycle

driver の evidence key は次の状態だけを取る。

```text
OBSERVED
  ├─ safety margin 内 ─> DEADLINE_SUPPRESSED
  ├─ target 不足      ─> NO_LEGAL_TARGET
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
  -> client-written checkpoint is verified beyond marker seq
  -> first PID is killed and reaped
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
動かし、親テストは再起動席向けの次の vote / runoff action-state と、それ以上の client-written
checkpoint を確認してから kill する。保持外ケースは同じ barrier 制御を使い、replay history limit を
1にして replay floor が marker seq を越えたことも確認してから kill する。wall-clock sleep の長さで
欠落範囲を推測しない。

再起動席は、Resume 後に開始する少なくとも1つの completed vote / runoff round まで生存させる。
Dummy target 選択では、受信した `valid_targets` に代替候補がある間だけ再起動席を対象から外す。
これは親から渡した player ID と受信列挙だけで行い、role、team、固定 player ID は使わない。必要な
post-Resume round を完了した後は全席と同じ決定論的選択へ戻してよい。

通常完走における各 action 種別の合否は次のとおり。

| Kind | Send-attempt requirement | Authoritative acceptance requirement |
|---|---|---|
| vote | 非再起動席は各 completed vote / runoff round で期待生存者集合との厳密一致を維持する。再起動席だけは `interrupted_rounds` を期待集合から除き、それ以外の生存 round すべてで `sent` を要求する。 | 除外に関係なく、各 round で distinct `VOTE_SUBMITTED.voter_player_id` が `ceil(living / 2)` 以上。全 game の下限は各 round の下限の和。 |
| ability | 全 eligible ability opportunity が `sent`。 | game 全体で distinct `(day, phase, actor, ability)` が `max(2, ceil(eligible / 2))` 以上。fixture 自体も eligible が2以上でなければ失敗。 |
| CO | 全 `CoDeclareAction` opportunity が席・日ごとに `sent`。 | day ごとに distinct `(day, player)` が `ceil(eligible / 2)` 以上、game 全体で2件以上。 |
| chat | 全 `ChatAction` opportunity が席・日ごとに `sent`。 | status 群で受信した distinct sender が、game 全体の eligible distinct sender の `ceil(eligible / 2)` 以上かつ2送信者以上。 |

ここで `deadline_suppressed`、`stale_before_send`、`no_legal_target`、`send_error` は `sent` の代わりに
ならない。通常 driver は安全マージンで required opportunity を抑止せず直ちに送信し、サーバが返した
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
- interruption evidence: round reply、client-written checkpoint 前進、Resume sync、interruption 前後の
  必須 round のいずれかが無い場合は、除外を推測せず scenario setup failure とする。
- stale work: stale outcome を記録し、同じ handle を再送しない。新 generation の同じ意味的 keyも
  game-wide の1回義務を既に満たしたなら再送しない。ただし再起動 incarnation の復帰後送信証明は別枠。
- cleanup: expected kill 以外の非0 return、未回収 PID、status 書込み失敗を失敗させる。全 subprocess を
  reap してから server を close する。

45秒の game-end timeout と15秒の status timeout は維持する。R-119 修正後のファイル単位5回が
112.19〜113.35秒で安定しており、各ゲームは2秒の phase deadline で進むため、送信数は正常系の
ゲーム所要を延ばさない。一方、通常完走と保持内 replay を統合し、拒否観測は game end を待たない
短いシナリオにして、正常なファイル実行へ20秒以上の余裕を作る。実装後5回の各所要時間と各 scenario
の game-end 実測を残し、1回でも120秒を超えれば契約未達とする。

失敗診断の最小セットは、test / scenario 名、elapsed、game phase/day、直近 server event type、
player_id、PID、process return code、client exit reason、last_seq、events received、各 outcome 件数、
rejection の action/reason/seq、gap tuple、exception type/message、stdout/stderr である。token、chat 本文、
private payload は出さない。

## Concurrency

- driver の event consumer だけが evidence ledger を更新する。action ごとの無制限 task は作らない。
- send は受信 loop 内で逐次 await するが、1 opportunity 1回に限定する。Network Client 自身の receiver /
  sender 分離は変更しない。
- 親テストが全 subprocess の唯一の owner であり、PID ごとに wait / kill / diagnostics を行う。
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

**Decision:** vote は全生存席・全 round、ability は全 eligible opportunity、CO / chat は全 eligible 席・日で
1回送る。受理は上記の server-side 動的クォーラムを満たす。

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
| deterministic game drift | fixed seed、2秒 duration、列挙順選択を維持し、fixture integrity の eligible count を検査する。 |

### 6. 後続完走テストへの再利用

**Decision:** rejection 分類、status 共通 envelope、Action Evidence outcome、動的クォーラム、Resume / gap の
証拠検証だけを `tests/fixtures/completion_evidence.py` で共有する。3.2〜3.5 / Phase 5 の driver は共通
record を出せるが、各 Phase 固有の完了条件と action 選択は自身のテストに置く。

- 不採用: Phase 3.1 driver 全体を共通 driver にする。World State、Brain、LLM queue を通す後続 Phase の責務を迂回する。
- 不採用: 共有しない。同じ rejection 0、固定件数、Resume bool だけの漂流を繰り返す。
- 不採用: production package に evidence helper を置く。テスト契約を製品 API に混入させる。

### 7. R-20260902-128 — interruption 中の vote 送信義務

**Decision:** 再起動席だけ、`stop_marker.last_seq < round_start_seq <= resume_state_sync.seq` の
round を送信義務から除外する。除外集合は共通検証部品が protocol reply ledger から導出し、0件・
全件を禁止する。非再起動席、サーバ受理クォーラム、interruption 外の再起動席には例外を設けない。

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
- interruption により除外する round が0件または再起動席の全 completed round なら失敗する。
- 再起動席以外は全 round で厳密な送信集合一致を満たし、再起動席も interruption 前後の必須 round で
  `sent` を満たす。
- defect 理由または未知理由を通常 status に1件注入すると失敗する。
- boundary rejection が存在しても、全送信義務と受理クォーラムを満たせば成功する。
- status に token、role 固有分岐、固定 channel ID、内部死因、chat 本文を含めない。
- `tests/test_phase3_1_completion.py` が同一 command で5回連続成功し、各回120秒以内である。

## Required Tests

### Contract unit tests

- 分類表が実コードの `PLAYER_ACTION_REJECTION_REASONS` と session 固有3理由を過不足なく覆う。
- boundary 5理由は許容、defect 全理由と未知理由は拒否される。
- defect を1件混ぜた status、重複 rejection seq、不正 schema version、未知 outcome が失敗する。
- `ceil(expected / 2)` の境界、distinct player 集計、重複 server event が水増しにならないこと。
- 9席の round に `VOTE_SUBMITTED` 1件だけを与えると失敗する。
- protocol reply ledger と stop / resume seq から interruption round が一意に導出され、区間端の
  `round_start_seq == stop_seq` は含まず、`round_start_seq == resume_sync_seq` は含む。
- interruption round が0件、全件、または ledger に無い round を caller が任意指定すると失敗する。

### Separate-process completion

- 9 child PID、全席 GAME_ENDED、server tick only、import guard、LLM 無し。
- 非再起動席は各 vote / runoff round、再起動席は interruption 外の各生存 round で `sent` を記録し、
  server accepted distinct voter は除外に関係なく全 round で動的下限以上。
- interruption round が1件以上・全件未満で、interruption 前後に再起動席の必須 round が各1件ある。
- 再起動席が1 round をまたいでも成功し、同じ欠落を interruption 外へ移すと席・round 付きで失敗する。
- ability / CO / chat の eligible、attempt、accepted が各動的下限を満たし、各 aggregate が2件以上。
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

- `pytest tests/test_phase3_1_completion.py` を `-k` なしで5回連続実行する。
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
