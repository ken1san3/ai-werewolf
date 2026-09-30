# D090: T550 R2a限定訂正後の連続実行

Status: ACCEPTED
Date: 2026-09-30

MainがR2a時間式と対応test条件だけの修正・独立追加review1回を質問した後、ユーザーが「実装→ローカル検証→独立tool review→有限LLM測定 これをできる限りノンストップで」と続行を指示した。これを当該限定例外と後続工程の再開許可として記録する。

- WP1同一designの§4.4/§6に限りR2aを訂正し、独立design review ROUND3を今回だけ許可する。新設計文書は作らない。既確認のR1/R3/B1/B2は再審査しない。
- APPROVED後、WP2実装・両Python検証・独立tool review、WP3有限測定、WP4独立意味評価まで、工程ごとの再確認なしで進む。
- 製品変更はD089のprior境界のみ。他の製品変更、main、Actions、game、Phase7は禁止のまま。
- 2行probeの停止条件、合格時96行一回/90分、同条件retry禁止、private/public境界を維持する。追加review許可を無制限のreview反復許可とは解釈しない。
