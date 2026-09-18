# Phase 6 speech_act NONE固着の解決提案

Status: PROPOSAL（未承認・未実装・未測定。製品変更0、provider操作0、実game0、生成0）
作成: 2026-09-19
作成者: Claude Code

## 対象

`PHASE6_MODEL_COMPARISON_20260918.md` が測定した次の事実に対する提案である。

> 全5モデルが32件ずつ完了したが、QUESTION/ANSWER/REBUTTAL/OPINION_CHANGE等のラベルは
> 一度も出ず、全160件がNONEだった。

5ファミリー、8B〜35B、3種の量子化、2種のruntimeを跨いで非NONEが0であり、
act/text不一致は23〜27/32と全モデルでほぼ同じである。
本提案はこのモデル非依存性を出力契約の構造で説明し、修正候補と検証計画を示す。
採否・実装・独立検証はReviewerとユーザーが決定する。

## 1. 静的に確認した機構

以下はすべて現HEADのコードとschemaから確認した。生成は行っていない。

### R1 speech_actは12 field中11番目に生成される

`ai_client/llm/backend.py:322` はrequest body全体を `sort_keys=True` で直列化する。
これはnestされたJSON Schemaの `properties` にも適用されるため、wire上の出現順は**アルファベット順**になる。

投影したschemaの実際の順序:

```
decision   : kind, message, option_id
discussion : assessment_updates, base_revision, claim_updates, co_judgment,
             decision_kind, option_id, pre_vote_reassessment, reaction,
             relation_updates, schema_version, speech_act, strategy_update
```

一方 `ai_client/discussion/projection.py:366` の `proposal_required` は
`schema_version, base_revision, decision_kind, option_id, speech_act, ...` と
**speech_actを5番目に置いている。** 実装者が意図した順序は早い位置だが、
`required` は配列で順序が保たれるのに対し `properties` はdictでありsortされるため、
wire schemaでは11番目へ後退している。

**speech_actの生成位置は設計判断ではなく `sort_keys=True` の副作用である。**
sort_keysはrequest hashの決定性のために置かれており、生成順を意図したものではない。

本文は `decision.message` として先に生成されるので、モデルは書いてから貼る順序になる。
ただしラベル選択までに10 fieldの構造出力が挟まる。

### R2 NONEは先頭枝かつ唯一の無償枝

`ai_client/discussion/projection.py:237` の `_speech_schema()` は次の順で枝を並べる。

| 枝 | kind | required数 | kind以外の必須field |
|---:|---|---:|---|
| 0 | NONE | 1 | （なし） |
| 1 | CLAIM | 5 | subject_player_id, topic, stance, evidence |
| 2 | QUESTION | 5 | addressee_player_id, subject_player_id, topic, source |
| 3 | ANSWER | 7 | addressee_player_id, in_reply_to, source_interpretation, topic, stance, evidence |
| 4 | REBUTTAL | 7 | addressee_player_id, in_reply_to, source_interpretation, topic, stance, evidence |
| 5 | OPINION_CHANGE | 6 | subject_player_id, dimension, prior, current, causes |
| 6 | RELATION_HYPOTHESIS | 6 | source_player_id, target_player_id, relation, confidence, evidence |

`backend.py:293` の `response_format.json_schema` / `strict: True` による制約付きdecodeでは、
`kind` を出した時点で枝が確定し、以後の必須fieldが強制される。
NONEだけが追加義務0で終端でき、かつ最初に現れる選択肢である。

### R3 非NONEの必須fieldは接地を要求し、promptは無接地を禁じている

ANSWER/REBUTTALは `in_reply_to`（EvidenceRef）と `source_interpretation` と `evidence` を要求する。
`ai_client/llm/prompt.py` の `_DISCUSSION_INSTRUCTION` は次を明示する。

> Use actual in_reply_to references and prior/current/causes;
> if unavailable, say something supportable, never invent them.

接地できない場合に安全側へ倒す指示と、接地必須fieldを持つ枝の回避は同方向に働く。
指示と契約が独立にNONEを選ばせている。

### 原因から除外できるもの

| 仮説 | 根拠 |
|---|---|
| 出力token不足 | T427で `finish_reason=length` が全160件0 |
| context不足 | 最大input 2062/8192、最大生成360/512 |
| モデル能力 | 5ファミリー・8B〜35B・3量子化・2runtimeで同一挙動 |
| schemaがactを表現できない | T406の別candidateでは非NONEが生成された |

R1〜R3はいずれもモデル非依存であり、観測されたモデル非依存性と整合する。

## 2. 未確認の前提

**制約付きdecodeのGBNFが `properties` の記載順を強制することは、本提案では未検証である。**
llama.cppのjson-schema→grammar変換の挙動に依存し、本リポジトリからは確認できない。

最も安価な確認は新規生成を伴わない。T427が保存した160件の生出力のうち1件について、
JSON keyの出現順がアルファベット順（`assessment_updates` … `speech_act` … `strategy_update`）に
一致するかを見ればよい。一致すればR1の前提は確認され、しなければR1は取り下げる。
保存原本はOwner専用rootにあり、既にT428の独立判定が読んでいる範囲である。

**この確認を最初に行うこと。** R1が否定されればC1系の候補は不要になる。

## 3. 候補

| # | 内容 | 対象 | 規模 | 主なリスク |
|---|---|---|---|---|
| C1 | wire schemaの `properties` 順を著者が指定し、speech_actを本文直後へ移す | R1 | backend直列化の分離 | request hash変化、freeze更新が必要 |
| C2 | NONEに必須の理由fieldを与え、無償枝をなくす | R2 | schema 1枝 | 沈黙の表現力を損なわない設計が要る |
| C3 | oneOfの枝順を変えNONEを末尾へ | R2 | 配列順のみ | 単独では弱い。対照として有用 |
| C4 | 非NONE枝の必須fieldを減らす | R3 | schema複数枝 | 接地保証の後退。捏造を招きうる |
| C5 | 本文生成とラベル付けを2 callに分ける | R1/R2 | 実行経路 | provider call倍増。9 agent共有では高コスト |
| C6 | 本文からラベルを機械導出する | — | — | **非推奨。** 自由文の意味をルールで保証しない方針に反し、メタデータを捏造する |

### C1の具体

`sort_keys=True` はrequest hashの決定性のために必要だが、決定性は挿入順でも同じく得られる。
schema部分木だけ著者指定順で直列化すれば、`proposal_required` が既に宣言している
speech_act 5番目という意図をwireでも実現できる。hash値は変わるのでfreeze再取得が要る。

### C2の具体

NONEへ `reason` を必須とし、閉じたenumで表現する。例:
`NO_NEW_INFORMATION` / `WAITING_FOR_OTHERS` / `DELIBERATE_SILENCE`。
合法な沈黙はそのまま表現でき、validatorを緩めず、枝の非対称性だけが縮む。
6候補中で最も小さく、R2へ直接効く。

## 4. 推奨する順序

1. §2の確認を行う。R1の前提が立つかどうかで候補集合が変わる。
2. **C2を単独で試す。** 変更が最小で、接地要求を緩めず、NONEの無償性だけを外す。
3. 不足ならC1を単独で試す。C2と同時に入れない。
4. C1とC2の単独結果が出た後にのみ組合せを検討する。
5. C4は最後の手段とし、接地保証の後退を明示的に承認した場合だけ使う。
6. C5/C6は上記が全て失敗した場合の再設計対象であり、今回の候補にしない。

`PHASE6_MODEL_COMPARISON_20260918.md` の「Phase6の次の最小作業順」の2と整合する。
モデル交換とschema変更を同時に混ぜない。canonical Qwen3.5-9Bのまま比較する。

## 5. 検証計画

既存の32 case人工suite（`scripts/phase6_conversation_suite.py`）をそのまま使う。
実game・Master Run・モデル交換は行わない。1 variantあたり32生成、retry0/repair0。

固定するもの: モデル、sampling、seed、context、出力上限、fixture、評価軸、
入力hash、schema以外のprompt。

### 成功条件は「非NONEが出ること」ではない

`PHASE6_MODEL_COMPARISON_20260918.md` は act出現だけで能力PASSにしないと定めている。
判定は次の3つを同時に見る。

1. **act/text不一致が減る**（現状23〜27/32）。これが主指標である。
2. **HARDが悪化しない。** 特に根拠捏造。
3. SEMANTICの改善は従指標とし、単独の採用根拠にしない。

### 最大のリスク

**非NONEを選びやすくすると、ラベル失敗が捏造失敗へ転化しうる。**
ANSWER/REBUTTALは `in_reply_to` と `evidence` を要求するため、
接地のないまま枝へ入れば、モデルは参照を作り出す方向へ押される。
現状でも `FABRICATED_EVIDENCE` はBonsai 5件、Qwen35 3件、Gemma 1件ある。

捏造はHARD違反であり、ラベル不一致（SEMANTIC/STYLE）より重い。
**捏造件数が増えた時点でその候補は不採用**とし、改善した他指標で相殺しない。
C2がNONEを塞ぎすぎないよう、合法な沈黙が明示的に表現できることを同じsuiteで確認する。

## 6. 限界

- R1の生成順強制は未検証である（§2）。確認前に実装しない。
- 32 case・各1生成・1 seedであり、母集団性能ではない。統計的優劣を主張しない。
- 本提案はschema/直列化の構造だけを扱う。act/text不一致の残りが
  prompt側の説明不足に由来する可能性を排除していない。
- 秘密自白・コピー・自己紹介反復はモデル依存の別問題であり、本提案の対象外。
- 本提案は製品コードを変更しておらず、pytest・実game・providerを実行していない。
