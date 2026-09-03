# D054 Design Request Lifecycle

## Status

Accepted (Implementer, 2026-09-02)

## Context

D051 / D052 の Design Gate は、同じサブPhaseに複数の設計要求が存在するとき、
実装済みの過去要求まで現在の Gate 項目として数えていた。REQUEST の `Status:` は
設計作業の状態であり、実装完了を表せない。

## Decision

REQUEST の設計作業状態と Gate の開閉状態を分離する。`Status:` は従来どおり
`REQUESTED` などの設計作業状態を表し、実装と検証まで完了した REQUEST には別行の
`Request status: CLOSED` を付ける。`Request status: OPEN` または未記載は Gate の
開項目として扱う。

`CLOSED` を Gate から除外できるのは、同じ stem の `_DESIGN.md` が存在し、
`Status: APPROVED` のときだけとする。対応設計が欠落・未承認、または明示状態が不正な
場合は Gate に残し、`check_docs.py` が不整合として報告する。

同じサブPhaseの複数の REQUEST を同時に OPEN にしてよい。`CURRENT_STATE.md` の
Next Task には `Target design:` を1行置き、現在の実装対象となる `_DESIGN.md` を
ファイル名で名指しする。Design Gate はその対象設計が存在し `Status: APPROVED` の
ときだけ実装を許可する。

## Why

REQUEST の作業状態を壊さずに、実装済み要求を明示的に閉じられる。承認前の要求が
誤って Gate から消えることも防げる。未記載を OPEN とすることで既存の REQUEST を
一括変更せず、段階的にライフサイクル状態を追加できる。

## Consequences

- 1サブPhaseに複数の REQUEST / DESIGN の対を置ける。
- `Request status: CLOSED` は実装完了時に Implementer が更新する。
- `Design gate: REQUIRED` は開いている要求が1件以上のとき Gate を有効にする。
  実装対象は `CURRENT_STATE.md` の `Target design:` で名指しし、対応する `_DESIGN.md`
  が `Status: APPROVED` のときだけ実装を許可する。
- `check_docs.py` は Request status の語彙と CLOSED の対応設計承認を検査する。
