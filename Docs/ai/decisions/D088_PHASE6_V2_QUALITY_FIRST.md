# D088 — v2品質をtest-only経路で先に測る

Status: ACCEPTED
Date: 2026-09-30

ユーザーの「他エージェントからの指示書を確認して実行せよ」を、HEAD a399cc0e25b9d6d8134c7d08c864f834f914d1e7 のT550 packet実行指示として適用する。

## 決定

1. PF3はfixtureで構成可能な最大出力と実入力をnative tokenizerで測る。合法な全出力の普遍上界証明を品質probeの条件にしない。lengthは不受理・失敗として分母に残す。各call上限512/context8192は維持する。
2. 最初の品質測定は製品の純関数を呼ぶ薄いtest-only経路で行う。製品capture/lease完成を待たない。T515/T549はBLOCKED（一時停止）、既存コード/設計/承認/証拠を保全する。
3. T506保存baseline96行を評価方式v2で初めて判定する許可を、candidateとの対応あり・混合blind比較だけに限定する。旧注釈/旧判定の変更、baseline再生成はしない。

## 実行範囲

T550 WP1の改訂設計1本→独立review→test-only実装/両Python/独立tool review→独立TesterのPF1/PF2/PF3実測→2行probe→条件合格時のみ96行一回/90分→独立意味評価→結果と次案を提示して停止。probe予測60分超または計画length1件で停止。未実施行の補完・同条件再実行は禁止。

設計追加2本目が必要、または同一artifact reviewが3回目に入る場合は、作業を止め理由と選択肢を示す。ユーザーに再許可を求める代わりにこの制限を黙って拡張しない。

## 維持する境界

authority/privacy/validator条件、所有processのみ操作、private原文非公開、v1既定を維持。製品コード変更、PF3証明/native DLLツール継続、game/Master Run/Phase7/model変更・DL/API/Actions/main変更は禁止。T550限定測定以外のprovider holdは維持する。

本decisionは品質測定の許可であり、未実行gateのPASSや製品採用を意味しない。指示書正本: Docs/ai/tasks/T550_PHASE6_V2_QUALITY_FIRST.md。
