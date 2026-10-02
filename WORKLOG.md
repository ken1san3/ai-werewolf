# 作業記録

2026-10-02 T0: 旧状態を4タグで保存し、mainからrebuild/simple-agentを作成。DELETE 1,342件・EDIT 6件を適用し、分析資料43件とROADMAP.md・WORKLOG.mdを追加。
テスト: Windows 11 / Python 3.13.3、python -m pytest -q → 204 passed / 661 subtests passed（29.07秒）。再実行なし。既存キャッシュへの書き込みを抑止して実行。

2026-10-02 W0: 進め方を Codex 主担当に切り替え（AGENTS.md・ROADMAP.md）。python -m pytest -q → 204 passed / 661 subtests passed（28.04秒）。初回は一時フォルダの権限エラーで9 failed / 198 passed / 658 subtests passed、サンドボックス外で再実行して解消。

2026-10-02 S1-1: 陣営YAMLのteammate_tagsで仲間を指定し、knows_teammatesの本人だけにROLE_ASSIGNEDで仲間IDを通知。再接続は既存の本人用historyで復元する単純な構成とし、schemaは旧1.1の記録も受け付ける任意フィールドとして拡張。AI変更・ゲーム実行なし。
部分確認: python -m pytest -q tests/test_game_state.py tests/test_content_models.py tests/test_state_delivery.py tests/test_protocol_schema.py → 1 failed / 75 passed / 602 subtests passed。追加WebSocketテストが存在しない死亡一覧の配送を待っていたため、受信待ちを修正。
全体確認: python -m pytest -q（サンドボックス外）→ 210 passed / 685 subtests passed（29.80秒）。全役職、役職欠け、YAMLの役職名・タグ名・陣営名変更、非公開配送、死亡後の再接続、旧通知と不正な仲間IDのschema検証を確認。
