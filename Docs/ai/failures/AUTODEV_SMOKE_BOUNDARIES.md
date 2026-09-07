# 無人実行smokeで確認した境界

2026-09-07 / Implementer GPT-6 Astra / Local Windows

- 契約レビューに段階の説明が不足すると、修正前の不具合を理由に契約そのものが差し戻された。
  `process.invoke`で契約の妥当性と実装後レビューを明示的に分けた。契約テストの成功を偽装しない。
- WindowsアプリのAppData仮想化により、指定パスと`Path.resolve()`のパスが異なった。
  v1はrepo_rootを正規化するため、上位承認のauthority比較も同じ正規化が必要だった。
  `approved_contract`を修正し、相対要素を含む絶対パスで回帰テストを追加した。
- 2026-09-08: 起動設定へAppDataの別名を保存すると、通常PowerShellからcampaignが見つからない。
  Codexから利用者向け設定へ実行記録のパスを渡す際は、存在確認だけでなく
  `Path.resolve(strict=True)`の実体パスを保存する。既存READYの起動設定を修正した。

初回2 campaignはそれぞれNEEDS_USERで停止し、ゲーム変更・自動再送はしなかった。
修正後の新規合成campaignはCOMPLETE。
保存先: `%LOCALAPPDATA%/AIwolf/autodev-validation/`。
具体的なcampaign IDと検証値は `../handoffs/INFRA_AUTONOMOUS_HANDOFF.md`。
