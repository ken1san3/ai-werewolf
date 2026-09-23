# T499 native template互換による生成前停止

## 観測

frozen-v1は`STOPPED / TEMPLATE_INVALID`。全32行はNOT_STARTED、generation/call消費/row claimは0。
source75件は不変、所有process残存0、listener free。
証拠: `Docs/ai/handoffs/tasks/T499_PRECALL_TEMPLATE_STOP.md`、`T499_PRECALL_SAFE_RESULTS.json`。

## 原因と分類

分類F: test-only設計とnative templateの互換欠陥。会話品質やmodel能力の失敗ではない。
GGUFのtokenizer.chat_templateをoffline抽出し、既存runtimeと同じSHA
`c17a933c26907f0982a96e5cb3b6a5ef393f1722f13558ebda7be039649cb4cd`を確認。
このtemplateはsystem messageが先頭以外なら例外にする。設計のsystem/user/system構成が違反した。
保存server logの同一拒否はTesterが本文非表示のbooleanで照合済み。

## 最小修正と再開gate

同一instructionをoriginal systemの末尾へ連結し、system/userの2messageにする。
original system prefix、canonical user、candidate schema、model、32case、sampling、token上限を維持する。
詳細design delta→独立design承認→限定実装/focused→独立tool delta承認→新commit/freeze。
旧run/markerは保存し再利用しない。品質生成は未実施の32件だけ。同条件retryや旧候補再生成は行わない。
この記録は修正完了・品質合格の宣言ではない。
