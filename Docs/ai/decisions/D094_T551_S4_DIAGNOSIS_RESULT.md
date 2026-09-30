# D094: S4限定診断の結果と次scope

Status: RECORDED
Date: 2026-10-01

## 確定した事実

T550候補のS4 FAIL4行はG16-1/2の情報不足fixture。fixture/catalogには能力結果候補が無く、planはREBUTTAL、disclose空、claim未選択だった。選択した7事実はday1・phase2・alive4で、ability resultではない。

保存sealとsourceを照合し、元request・schema・plan・presenter・本文の構造受理を4/4行でoffline再現した。不正ref・余分なmessage field・presenter改変の負例12件は拒否された。既存validatorのID/authority/visibility境界が緩んだことや、能力結果の投影漏れを示す証拠はない。

本文の意味と選択plan/根拠の一致を検査する関数は現行経路に無い。presenter検査は入力の完全性を検査するだけで、messageはJSON/文字数・byte境界・一般text guardまで。これが構造受理後に自然文で未選択の結果を主張し得る境界である。

## 評価境界との非整合

凍結時のS4根拠は自然文の権威記録存在の断定。4行の共通provenanceはPUBLIC_FACTのみで、AUTHORITATIVE_ABILITY/FORGED_REFERENCE/CLAIMED_REPORTはいずれも無かった。
一方、評価design §8.1は権威refを持たない公開結果主張をCLAIMED_REPORTへ分け、それ単独ではS4 FAILにしない。§3.4の広い自然文条件と、§8.1のrefに基づく条件を同一の閉じた規則として適用できていない。

これは既存判定の適用根拠と契約の不一致を特定した診断であり、4件をPASSへ再裁定したものではない。合法な騙りか否かの新しい意味判定、旧注釈の補正、再採点は行っていない。D092のMEASUREMENT_INVALID/製品採用見送りは維持する。

## 次の最小scope

source修正や追加LLM測定より先に、**自然文の無ref結果主張と、明示的なAUTHORITATIVE_ABILITY/CLAIMED_REPORTの共通provenanceをどう対応させるか**を限定設計で整合させる。合法な騙りを禁止せず、host意味推測/regex抽出・validator緩和を使わない。

受理/評価境界を変える場合はArchitect→独立design reviewが必要。T551の診断許可をその実装・再測定枠へ拡張しない。ゲームルールやprivacy変更を今判断する必要はない。

T551診断は完了、次scope待ちとしてDECISION_REQUIRED。正本は `Docs/ai/handoffs/tasks/T551_S4_SAVED_EVIDENCE_DIAGNOSIS.md` と安全なhash付き集計。
