Status: DRAFT — DESIGN BLOCKED

# Phase 3.4 Reaction・Chat Controller 詳細設計

DESIGN BLOCKED

## Conflict

### 1. `action.rejected` が Reaction・Chat Controller から観測できない

`ROADMAP.md` §3.4 は、`action.rejected` を受けたことがテストから観測でき、握り潰されないことを
完了条件にしている。設計依頼 Q6 はさらに、拒否後に再送・断念・次機会のどれを選ぶかと、拒否理由
ごとの分類を Phase 3.4 で決めるよう求めている。

一方、承認済み Phase 3.2 設計は `WorldState` を `NetworkClient.events()` の唯一かつ exclusive な
consumer とし、3.3〜3.5 は World の snapshot / query / update wait だけを使うと定めている。
現行 `WorldState` の公開 API には transport notice の query / stream がない。

実装もこの契約どおりである。

- `NetworkClient` は `action.rejected` の `ServerEvent` に続けて、reason を持つ typed
  `ActionRejected` notice を event stream へ公開する。
- `WorldReducer` は top-level `action.rejected` を transport fact として履歴へ保存しない。
- `WorldState` は typed `ActionRejected` notice も内容を保存せず、World version の commit だけを
  行う。
- `WorldSnapshot`、`HistoryView`、`BrainInput` のいずれからも、拒否の action / reason / seq を復元
  できない。

したがって、`ai_client/network/` と `ai_client/world/` を変更せず、かつ World を唯一のconsumerに
保ったままでは、Reaction・Chat Controller は拒否を受けた事実も理由も知れない。server側だけを
観測するcompletion testは「クライアントが拒否後を扱う」というQ6の証拠にならない。

### 2. server deadline と local monotonic の対応が Reaction・Chat Controller へ公開されない

`ROADMAP.md` §3.4 は締切前の打ち切りを要求する。承認済み Phase 3.3 設計は
`phase_ends_at` と local clock を直接比較しないと定め、送信余裕の決定を Phase 3.4 / 3.5 に委ねた。
設計依頼 Q2 は、その対応をどこで取得し、整数秒の2秒フェーズでどう打ち切るかを今回確定するよう
求めている。

現行 `NetworkClient` は、受信 envelope の server `timestamp` と `phase_ends_at` の差を使って local
sleep を起動する。この対応を知るのは Network 内部だけであり、公開 snapshot には local deadline
または残り時間がない。deadline 到達時には `PhaseDeadlineReached` notice を公開するが、唯一の
consumer である `WorldState` はその内容も保存せず、World version の commit だけを行う。

`WorldSnapshot.phase.phase_ends_at` は server clock の整数値だけであり、対応する server timestamp、
受信時の local monotonic、Network が設定した local deadline のいずれも含まない。そのため
Reaction・Chat Controller は次を行えない。

- server deadline より所定の余裕を引いた local cutoff を計算する。
- `PhaseDeadlineReached` と通常の World update を区別する。
- cutoff により意図的に黙った席と、deadlineを既に越えて喋れなかった席を区別して記録する。
- 遅延した初回sync / reconnectでもdeadline前送信であることを保証する。

最初にCURRENT snapshotを見たlocal時刻から固定秒数を測る案は、phaseの残り時間との対応がなく、
遅延接続時にdeadlineを越える。`phase_ends_at` を直接local monotonicとして扱う案は、承認済み3.3
設計に明示的に反する。どちらも採用できない。

## Affected canonical sources

- `Docs/ai/ROADMAP.md` §3.4
  - 締切前の打ち切りと、`action.rejected` の観測・非握り潰しを完了条件にしている。
- `Docs/ai/design/PHASE3_4_REACTION_CHAT_REQUEST.md` Questions 2 / 6 and Constraints
  - deadline対応と拒否後policyを今回決定するよう要求する一方、Network / World の変更を禁止する。
- `Docs/ai/design/PHASE3_2_WORLD_STATE_MEMORY_DESIGN.md` Public Interfaces / Upper layers
  - WorldをNetwork event streamの唯一のconsumerとし、上位をWorld read APIだけに限定する。
- `Docs/ai/design/PHASE3_3_BRAIN_INTERFACE_DESIGN.md` Responsibilities / Main Design Decision 2
  - Brain / ControllerはWorld read APIを使い、`phase_ends_at` とlocal clockを直接比較せず、送信余裕を
    3.4 / 3.5へ委ねる。
- `Docs/ai/design/PHASE3_1_NETWORK_CLIENT_DESIGN.md` Failure Handling / Concurrency
  - `action.rejected` と deadline notice はNetwork event streamで上位へ通知し、Networkは自動再試行
    しない。
- `Docs/ai/spec/DESIGN.md` §9.3
  - serverがdeadlineを強制し、client側もtimerを持つ。
- `ai_client/network/client.py`
  - server timestampとの差からdeadline taskを作り、`PhaseDeadlineReached` と `ActionRejected` を
    event streamへ公開する現行実装。
- `ai_client/world/service.py` / `ai_client/world/reducer.py`
  - 両通知を公開modelへ残さずcommitだけ行う現行実装。

## Decision required

Reviewer / canonical owner は、次のいずれかを決定し、Phase 3.4 DESIGN REQUEST の Constraints を改訂
する必要がある。

1. **推奨: World / Network の公開契約への最小拡張を許可する。**
   Reaction・Chat Controller が World 経由で読める immutable な transport-observation API に、
   少なくとも `ActionRejected(action, reason, seq)` と、deadline前のlocal cutoffを導ける
   phase / day / action generation / local deadline対応情報を保持する。Networkが既に計算している
   deadline対応をどのtyped valueとしてWorldへ渡すか、retention、version、再接続時の置換規則も
   新しいcanonical contractで決める必要がある。
2. **World単一consumer契約を改訂し、明示的なevent fan-out境界を新設する。**
   Network eventを一回だけ消費してWorldとControllerへtyped noticeを配送する。ただしbounded queue、
   backpressure、順序、再接続、secret境界を新たに設計する必要があり、推奨案より変更範囲が大きい。
3. **Phase 3.4 の完了条件を変更する。**
   client側の拒否処理とserver deadline対応を要求しない形へROADMAPを変更する。これは機能要件の
   縮小なので、Detailed Design roleは選択できない。

採らない案:

- Reaction・Chat Controllerが `NetworkClient.events()` を直接読む案は、Worldとの複数consumerになり、
  イベントの奪い合いと受信欠落を起こすため採らない。
- 新しいproxyがraw `ServerEvent`を横取りしてControllerへ渡す案は、承認済み3.2のexclusive-consumer
  契約と「3.3〜3.5はraw payloadへ触らない」に反するため採らない。
- test fixtureだけでserver側rejectionを数える案は、Q6のclient lifecycleと再送判断を検証できない
  ため採らない。
- snapshot初回観測から固定delayを置く案は、server deadlineとの対応も残り時間の下限も保証されず、
  「喋らなかった」と「喋れなかった」を区別できないため採らない。

上記決定が行われるまで、Q1〜Q7を相互整合した詳細設計へ確定できない。特にQ1の複数invocation、
Q3/Q4の反応timer、Q5のCO trigger、Q7の完走証拠を先に固定すると、後から選ばれるdeadline / notice
境界によってtask ownership、cancellation、観測schema、既存Phase 3.3 Acceptance 9への影響が変わる。
そのため本書は `DRAFT` のまま停止する。
