Status: APPROVED — Reviewer / Claude 承認（2026-09-02、Addendum A 反映後の再承認）。A1〜A5 の回答を canonical と突き合わせて確認した: `views.py` の `player_list()` と `revealed_roles` はどちらも `game.players.values()` を辿るので A1 の順序は決定的、protocol schema の `revealed_roles` は `uniqueItems` も `players` との相互検査も持たないので A2 の重複・players外の判定は World State が持つべき責務、A4 は Design invariant 4 に一致する。A1〜A5 以外の変更は無い。実装レビューでの確認事項: recovery sync を malformed として不受理にし続けた場合、freshness が `STALE` のまま留まることが上位から観測でき、沈黙したハングにならないこと。

# Phase 3.2 World State・Memory — Detailed Design

## Purpose

Network Client が渡す本人視点の検証済みイベントを、3.3〜3.5 が通信payloadを
解読せず読める現在像と発生順履歴へ変換する。World State は受信済みの事実を
materializeして検索可能にするが、事実の評価、推測、信頼度、行動選択は行わない。
Network Client の bounded event queue を専用taskで速やかに消費し、Brain の速度を
network receive path へ持ち込まない。

## Scope

ROADMAP §3.2 の現在像、履歴、全量syncによる再基準化、増分適用、記憶上限、
読み取り専用API、Network Clientとの取り込み境界を対象とする。
既知のwire eventを世界像のtyped recordへ正規化するが、ゲームルールや発言の意味は
解釈しない。

## Files / Modules

| File | Responsibility |
|---|---|
| `ai_client/world/__init__.py` | 3.3〜3.5へ公開するWorld State APIとimmutable typeだけを再exportする |
| `ai_client/world/service.py` | `ClientEvent` consumer、lifecycle、sourceとのseq同期、読取snapshotの公開 |
| `ai_client/world/model.py` | player / death / phase / self / history record / query / retention情報のimmutable型 |
| `ai_client/world/reducer.py` | syncと増分を共通mutationへ正規化し、1つの世界像へ適用する |
| `ai_client/world/memory.py` | 発生順history window、件数・byte上限、evictionとquery index |
| `tests/test_phase3_2_world_state.py` | reducer、API、履歴、sync等価性、失敗・並行性の単体テスト |
| `tests/fixtures/phase3_2_world_client_process.py` | World Stateだけを読んでDummy操作する別プロセスdriver |
| `tests/test_phase3_2_completion.py` | 9プロセス、保持内replay、保持外sync回復をWorld State経由で検証する |

`ai_client/world/` は `ai_client/network/` の下ではなく同階層に置く。
依存方向は `world -> ai_client.network` のpublic typeだけとし、Network Clientから
World Stateをimportしない。`server.aiwolf_core` / `server.network` はimportしない。
`ai_client/network/`、server、schema、contentは変更しない。

## Responsibilities

### World State service

- `NetworkClient.events()` の唯一の通常consumerとして専用taskで全`ClientEvent`を読む。
- `ServerEvent`をnormalizer / reducerへ渡し、connection・gap・termination noticeを
  freshnessへ反映する。
- 世界像を変更しないsession / unknown eventを含め、消費した全`ServerEvent`のseqを
  `last_applied_seq`へ進める。これはWorld Stateのdrain位置であり、transport回復判断には使わない。
- reducerが完成させたimmutable snapshotをatomicに差し替える。
- BrainやControllerのcallbackを呼ばず、更新待ちにはversion通知だけを行う。

### Reducer

- `game.state_sync`をresetと複数のcanonical mutationへ分解する。
- 個別`player.list` / `player.deaths` / `player.action_state`を同じmutationへ正規化する。
- sync内history entryとlive `game.event` / `chat.message`を同じhistory normalizerへ通す。
- 既知payloadの構造だけを検査し、役職・死因・結果IDの意味を判断しない。

### Current world view

- player一覧、death全量、phase / day / phase end、自分のrole ID / modifier IDsを保持する。
- state syncで本人へ開示された他playerのrole IDを、独立したrevealed-role viewとして保持する。
- 生存者はcanonical DESIGN §9.4どおり player一覧からdeathのplayer IDを引いて導出する。
- current actionはNetwork Clientのaction handleをそのまま参照し、World Stateで再構築しない。
- self role、modifier、public cause、channel、ability resultなどのIDは受信値をopaqueに保持する。

### Memory

- chat、CO declaration / report、vote result / reveal、death、phase transition、
  private ability resultを1つの発生順windowへ正規化する。
- retained recordだけを指すcategory indexを持ち、eviction時にindexからも除く。
- recordの重要度を評価しない。保持優先度や要約はPhase 6の責務である。

### Upper layers

- 3.3〜3.5は`WorldSnapshot`とquery APIだけを読む。network payloadへ直接触らない。
- `freshness`、`is_caught_up`、retention metadataを確認し、不完全な像を完全とみなさない。
- action送信時はWorld Stateの`current_actions()`が公開したhandleをNetwork Clientへ戻す。
- belief、suspicion、発言意図、投票・能力方針は上位が後続Phaseで所有する。

## Public Interfaces

以下は公開契約を示すsignatureであり、内部helperや逐次実装を指定しない。

```python
@dataclass(frozen=True)
class WorldStateConfig:
    max_history_records: int = 1024
    max_history_bytes: int = 2 * 1024 * 1024

class NetworkEventSource(Protocol):
    def snapshot(self) -> ClientSnapshot: ...
    def events(self) -> AsyncIterator[ClientEvent]: ...

class WorldState:
    def __init__(
        self,
        source: NetworkEventSource,
        *,
        config: WorldStateConfig = WorldStateConfig(),
    ) -> None: ...

    async def run(self) -> WorldStateExit: ...
    async def stop(self) -> None: ...
    def snapshot(self) -> WorldSnapshot: ...
    def current_actions(self) -> CurrentActionsView: ...
    def revealed_role(self, player_id: str) -> RevealedRoleView | None: ...
    def history(self, query: HistoryQuery = HistoryQuery()) -> HistoryView: ...
    def co_for_day(self, day: int) -> CoView: ...
    def ability_results(self) -> AbilityResultView: ...
    async def wait_for_update(self, after_version: int) -> WorldSnapshot: ...
```

`WorldSnapshot` は少なくとも次を持つ。

- `version`: reducer commitごとに増えるWorld State内の単調増加version
- `freshness`: `EMPTY | CURRENT | STALE | ENDED | FAILED`
- `is_caught_up`: World Stateの最終適用server seqとNetwork Client snapshotの
  `last_seq`が一致するか
- `last_applied_seq`: 最後にWorld Stateへ適用したserver event seq
- `players: tuple[PlayerView, ...]`
- `alive_player_ids: tuple[str, ...]`
- `deaths: tuple[DeathView, ...]`
- `phase: PhaseView | None`
- `self_view: SelfView | None`
- `revealed_roles: tuple[RevealedRoleView, ...]`
- `history_retention: HistoryRetention`
- `unknown_event_count` / `known_unmodeled_event_count` / `malformed_event_count`

`PlayerView`は`player_id`、`display_name`、alive、対応するdeathを持つ。
`DeathView`は`player_id`、day、受信した`public_cause | None`を持つ。
`PhaseView`はphase、day、`phase_ends_at`を持つ。
`SelfView`はplayer ID、role ID、modifier IDsを持つ。
`RevealedRoleView`はplayer IDと、serverから受け取ったrole IDだけを持つ。
`revealed_roles`はsyncの`players`順で決定的に並べ、`revealed_role(player_id)`は
対応するimmutable viewを返し、開示されていなければ`None`を返す。
全modelはfrozenかつdeep immutableとし、返却値の変更で内部状態が変化しない。

`current_actions`は`WorldSnapshot`から外し、別の`current_actions()` APIで返す。
`WorldSnapshot.version`が同じなら、`WorldSnapshot`内の全内容は同じである。
`CurrentActionsView`は呼出し時点の`world_version`、World State / Network Clientの
`last_seq`、`is_caught_up`、`actions`を持ち、World Snapshotのversion同期契約には含めない。

`current_actions()`はWorld Stateが最後に適用したseqとNetwork Client snapshotの
`last_seq`が一致し、Network Clientが`CONNECTED`のときだけ、そのsnapshotのhandleを返す。
それ以外は空tupleとする。同じworld versionでもNetwork Clientが先行すれば結果は空へ
変わりうるため、callerは毎回`CurrentActionsView.is_caught_up`を確認する。
これによりNetwork Client固有のconnection / action generationを複製せず、先行した
network stateのhandleを遅れた世界像へ混ぜない。

`NetworkEventSource.events()`はbroadcastではなく1つのqueueを共有する。
**World Stateが、そのNetwork Clientに対する唯一かつexclusiveなconsumerであることを
public interfaceの前提条件とする。** 他componentは`events()`を呼ばず、World Stateの
snapshot / query / update waitを使う。複数consumerを実行時に確実に検出するAPIは無いため、
起動側が構成として保証する。

### History records

public historyは次のtyped unionとする。

- `ChatRecord`: order、day / phase、channel ID、player ID、display name、message
- `CoDeclarationRecord`: order、day、player ID、claimed role ID、comment
- `CoReportRecord`: order、day、player ID、kind、target player ID、claimed result
- `VoteResultRecord`: order、day、phase、result ID、tallies、lynched player ID、runoff candidates
- `VoteRevealRecord`: order、day、phase、voter / targetまたはfinal votes
- `DeathRecord`: order、day、player ID、public cause
- `PhaseTransitionRecord`: order、phase、day、phase end
- `PhaseTimingChangedRecord`: order、`DAY_EXTENDED | DAY_SHORTENED`、day、phase、更新後phase end、
  extension回数（eventに存在するとき）
- `AbilityResultRecord`: order、day、受信event type、target player ID、result IDまたはrevealed role ID
- `GameLifecycleRecord`: order、`GAME_CREATED`ではgame ID / day / phase / player views、
  `GAME_ENDED`ではwinner team ID / outcome ID / player results。終了そのものはlifecycle noticeと
  二重適用しない
- `PublicNotifyRecord`: order、day / phase context、受信したnotify ID
- `TieResolvedRandomRecord`: order、day、phase、candidate player IDs、selected player ID
- `KnownUnmodeledEventRecord`: 既知eventだがWorld State public modelへ型付けしないもの。
  unknownとは別countにし、raw payloadは公開しない
- `UnknownEventRecord`: order、top-level typeまたはcore event type。raw payloadは公開しない
- `MalformedEventRecord`: order、event type、欠けた既知shapeであること。raw payloadは公開しない

既知のprivate result adapterは現行wire出力の`INSPECT_RESULT`、`MEDIUM_RESULT`、
`INSPECT_DEAD_ROLE_RESULT`、`GUARD_SUCCEEDED`を対象にする。event名はprotocol上の事実種別であり、
role名ではない。result / role IDは受信値のまま保持する。

現行のPUBLIC event surfaceは次のように全件を分類する。

| Event | World Stateでの扱い |
|---|---|
| `CO_DECLARED` / `CO_REPORTED` | CO typed records |
| `VOTE_RESOLVED` / `VOTE_REVEALED_LIVE` / `VOTES_REVEALED_AFTER` | vote typed records |
| `TIE_RESOLVED_RANDOM` | `TieResolvedRandomRecord` |
| `PLAYER_DIED` | `DeathRecord` |
| `PHASE_STARTED` | `PhaseTransitionRecord` |
| `DAY_EXTENDED` / `DAY_SHORTENED` | `PhaseTimingChangedRecord`を追加し、current phaseとdayが一致するとき`PhaseView.phase_ends_at`も更新 |
| `GAME_CREATED` / `GAME_ENDED` | `GameLifecycleRecord`。`GAME_CREATED`だけでunknown / completeness warningを立てない |
| `PUBLIC_NOTIFY` | notify IDをopaqueに持つ`PublicNotifyRecord` |

型付けの判断基準は、**現在PUBLIC / PRIVATEで本人へ届く既知eventのうち、current viewを
更新するもの、またはROADMAP §3.2の履歴と3.3〜3.5が読むplayer-visible factを持つものは
typed recordにする**、で固定する。それ以外の既知eventは`KnownUnmodeledEventRecord`、
catalogにも無いeventだけを`UnknownEventRecord`とする。

`HistoryQuery`はkind集合、day、player ID、`after_order`、limitによる絞り込みを持つ。
`HistoryView` / `CoView` / `AbilityResultView`はrecordに加え、問い合わせ範囲がevictionで
不完全かを示す`complete`とretention metadataを必ず返す。

`HistoryRetention`は少なくとも次を公開する。

- `total_seen`: 現在のsync基準内で正規化した総record数
- `retained_count` / `retained_bytes`
- `dropped_count` / `dropped_through_order`
- `first_retained_order | None` / `last_order | None`
- configured record / byte capacities
- `complete`: prefix lossが無いか

raw `ServerEvent`、raw state sync、mutable mappingをpublic APIから返さない。

## Data Flow

### Incremental event

1. World State専用taskが`source.events()`から`ClientEvent`を1件取り出す。
2. `ServerEvent`ならtop-level typeに応じてcanonical mutationへ正規化する。
3. reducerがcurrent viewまたはhistory windowへmutationを適用する。
4. world factを変更しないeventでも、消費したseqをdrain位置として更新する。
5. retained historyのindexとretention metadataを同じcommitで更新する。
6. immutable WorldSnapshotを1回差し替え、version waiterを通知する。
7. Brain / Controllerは必要な時点でsnapshotまたはqueryを同期的に読む。

### Full state sync

1. `game.state_sync`受信でWorld Stateを新しいbaseline epochへ切り替える。
2. players、deaths、action state、self、revealed rolesを共通のreplace mutationへ変換する。
3. historyを先頭から、live eventと共通のhistory normalizer / append mutationへ通す。
4. history内の`PHASE_STARTED`を文脈として、dayを持たないchat / CO / ability resultへ
   受信時点のday / phaseを付ける。文脈が無ければ`None`としmalformed扱いにはしない。
5. 明示されたaction stateをcurrent phaseの最終authorityとしてcommitする。
6. reset前のhistory、category index、revealed roles、unknown / malformed countを残さない。

`revealed_roles`は全量sync専用のauthoritative stateである。受理したsyncごとに全置換し、
空配列なら以前の開示をすべて消す。増分server eventから追加・推測・維持しない。
同じplayer IDが2回現れる場合はrole IDが同一でもsync全体をmalformedとし、
`players`に存在しないplayer IDが現れる場合もsync全体をmalformedとする。
いずれもbaselineを部分適用せずlast good viewを保持してfreshnessを`STALE`にする。
subsetは受理し、含まれないplayerは未開示として扱う。

sync historyをすべて適用しても、windowは各append時に上限を守る。
一度全件を無制限に保持してからtrimしてはならない。

### Action handles

World Stateはwire action specからaction handleを再構築しない。read API呼出し中に
同じevent loop上でNetwork Client snapshotを取得し、`last_seq`一致と`CONNECTED`を確認して
`CurrentActionsView`へ載せる。これはversion付き`WorldSnapshot`の外側にあり、同期関数内に
awaitを置かないため、この比較中にtask switchしない。

## State / Lifecycle

```text
EMPTY
  └─ game.state_sync commit ─> CURRENT

CURRENT
  ├─ incremental event ─> CURRENT (version + 1)
  ├─ disconnect / synchronizing / seq gap ─> STALE
  ├─ GAME_ENDED / client ENDED ─> ENDED
  └─ fatal termination / source failure ─> FAILED

STALE
  ├─ replay events ─> STALE
  ├─ authoritative game.state_sync + CONNECTED ─> CURRENT
  ├─ stop ─> ENDED
  └─ fatal termination ─> FAILED

any nonterminal ── newer game.state_sync ─> baseline reset in same WorldState instance
```

- 初回`game.state_sync`前の個別incremental eventはhistoryへ保持しても、freshnessは`EMPTY`のまま。
  current viewを利用可能とはしない。
- `LifecycleChanged`のJOINING / RESUMING / SYNCHRONIZING / RECONNECT_WAIT、
  `SequenceGapDetected`で`STALE`にする。last good snapshotは読めるがfreshではない。
- authoritative syncを適用し、Network ClientのCONNECTEDを観測した時点で`CURRENT`に戻す。
- `GameEnded` noticeは同じGAME_ENDED server eventをhistoryへ二重追加せず、終了状態だけを更新する。
- `ActionRejected`などServerEventと専用noticeの両方があるものは、事実recordを二重化しない。

## Main Control Flow

起動側はWorld Stateの`run()`をtaskとして開始してからNetwork Clientの`run()`を開始する。
World State taskはeventを待ち、受信ごとに短い同期reducer処理だけを行う。
上位は`wait_for_update()`または必要な周期でimmutable snapshotを取得し、Brain処理を別taskへ渡す。
Brainが停止してもWorld State consumerは継続し、Network Client queueをdrainする。

通常のphase pushでは`game.event`群を発生順にhistoryへ追加し、続く`player.list`、
`player.deaths`、`player.action_state`でcurrent viewを置換する。再接続時にreplayが届けば同じ
incremental経路を通り、最終`game.state_sync`でbaselineを再構築するため、replayの有無にかかわらず
回復後の像はserver snapshotへ収束する。

## Failure Handling

### Unknown and malformed events

- schemaを通過した未知top-level server type、catalogに無い`game.event.event_type`はfatalにしない。
  current viewを変更せず、payload非公開の`UnknownEventRecord`をhistoryへ追加しcountを増やす。
- catalog上は既知だが上記の型付け基準に当たらないeventは`KnownUnmodeledEventRecord`と
  `known_unmodeled_event_count`へ入れ、`unknown_event_count`を増やさない。
- 現行PUBLIC eventはすべてtyped分類済みであり、`GAME_CREATED`や`PUBLIC_NOTIFY`だけで
  unknown / malformed completeness warningを立てない。
- 既知event typeなのにWorld Stateが必要とするfield / typeを満たさない場合は
  `MalformedEventRecord`として記録し、そのtyped index更新だけを行わない。
- state sync内のrevealed-role player重複またはplayers外参照はhistory recordではなく
  malformed syncとして数え、syncの他fieldを含めてatomicに適用しない。
- 未知の非Server `ClientEvent` noticeもconsumerを止めずcountへ記録するが、historyには入れない。
- unknown / malformedがあればsnapshotのcompleteness warningで観測できる。
  protocol major mismatchはNetwork ClientがWorld Stateより前でfatalにする。

### History limits

- defaultは1024 recordsかlogical UTF-8 JSON 2 MiBの先に達した方を上限とする。
- append後に両方を満たすまで最古recordから捨てる。categoryによる優先順位は付けない。
- 1 recordだけでbyte上限を超える場合、そのrecordを保持せず、それ以前を含むprefix lossとして
  `dropped_through_order`を進める。後続recordは通常どおり保持できる。
- evictionはsilentにせずretention metadataへ反映する。queryは失われたrecordを補完しない。
- current player / death / phase / self / action viewはhistory window外なのでevictionで失わない。
  COやability resultなど履歴由来のviewはretentionと同じ範囲であり、`complete`を伴う。

### Ingestion delay and exceptions

- consumerはNetwork Client receiverと別taskで、Brain callbackやdisk I/Oを行わない。
- full syncはhistoryをstream適用し、内部memoryを上限以上へ膨らませない。
- reducer内部の予期しないexceptionは`FAILED`としてconsumerを終了する。Network Clientを
  World Stateから直接stopしない。ただしexclusive consumerが消えるためNetwork Clientの
  bounded queueは以後drainされず、残りslot数（既定capacity 1024件）が起動側の対応猶予になる。
  埋まればNetwork Clientも`CONSUMER_OVERRUN`でfatal終了する。この連鎖は意図的なfail-fastであり、
  起動側はWorld State failureを待ち、猶予内にNetwork Clientをstopするかconsumerを含め再起動する。
- sourceが正常に閉じたらlast client lifecycleに応じて`ENDED`または`FAILED`を返す。
- `stop()`はconsumerだけを終了し、Network Clientのsocket ownershipを奪わない。ただし
  Network Clientを継続するなら、起動側は同時にstopするかexclusive replacement consumerを
  queue容量の猶予内に開始しなければならない。

### Partial and stale state

- disconnect / gap中はlast good viewを保持し`STALE`を返す。推測でstateを進めない。
- gap後に届く保持外recovery syncはold state / history / indexをatomicに置換する。
- recovery syncの`revealed_roles`も同じ全置換規則に従い、以前の開示をmergeしない。
- World StateがNetwork Clientより遅れている間は`is_caught_up=False`かつactions空とする。
- `DAY_EXTENDED` / `DAY_SHORTENED`のpayloadがcurrent `PhaseView`と同じday / phaseなら、
  更新後の`phase_ends_at`をcurrent viewへ反映する。不一致ならhistoryだけに記録し、stale eventで
  current viewを巻き戻さない。
- duplicate server factはNetwork Clientがseqで抑止する。World Stateはtransport seqで独自の
  replay並べ替えや欠番回復を行わない。

## Concurrency

- 1 WorldState instanceは1 Network Client・1 asyncio event loopに属し、thread-safeではない。
- `NetworkClient.events()`のqueueにはWorld State consumerだけを接続する。複数iteratorは
  eventを複製せずconsumer間に分配するため、2つ目のconsumerは禁止する前提条件である。
- consumer taskだけがmutable reducer stateを書くsingle-writer modelとする。
- read APIはimmutable snapshot pointerを同期的に読む。state lockを公開せず、I/O中にlockを持たない。
- `wait_for_update`のwaiter通知はsnapshot commit後に行い、waiterの処理をconsumer taskで実行しない。
- eventごとのtask生成、上位callback、Brain awaitをconsumer pathへ入れない。
- World State内に無制限の二次event queueを作らない。Network Clientのbounded queueから
  1件ずつ取得し、その場でbounded reducerへcommitする。
- sync処理中にnetwork queueが増える可能性はあるが、sync payload自体を取り出した時点で1 slotを
  解放し、保持処理はrecord / byte cap内で行う。負荷テストでcapacity 1024以内を実測する。

## Resolved Design Questions and Rejected Alternatives

### 1. 世界像の内部表現と読み取りAPI

**Decision:** typed current viewとbounded chronological recordsを差分更新し、commitごとに
immutable `WorldSnapshot`を差し替える。頻出queryはmaterialized indexから読む。

- 不採用: queryごとに全historyを再走査する。会話量に比例して3.3〜3.5の読取遅延が増える。
- 不採用: raw `ClientSnapshot` / payloadを上位へ渡す。network schema解読が各componentへ漏れる。
- 不採用: mutable modelを共有する。readerとconsumerが同じstateを変更でき、再現性を失う。

### 2. 履歴の保持単位・上限・捨て方

**Decision:** 1つの正規化recordを保持単位とし、1024 records / logical 2 MiBの二重上限、
global oldest-firstで捨てる。lossは`HistoryRetention`と各queryの`complete`で公開する。

- 不採用: 無制限保持。長時間chatと9プロセスでmemory上限が無い。
- 不採用: category別固定枠。traffic構成によって空き枠を使えず、global occurrence orderも複雑になる。
- 不採用: COやability resultを優先保持する。重要度判断をPhase 3.2へ持ち込みPhase 6を先取りする。

### 3. 全量syncと増分イベントの適用経路

**Decision:** 両方をcanonical mutationへ正規化し、1つのreducerへ通す。syncはreset、
共通replace mutations、共通history append mutationsの列として扱う。

- 不採用: sync専用builderとincremental専用handler。field追加時に片方だけ更新され等価性が崩れる。
- 不採用: sync payloadをそのまま保存してincrementalを別overlayにする。queryが2つのauthorityを読む。

### 4. イベント取り込みtask

**Decision:** Network Client receiverとは別の、World State専用single consumer taskで回す。
Brain / Controllerはimmutable snapshotを読むだけでconsumer taskへ入らない。

- 不採用: Network Clientのreceiver task内で適用する。`ai_client/network/`変更になり責務を逆流させる。
- 不採用: Brain taskが直接`events()`を読む。推論遅延でbounded queueを枯渇させる。
- 不採用: eventごとのparallel task。発生順とsingle-writerの保証を失う。

### 5. 未知イベント種別

**Decision:** catalogに無いeventはstateを変えずmetadata-only unknown record / countとして保持し、
completeness warningを公開する。catalog上既知だが型付けしないeventはknown-unmodeledへ分離し、
unknown countを汚さない。既知shape不正もmalformed recordへdowngradeし、consumerを継続する。

- 不採用: 未知種別でprocess終了。同一majorのminor拡張に追従できない。
- 不採用: 完全にsilent ignore。世界像が情報を落とした事実を上位が判断できない。
- 不採用: raw payloadを上位へ公開する。World State境界を迂回しschema依存を拡散する。

## Addendum A Resolutions — Revealed Roles

### A1. Public representationと置き場所

**Decision:** `RevealedRoleView(player_id, role_id)`のimmutable tupleを`WorldSnapshot`へ独立して置き、
`WorldState.revealed_role(player_id)`を頻出lookup APIとする。player順はsyncの`players`順に合わせる。

- 不採用: `PlayerView.revealed_role_id`。player identity / deathの現在像と、本人に許可された
  knowledgeを同じmodelへ混ぜ、未開示とrole無しの意味が近くなる。
- 不採用: history recordだけ。開示は発生eventではなくsync時点のauthoritative viewであり、
  `p1`の現在の開示値を毎回history走査しなければならない。
- 不採用: mutable mapをpublicに返す。外部変更から内部stateを保護できない。

### A2. Full syncの置換・整合規則

**Decision:** 受理した`game.state_sync.revealed_roles`で毎回完全置換する。空配列は消去、
同一player IDの重複と`players`外playerはsync全体をmalformedとしてatomicに不受理、subsetは許容する。
開示を増やすincremental messageは現行protocolに無く、World Stateも増分追加経路を持たない。

- 不採用: 以前の開示とのmerge。後続の非開示syncでも秘密情報が残り、serverの現在の開示判断を覆す。
- 不採用: duplicateのfirst-wins / last-wins。同じauthoritative sync内の矛盾を順序で隠す。
- 不採用: unknown playerだけを無視して残りを適用。全量baselineを部分適用し、server snapshotとの
  対応関係が不明になる。

### A3. Immutabilityとversion契約

**Decision:** `RevealedRoleView`はfrozen value、collectionはtupleとし、内部mutable indexを返さない。
受理したstate syncはrevealed rolesが同値でも1回のatomic commitとして`WorldSnapshot.version`を1進める。
開示の変更は同じcommitに含まれ、同じversionのsnapshot内では値が変化しない。

- 不採用: 開示だけをversion外でlive合成する。以前解消したcurrent actionsと同じく、同一versionの
  snapshot内容が変わりうる。
- 不採用: contentが同じsyncではversionを進めない。history / freshnessなど同時に再基準化した事実を
  update waiterが観測できない。

### A4. Role IDの扱い

**Decision:** role IDはserverから受け取った非空文字列をそのまま保持・返却する。
role名、team、ability、真偽、勝敗上の意味をclient literalや分岐で解釈しない。
ability resultの`revealed_role_id`とは別collection・別APIのままにする。

- 不採用: client側role catalogへの変換。role追加時にPython変更が必要になり、content authorityを複製する。
- 不採用: ability resultと統合。墓場の全量開示と個別ability resultはscope・更新規則が異なる。

### A5. Required test contract

**Decision:** public APIによる非空開示取得、次の空syncによる消去、nested immutabilityを必須testにする。
加えてduplicate / unknown playerのatomic rejectionとrepeated syncのversion進行を固定する。

- 不採用: reducer private fieldだけをassertする。3.3〜3.5が利用するpublic contractを保証しない。
- 不採用: 非空syncのpositive caseだけ。stale secretの消去と外部mutation耐性が未検証になる。

## Explicitly Out of Scope

- Brain interface / Dummy Brain（3.3）
- 発言生成・反応制御（3.4）、投票・能力選択方針（3.5）
- belief、suspicion、strategy、ライン、反応score、重要度評価、履歴要約（Phase 6）
- LLM、prompt model、structured output、embedding / vector search（Phase 4以降）
- Network Clientのqueue、event type、snapshot、action handleの変更
- server、schema、game core、content、visibility、replay retentionの変更
- World Stateの永続化、process restart後のlocal memory復元。server syncから再構築する
- historyの意味ベース圧縮、優先保持、disk spill
- role ID、channel ID、death cause ID、result IDのclient literal登録

## Acceptance Criteria

- `ai_client.world`が`server.aiwolf_core` / `server.network`をimportしない。
- World StateがNetwork Clientのexclusive single consumerとして`ClientEvent`を専用taskで消費し、
  Brainの遅延と独立してqueueをdrainする。
- 初回`game.state_sync`だけからplayer、alive / death、phase / day、self、historyを構築でき、
  caught-up後は別APIの`CurrentActionsView`からcurrent action handleを取得できる。
- 非空`revealed_roles`を含むsyncからraw payloadを読まずplayer IDとrole IDを取得でき、
  後続の空配列syncで以前の開示が消える。
- 同じ事実列をsync historyとlive incrementalで与えた結果が、transport metadataを除いて一致する。
- 保持内replay適用後と、保持外gap後のauthoritative sync適用後が、継続接続時の世界像と一致する。
- chat、CO、vote、random tie result、death、phase transition / timing change、game lifecycle、
  public notify、ability resultを発生順かつtyped queryで取得できる。
- `DAY_EXTENDED` / `DAY_SHORTENED`後のcurrent `PhaseView.phase_ends_at`が更新値になる。
- 現行PUBLIC eventだけでは`unknown_event_count`とunknown completeness warningが増えない。
- 1024 records / 2 MiBを超えず、eviction範囲・件数・query不完全性を観測できる。
- unknown / malformed eventでconsumerが停止せず、state非変更とwarningが観測できる。
- public APIがraw network payloadやmutable内部参照を返さない。
- role IDを受信値のまま保持し、role固有分岐やability resultとの混同が無い。
- `WorldSnapshot.version`の同値性はsnapshot内だけで完結し、dynamicなaction handleは
  `CurrentActionsView`へ分離される。追いついていない間はstale handleを公開しない。
- World State停止後は残りnetwork queue容量だけが猶予であり、drainを再開しなければ
  Network Clientが`CONSUMER_OVERRUN`へ至ることが終了結果から判別できる。
- 9別プロセス完走がWorld State経由のplayer / action / game-end読取でも通る。
- 全テストがLLM無しで走り、Phase 3.1の既存テストを壊さない。

## Required Tests

### Current view and immutability

- syncだけでplayers、alive IDs、deaths / public cause、phase / day / deadline、self role / modifiersを構築する。
- player.list / deaths / action_state全量置換とalive導出。
- public snapshot、record、nested collectionの変更試行が内部stateへ影響しない。
- `WorldSnapshot`を同じversionで2回読んだ内容が同じであり、actionはsnapshot外の
  `CurrentActionsView`から取得する。
- caught-up時だけNetwork Clientのcurrent action handleを公開し、seq先行・disconnect・generation更新時は空。
- `test_state_sync_exposes_revealed_roles_through_public_api`: 非空syncから
  `revealed_role(player_id)`とsnapshot tupleだけで受信role IDを取得できる。
- `test_later_empty_sync_clears_revealed_roles`: 後続`revealed_roles: []`で以前の値が残らない。
- `test_revealed_roles_are_deeply_immutable`: tuple / viewを外から変更できず内部snapshotが変わらない。
- `test_revealed_role_duplicate_rejects_entire_sync`: 同一playerの同値・異値重複をatomicに不受理とする。
- `test_revealed_role_for_unknown_player_rejects_entire_sync`: players外参照で部分適用しない。
- `test_repeated_sync_advances_version_with_revealed_roles_in_same_commit`: repeated syncごとにversionが1進み、
  同じversionのsnapshot内容は不変である。
- ability resultのrevealed roleを墓場開示collectionへ混入させず、受信role IDを意味解釈しない。

### History normalization and query

- chat、CO declaration / report、vote result / reveal、death、phase transitionを発生順に正規化する。
- `GAME_CREATED` / `GAME_ENDED`、`DAY_EXTENDED` / `DAY_SHORTENED`、`PUBLIC_NOTIFY`、
  `TIE_RESOLVED_RANDOM`を各typed recordへ正規化する。
- day timing changeがcurrent day / phase一致時だけ`PhaseView.phase_ends_at`を更新し、
  不一致eventではcurrent viewを巻き戻さない。
- inspect / medium / dead-role / guard private resultを本人のability resultとして取得する。
- sync historyと同じlive event列から同じtyped recordsとCO day indexを得る。
- dayを持たないrecordが直前のphase contextを使い、context不在では`day=None`となる。
- kind / day / player / cursor / limit filterと、CO / ability convenience query。
- `GameEnded`、`ActionRejected`のServerEventとnoticeを二重recordしない。

### Retention

- record countだけ、byte budgetだけ、両方同時の上限到達。
- oldest-first eviction、category index除去、dropped count / order / completeの更新。
- byte上限を単独で超えるrecordの非保持と、その後のrecord保持。
- state syncに上限超過historyを与えても一時的にunbounded local historyを作らない。
- reset後のretention metadataが新しいsync baselineだけを表す。

### Sync, replay, and lifecycle

- initial syncのみとinitial sync + incrementalのfinal semantic equality。
- disconnect → Resume → retention内replayでuninterrupted worldと一致。
- gap detectedでSTALE、保持外syncでold history / indexを置換し、回復後CURRENTとなる。
- sync前incrementalをNetwork Clientが落とす経路でWorld Stateが推測更新しない。
- repeated syncがhistoryを重複appendせずbaselineを置換する。
- client stop / game end / fatal / source iterator closeに対応するWorldStateExit。

### Unknown and malformed input

- unknown top-level event、unknown core event、unknown client noticeがconsumerを停止しない。
- 現行PUBLIC event全種を与えてもunknown countが0で、`GAME_CREATED`だけではwarningが立たない。
- catalog上既知だが型付けしないfixture eventがknown-unmodeled countだけを増やす。
- known core eventのfield欠落・type不正がtyped current / indexを変更しない。
- unknown / known-unmodeled / malformed count、metadata-only record、completeness warningを確認する。
- unknown recordからraw payloadへアクセスできない。

### Concurrency and completion

- slow / hung Brain相当taskがあってもWorld Stateのapplied seqがNetwork Client last_seqへ追いつく。
- burstがNetwork Clientのdefault 1024 event capacity内なら`CONSUMER_OVERRUN`を起こさない。
- 複数readerとsingle consumerでsnapshot versionが単調、partial commitが見えない。
- 1つのNetwork Client event queueへWorld State以外のconsumerを接続しない構成で全eventを取り込む。
- stop / cancellationでconsumer / waiter taskが残らず、Network Clientを直接stopしない。
- consumer failure後にnetwork queueをdrainしなければ、残りslot数分のevent後に
  Network Clientが`CONSUMER_OVERRUN`へ至る連鎖を小容量queueで検証する。
- 9個の別PID driverがWorld State APIだけからalive / phase / action handlesを読み、server tickで完走する。
- 1 client再起動の保持内 / 保持外両ケースで、回復後World Snapshotが継続clientと一致する。
- subprocess import guardで`server.aiwolf_core` / `server.network`非依存を固定する。

## Deliberately Not Decided

- private reducer helper、index container、module内classの分割方法は決めない。
- logical byte sizeを求めるJSON encoderの実装は、UTF-8・決定的・同じrecordで同値なら決めない。
- immutable collectionの具体実装は、nested mutationが内部へ届かずsemantic equalityを保つ限り決めない。
- log文言とmetrics backendは決めない。public retention / warning値だけを契約とする。
