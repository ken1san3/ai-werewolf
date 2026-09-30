# D091: T550 判断6の照合付き再接続と新規一回実測

Status: ACCEPTED
Date: 2026-09-30

ユーザーは最新T550指示書の「判断6を反映して再開」と明示した。packet commit
`9b7698bff6392ef09ed83abf0056b5d8c9454ec7` を正本とする。

- 既存改訂設計の接続契約だけを訂正する。設計訂正review 1回、tool review 1回、新しいrun identityの実測1回を追加許可する。いずれかのreviewで追加回が必要なら停止して報告する。
- 切断・サーバーclose・新規connectでは旧接続を捨て、保持process handle/snapshot、生存、唯一listener owner、開始runtime identity、source hashを再照合する。一つでも不一致ならintegrity falseで停止する。
- 中途切断した呼び出しは受理・再送せず段の失敗へ計上する。再接続回数・時刻・固定理由を保存する。
- model/runtime/configの大きなfile hashは最初の接続前と終了close後に限る。witness接続前prepareは保持する。
- model-free HTTPでidle切断・request-count切断・owner変更を検証し、両Pythonのfocusedと独立承認後だけ実測する。
- 旧runのclaimと証拠を保持する。新しい固定一回枠を旧run hashへ束縛し、旧runの補完・再開・同条件retryにしない。
- 実測は既存2行probe→見積り条件合格時96行一回/90分。分母、sampling、予算、モデルは維持する。

D088〜D090の他の境界は維持する。T515/T549一時停止、v1既定、製品変更はD089 priorだけ。
main/Actions/game/Master Run/Phase7/model変更・DL/API、privacy/authority/validator緩和は禁止。
追加枠は無制限のreview・retry許可ではない。
