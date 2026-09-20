# T444 Intent-firstの本文/act不一致

## 観測

固定Qwen9B/32case/seed4242026/context8192/max512、retry0/repair0の一回測定。
生成順は設計どおり32/32だが、不一致23→24、質問回答9/18→7/18。
HARD fail14→14、秘密5→1/state3→2の改善で主条件不達を相殺せず不採用。
独立tool承認と品質採否を分離した。原文はprivate保持。再現確認目的の再実行はしていない。

## 仮説と扱い

既存oneOf/grounded fieldの負担は意図先行envelopeだけでは除去されない、という仮説が残る。
一条件の結果で因果確定・全Intent-first方式の否定はしない。
限定実装欠陥は未確認。次の単一shape＋strict validatorは別の設計判断を要するため停止。
製品baselineを保持し、同bytes再測定禁止。通常game/Phase7へ進まない。

## 証拠

`Docs/ai/PHASE6_INTENT_FIRST_RESULT_20260920.md`、T446 quality/T447測定handoff。
annotation SHA: `9fb41e9157cd1e0715b99de127c26326db8138f9d2c9cb679e262679e86241dd`。
result SHA: `7bf556bdcb182d0661ec2eee3f0f2d7df56d10468a40801fb0a8ed5fbfd1448a`。
