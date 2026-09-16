# Phase 6 方向転換指示（ユーザー → Main Integrator）

作成: 2026-09-14
性質: **ユーザーによる product scope / direction の決定。** `AGENTS.md` の
「The user retains final authority over product rules, scope, and direction」に基づく。

## 0. この指示で変更してよいファイル / 変更してはならないファイル

**変更する（product / coordination）**

- `Docs/ai/ROADMAP.md` — Phase 6 完了条件
- `Docs/ai/decisions/D072_*.md` — 本決定の記録（新規）
- `Docs/ai/CURRENT_STATE.md` — scope / holds / critical path
- `Docs/ai/TASKS.md` — 該当 task の lifecycle
- `tests/` `scripts/` `.gitignore` `content/` — 実装・テスト

**変更しない（開発基盤）**

- `AGENTS.md`
- `Docs/ai/OPERATIONS.md` — ただし §「acceptance evidenceの永続保全」内の
  **「新規受入rawの生成を保留」の 1 文のみ** 第 2 節に従って解除する。他は触らない
- `Docs/ai/INDEX.md` / `WORKFLOW.md` / `roles/`
- `scripts/ai_status.py` / `scripts/check_docs.py`
- 既存の decisions（D051 / D065 / D066 / D067 / D068 / D069 / D070 / D071）

独立 Tester / fresh Reviewer / Design Gate / self-approval 禁止 /
「新規独立 session を確保できなければ BLOCKED」は **すべて維持する**。

---

## 1. 方向転換の理由（事実）

T283 が 2026-09-14T03:23:37 UTC に canonical 語彙で 24/24 の content token を実測した。

| witness | compact | indented |
| --- | ---: | ---: |
| baseline none（最小応答） | 78 | 144 |
| peer ANSWER | 157 | 300 |
| OPINION_CHANGE | 186 | 331 |
| REBUTTAL | 208 | 372 |
| strategy + assessment | 232 | 406 |
| nine-seat pre-vote | 368 | 510 |

現在の whole-response 上限は **96 token**。最小応答 78 すら余裕がなく、
REBUTTAL / OPINION_CHANGE / pre-vote 再評価という Phase 6 の中核機能は
すべて length-finish で拒否される。

つまり **Phase 6 の製品は現在 4〜5 倍の設定ミスを抱えたまま一度も動かされていない。**
一方で証拠 harness は 8,139 行あり、製品 `ai_client/discussion/` の 6,124 行を超えた。
検証基盤の精度向上を、検証対象の妥当性確認より先に進めた結果である。

Phase 6 本体（Belief / Suspicion / memory / semantic output / reaction / pre-vote /
transaction / runtime）は実装済みで、不足しているのは**実際の会話を一度も見ていないこと**である。

---

## 2. 即時に実施すること（順序どおり）

1. **バックアップ — 完了済み。再実行不要。**
   `D:\AIwolf_backup\20260914T041928Z_full\repository\` に `C:\AIwolf` 全体を除外なしで複製した。

   - 8,620 files / 2,380 dirs / 132.20 MB、robocopy FAILED 0 / Skipped 0 / Mismatch 0
   - コピー後に source と destination を両方 SHA-256 で再走査し、
     **8,620 / 8,620 identical**、snapshot 中変化 0、欠落 0、余剰 0、read error 0
   - `manifest-sha256.json` SHA-256
     `c47a78a156b8a62404a98bf7b70f2f88b71ce5fb4388584e94656d3c524f845e`、
     照合結果は同 directory の `verify-summary.json`、コピーログは `robocopy.log`
   - backup root は `Protected: True` / Owner・SYSTEM・Administrators のみで新規作成。
     `logs/phase6-private-evidence` 配下もこれを継承し `Users` / `Authenticated Users` を持たない。
     既存 ACL は変更していない。非昇格で実施
   - 既存 `D:\AIwolf_backup\20260914T040024Z_T287` は上書きせず無変更（1,304 files / 7.36 MB）

   **注意: その T287 backup は失敗した部分バックアップである。**
   `first-failure.txt` に `RuntimeError: source changed before copy` が記録され、
   8,620 file 中 1,299 file（132.2 MB 中 4.29 MB ≒ 3%）で停止している。
   `logs` / `.git` / `ai_client` / `tests` / `scripts` を含まない。復旧源として使わない。
   稼働中の tree に対しコピー前 size/mtime 照合で abort する設計が原因である。
   今後の backup は「コピーしてから両側を hash 照合し、差分は *snapshot 中に変化* として
   記録する」方式にし、変化の検出で backup 全体を失敗にしない。

2. **`.gitignore` に `.phase4-real-*/` を追加。** 実 LLM transcript が untracked かつ
   gitignore 外で公開 remote 直下にある。backup 済みだが commit 経路は未だ開いている。

3. **`OPERATIONS.md` の raw 生成保留を解除。**（ユーザー承認済みの例外）
   「具体方式は T288 の設計・独立承認後に実装するまで、新規受入 raw の生成を保留」を、
   「新規受入 raw は実 game 原本を `logs/phase6-private-evidence/game/<task>-<utc>/`、
   synthetic を `.../synthetic/<task>-<utc>/` に分けて出力する」に置き換える。
   T288 の詳細設計・T290 の独立審査は不要とし、両 task を CANCELLED にする（ユーザー承認済み）。
   既存 evidence は移動・削除しない。**この 1 段落以外の `OPERATIONS.md` は変更しない。**

4. **出力・時間の制限を「プレイが成立するまで」引き上げる。** 第 2.1 節を参照。
   T284 / T286 の追加審査は不要とし、両 task を CANCELLED にする。

---

## 2.1 「プレイが成立するまで増やす」の具体化

ユーザー指示: **トークン数・昼・夜の時間は、プレイが成立する方を優先する。成立するまで増やす。**

### 効く順序（重要）

制限は 1 つではなく 4 つある。**最初に効くのは whole-response token ではなく発話長である。**

| # | 制限 | 現在値 | 実装箇所 | 変更の性質 |
|---|---|---|---|---|
| 1 | chat 本文の最大文字数 | **80 chars** | `ShortChatConfig.max_text_chars`、`_require_bounded_int(..., 1, 80)` | 上限 80 が直値。**コード変更** |
| 2 | chat 本文の最大 UTF-8 bytes | **96 bytes** | `ShortChatConfig.max_text_utf8_bytes`、range 1–96 | 同上。**コード変更** |
| 3 | text token の誘導範囲 | **5–30** | `Literal[5]` / `Literal[30]`、`__post_init__` が他値を ValueError | **型変更** |
| 4 | whole-response token | **96** | runner `_phase5_broker_settings` の `GenerationSettings(max_output_tokens=96)`、`ShortChatConfig.max_output_tokens` は `Literal[96]` | 512 までは値変更。**513 以上は検証範囲 1–512 の変更が必要** |

**96 UTF-8 bytes は日本語で約 32 文字である。** T154 の実測発話長は 14〜18 文字で、
D068 が記録した「5 種類の短い役職主張しか出なかった」は、モデルの能力ではなく
この上限の直接の帰結である。人狼の議論は 32 文字では成立しない。
**#1〜#3 を上げずに #4 だけ 512 にしても、32 文字の役職主張が出続ける。**

D068 の M2 は「`ShortChatConfig` の literal 値は承認済みの検査可能な固定 profile」と判断したが、
それは Phase 5 の短文プロファイルに対する判断である。Phase 6 の議論品質には適用しない。
Phase 5 / Q8 の旧 profile は 96 / 80 / 96 / 5–30 のまま**変更せず保持する**。
Phase 6 用の別 profile として新しい値を持つ。

### 初期値（ここから始める）

| 制限 | 初期値 | 根拠 |
|---|---|---|
| chat 最大文字数 | **200 chars** | 日本語 2〜3 文。人狼の 1 発言として最小限成立する長さ |
| chat 最大 UTF-8 bytes | **600 bytes** | 200 全角文字を収容 |
| text token 誘導範囲 | **20–120** | 上記に対応。`Literal` を外し bounded int にする |
| whole-response token | **512** | T283 実測 max 510（nine-seat pre-vote indented）を包含。検証範囲 1–512 の上端 |
| `day_seconds` | **180**（据置き） | D069。まず据え置き、queue 待ちが溢れたら上げる |
| `vote_seconds` / `night_seconds` | **45**（据置き） | 下記訂正を参照。現行の実効値 |

**［訂正 2026-09-14］本節の初版は `vote_seconds` / `night_seconds` を 60 と記載していたが誤り。**
`content/presets/standard_9.yaml` は 180/60/60 だが、実行経路では runner の `GAME_PLAN` が勝つ
（`scripts/run_phase5_local_smoke.py:2284-2286`）。**Phase 6 の実効値は day 180 / vote 45 / night 45。**

```python
# scripts/run_phase5_local_smoke.py:101-103, 126
day_seconds:   Literal[60, 180] = 60
vote_seconds:  Literal[45]      = 45
night_seconds: Literal[45]      = 45
PHASE6_GAME_PLAN = replace(GAME_PLAN, day_seconds=180)
```

**秒数も `Literal` 固定である。** `ShortChatConfig` と同じ構造で、
`vote_seconds` / `night_seconds` は 45 以外を、`day_seconds` は 60 / 180 以外を型が拒否する。
したがって「昼夜の時間を増やす」には **設定変更ではなく型変更が必要**である。
Phase 5 / Q8 の既存値（60/45/45）は変更せず保持し、Phase 6 用に可変の plan 値を持てるようにする。
これは第 6 節の項目 5 と同じ作業であり、別 task に分割しない。

`day_seconds` を最初から上げないのは、**ボトルネックが時間ではなく provider queue だから**である。
Phase 5 は concurrency 1 / 出力 14〜18 文字で既に offer queue 最大待ち 15.6 秒だった。
出力が 5〜10 倍になれば待ちも比例して伸びる。**まず出力長を上げて 1 回回し、
queue 待ちと deadline 抑制の実測を見てから昼夜の秒数を決める。**
根拠なく先に 600 秒にすると、遅い原因が queue なのか生成なのか分からなくなる。

### 引き上げの停止条件

「プレイが成立する」= 第 3 節の完了条件 1〜5 がすべて満たされること。
成立したらそれ以上増やさない。以下のいずれかに達したら増やすのをやめ、ユーザーへ報告する。

- whole-response token が 512 を超える必要が出た（`GenerationSettings` の検証範囲変更を伴う）
- `day_seconds` が 600 を超える必要が出た（1 ゲームが 1 時間を超える）
- 実 LLM ゲームを規定回数実行しても完了条件 2 または 3 が一度も満たされない

**［訂正 2026-09-14］実 LLM の実行回数は D073 / Master Test Plan の規定が優先する。**
本節初版の「調整実行 5 回」は採用しない。現行規定は
「実 LLM は 1 回を基準。修正確認に不可欠なら追加 1 回まで。2 回後に blocker が残れば停止し、
別モデルや追加 run で迂回しない。追加は実起動前のユーザー承認を要する」である。
無条件の反復は本指示が正そうとした増殖と同じ形になるため、計画側の規律を採る。

---

## 2.2 調整実行と受入実行の分離（design §11 との関係）

ユーザーの「成立するまで増やす」は、選択設計 §11 の
「exactly one finite game / must not retry the game」と直接衝突する。
次のように 2 段に分けて両立させる。**これはユーザーの product 決定である。**

**［訂正 2026-09-14］調整実行は「反復可」ではない。D073 / Master Test Plan の回数規定が優先する。**
本節初版は無制限の反復を前提にしていたが、無条件の反復は本指示が正そうとした増殖と同じ形になる。
現行規定は **実 LLM 1 回を基準、修正確認に不可欠なら追加 1 回まで、追加は実起動前のユーザー承認、
2 回後に blocker が残れば停止して報告（別モデルや追加 run で迂回しない）**。
以下の「調整実行」はこの上限の中での 1 回目を指す。

**調整実行（tuning run）— 受入証拠ではない。**

- 目的はパラメータ探索。回数は D073 / Master Test Plan の上限に従う
- **実起動直前にユーザーの明示承認を得る**（`OPERATIONS.md` の既存規定どおり。
  本指示は起動許可を与えない）
- 独立 Tester / fresh Reviewer は不要
- raw は `logs/phase6-private-evidence/game/<task>-<utc>/` へ即時保全し、privacy 境界を維持する
- 各回で記録するもの: 使用パラメータ 4 種 + 昼夜秒数、accepted text 件数、
  responsive chat 件数、length-finish 拒否件数、offer queue の最小/中央/最大、
  game end 到達可否、owned process 残存数
- **調整実行の結果を Phase 6 の受入 PASS として使わない**

**受入実行（acceptance run）— 1 回のみ。従来の厳格さを維持。**

- パラメータ確定後に 1 回だけ実施
- **実起動直前にユーザーの明示承認を得る**（`OPERATIONS.md` の既存規定どおり）
- 自動再実行なし、soak なし、35B / 代替 model / fallback なし
- raw 即時保全 → 独立 Tester が manifest / hash / linkage / completion を検証
  → Implementer・Tester から独立した Reviewer が全 accepted text を評価
- 第 3 節の完了条件 1〜5 で判定する

この区別が本指示の要点である。**合成 fixture と調整実行は軽く、受入実行だけ重くする。**

---

## 3. 新しい Phase 6 完了条件（ROADMAP を置き換える）

「前の発言を受けた会話が成立する」を、次の 5 条件で判定する。

1. 9 AI で 1 ゲーム完走する（server が game end に到達し、owned process 残存 0）
2. 他人の発言を受けた発言が実際に出る（accepted responsive chat が 1 件以上）
3. 質問→回答 / 主張→反論 / 意見変更 のいずれかが観測できる（1 件以上）
4. private 情報漏洩がない（private channel 本文・自分の private 結果・token の
   公開境界への流出が 0）
5. 同一文・同一主張の異常な反復がない（正規化後、同一プレイヤーの直前発言と不一致、
   かつ全ゲームで同一正規化文が 3 回以上出ない）

1・2・4・5 は既存の machine semantic evidence で判定できる。3 は独立 Reviewer が
transcript を読んで判定する。**この 5 条件以外を Phase 6 の closure 条件にしない。**

---

## 4. 今回のスコープ外（明示 hold）

以下は Phase 6 closure の条件にせず、新規 task を作らない。既存成果は削除せず履歴として残す。

- Windows ACL / Win32 FFI による private path 検証の追加強化
- POSIX 版 ACL / path hardening の新規実装（unsupported platform は fail closed のままでよい）
- tokenizer provenance の追加調査（T283 で計測完了）
- exact token budget の追加審査（本指示の 512 で確定）
- 512 record 上限の事前証明・上限変更・checklist 分割
- 証拠の証拠（seal の hash を別 session が再確認する層）の追加
- evidence provenance / manifest atomicity / file rename atomicity の追加設計
- CI の platform 分割（`windows_private` marker の既存追加分は保持。それ以上は行わない）

**512 については、超過時に件数と reason を安全に記録して停止する 1 箇所のガードだけ実装する。**
T289 が `run_phase5_local_smoke.py:2075-2076` で 513 件到達時に例外となり
manifest publish に進まず machine semantic PASS が false になることを確認している。
一回限りの実ゲームを失う経路なので、記録付き停止だけは実ゲーム前に入れる。

---

## 5. 開発ループ（これに戻す）

```
deterministic fixture を 1 回通す
  ↓
実モデル 9 人を 1 回動かす
  ↓
transcript を読む
  ↓
実際の会話上の問題を 3〜5 件抽出
  ↓
ai_client/discussion/ を修正
  ↓
もう一度ゲーム
```

**合成 fixture の単体テスト失敗は、同一 packet 内で原因を確認し、修正し、
同じテストを再実行してよい。** 新 task / 新担当 / fresh review を挟まない。
最初の失敗の raw は従来どおり保全し、失敗を成功へ読み替えない。

**実モデルを使う実ゲームには従来の厳格さを維持する。** 事前のユーザー明示承認、
一回限り、自動再実行なし、raw の即時保全、独立 Tester と独立 Reviewer。
この区別が本指示の要点である。

---

## 6. テスト側で実施する修正（製品スコープ）

1. **`scripts/phase6_private_review.py` に reason code を追加。**
   `ReviewFailure("corrupt")` が 43 箇所あり `main()` は
   `phase6 private review failed` の 1 行しか出さないため、失敗原因が特定できず
   調査 task が毎回必要になっている。`manifest_hash_mismatch` /
   `visibility_mismatch` / `schema_closed` 程度の機械可読コードを付け、
   category と code を stderr に出す。path と本文は出さない。
2. **private CHAT の literal oracle を 1 件追加。**
   `tests/test_phase6_private_review.py` の `_terminal()` が kind から visibility を
   導出しているため、AUTHORIZED_PRIVATE な chat が G matrix に 1 件も存在しない。
   `visibility` を引数化し、期待値を
   `tests/fixtures/phase6_private_review_vectors.json` の literal に置き、
   private channel chat の行を 1 行足す。完了条件 4 に直結する。
3. **新規 evidence container の path 分離**（第 2 節 3 と同じ）。
4. **512 超過時の記録付き停止**（第 4 節）。
5. **Phase 6 用の出力 profile を追加。**（第 2.1 節）
   `ShortChatConfig` の `target_min_text_tokens` / `target_max_text_tokens` /
   `max_output_tokens` は `Literal` 固定で、`__post_init__` が他値を ValueError にする。
   `max_text_chars` / `max_text_utf8_bytes` も `_require_bounded_int` の上限が 80 / 96 の直値。
   **Phase 5 / Q8 の既存 profile を変更せずに** Phase 6 用の別 profile を持てるようにし、
   200 chars / 600 bytes / 20–120 text token / 512 whole-response を設定できるようにする。
   runner の `_phase5_broker_settings` は Phase 6 経路で
   `GenerationSettings(max_output_tokens=512)` を渡す。Phase 5 経路は 96 のまま。
   既存の「length-finish は完全な JSON でも拒否」「repair は同一 lease 内で最大 1 回」
   「CHAT start は player/phase あたり最大 2」「CO は別経路」は変更しない。

これら 5 件は 1 つの Implementer packet にまとめてよい。完了後、独立 Tester 1 回と
fresh Reviewer 1 回で G を閉じる。**G に対してこれ以上の証拠検証層を追加しない。**
項目 5 は製品の挙動を変えるため、focused test の期待値更新を伴う。
**その更新は「新しい上限で通ること」を確認するもので、既存の privacy / authorization /
linkage / schema の検査を緩めてはならない。**

---

## 7. 停止中 task の扱い

| task | 判断 |
| --- | --- |
| H1 バックアップ | **完了**（第 2 節 1）。T287 の部分 backup は復旧源にしない |
| T287 external audit 再照合 | 続行。本指示を disposition に反映する。backup 手順の失敗も記録する |
| T288 provenance 設計 / T290 独立審査 | CANCELLED（第 2 節 3 で置換） |
| T284 数値選択案 / T286 独立審査 | CANCELLED（第 2 節 4 で確定） |
| T285 platform / CI 補正 | CANCELLED（第 4 節でスコープ外） |
| T289 512 境界調査 | 調査結果を保持して終了。上限変更は行わない |
| T267 / T278 / T280 | 第 6 節の修正後、新しい独立 Tester 1 回でまとめて再確認 |
| T272 G fresh review | 第 6 節完了後に新規独立 session を割当 |
| T273 H 全 A–G review | G 承認後に 1 回。新しい 5 条件に対する review とする |
| T051 | 非 critical BLOCKED のまま |

---

## 8. 維持する原則（緩めない）

- server が単一の真実源。client を信用しない
- private 情報は送信境界で落とす。prompt でフィルタしない
- role 名を game core / AI client にハードコードしない。YAML から読む
- structured output は必ず validate し、不正な proposal を commit / send しない
- 実ゲームの raw transcript は pytest 管理外の private 領域へ保存する（調整実行も含む）
- **実 LLM を起動するすべての実行**で、実起動直前にユーザーの明示承認を得る
- closure 時に Implementer / Tester から独立した Reviewer が 1 回判定する
- 出力上限・昼夜秒数を上げてよいのは第 2.1 節の範囲まで。停止条件に達したらユーザーへ報告する
- 上限を上げることと、privacy / authorization / schema / linkage の検査を緩めることは別である。
  後者は行わない
- 失敗を成功へ読み替えない。過去の FAIL / 欠落を書き換えない
- commit / 破壊操作 / ACL / TEMP / security 設定の変更は従来どおり未承認

---

## 9. 記録方法

本指示の採用を `Docs/ai/decisions/D072_PHASE6_REBASELINE.md` に記録し、
`ROADMAP.md` の Phase 6 完了条件、`CURRENT_STATE.md` の scope / holds / critical path、
`TASKS.md` の lifecycle を更新する。`AGENTS.md` / `INDEX.md` / `WORKFLOW.md` / `roles/` /
`ai_status.py` / `check_docs.py` は変更しない。`OPERATIONS.md` は第 2 節 3 の 1 文のみ。
