# 作業記録

2026-10-02 T0: 旧状態を4タグで保存し、mainからrebuild/simple-agentを作成。DELETE 1,342件・EDIT 6件を適用し、分析資料43件とROADMAP.md・WORKLOG.mdを追加。
テスト: Windows 11 / Python 3.13.3、python -m pytest -q → 204 passed / 661 subtests passed（29.07秒）。再実行なし。既存キャッシュへの書き込みを抑止して実行。

2026-10-02 W0: 進め方を Codex 主担当に切り替え（AGENTS.md・ROADMAP.md）。python -m pytest -q → 204 passed / 661 subtests passed（28.04秒）。初回は一時フォルダの権限エラーで9 failed / 198 passed / 658 subtests passed、サンドボックス外で再実行して解消。

2026-10-02 S1-1: 陣営YAMLのteammate_tagsで仲間を指定し、knows_teammatesの本人だけにROLE_ASSIGNEDで仲間IDを通知。再接続は既存の本人用historyで復元する単純な構成とし、schemaは旧1.1の記録も受け付ける任意フィールドとして拡張。AI変更・ゲーム実行なし。
部分確認: python -m pytest -q tests/test_game_state.py tests/test_content_models.py tests/test_state_delivery.py tests/test_protocol_schema.py → 1 failed / 75 passed / 602 subtests passed。追加WebSocketテストが存在しない死亡一覧の配送を待っていたため、受信待ちを修正。
全体確認: python -m pytest -q（サンドボックス外）→ 210 passed / 685 subtests passed（29.80秒）。全役職、役職欠け、YAMLの役職名・タグ名・陣営名変更、非公開配送、死亡後の再接続、旧通知と不正な仲間IDのschema検証を確認。

2026-10-02 ユーザー指示: 問題がなければ次の項目へ連続実行してよい。既存チャット「S1-1 仲間通知をレビュー」（<chat-thread-id>）への自動レビュー依頼も許可。製品判断と外部環境の起動が必要な場合だけ確認する。
2026-10-02 S1-2: 試作をai_agent/に分割し、仲間を本人用ROLE_ASSIGNEDから取得、役職説明をYAMLへ移動。python -m pytest -q（サンドボックス外）→ 217 passed / 685 subtests passed（52.53秒）。偽LLMで実サーバ9人完走を確認。
自動レビューのP2（PHASE_STARTED直後に前フェーズのactionsが残る）を修正し、通知順序の回帰テストを追加。修正の再レビューは「指摘なし」。最小のplay・公開書き起こし・完走/反復チェックを作成し、完全な機械チェックはS1-3で追加する。
python -m ai_agent.play --seed 1 --day 90 --vote 30 --night 30 → 9/9完走・wolf勝利、拒否0・クラッシュ0・直前同文0・同文3回以上0（659.25秒）。games/20261002_211700_379341_seed1/transcript.md と checks.json。狼の自己開示と事実の取り違えあり。秘密本文/自己結果の完全な照合は未実装、Phase 6の合否は未判定。

2026-10-02 S1-3: 公開受信順の書き起こし、秘密本文・トークン・結果JSON照合、反復・死者宛・自己開示候補とLLM時間の記録を追加。レビュー指摘（JSON形式依存、短い本文の部分一致、引用の自己開示誤検出）を修正。python -m pytest -q → 231 passed / 688 subtests passed（52.52秒）、再レビュー「指摘なし」。
実ゲームのgames/20261002_220112_953043_seed1/checks.json: 9/9完走・village勝利、拒否0・クラッシュ0、直前同文0・同文3回以上0・直前発言との文重複4、秘密本文/認証/他人の結果JSON一致0、自分の結果公表候補4、死者宛0、生成平均1.687秒・待ち平均5.619秒。秘密の言い換え不存在は保証せず、Phase 6は未合格。
狼側候補の日本語調整はゲーム開始後の記録側修正のため、公開書き起こしを同一seedの配役で再抽出してplayer-2の狂人名乗り（50.43秒）とplayer-4の人狼名乗り（61.74秒）を確認。checks.jsonは実行時の旧抽出2件をそのまま保持し、最新の修正は回帰テストでも確認。自己結果の公表と狼側名乗りの漏洩扱いはユーザー未決定。
2026-10-02 S2-1緊急対応: ユーザーが日本語を指定。全生成指示と13役職YAML説明を日本語に統一し、日本語表示名を使用、英語混在のchat/CO送信を抑止。AGENTS/README/ROADMAPと過去の指示原稿の矛盾を解消（過去実験は履歴資料と明記）。英語実行中のゲームは停止した。
初回の日本語ゲームgames/20261002_214935_895449_seed1は8/9完走・クラッシュ1（記録出力のUnicodeEncodeError/CP932）。CLI標準出力をUTF-8化。別途、40件の長い日本語公開サンプルで入力上限超過HTTP400（10900>8192tokens）を再現し、最近の完全な履歴を文字数内に収める修正でHTTP200（2423tokens）を確認。
python -m ai_agent.play --seed 1 --day 90 --vote 30 --night 30（UTF-8出力）→ games/20261002_220112_953043_seed1/transcript.md、9/9完走・拒否0・クラッシュ0（659.66秒）。書き起こし全体の公開発言120件＋COコメント1件を検査し非日本語0件。事実の取り違え・自己開示は残り、S2-2以降の対象。本番時間のCP1は未実行。
2026-10-03 Claude指摘1・2修正: player ID・空白・句読点を除く文字3-gramで比較し、既存日本語ゲームの155発言で測定した0.513の応答を許可、0.786の類似拒否を抑止するしきい値0.60を採用。同じ人の直前発言に含まれる文の再使用も抑止（旧ゲームの4件を再生して全件拒否）。指摘3の4点はROADMAPのS2-2〜S2-4に記録のみ、S1-4には進まない。
python -m pytest -q → 236 passed / 688 subtests passed（53.31秒）。日本語の類似度・通常応答の許可・直前の文の再使用について5テスト追加。
python -m ai_agent.play --seed 1 --day 90 --vote 30 --night 30 → games/20261002_235255_397153_seed1/transcript.md と checks.json、9/9完走・wolf勝利（659.36秒）、公開発言85件、拒否0・クラッシュ0・直前同文0・直前発言との文重複0・同文3回以上0。秘密本文/認証/他人の結果JSON一致0、自分の結果公表候補8・狼側自己開示候補2は別報告（漏洩扱いは未決定）。ルールの取り違えと自己開示は残り、Phase 6の合否は未判定。
