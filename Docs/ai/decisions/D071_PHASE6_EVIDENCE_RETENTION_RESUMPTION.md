# D071 — 原本欠落を保持したP6-F新規証拠取得

Status: ACCEPTED（2026-09-14のユーザー判断）

ユーザーはT252原private61artifactを明示バックアップしていないと回答し、元原本の復元を
必須にせず、欠落を未解消の履歴として保持した新規証拠取得を承認した。
T252のFAIL・欠落・過去hash/log/failure recordは変更せず、新しいPASSへ読み替えない。

新規完走の前にpytest管理領域外へのraw private証拠の永続保全を確定し、後続pytestを
実行しても原本が残ることとprivacy境界を独立確認する。既存ACL/security/TEMP設定・
管理者権限を変更しない。製品・受入・test semanticsを弱めず、巨大なframeworkを作らない。

順序は最新状態/所有権照合→保全方法の確定と独立検証→T253/T254のfresh review→
承認後の別packetで必須9-client完走一回→raw原本の即時永続保全→hash/manifest/linkage/
completion独立確認→F全acceptance fresh review→成立時のみF DONE。
安全な範囲で逐次のユーザー確認は不要。現在の依頼境界はP6-F正式DONEまで。Gは開始しない。

既存の新規private object作成時の適切なmodeは保持する。ACLの書換え、保護の無効化、
TEMP再配置での回避は行わない。原本は一時領域へ保存して後で回収する方式よりも、
pytestの所有しないprivate領域へ最初から直接書く最小経路を優先し、実測と独立レビューで確定する。
