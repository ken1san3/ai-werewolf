# Phase 6 quality-r1：品質修正後も完走未達

## 実行と確定した事実

T378の限定品質修正後、T381の設計・実装・起動条件の独立承認とT383の独立検証を経て、run `T383B-20260916T112045636834Z` を1回実行した。retryは0。既存1200秒のゲーム待機上限を維持し、runnerは1208.331821秒、exit1、`game_end=false` で終了した。外側1500秒timeoutは発火していない。owned process14件の終了時残存は0、source/binding/providerの事後検査はPASS。

公開証拠は `logs/t383-quality-verification/game-quality-r1-20260916/`。safe-result SHA256は `6f5dc6d8ab43cc99ccc523f032047a2545efaaa4cff1299c03c2a4a00fae5460`。原本の非公開locator値と本文は転記しない。

## 改善と残存失敗の区別

- 修復時の入力拒否は旧r3の39件から0件へ減った。
- 応答ありstatusのうち妥当な応答は旧38/96件から69/102件へ増えた。generation総数やprovider実call数と混同しない。
- 同player直前文の反復は独立審査で旧9件から0件へ減った。
- 全gameでは同一正規化文3回の1群が残り、P6-B09は既知FAIL。直前文だけを抑止する承認済み修正の範囲と、全game反復条件を区別する。
- pre-vote再評価は0件から12件になったが、完走の代わりにはならない。
- processorの完全相関条件が成立せず実行0。観測accepted件数を完全母集団とみなさない。

## 経路の再評価

同じ条件の実ゲームを繰り返すだけでは、残る不正応答・反復・完走未達の原因を確定できない。今回の既知不具合の改善は保持し、次の調査は保存済み原本の不正応答とゲーム進行の対応、queue・要求期限・完了状態の相関へ限定する。ゲームロジックの先行変更、入力予算やtimeoutの無根拠な緩和、旧FAILの取消は行わない。

処理能力だけが排他的な原因であるという結論は未証明。追加実行・製品変更を行った事実はない。T381最終審査は `logs/t381-external-review/game-review.json` SHA256 `21b1c40b857eb344185616da9f17833e9d64af82eacf2e5b2e9bd680f56dfaf8`。PASS3/FAIL2/BLOCKED6、B08とB10はPASS、provider全106callがPROVEN_TERMINAL、UNKNOWN/poison0。queue offer中央値は39.285秒から19.244秒へ減ったが、day4 voteでGAME_TIMEOUTとなった。最終統合は `Docs/ai/handoffs/tasks/T378_EXTERNAL_CONTRACT_RECOVERY.md`。
