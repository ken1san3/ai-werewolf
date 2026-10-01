# D099 — 新契約S4の限定評価・継続許可

日付: 2026-10-01
Status: ACCEPTED

## 許可

T555完了報告で、T556の対象を「保存済みデータへ新契約S4を一回適用する、既存再評価禁止の例外」と提示した。ユーザーの直後の「許可します」を、その一点への明示許可として記録する。

続く「何度でも適応する許可」「クリティカルな問題以外は停止しない」により、必要な限定S4再適用/修正サイクルの都度確認は不要とする。通常のtest failure/reviewer限定指摘/機械接続不整合で停止しない。各version/runのscope/hash/freeze/独立性は固定し、同じ証拠の承認は再利用する。理由のない再採点や得点選別を目的とする反復は行わない。privacy/authority違反や安全な継続不能な問題は該当gateだけ保留する。

対象はT553と同じ保存済み192行/96pair。T506 baseline96行、T550判断6 candidate96行、既存32case×3seedを落とさず保持する。新契約はS4_PROJECTED_ABILITY_PROVENANCE_V1。旧raw/source/accepted surfaceは接続と新S4評価に必要な範囲だけ読み、旧annotation/旧得点は採点入力にしない。

## Gate

test-only接続設計の独立承認→公開synthetic focused→独立tool承認→source/入力/新packet/root/rubric/評価者独立性の事前freeze→fresh blind S4意味評価→annotation freeze→全192行の機械判定/96pair集計。必要な再適用は原因/差分/新versionを記録する。旧結果は保持し、新version別artifactへ保存する。T554/T555同bytesの承認は再利用するが、新接続と新評価の承認を代替しない。

## 維持事項

新provider/LLM生成、baseline/candidate再生成、他metric再採点、旧annotation補正、本文意味のhost推測、authority昇格、validator緩和、製品/model/config/game/Master Run/Phase7/Actions/main変更は許可しない。通常commit/pushと終了Context Size Audit委任は常設ルールを維持する。
