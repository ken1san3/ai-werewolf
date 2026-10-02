# 停滞原因分析の再現用資料（2026-10-02）

Phase 6 停滞の原因分析で使ったスクリプトと、私（Claude）が行った実験のうち
private ゲームログを含まない結果だけを置いています。

- 結論のまとめ: `FINDINGS.md`
- 再構築の一覧とプロンプト: `rebuild/`
- このフォルダは再構築ブランチでは `Docs/analysis/2026-10-02/` にコピーされます。以下のパスはそこに読み替えてください。

## 前提

- `C:/AIagent/llama-server.exe`（build 10697）と `C:/models/Qwen3.5-9/Qwen3.5-9B-Q4_K_M.gguf`
  （製品と同じモデル・同じ runtime）。
- サーバ起動（製品と同じ設定、ポートだけ 8090）:

```
C:/AIagent/llama-server.exe -m C:/models/Qwen3.5-9/Qwen3.5-9B-Q4_K_M.gguf --host 127.0.0.1 --port 8090 -c 8192 -np 1 --jinja --reasoning off --chat-template-kwargs '{"enable_thinking":false}' --fit off -ngl 99
```

- `scripts/` を作業ディレクトリにして実行（`llm.py` が 127.0.0.1:8090 を叩きます）。

## 実験一覧

| ID | 内容 | スクリプト | 結果 |
|---|---|---|---|
| E1 | T401 の保存プロンプトを製品と同じ body で再送（再現確認） | `exp_context.py` | scratchpad のみ（private） |
| E2 | E1 のプロンプトに、token proxy で省かれた会話履歴を戻して再送 | `exp_context.py` | scratchpad のみ（private） |
| E3 | 同じ局面・同じ情報を平文プロンプト＋自由文で生成 | `exp_natural_replay.py` | scratchpad のみ（private） |
| E4 | 平文プロンプトだけの最小 9 人ゲーム（server/network を通さない模擬） | `sim_game.py` / `sim_game_ja.py` | `results/sim_*_transcript.txt` |
| E5 | 発話行為ラベルを生成後に別の小さな呼び出しで付与 | `exp_label.py` | `results/e5_labels_sim1.json` |

`sim_metrics.py results/sim_1.json ...` でラベルを使わない機械指標（完全反復、同一文3回以上、
狼側の自己開示、`I am player-N` 型の自己紹介）を再計算できます。

## 実サーバでのリアルタイム試作（`spike/`）

既存のゲームサーバ（`server/`、standard_9、サーバ権威・private配送）に、普通の文章で指示する簡易AIを
9人接続してリアルタイムで対戦させた試作です（約400行、1プロセス・各自のWebSocket接続、固定の発言順なし）。

| ゲーム | プロンプト | 結果 |
|---|---|---|
| A（seed1、昼60秒） | 初版 | 完走、公開チャット86、サーバ拒否0。「I agree」で始まる発言42% |
| B（seed2、昼90秒） | 初版 | 完走、129件、拒否0。死亡者に呼びかける発言60% |
| C（seed2、昼90秒） | 改訂版（進行説明・当日の会話のみ・死亡者表示・近似重複除外） | 完走、104件、拒否0。死亡者への呼びかけ3%、「I agree」2% |

生成時間の中央値は1.3〜2.0秒、待ち時間の中央値は約2.5秒（最大約9秒）でした。各条件1ゲームだけなので、差は目安です。

- 起動例（リポジトリのルートから、llama-server を 8090 で起動した状態で）:
  `python analysis/stagnation-2026-10-02/spike/spike_live_v2_improved_prompt.py --seed 2 --day 90 --vote 30 --night 30 --out gameC.json`
- 集計: `python analysis/stagnation-2026-10-02/spike/spike_metrics.py analysis/stagnation-2026-10-02/spike/results/game*.json`
- 人狼の相方は、サーバが現在送っていないため、試作のハーネス側から渡しています（`--no-teammates` で無効化）。

## 注意

- E4 は製品ではありません。server の権威判定・private 配送・realtime 進行を通していません。
  人狼に相方を教えている（製品は教えていない）点、1日の発言上限が製品（2回）より多い点も違います。
- 出力は確率的です。seed を変えれば別の会話になります。
- E1〜E3 と E5 の T401 版は製品の private ゲームログ（`C:/AIwolf/logs/phase6-private-evidence/game/`）
  から抽出した本文を含むため、ここには置いていません。
