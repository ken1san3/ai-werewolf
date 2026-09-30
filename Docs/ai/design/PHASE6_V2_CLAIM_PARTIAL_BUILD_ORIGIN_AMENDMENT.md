# Phase 6 v2 claim partial build origin 限定追補

Status: APPROVED

## 1. 目的と境界

T536の`CLAIM_GRANTED_BUILD`中にallocationまたはactivationが失敗した場合、既存owner fieldだけで、どの所有edgeまで
発行済みかを一意に保全する。新しいauthority、公開field、broker call、retry、状態遷移は追加しない。既承認T536と
T540 preclaim hold追補は変更しない。

## 2. closed build-origin shape

`ClaimActivateOwnerV2`へprivate診断field `build_origin`を追加する。値は
`BUILD_NOT_STARTED -> BUILD_CONTEXT_ONLY -> BUILD_CELL_BOUND -> BUILD_CANDIDATES_BOUND -> BUILD_ENTERED`
のclosed enumで、該当edgeをownerへ発行した同じno-await区間で一方向にだけ更新する。authority、CAS expected、
broker control、receipt再構築、retry判断の入力には使わない。UNKNOWNとretireまで最後の値を保持する。
partial buildの正本は`build_origin`と次のstrong-ref shapeの双方向一致である。validatorはshapeからenumを修復せず、
近いrowへdowngradeしない。

| `build_origin` | `activation_context_or_null` | active cell / receipt / selection | cleanup candidates | 許可する次手 |
|---|---|---|---|---|
| `BUILD_NOT_STARTED` | null | 全null | null | 一回だけbuild開始 |
| `BUILD_CONTEXT_ONLY` | exact `_LeaseActivation`、ownerのsession/invocationと一致、未enter | 全null | null | context refを保持したまま既存clean release。一回もenterしていないcontextをnull化しない |
| `BUILD_CELL_BOUND` | exact context | exact ACTIVE cell・receipt・`BOUND_EMPTY` selection。receiptからcontext/owner/cellへのT536 backlinkが全一致 | null | 既存clean release。cell/receipt/contextを保持 |
| `BUILD_CANDIDATES_BOUND` | exact context | `BUILD_CELL_BOUND`と同じ | reason順4件のimmutable exact tuple | 既存activate/CAS、または既存clean release |
| `BUILD_ENTERED` | exact context、entered | `BUILD_CANDIDATES_BOUND`と同じ | exact 4件 | contextを一回exit後、既存clean release |

identity/postcheck/selection/receiptや個々のinvalid candidateをlocalで構築中に失敗し、まだowner fieldまたはcellへ発行して
いない場合は、外部効果が無いため直前のrowを維持する。candidate prefix 1〜3件はowner strong fieldへ発行しない。
現`_issue_bundle_cell_v2`によるno-await/no-callback区間の一時weak registry登録だけは許すが、例外unwind後かつUNKNOWN publish前に
prefix cellのlive registry entryを全て除去する。prefixはauthority発行、strong ownership、保持証拠とは扱わず、失敗時は
`BUILD_CELL_BOUND`を維持する。ACTIVE cell発行後はreceipt backlinkを含む`BUILD_CELL_BOUND`以降だけを許す。candidateは4件全てをtuple化して
owner/selectionへ同一no-await区間で発行し、その区間で`BUILD_CANDIDATES_BOUND`へ進める。

`CLAIM_CLEANUP_UNKNOWN`または`ACTIVE_CLEANUP_UNKNOWN`へ移る際、発行済みcontext、cell、receipt、selection、candidate
tupleをnull化しない。contextをexitした場合もexact objectを保持し、`_entered=false`を観測する。特に
`BUILD_CONTEXT_ONLY`をcontext nullの`BUILD_NOT_STARTED`へdowngradeすることを禁止する。`BUILD_ENTERED`後にcleanupで
contextをexitしてもexact context refと`build_origin=BUILD_ENTERED`を保持し、`_entered=false`を照合する。broker terminalが既存契約で
確定し、owner graphをretireする原子的区間だけがrefをclearできる。

## 3. acceptance

focused offline testはcontext取得直後、identity/postcheck/selection/receipt各allocation、ACTIVE cell発行直後、4 candidate
各allocation、tuple化、context enterの各failure pointを一回ずつ注入する。各結果が表の一rowだけに一致し、発行済み
strong refとbacklinkを保持することを確認する。各enum×shapeの1-bit不一致、enum逆行、context-onlyのnull化、
cellありreceipt/backlink欠落、candidate prefixのowner strong publish、UNKNOWN時のlive prefix registry残留、entered contextのref clear、
foreign/same-value object置換を拒否する。provider、server、game、Actionsは0である。
