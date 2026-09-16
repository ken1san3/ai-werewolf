# 開発・テスト運用方式の即時変更

現在の開発では、テスト実行中に新しい検証・調査・レビュー・追加テストが次々に派生し、製品実装よりも検証手続き自体の開発に工数が偏っている。

この状態を終了するため、**ここからテスト運用方式を全面的に変更する。**

以下を最優先の運用規則として適用すること。

---

## 1. 現在実行中のテストを停止する

現在実行中、待機中、または途中状態にあるテストは停止してよい。

そのテスト結果をPhase完了判定に使用する必要もない。

現在のテスト実行から派生している以下のものも、原則として打ち切る。

* 追加テスト
* 追加監査
* 追加Investigator
* fresh review
* evidence再検証
* provenanceの追加設計
* provenanceの独立レビュー
* テスト実行結果を証明するためだけの追加テスト
* 既存検証ハーネスを検証するためだけの追加検証

特に、現在進行中のテストを完遂することを優先してはならない。

**現在の実装成果を保存した上で、テスト計画を一度リセットすること。**

なお、既存の正常な製品コード・既存pytest・既に有効性が確認されているテストまで削除する必要はない。

破棄対象は主に「現在進行中のテストサイクルと、その途中結果・未完了タスク」である。

---

# 2. 今後は「テスト計画 → 全テスト実行」の順序を固定する

今後、以下のような進め方は禁止する。

```text
テスト
↓
問題発見
↓
新しいテストを設計
↓
実行
↓
別の問題発見
↓
さらに新しいテストを設計
↓
……
```

代わりに必ず、

```text
実装完了
↓
テスト計画作成
↓
テスト計画レビュー
↓
TEST PLAN FREEZE
↓
全テストを実行
↓
全結果を一括集計
↓
FAILを一括分析
↓
一括修正
↓
必要範囲を再テスト
↓
最終回帰テスト
```

の順で進める。

---

# 3. Master Test Planを先に完成させる

Phaseごとに、テストを実行する前に **Master Test Plan** を作成すること。

Master Test Planには最低限、各テストについて以下を記載する。

* Test ID
* 検証対象
* 検証目的
* 実行方法
* 入力
* PASS条件
* FAIL条件
* deterministic / synthetic / real-game の区分
* Phase completion blockerかどうか
* 必要な既存テスト
* 必要な実LLMゲーム試験

テスト項目をすべて列挙してから実行へ進む。

---

# 4. TEST PLAN FREEZEを導入する

Master Test Plan承認後、

**TEST PLAN FREEZE**

を宣言すること。

FREEZE後は、原則として新しいテストケース・調査タスク・レビュータスクを追加してはならない。

Reviewer、Tester、Investigatorが独自判断でテスト範囲を拡大することも禁止する。

Reviewerの役割は、

* PASS
* FAIL
* BLOCKED

を既存Test IDに対して判定することに限定する。

Reviewerが、

「念のため追加検証するべき」
「さらにこの境界も確認するべき」
「別のfresh reviewerで確認するべき」

などを理由として新しいcritical pathを生成してはならない。

---

# 5. 実行中に新しい問題を発見した場合

Test Plan外の問題を発見しても、その場で新しいテストを設計しない。

以下の形式で Deferred Finding として記録する。

```text
DF-xxx

現象:
影響:
再現条件:
Severity:
Phase completion blocker: YES / NO
推奨対応:
```

## blocker = NO の場合

現在のテストサイクルでは対応しない。

次Phaseまたはtechnical debtとして保留する。

## blocker = YES の場合

以下に該当する場合のみblocker候補とする。

* クラッシュ
* データ破壊
* private情報漏洩
* security上の重大問題
* Phaseの明示的完了条件を満たせない
* ゲームが進行不能
* 既存主要機能を破壊する重大regression

単なる、

* 将来的な懸念
* 理論上の境界条件
* provenanceの完全性
* evidence管理の美しさ
* reviewer独立性の改善
* 証拠ファイル管理の改善
* より厳密な監査可能性

はblockerにしてはならない。

---

# 6. FAILは一件ずつ処理しない

以下は禁止する。

```text
FAIL A
→ Investigator
→ Fix
→ Tester
→ Reviewer

FAIL B
→ Investigator
→ Fix
→ Tester
→ Reviewer
```

全テストをまず最後まで実行する。

その後、

```text
FAIL一覧
↓
一括Triage
↓
原因グループ化
↓
Repair Plan
↓
一括修正
```

とする。

同じroot causeで複数FAILしている場合、1件の修正として扱う。

---

# 7. テストを2段階に分ける

## Stage A: deterministic / synthetic

先に以下をまとめて実施する。

* unit test
* integration test
* schema validation
* privacy
* memory
* transaction
* boundary
* regression
* deterministic fixture
* synthetic fixture

この段階では原則として実LLMを使用しない。

Stage Aが安定してからStage Bへ進む。

---

## Stage B: real LLM game

実LLMを使用する高コスト試験は最後にまとめる。

可能な限り、**1回の実ゲームから複数項目を同時に評価する。**

例えば1ゲームから、

* ゲーム完走
* 前発言へのreaction
* question → answer
* rebuttal
* belief change
* pre-vote reconsideration
* private情報漏洩
* token使用量
* queue wait
* generation latency
* repetition
* dialogue quality

を同時に測定する。

1項目ごとに別ゲームを実行してはならない。

---

# 8. 実LLM試験回数を無制限に増やさない

Phase完了用の実LLM試験は、事前のMaster Test Planで回数を決定する。

失敗するたびにゲーム数を追加してはならない。

追加実行が必要な場合は、既存FAIL修正後の確認としてのみ実施する。

---

# 9. Repair Cycleを制限する

原則として1 Phaseにつき、

```text
Master Test Run
↓
Repair Cycle 1
↓
Regression Run
↓
必要なら Repair Cycle 2
↓
Final Run
```

までとする。

Repair Cycleを無制限に増やしてはならない。

Final Run後に残った非blocker問題は、

* Deferred Finding
* technical debt
* 次Phase

へ送る。

「すべての潜在問題がゼロになるまでPhaseを終了しない」という運用は禁止する。

---

# 10. provenance / evidenceを簡素化する

高度なprovenanceシステムを新規開発しない。

最低限、

```text
evidence/
├── game/
└── synthetic/
```

のように実ゲーム由来とsynthetic由来を物理的に分離する。

必要ならファイル名またはディレクトリ名へTest IDを含める。

例:

```text
game/P6-T10/
synthetic/P6-T03/
```

これ以上の、

* provenance schema
* 独立provenance review
* producer ownership証明
* 時刻相関による所有推定
* provenance専用監査

は、Phaseの明示的要求がない限り作らない。

T288 / T290相当のprovenance設計・独立レビューはCANCELLEDとしてよい。

---

# 11. 96 → 512 tokenについて

出力上限は512 tokenを採用してよい。

理由:

* 実測最大510 tokenを包含する
* REBUTTAL / strategyだけでなくpre-vote reconsiderationを切らずに評価できる
* schema変更を必要としない

ただし512は「毎回512 token生成する」という意味ではなく最大値である。

最初の実LLMゲームで必ず、

* queue wait
* generation latency
* day全体の所要時間
* token使用量

を計測する。

性能問題が実測された場合に初めて最適化を行う。

実測前に256へ落として機能を制限してはならない。

---

# 12. 現在のPhase 6について

まず現在の実装状態を固定し、現在走っているテストサイクルを停止する。

その後、Phase 6のMaster Test Planを一度だけ作り直す。

Phase 6の最終確認では、最低限次を評価対象とする。

1. 9 AIでゲームが完走する
2. 前の発言を受けた応答が成立する
3. question → answer が成立する
4. 主張へのrebuttalが成立する
5. 新情報によるbeliefまたは判断更新が成立する
6. pre-vote時の再評価が成立する
7. private情報が他AIへ漏洩しない
8. bounded memoryが維持される
9. 異常な同文・同主張反復がない
10. 既存主要regression testがPASSする
11. token / latency / queue waitを実ゲームから取得する

必要に応じて既存の有効なTest IDをここへ統合してよい。

---

# 13. 開発の優先順位

以後の判断優先順位を以下とする。

1. 実際のゲームが正しく動くこと
2. Phaseの明示的完了条件
3. private情報漏洩など重大安全性
4. regression防止
5. 実ゲームの会話品質
6. 性能
7. テスト運用の便利さ
8. evidence / provenance / auditの完全性

下位項目の完全性を高めるために、上位項目の完成を長期間停止させてはならない。

---

# 14. 最初に行うこと

この指示を受領後、コード修正や新しいテスト実行を直ちに開始しないこと。

最初に以下だけを行う。

1. 現在実行中のテストを停止
2. 現在のコード・成果物の状態を保存
3. 現在進行中のテスト・監査・調査タスクを整理
4. CANCELLED / DEFERRED / KEEP に分類
5. Phase 6 Master Test Planを作成
6. 今後実行する全Test IDを一覧化
7. TEST PLAN FREEZE候補を提示

その時点で、現在の実装を変更せずに一度報告すること。

報告には、

* CANCELLEDにしたタスク
* DEFERREDにしたタスク
* KEEPしたタスク
* Master Test Plan
* 各テストのPASS条件
* Stage A / Stage Bの分類
* 想定される実LLMゲーム実行回数

を記載すること。

**テスト項目を後から無制限に追加する運用をここで終了する。**

目的は「監査手続きの完全性」ではなく、**AI人狼を完成させること**である。
