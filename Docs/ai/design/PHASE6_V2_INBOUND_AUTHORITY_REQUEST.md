# v2 inbound authority限定接続設計要求

Status: APPROVED
Request status: OPEN
DESIGN: REQUIRED
Task: T516

T514のsource証明を実NetworkClient→WorldReducerへ接続する最小契約を定義する。任意Mappingのshape/hash検査だけでverified authorityへ昇格させない。
実受信owner、認証・sequence・credential commit後の発行点、reconnect/syncの失効、typed immutable sidecar、offline正負caseを具体化する。
既存T514承認本文は不変。実broker/audit/brain接続はこの設計へ混ぜない。provider0、v1既定、server/protocol不変。
詳細scopeはT516 packet。
