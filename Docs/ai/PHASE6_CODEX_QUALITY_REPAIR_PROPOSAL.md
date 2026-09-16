# Phase 6 会話品質修正の提案（Codex引き渡し用）

Status: PROPOSAL（未承認）
作成: 2026-09-16
作成者: Claude Code（評価・提案のみ。製品コード変更0、実ゲーム実行0、provider操作0）
根拠原本: `logs/t372-review/fresh-session/game-review-recovery-r3.json`
  SHA256 `6f0cfae88a9a57ba489dfcbe910a11d24ac3673498d5866ba05e672c65409d75`（本提案作成時に再計算・一致）

この文書は Codex へ渡す作業提案である。承認・dispatch・凍結の権限は持たない。
既存の D073 運用規則、合格式、ゲーム規則、期限値、P3保留、private保全、独立gateを変更しない。

---

## 1. 提案の要点

**次の修正対象は admission / queue / deadline / 実行治具ではなく、討論生成そのものである。**

直近の T370〜T377 サイクル（期限管理の限定修正）は手続きとして正しく、UNKNOWN/POISONED 連鎖を 0 件に
抑え込むという実効果を出した。しかしゲームは依然 `game_end=false` で、Stage B は PASS 1 / FAIL 4 / BLOCKED 6。
r3 の保存原本は、完走を妨げている欠陥が **会話生成側の 3 点** であることを数値で示している。

この 3 点は **実LLMゲームを追加実行せずに** 着手・検証できる。

---

## 2. 実測（recovery-r3、2026-09-16T07:38:51Z〜07:59:06Z、1214.462秒、exit1）

| 観測 | 値 | 読み方 |
|---|---:|---|
| provider call | 103 | 全件 PROVEN_TERMINAL、backend failure 0、UNKNOWN 0、poison 0 |
| provider latency mean / median / max | 8.349 / 7.653 / 18.732 秒 | |
| ゲーム総wall | 1200.009 秒 | 上限で打ち切り |
| provider稼働率（推定） | 71.7% | 859.9秒 / 1200秒 |
| queue待ち median / p95 / max | 39.285 / 79.059 / 90.590 秒 | 同時実行1に対する滞留 |
| EXPIRED / OVERLOADED | 21 / 4 | 期限切れと過負荷で捨てられた要求 |
| generation records | 143 | DECISION 38 / OUTPUT_INVALID 49 / REPAIR_FAILED 9 / PROMPT_REJECTED 39 / CANCELLED 8 |
| **応答の契約適合** | **38 / 96** | finish_reason=stop の 96 件中、契約を通ったのは 38 件 |
| **PROMPT_TOO_LARGE** | **39 件** | proxy 最大 8360 に対し上限 8192 |
| **反復違反** | **24 / 33** | 受理発話 33 件中。同player直前反復 9 件 |
| 受理発話の speech act | NONE 32 / RELATION_HYPOTHESIS 1 | 質問・反論がほぼ発生していない |
| pre-vote 再評価 | 0 件 | B06 FAIL |
| 計測 B10 | PASS | usage/timing 全件既知、metrics drop 0 |

### 2.1 完走しない理由（算数）

同時provider 1・1call 8.349秒・ゲーム上限 1200秒 → **理論最大 143 call**。
実績 103 call（稼働率71.7%）から得られた有効決定は 38 件。

つまり **provider を 2 倍速くしても、応答の約 6 割が捨てられている限り完走しない。**
逆に契約適合率を 38/96 から実用水準へ引き上げれば、同じ call 数で約 2.5〜3 倍の進行が得られる。
GPU 性能不足を唯一の原因と断定する根拠は、現時点の原本には無い（r3 判定文も同旨）。

---

## 3. Codex への依頼

### W1. prompt 予算の下限超過（B08 FAIL / PROMPT_TOO_LARGE 39件）

- 対象: `ai_client/discussion/projection.py`
  - `DiscussionProjectionConfig.max_token_proxy_units = 8192`（47行目、ceiling も 63行目）
  - `effective_proxy = min(8192, config.max_token_proxy_units)`（478行目、768行目）
  - 縮約ループと送出拒否: 588〜700行目、`PROMPT_TOO_LARGE` は 454 / 590 / 606 / 612 / 699 / 802行目
- 症状: 観測 proxy 最大 8360 に対し上限 8192。**超過は約 2%（168 units）** で、
  縮約ループが excerpt を空にしても収まらない経路が存在する。つまり
  **system message + decision schema + state セクションからなる「縮約不能な下限」が上限をわずかに超えている**可能性が高い。
- 依頼: 縮約不能部分の proxy 実測内訳（system / schema / state / memory / recent）を算出し、
  超過の発生源を特定したうえで、最小の修正で恒常的に上限内へ収める。
- 判断が必要な点: schema 側を縮めるか、`max_token_proxy_units` を実測に合わせて引き上げるか。
  **上限値の変更は合格式・設計の変更にあたるため、Codex は数値根拠と選択肢を提示するまでとし、変更は承認後とする。**
- 受入条件: 保存済み r3 原本の入力から再構成した投影で PROMPT_TOO_LARGE が 0 になること。既存 pytest 全PASS。

### W2. 出力契約の不適合（応答 38/96、OUTPUT_INVALID 49 + REPAIR_FAILED 9）

- 対象: `ai_client/llm/prompt.py`（406 / 564 / 612行目）、`ai_client/discussion/model.py`（生成ステータス定義 1120〜1170行目）、
  `ai_client/discussion/transaction.py`（55〜64行目の終端理由マッピング）
- 症状: finish_reason は 96 件すべて `stop`、`response_text_empty=0`、`response_bytes_mismatches=0`。
  すなわち **モデルは最後まで生成しており、生成物が自前の契約検証で落ちている。** 修復試行も 9 件が REPAIR_FAILED。
- 依頼: r3 の保存原本にある不適合応答の実文を分類し、
  (a) schema 定義側の過剰制約、(b) prompt 指示と schema の不一致、(c) 修復プロンプトの無効性
  のいずれであるかを分離する。分類結果に基づき最小修正を提案・実装する。
- 注意: T348 で確認済みの think prefix / grammar 衝突（`Docs/ai/failures/2026-09-15_T348_THINK_GRAMMAR_COLLISION.md`）と
  同一原因かどうかを先に判定すること。既知原因の再調査で工数を重複させない。
- 受入条件: 保存原本の応答集合に対する再検証で適合率が有意に改善すること。既存 pytest 全PASS。数値は実測で示す。

### W3. 反復抑制が未実装（B09 FAIL、24/33 違反）

- 対象: `ai_client/discussion/` 配下
- 確認済みの事実: `ai_client/discussion/` に反復・類似判定の実装は**存在しない**。
  `duplicate` の既存出現はすべて ID 重複や二重終端の検査であり、発話内容の反復抑制ではない。
  「反復」は設計・テスト文書にのみ存在し、製品側は未実装である。
- 依頼: 判定側（NFKC / strip / Unicode空白正規化での一致）と同じ正規化を用いた発話反復の抑制を、
  最小スコープで設計・実装する。直前同一player反復（9件）を優先する。
- 受入条件: 決定的テストで反復が抑止されること。既存 pytest 全PASS。

### 優先順位

**W2 → W1 → W3。** W2 が最も進行への寄与が大きく（call あたりの有効決定数を直接押し上げる）、
W1 は 39 件の即時損失を止める。W3 は完走後の品質判定（B02〜B05）に効く。

---

## 4. スコープ外（今回は作らない・触らない）

D073 と 2026-09-14 のユーザー運用指示に従い、以下は本提案に含めない。

- 新しい実LLMゲームの実行、追加 probe、provider の起動・停止・設定変更
- freeze / preflight / launch adapter / 一意性検査など**実行治具の新規開発と改修**
- 新しい provenance schema、証拠管理層、証拠を証明するための追加試験
- 新しい Test ID、追加 review chain、新しい critical path の生成
- 合格式、ゲーム規則、人数、同時provider数、各期限値、P3保留の変更
- 旧 FAIL / UNKNOWN の読み替え、原本の改変・移動・削除

計画外の発見は `Docs/ai/failures/` へ Deferred Finding 形式で記録し、非blocker は次Phaseへ送る。

---

## 5. 検証方法（実LLM不要）

1. 対象は保存済み原本 `logs/t375-verification/game-recovery-r3-20260916` と上記 r3 判定 JSON。
2. 再現・検証は既存 pytest と決定的 fixture のみで行う。新しい実ゲームを条件にしない。
3. 3件の修正後、既存回帰（直近記録は 344 PASS + 179 subtests）を通す。
4. 実ゲームは 3 件すべての修正と独立検証が揃った後に **1 回だけ**。回数の追加はユーザー承認事項。

### 5.1 全件 pytest の独立実測（2026-09-16、本提案作成者による）

通常 Owner ホストで `python -m pytest`（`testpaths=["tests"]` 全件）を実行した実測。

```
4 failed, 1327 passed, 4 warnings, 1083 subtests passed in 1559.27s (0:25:59)
```

FAIL 4 件はすべて時間依存の completion 系である。

| テスト | 失敗内容 | 既知履歴 |
|---|---|---|
| `test_phase3_4_completion.py::test_all_seat_and_one_silent_reaction_scenarios_complete` | SUBFAILED(`one-silent`)、発話回数の不一致 | T051 既知flake（BLOCKED） |
| `test_phase3_4_reaction_chat.py::test_deadline_extension_creates_one_remaining_chat_opportunity` | `CancelledError` → `TimeoutError`。`_wait_until` の固定 1.0 秒上限 | **文書化された履歴なし** |
| `test_phase3_5_completion.py::test_nine_process_cumulative_reservations_are_exact_and_reproducible` | `same-a scenario failed: 1 != 0`（player-0） | `Docs/ai/failures/2026-09-11_PHASE3_5_CUMULATIVE_CHAT_TIMING.md` |
| `test_phase5_completion.py::test_one_broker_nine_production_llm_clients_complete` | 完了集合に player-0 / player-8 が不足 | `Docs/ai/failures/2026-09-11_PHASE5_ADMISSION_TERMINAL.md` ほか |

実行条件の限定: 同一ホストで `llama-server.exe`（PID 572、約7.4GB）が稼働中であり、
completion 系は別プロセス起動とタイミングに敏感である。**この 4 件を確定的な regression とは断定しない。**

重要な差異: 記録されている「344 PASS ＋ 179 subtests」は**固定モジュール部分集合**の測定であり、
全件の測定ではない。**現時点のリポジトリには全件 pytest の最新 baseline が存在しない。**
上表がその最初の実測値である。

Codex への追加依頼: W1〜W3 の前後で全件 pytest を実測し、上表 4 件を基準線として比較すること。
`test_deadline_extension_creates_one_remaining_chat_opportunity` は既知履歴が無いため、
負荷由来の flake か、T374 の期限修正に関係する退行かを判別すること。なお同ファイルには作業ツリー上で
`brain_timeout_seconds` を 0.01 → 0.2 へ緩めた未コミットの 1 行変更が既に入っている（別テスト）。
**閾値の緩和で FAIL を消す対応は行わず、原因を判別してから扱いを決めること。**

---

## 6. 併せて報告すべき運用リスク（Codex の作業前提）

1. **未コミット**: HEAD は `71607b1`（2026-09-12）。作業ツリーに +10,625 行、未追跡 548 ファイル。
   うち Docs 528 / コード 16。4日分の成果がコミットされていない。
2. **証拠が Git 管理外**: `logs/` は `.gitignore` 済み。**673MB / 160 ディレクトリ**。
   PASS/FAIL の根拠がローカルのみに存在し、バックアップ状況は未確認。
3. **provider 常駐**: `llama-server.exe` PID 572 が 127.0.0.1:8080 で稼働中（約7.4GB）。
   今回限りの許可で起動され、停止されていない。停止判断はユーザー。

Codex は 1 と 2 を作業開始前にユーザーへ確認すること。コミット・保全は勝手に行わない。

---

## 7. 運用方向の評価（背景、Codex は変更しない）

D073 / 2026-09-14 リセット指示との照合結果。Codex への依頼内容の理由として記す。

| 指示 | 実態 | 判定 |
|---|---|---|
| 実装 → 計画 → FREEZE → 一括実行 → 一括修正の固定順序 | 直近 T370 サイクルは順序を遵守 | 適合 |
| 1ゲームで複数項目を同時評価 | r3 は 11 項目を 1 ゲームで判定、retry 0 | 適合 |
| repair は 1 Phase 最大 2 cycle | R09/R10/R11/R13/R14/R16/R17 + sampler + freeze + deadline ≒ 9 cycle | 逸脱 |
| 実ゲームは 1 回、失敗ごとに追加しない | T307 / T316 / T330 / T344 / R7 / r3 = 6 回（各回ユーザー個別許可） | 部分逸脱 |
| FREEZE 後に治具・調査・review 層を増やさない | freeze 改訂・addendum が 6 回（T317/T329/T346/T361/T366/T377） | 逸脱 |
| 証拠管理の完全性だけを blocker にしない | T377 は control 文書の重複 1 件で preflight 停止 → 専用修正タスク化 | 逸脱 |

リセット後 2 日間の新規ドキュメント 230 件（handoff 104 / task packet 92）に対し、新規製品コードは 16 ファイル。
**リセット後の 92 タスクを分類した結果、Phase 6 の目的である会話品質の製品コードを修正したタスクは 0 件。**
修正系 19 件はすべて admission / deadline / sampler / collector / 文書 / 実行治具に向いている。

「テスト→新テスト」の反復は止まった。代わりに「テストを走らせる治具→治具の不具合→治具の修正→治具のレビュー」に
置き換わっている。本提案はこのループを抜けて製品側へ戻すためのものである。

Stage B 判定の推移は改善方向にはある: BLOCKED 11 → 9 → 8 → 8 → 8 → 6、PASS 0 → … → 1。
B02〜B05 / B07 / B11 の 6 項目は「ゲーム完走」が前提条件で BLOCKED のため、
**完走を成立させれば 6 項目が一度に判定可能になる。** W1〜W3 はその完走に直接効く。

---

## 8. 未解決・前提

- W1 の上限値変更可否、W2 の schema 変更可否はユーザー／Reviewer の決定事項。Codex は根拠提示までとする。
- queue 滞留（median 39.285秒）と同時provider 1 の設計判断は本提案の対象外。W1〜W3 の後に別途評価する。
- 本提案は新しい PASS / 承認 / 完了を意味しない。Phase 6 / Stage B は FAIL のままである。
