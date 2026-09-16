# 復旧実行準備の反復失敗と接続検証

責務: Main Integrator（T370）。環境: Windows 11、Python 3.13.3、通常Owner実行。実行原本は変更せず、日本語説明のみを記録する。

## 失敗

- 最初の非送信preflightは外部監査文書 `EXTERNAL_REVIEW_LOG.md` の追記と旧実効allowlistの不一致でfreeze生成前にexit1。T372が非canonical監査履歴としてexact bytesを独立照合し、新source approvalへ結合した。製品変更はなかった。
- recovery-r2は準備成功・private admission承認後、`probe_once.validate_inputs` が `duplicate frozen source` で送信前exit2。AGENTS.mdがbaseline documentsとsupplemental controlsの両方に存在した。全214行のhashは一致していても、一意性契約は満たしていなかった。claim/child/HTTPは0。
- 局所helperテストとhash検査のPASSだけでは、生成物が後段の実関数に受理されることを証明できていなかった。

## 経路再評価と修正

T370はD068に従い実行補助という同じobjectiveの修正反復を再評価し、生成した現実的freezeを変更なしのprobe/game受理関数へ渡す非送信接続回帰をT377へ割当した。新frameworkや承認層は追加せず、既存一意性/改変拒否を緩めなかった。

T377はAGENTS.mdをsupplemental側から1件除去し、baseline document側のhash検査を維持。191source＋6docs＋16control＝213unique。回帰は修正前の同じ例外を再現し、修正後は両受理関数の成功と、重複/改変時の拒否を確認した。独立T375は14 PASS、OS exit0、前後2file一致。T372はexact bytesを独立承認した。

## 証拠と限界

- 失敗原本: `logs/t375-verification/preflight-recovery-20260916`、`logs/t375-verification/probe-recovery-r2-20260916`。
- 修正前bytes/差分/RED・GREEN: `logs/t377-freeze-repair`。
- 独立測定: `logs/t375-verification/t377-control-final-20260916`。
- 独立承認: `logs/t372-review/fresh-session/t377-freeze-uniqueness-review.json`。
- 後続recovery-r3の新準備は213unique/hash mismatch0、probeは実1回HTTP200/schema適合。gameは実1回exit1で、これを準備修正のPASSからゲーム成功に読み替えていない。終了原因とStage B判定はT370 handoffに統合する。

同じprivate claim/run/承認の使い回しは行わず、新freezeと新独立admissionを使用した。旧FAILや欠測は保持した。
