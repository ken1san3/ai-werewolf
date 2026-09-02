# Runbook

ユーザーが打つ言葉は3つだけ。各セッションは
`python scripts/ai_status.py <role>` が出力する対応節で手順を決める。

| ユーザーの指示 | 送る先 | 手順 |
|---|---|---|
| 「レビューして」 | Reviewer | `review` / §2 |
| 「Phase X.Y を詳細設計して」 | Detailed Design | `design` / §4 |
| 「Phase X.Y を実装して」「次のフェーズを実装して」 | Implementer | `implement` / §1 |
| 「レビュー内容を確認して修正して」 | Implementer | `fix` / §3 |

フェーズ番号が指定されなかった場合は `CURRENT_STATE.md` の Next Task に従う。
**次にどれを送るかは Reviewer が決め、Next Task に名指しで書く**（D051）。
方向性の変更や新しい仕様判断は、この4つのどれでもない。ユーザーが別途指示する。

---

## 1. 実装セッション（Implementer / `implement`）

### 1.1 開始時

```
python scripts/ai_status.py implement
Docs/ai/ROADMAP.md      ← 対象サブPhaseの「含む / 含まない / 完了条件」
Docs/ai/spec/DESIGN.md  ← ROADMAP が指定した節のみ
Docs/ai/TEST_POLICY.md  ← 対象サブPhaseに関係するカテゴリのみ
```

`AGENTS.md` はセッション開始時に既に読み込む。`ai_status.py` の出力と
そこが指定した範囲以外は、必要になってから読む。リポジトリ全体を読まない。
`review_archive/` は過去の記録であり、現在の状態を書き足さない。FIXED /
REJECTED / DEFERRED の指摘はここへ退避し、通常のセッションでは読まない。

`Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md` は元になった旧仕様であり、
DESIGN.md と矛盾する箇所がある。**矛盾したら DESIGN.md を優先する。**
`Docs/ai/decisions/` は判断の理由。DESIGN.md の意図が読み取れないときだけ開く。

### 1.2 着手前の確認

- **Design Gate を通ったか確認する**（D051 / §2.5）。対象サブPhaseが
  `DESIGN: REQUIRED` と判定されている場合、`Docs/ai/design/` に該当する詳細設計があり、
  Reviewer の `DESIGN REVIEW: APPROVED` を受けているときだけ実装へ入る。
  判定が無い、または設計が未承認なら、**実装せずユーザーへ報告して止まる**
- `REVIEW_INBOX.md` に `[OPEN]` の Critical / High があれば、**新機能より先に対応する**
- 未コミットの変更があれば先にコミットする
- ROADMAP の「含まない」に書かれたものは実装しない

### 1.3 実装中

- 仕様に疑問が出たら勝手に決めず `OPEN_QUESTIONS.md` へ起票し、
  そこを避けて実装できるなら続行、できないなら止めて報告する
- レビュー観点は `AGENTS.md` の Review checklist にある。自分でも確認する
- テスト出力・200行超の diff・大きい参照文書は、**会話へ入れる前に**
  ローカルLLMで圧縮する（D034）。通さないなら理由が要る。
  ただし合否の値は終了コードと生の最終行から取る
- 設計の形を新たに決めたら `decisions/` へ D0NN として記録する
- **`spec/DESIGN.md` は書き換えない**（Reviewer の担当）。
  DESIGN を直さないと `check_docs.py` が通らない状況になったら、**直さずに報告する。**
  検査側の想定漏れである可能性が高い。DESIGN §5 の「未実装」注記は、
  実装が追いついても検査を落とさない（注記の除去は Reviewer が行う）

### 1.4 終了時

```
[ ] ROADMAP の完了条件を満たしたか確認
[ ] テスト実行（報告は成功数・失敗数・重要エラーのみ。全出力を貼らない）
[ ] `python scripts/check_docs.py` を実行し、不整合を0にする
[ ] git diff 確認 → コミット
[ ] CURRENT_STATE.md 更新（Current Phase / Completed / Next Task / Test Status）
    Test Status には commit hash を入れる。
    Known failing が「なし」以外なら、対応する OPEN / IN_PROGRESS 指摘を残す。
    **Latest Review は書き換えない**（Reviewer の担当）
[ ] 新しい判断があれば decisions/、再発しそうな失敗があれば failures/
[ ] Phase 全体が完了したときのみ handoffs/PHASE<N>_HANDOFF.md を作成
[ ] コミット後、人間へ `git push` を促す（エージェント環境に GitHub 認証情報は無い）
```

報告は「実装したもの / テスト結果 / 未実装 / 次にやること」を各数行。
加えて**ローカルLLMを何に使ったか**を1〜2行（使わなかったならその理由）。D034。

---

## 2. レビューセッション（Reviewer / `review`）

実装は行わない。指摘を `REVIEW_INBOX.md` へ残す。

**Reviewer は Claude と Sol の2 model いる**（D053）。通常レビューはどちらでもよい。
深掘りレビューと詳細設計は Sol。Sol の各レーンは**別チャット**で、文脈を共有しない。

- **`_DESIGN.md` の `Status: APPROVED` は、設計を書いた model と別の model が付ける。**
  Sol が書いた設計は Claude が承認する。文脈ではなく model を変えることが目的
- **起票前に `REVIEW_INBOX.md` と `review_archive/` の両方で同じ日付のIDを確認し、
  最大値+1を使う。** 同じ日に複数レーンが起票するため、inbox だけの採番は衝突する

### 2.1 開始時と読む順

```
python scripts/ai_status.py review
git log / git diff            ← 前回レビュー以降の差分に限定
Docs/ai/spec/DESIGN.md        ← 差分が触れている節
Docs/ai/TEST_POLICY.md
差分のあるコードと、その影響を直接受けるコードのみ
```

変更されていないファイルは、必要が生じない限り読まない。
どれを読むかの絞り込みにローカルLLMを使ってよいが、
**差分に含まれるファイルは減らさず全部開く**（D034）。

**Reviewer 環境からローカルLLMへは到達できない。** Reviewer が動く Linux VM は
Windows とは別ホストであり、llama-server が bind している Windows 側の
`127.0.0.1:8080` は VM からは別物である（`Network is unreachable`）。
したがって Reviewer の報告では
ローカルLLMは常に「環境から到達不可のため未使用」であり、
**サーバが起動しているかどうかとは無関係である。**
ただし **VM から PyPI へは出られる。「ネットワークが無い」ではない。**

**Reviewer VM には実行時依存が入っていないことがある。** 素の状態では
`websockets` が無く `jsonschema` が古いため、ネットワーク系の5モジュールが
import に失敗し、収集が 179 から 132 へ落ちる。エラーは出るので緑にはならないが、
環境ノイズとして流すと**網羅が落ちたまま報告することになる。**
テスト前に `python -m pip install -e ".[dev]"` を実行し、
**収集数が `CURRENT_STATE.md` の Test Status と一致することを確認する**（D034）。

同じ理由で、`usage.jsonl` の `outcome: unreachable` が Reviewer の実行によるものなら、
それはサーバ停止を意味しない。`tool` 欄で実行元を確認すること。
入力長やトークン数の Verification のように**サーバが必要な確認は Implementer が行う。**

### 2.2 確認する

`AGENTS.md` の Review checklist を必ず通す。加えて:

- DESIGN.md との差異（実装が設計から外れていないか）
- ROADMAP の完了条件を満たしているか
- TEST_POLICY の該当項目がテストとして存在するか
- 情報漏洩（private が broadcast に乗っていないか、内部死因が外へ出ていないか）
- 競合状態、async の扱い
- 拡張性を壊す実装
- 指摘の原因が Reviewer 側の文書にある場合、**その文書を直したうえで**起票する

テストは実際に走らせて結果を確認する。
`scripts/check_docs.py` も走らせる。文書と実装のずれはここで機械的に落とす。

### 2.3 起票の形

```
## R-YYYYMMDD-NN [OPEN] Critical|High|Medium|Low

File:
Problem:
Required:
Verification:
```

- 長い解説を書かない
- Reviewer 側の文書（DESIGN.md / TEST_POLICY.md）に原因がある指摘は、
  **その旨を明記し、修正は Reviewer が行うと書く**
- 終了時に `CURRENT_STATE.md` の Latest Review と Test Status を更新する
- `python scripts/check_docs.py` を実行する。**DESIGN / ROADMAP / TEST_POLICY を
  書き換えたら必ず走らせる。** 実装より先に書いたルールは DESIGN §5 の該当行へ
  「未実装（Phase X.Y）」と注記すれば検査を通る

### 2.4 報告の最後に「次に誰へ何を送るか」を書く

**レビュー報告は必ずこれで締める。** ユーザーが判断せずに済む形で、
そのまま送れる文面を1つ名指しする。省略しない。

```
## 次に送る指示

→ <役割> / <model> へ「<そのまま打つ文面>」

理由: 1〜2行
並行して送れるもの: あれば1行（無ければ書かない）
先に潰すべきもの: あれば1行
```

対応表は `AGENTS.md` の「このリポジトリの動かし方」にある。ここへ複製しない。
同じ内容を `CURRENT_STATE.md` の Next Task にも残す。
**チャットだけに書かない。**

**Next Task には機械が読む宣言を2行入れる**（D052）。書き忘れると
`check_docs.py` が落ち、`ai_status.py implement` が `blocked` を出す。

```
Target subphase: 3.1     ← 行頭に1回。ROADMAP に `## 3.1` 節が実在すること
Design gate: REQUIRED    ← REQUIRED / NOT REQUIRED のどちらか。行頭に1回だけ
```

`REQUIRED` なら `design/PHASE<N>_<M>_<名前>_REQUEST.md` を1枚置く。
`NOT REQUIRED` なら依頼書を作らない。**宣言と依頼書が食い違うと門が止まる。**
散文中で行頭から `Design gate:` と書き始めない（2件目の宣言として数えられる）。

**対象を次のサブPhaseへ進める前に、そのサブPhaseの ROADMAP 節を書く。**
`## 3.2`〜`## 3.5` はまだ無い（R-20260901-82）。節が無いまま
`Target subphase` を進めると `check_docs.py` が落ちる。

複数を並行して送れるときも、**最初に送る1つを先頭に置く。**
「どれでもよい」と書かない。順序に理由があるならそれを書く。

### 2.5 Implementation Design Gate（D051 / D052）

**門の出力は2値である。** `DESIGN: NOT REQUIRED` は正常な結論であり、
「未確定」でも「準備不足」でもない。NOT REQUIRED と判定したサブPhaseには
依頼書を作らない。機械側もこの状態を表現できる必要がある。


**新しい実装タスクへ進む前に、Reviewer が2値で判定する。**
判定リストに当たれば必要、当たらなければ不要。迷いを理由に必要へ倒さない。

不要（Implementer へ直行）: 局所的な bug fix / Reviewer 指摘への明確な修正 /
validation 追加 / テスト追加 / 既存 pattern に従う実装 /
canonical design から実装方法がほぼ一意 / public API と state 構造を新設しない /
component 間の責務変更が無い / 小規模な既存機能拡張。

必要（Detailed Design へ）: 新しい subsystem・module / 複数 component の責務分担 /
新しい state machine / lifecycle / async・concurrency / queue /
timeout・reconnect / protocol との複雑な相互作用 / public API の新設 /
影響が複数モジュールへ広がる / 実装方法が複数あり選択を誤ると手戻りが大きい /
canonical design が目的だけを定め実装構造を定めていない / Phase の中核となる新機能。

```
DESIGN: NOT REQUIRED          DESIGN: REQUIRED

Reason:                       Reason:
Implementation scope:         Design scope:
Files to read:                Relevant canonical sources:
Acceptance criteria:          Constraints:
                              Out of scope:
                              Questions Sol must resolve:
```

Detailed Design の成果物は**実装開始前に必ず Reviewer が読む。** 確認するのは、
canonical design / accepted decision / protocol・schema と矛盾しないこと、
責務分離・state・lifecycle・failure handling・concurrency 前提が明確なこと、
将来 Phase を先取りしていないこと、Implementer が追加の重要設計判断をせず書けること、
そして**詳細すぎてコードの二重管理になっていないこと。**
問題が無ければ `DESIGN REVIEW: APPROVED` とし、Implementer 向けの実装入口を明示する。

実装後のレビューでは、詳細設計を正しい前提として扱わない。確認は
**canonical source → 実コード → schema → tests → approved detailed design** の順。
**設計どおりでも canonical specification に反していれば指摘する。**

---

## 3. レビュー修正セッション（Implementer / `fix`）

新機能は実装しない。

### 3.1 開始時

```
python scripts/ai_status.py fix
Docs/ai/spec/DESIGN.md    ← 指摘が参照している節だけ
指摘された実装ファイル
```

`ai_status.py` が `REVIEW_INBOX.md` の OPEN / IN_PROGRESS 指摘を本文ごと出力する。
`Docs/ai/SPEC_REVIEW.md` は元仕様への指摘履歴であり、修正対象ではない。

### 3.2 対応順

Critical → High → Medium → Low。

- 「修正は Reviewer が行う」と書かれた指摘は**触らない**
- Reviewer 推奨が示されている指摘はそれに従う。
  異論があれば実装せず `OPEN_QUESTIONS.md` へ起票する
- 設計の形を変える必要がある場合、形は Codex が決めてよい。
  決めたら `decisions/` へ記録する。**DESIGN.md は書き換えない**

### 3.3 各指摘の完了時

`REVIEW_INBOX.md` の該当項目を編集する。

- `[OPEN]` を `[FIXED]` に変える
- 直下に `Fix:` の1行を足し、何をどう直したかを書く
- 対応しない判断は `[REJECTED]` または `[DEFERRED]` にし、理由を書く
- **項目を削除しない**

### 3.4 終了時

§1.4 と同じ。加えて `REVIEW_INBOX.md` に `[OPEN]` が残っていないか確認する。

報告は「対応した指摘ID / 変更したファイル / テスト結果 / 未対応と理由」を各数行。
加えて**ローカルLLMを何に使ったか**を1〜2行（使わなかったならその理由）。D034。

---

## 4. 詳細設計セッション（Detailed Design / `design`）

**実装しない。テストも書かない。** 成果物は `Docs/ai/design/` の Markdown 1枚。

### 4.1 開始時

```
python scripts/ai_status.py design
Docs/ai/design/<対象>_REQUEST.md  ← Reviewer が出した依頼書。これが入力
Docs/ai/ROADMAP.md                ← 対象サブPhaseの 含む / 含まない / 完了条件
依頼書の Relevant canonical sources が名指しした節とファイルだけ
```

依頼書が Out of scope に置いたものは設計しない。
`ROADMAP.md` のスコープを設計側で広げない。広げたくなったら
**設計へ書かず Reviewer へ報告する。**

### 4.2 書く

Purpose / Files・modules / Responsibilities / Public interfaces / Data flow /
State・lifecycle / Main control flow / Failure handling /
Concurrency assumptions / Explicitly out of scope / Acceptance criteria /
Required tests。

- **完成コードを書かない。関数内部を1行ずつ指定しない。**
  シグネチャは公開 API に限る。内部ヘルパーは列挙しない
- 依頼書の Questions must resolve には全部答える。
  **採らなかった案と、採らなかった理由を1〜2行ずつ**添える
- どちらでもよいと判断した点は「決めない」と明示する。
  Implementer に暗黙の設計判断を残さない
- canonical（`spec/DESIGN.md` / `ROADMAP.md` / schema / `decisions/`）と
  矛盾したら設計を曲げる。canonical のほうを直したくなったら Reviewer へ報告する

### 4.3 ファイルと Status の約束

`Docs/ai/design/` には対象サブPhaseごとに、設計単位の REQUEST / DESIGN の対を置く。
同じサブPhaseに複数の設計単位が並んでもよく、ファイル名の stem で対応付ける。
同じ設計の改訂は既存の `_DESIGN.md` を `DRAFT` へ戻し REQUEST に Addendum を足す。
別の設計を追加するときは、新しい stem の REQUEST / DESIGN の対を作る。

各 REQUEST の `Request status:` は `OPEN` または `CLOSED` を取る。
未記載の既存 REQUEST は `OPEN` とみなす。`CLOSED` は対応する `_DESIGN.md` が
`Status: APPROVED` で、実装と検証まで完了したときだけ設定する。Design Gate が数えるのは
開いている REQUEST だけであり、`CLOSED` の要求は除外する。対応設計が未承認・欠落の
`CLOSED` は除外せず、`check_docs.py` で不整合として報告する。

| ファイル | 誰が書くか | 何のためか |
|---|---|---|
| `PHASE<N>_<M>_<名前>_REQUEST.md` | Reviewer / Implementer | 依頼。`DESIGN: REQUIRED` と `Request status:` の宣言 |
| `PHASE<N>_<M>_<名前>_DESIGN.md` | Detailed Design | 成果物 |

**`Status:` 行は先頭に1行、値は次の語のいずれか1つで始める。**
そのあとに ` — 一言` を足してよいが、**語の前に何も置かない。**
散文で「APPROVED 待ち」などと書かない（機械が承認済みと読む）。
**区切りは em dash `—`（U+2014）だけ。** `-` や `–` は語彙外として扱われ、
実装が止まる（D052）。

```
REQUESTED   依頼を出した（REQUEST の既定値）
DRAFT       設計を書いている途中
IN_REVIEW   Reviewer のレビュー待ち
APPROVED    Reviewer が承認した。実装へ渡してよい
SUPERSEDED  作り直した。後継を1行で指す
```

**設計作業の承認は `_DESIGN.md` の `Status:` に書く。** `_REQUEST.md` の
`Status:` は依頼の作業状態として `REQUESTED` のまま置いてよい。
実装と検証まで終えたときだけ、別行の `Request status: CLOSED` へ更新する。
Design Gate は `_DESIGN.md` の承認状態と、REQUEST の open / closed の両方を見る。

### 4.4 終了時

```
[ ] `Docs/ai/design/<対象>_DESIGN.md` を作成
[ ] 先頭に `Status: IN_REVIEW` を置く（`APPROVED` へ変えるのは Reviewer）
[ ] 依頼書の Questions must resolve に全部答えたか確認
[ ] `python scripts/check_docs.py` を実行
[ ] 実装へ渡さない。Reviewer の `DESIGN REVIEW: APPROVED` を待つ
```

報告は「決めたこと / 決めなかったこと / 依頼書から外れた点と理由」を各数行。
