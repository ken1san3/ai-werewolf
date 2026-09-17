# Phase 6 コードレビュー結果（外部レビュー）

Status: FINDINGS（未承認・参考情報）
作成: 2026-09-17（同日 F1 に実測訂正を追記）
作成者: Claude Code（レビューのみ。製品変更0、テスト実行0、provider操作0）

## レビュー範囲

1. commit `82fcf07` の `ai_client/` 配下（11,455 insertions / 28 files）
2. 上記commit後の未コミット作業ツリー変更
   （`admission_broker.py` / `backend.py` / `network/client.py` / 新規 `ai_client/game_time.py`）

論理ゲームクロック（`AIWOLF_TIME_SCALE`）の導入は**ユーザー指示による決定事項**であり、
本レビューは導入の可否を論点としない。指示どおり導入する前提で、実装上の不整合のみを指摘する。

指摘は5件。重大度順に記載する。いずれも未承認の参考情報であり、
採否・修正範囲・独立検証の要否は Reviewer とユーザーが決定する。

---

## F1（最重要）drain grace だけが未スケールで残っている

対象: `ai_client/llm/admission_broker.py:1206`（関連: 同 `:296`、`:1005`）

コンストラクタは**未スケール値**で不変条件を検証する。

```python
# :296
if config.provider_drain_grace_seconds < backend_request_timeout_seconds:
    raise ValueError("provider_drain_grace_seconds must cover backend request timeout")
```

しかし実行時に provider call へ渡す予算は**スケール後**である。

```python
# :1005
self._game_time.real_budget(self._backend_request_timeout_seconds)
```

一方、その call を見張る drain watchdog は**未スケールのまま**待つ。

```python
# :1206
await asyncio.sleep(self._config.provider_drain_grace_seconds)
```

### 想定シナリオ（コード経路からの導出。実測ではない）

`AIWOLF_TIME_SCALE=0.5`、`request_timeout_seconds=5.0`、`provider_drain_grace_seconds=5.0`。

1. `:296` の検証は未スケール値で 5.0 >= 5.0 → **通過**
2. `:1005` は `real_budget(5.0) = 10.0` 秒の予算で call を開始
3. 正常に 7 秒かかる call が実行中に、`_drain_timeout` が **5 秒**で起床
4. `_poison_locked("PROVIDER_QUIESCENCE_UNKNOWN")` を呼び、provider task を cancel

### 実測による訂正（2026-09-17 追記）

**T389 実ゲームではこの故障は発生しなかった。** 初版で「再発する」と断定したのは誤りであり、
以下に訂正する。指摘そのもの（drain grace だけが未スケール）は取り下げないが、
確度は「実測で確認された故障」ではなく **「未実行の潜在欠陥」** である。

T389（`logs/t389-logical-clock-acceptance/measurement.json`）の実測:

| 項目 | 値 |
|---|---|
| `game/time_scale` | 0.1 |
| `semantic/unknown` | 0 |
| `semantic/poisoned` | 0 |
| `topology/broker_shutdown_clean` | True |
| `provider/terminal_counts` | `RELEASED = 117` のみ |

未発火の理由は terminal 内訳にある。EXPIRED / OVERLOADED / ABANDONED_DRAINED がいずれも 0 件で、
**DRAINING 経路自体が一度も通っていない**。`network/client.py:1080` によりフェーズ期限も同率で
10倍になったため、期限切れによる打ち切りが起きなかった。

ただし潜在的な危険性は減っていない。`time_scale=0.1` では backend 予算が 10 倍になる一方、
drain grace は実秒のまま据え置かれる。**取消や期限切れが一度でも発生すれば、
未スケールのときより踏みやすくなる。** 現在の観測は「経路が通っていない」ことを示すだけで、
「不整合が無害である」ことを示すものではない。

### 影響

`time_scale < 1` の実ゲームで DRAINING 経路に入った場合、
T370 が除去した UNKNOWN / poison 終端が再発しうる。
recovery-r3 実測では `provider_unknown_count=0`、`poison_transition_count=0` を達成していた。

### 検討すべき対応

`provider_drain_grace_seconds` と `cancellation_grace_seconds` を論理秒として扱い、
`:296` の検証と `:1206` の待機の双方を同じ `real_budget()` で換算して整合させる。
どちらを論理秒・実秒とみなすかの定義を先に確定すること。

---

## F2 time scale が freeze と証拠に現れない

対象: `ai_client/game_time.py:24`（`from_env`）

`GameTime.from_env()` は構築時に `AIWOLF_TIME_SCALE` を読む。
この値は全期限値を一律に伸縮させるが、run freeze・config hash・判定JSON のいずれにも現れない。

### 再現シナリオ

freeze で 191 source ＋ docs ＋ controls の全hashを記録し、
`AIWOLF_TIME_SCALE=0.5` を環境に置いて起動する。全フェーズ期限・lease cutoff・provider予算が倍になるが、
freeze SHA・dispatch record・review JSON は scale 1.0 の run と**バイト同一**になる。

結果として、原本照合上は同一と検証される2つの run が比較不能になる。
Stage B の PASS/FAIL 証拠から、唯一変わった変数が消える。

さらに `admission_broker` / `backend` / `network/client` が**それぞれ独立に** `from_env()` を呼ぶため、
一部コンポーネント構築後に環境変数を設定した場合、コンポーネント間で期限値が食い違う。

### 検討すべき対応

time scale を freeze 対象の設定値として記録し、判定JSON と preflight 記録へ出力する。
`GameTime` は1箇所で構築して注入し、プロセス内で単一値を保証する。
`game_time.evidence()` が既に存在するので、これを実際の証拠出力へ結線する。

---

## F3 反復抑止が「契約違反」として記録される

対象: `ai_client/brain/controller.py:700`

```python
if _invalid_or_repeated_self_text(request, decision):
    return await self._abort_after_generation(
        request, generation, proposal,
        DiscussionAbortReason.STAGE_FAILED,
        DecisionStatus.INVALID_DECISION,
    )
```

構文的に妥当な ChatDecision が、直前自己発話と一致するという理由だけで、
**schema違反・契約違反の出力とまったく同じ経路**で abort される。

### 影響

`OUTPUT_INVALID` / `STAGE_FAILED` のカウンタに W3 の抑止が混入する。
現在このカウンタを見て次の修正対象を決めている（r3: OUTPUT_INVALID 49、quality-r1: 20）ため、
**W2 の改善効果が過小に、出力契約の問題が過大に見える。**

加えて、そのターンは provider call を1回消費して発話ゼロで終わる。
B01（完走）を目標とする修正が、完走に逆風を与えている。

### 検討すべき対応

反復抑止専用の終端理由 / status を追加し、契約違反と分離して集計する。

---

## F4 `GameTime` を `__init__` 内でローカル import している

対象: `ai_client/llm/backend.py:262`、`ai_client/llm/admission_broker.py:305`

```python
from ai_client.game_time import GameTime
self._game_time = GameTime.from_env()
```

`network/client.py:22` はモジュール先頭で import しており、記述が不統一である。

`AIWOLF_TIME_SCALE` に `0` や `abc` を設定した場合、import 時にも設定検証時にも失敗せず、
broker / backend が最初に構築された時点で `ValueError` になる。
実ゲーム起動では、preflight・freeze・provider health check が**すべて成功を報告した後**にあたる。

### 検討すべき対応

モジュール先頭 import へ統一し、scale の検証を起動時検証（preflight）へ前倒しする。

---

## F5 `str(error)` の文字列一致で制御フローを分岐（PLAUSIBLE）

対象: `ai_client/llm/prompt.py:406`

```python
except ValueError as error:
    code = str(error)
    if code not in {"PROMPT_INVALID", "PROMPT_TOO_LARGE"}:
        raise
```

例外メッセージの完全一致に依存している。

`projection.py` の予算検査を診断目的で
`ValueError(f"PROMPT_TOO_LARGE: {proxy} > {limit}")` のように改善した瞬間に一致しなくなり、
本来 PROMPT_REJECTED として記録されるはずのものが未処理例外になる。
逆に、無関係な `ValueError` のメッセージがたまたま一致すれば、黙って rejected projection に変換される。

W1 の原因特定では「超過が何単位か」を知る必要があったので、この改修は現実に起こりうる。

### 検討すべき対応

専用の例外型または列挙コードを持たせ、文字列一致をやめる。

---

## レビュー範囲の限界

- 対象の `ai_client/` は 11,455 insertions あり、`discussion/` 配下（5 file、約6,200行）は
  新規追加として全文が差分に含まれる。本レビューは W1/W2/W3 と admission 期限管理、
  および未コミットの game_time 関連に重点を置いた一巡であり、全行の網羅審査ではない。
- 未コミット変更は別セッションが作業中であり、本レビュー時点のスナップショットに対する指摘である。
- 本レビューは pytest・実ゲーム・provider操作を実行していない。
  F1 の再現シナリオは実測ではなくコード経路からの導出である。
