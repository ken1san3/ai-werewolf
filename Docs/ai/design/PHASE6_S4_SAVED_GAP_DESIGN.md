# Phase 6 S4 保存gap接続設計

Status: APPROVED
Task: T557
Scope: T556で `CONNECTOR_UNKNOWN` のまま保存した12行だけ
Approval-Content-SHA256: 89895986d45d8e4a651d82ce7b70663125b69f69696610a189fb72c9708d20b8
Approval-Record: Docs/ai/handoffs/tasks/T557_S4_GAP_DESIGN_REVIEW.md
Approval-Record-SHA256: 944625b6eac27d7d64914634ba408962dae72086351c01ce0dee88a8b8ae26ac

## 1. 目的と不変条件

T556の凍結結果（192行、PASS 165、FAIL 0、UNKNOWN 14、未観測13）を変更せず、接続不能12行について、保存済み構造から追加のlossless witnessを作れる条件を定義する。対象は `ROOT_BINDING` 7行と `FINAL_SURFACE` 5行である。件数分類は入口情報であり、12行を成功扱いする根拠ではない。

旧 `S4_PROJECTED_ABILITY_PROVENANCE_V1`、旧binding、旧public surface、accepted final、annotation、decision、hashは不変とする。既存T555 validatorを緩めない。新しい橋渡しが成立してもV1行を書き換えず、別versionのcandidate rowと結果をcreate-onlyで作る。本文、alias、role、option、channel名の意味、ゲーム規則から対応を推測しない。旧annotation・旧scoreは読まず、新provider、再生成、製品変更を行わない。

## 2. 共通入力と判定状態

Mainが別系統で固定したT556 source manifest、preflight、旧row binding、旧mechanical row、accepted plan、selection envelope、保存fixture、accepted-final locatorを入力とする。全source bytesとhelper bytesを開始・終了で再hashする。callerが同じbundleから計算したexpected hashを承認値として扱わない。

gap adapterの行結果は次のclosed型とする。

```text
SavedGapBridgeResultV1 exact keys:
  evaluation_id: opaque str
  gap_kind: "ROOT_BINDING"|"FINAL_SURFACE"
  status: "WITNESS_PROVED"|"UNKNOWN"
  reason: "EXACT_PUBLICATION_WITNESS"|
          "EXACT_ACCEPTED_SURFACE_WITNESS"|
          "SOURCE_PUBLICATION_NOT_PROVED"|
          "ACCEPTED_SURFACE_NOT_PROVED"|
          "INPUT_INTEGRITY"
  witness_sha256: Digest|null
```

`WITNESS_PROVED` の2 reasonだけwitnessが非nullである。これは診断witnessの完成だけを表し、projected row、S4入力、判定可能性を表さない。本設計ではprojected rowとそのdigestを作らない。欠測、複数候補、型差、hash差は `UNKNOWN`。adapter例外をPASS/FAILへ変換しない。

## 3. ROOT_BINDING bridge

### 3.1 問題の分離

現V1は `fixture.channel == binding.channel == source中の一意public channel` を要求する。非chat fixtureの `fixture.channel=null` とability bindingの `channel=null` を、一意public channel文字列へcloneする処理は値を補っているため、V1の完全なsource identityにはならない。`chat_channels` に一件だけpublic channelがある事実だけでは、そのability resultがそのchannelへ公開された事実を証明しない。

将来の限定versionでは、ability resultの生成sourceと、そのresultを公開surfaceへ選択したpublicationを別因子にする必要がある。既存V1 freezeは変更しない。本設計は将来versionのrow、validator、decisionを定義せず、diagnostic witnessだけを定義する。

```text
ProjectedAbilityPublicationWitnessV1 exact keys:
  contract: "S4_PROJECTED_ABILITY_PUBLICATION_DIAGNOSTIC_V1"
  old_blind_id, disclose_id, opaque_provenance_id: str
  source_sha256, bindings_sha256, catalog_sha256: Digest
  ability_pointer: JSON pointer
  ability_value_sha256: Digest
  owner_id: str
  source_channel: {tag:"VALUE", value:str}|{tag:"ABSENT", value:null}
  publication_channel_id: str
  publication_channel_visibility: "PUBLIC"
  accepted_plan_sha256, old_selection_envelope_sha256,
  accepted_surface_sha256, accepted_final_sha256: Digest
  witness_sha256: Digest
```

`witness_sha256` は自身を除くcanonical payload hash。`source_channel=ABSENT` は「publicと同値」を意味せず、保存edgeのnullをそのまま表す。

### 3.2 diagnostic witnessの必要十分条件

現在保存artifactについて、publication channelを取得してよいartifact/pointer集合は **空集合** とする。T557着手時点で、accepted actionまたはpublic surfaceにpublication channelを明示する保存fieldはcanonicalに確認されていない。このためROOT_BINDING 7行の診断は、条件1〜3と5が成立しても必ず `SOURCE_PUBLICATION_NOT_PROVED / UNKNOWN` であり、`ProjectedAbilityPublicationWitnessV1` を作らない。

将来captureでpublication fieldを追加する場合だけ、別設計で次の型を先に承認する。

```text
PublicationFieldSourceV1 exact keys:
  source_format: closed enum
  artifact_kind: closed enum
  artifact_whole_sha256, member_sha256: Digest
  row_identity: {case_id:str, seed:int, stage:str, ordinal:int|null}
  json_pointer: closed enum
  channel_id: nonempty str
```

許可artifact、`json_pointer`、outer exact shapeをsource formatごとに列挙し、whole/member hashとcase/seed/stage/ordinalを保存rowへexact関連付けする。null、欠測、複数候補、未知fieldはUNKNOWNである。現在はsource format、artifact kind、pointerの許可enumがいずれも空なので、保存artifactの探索や似たfield名の採用は禁止する。

将来の別設計で上記fieldが承認された場合でも、次をすべて機械照合した場合だけ `EXACT_PUBLICATION_WITNESS` とする。

1. 保存bindingは `source_kind=ABILITY_RESULT`、actorはsourceの `/context/player_id` とexact一致、authorityは `INTENTIONAL_OWNER_ABILITY`、pointer/value/source hashがexact一致する。
2. `accepted_plan.disclose_ids` に対象 `disclose_id` がexact一回あり、catalog全IDを先にstrict検査する。
3. 旧selection envelopeに同じselected IDの `SELECTED_DISCLOSURE` がexact一回あり、その既存opaque provenance IDを保持する。
4. 承認済み `PublicationFieldSourceV1` のclosed pointerが示すchannel IDだけを取得し、source `chat_channels` の同IDが `is_public=true` でexact一件である。
5. accepted final bytes、保存public surface、accepted plan、envelopeのhashがrow witnessと一致する。

この条件を満たす保存fieldが現在のROOT_BINDING 7行に無ければ、7行は `SOURCE_PUBLICATION_NOT_PROVED / UNKNOWN` のままである。alias、stage、`CO_OPPORTUNITY`、public channelが一件だけという状況、本文中の発言からpublication channelを作らない。

将来のprojected rowはsource identityにnullable `source_channel` を保持し、公開性はpublication witnessにだけ依存させる必要がある。ただしexact row keys、hash payload、旧base/selectionとのassociation、validator入力は本設計の対象外であり、別の独立設計・承認まで作成禁止とする。T555 V1へ偽の非null channelを渡さず、再判定しない。

## 4. FINAL_SURFACE bridge

5行は個別の差を未確認であり、同一原因と扱わない。raw本文を表示せず、strict parserが作る型付きcomponent digestで差を分類する。

```text
AcceptedSurfaceBridgeWitnessV1 exact keys:
  contract: "S4_SAVED_ACCEPTED_SURFACE_BRIDGE_V1"
  source_format: "BASELINE_V1"|"CANDIDATE_V2"
  accepted_final_sha256, saved_surface_sha256: Digest
  text_component: {
    status:"EXACT"|"ABSENT"
    source_pointer:"/decision/message"|"/message"|"/comment"|null
    destination:"text"|"comment"|null
    value_sha256:Digest|null
  }
  claim_component: {
    status:"EXACT"|"ABSENT"
    source_pointer: closed pointer|null
    decision: "DECLARE"|null
    option_sha256, claimed_role_sha256: Digest|null
    saved_claim_sha256: Digest|null
  }
  kind: "ABSENT"|"TEXT"|"STRUCTURED"|"TEXT_AND_STRUCTURED"
  witness_sha256: Digest
```

strict parserはouter/memberのduplicate key、nonfinite、UTF-8不能を先に拒否し、baseline wrapper whole SHAと `/final_content` member SHA、candidate response whole SHAと `/choices/0/message/content` member SHAをT556 witnessへexact照合する。inner JSONは次のclosed variantのどれかexact一つでなければUNKNOWNとする。保存surfaceはvariant選択に使わず、variant決定後の比較対象にだけ使う。

| format/variant | inner exact shapeと型 | text component | claim component | 決定kind |
|---|---|---|---|---|
| BASELINE_ABSENT | `{decision:null, discussion:{co_judgment:null}}` | ABSENT/all null | ABSENT/all null | ABSENT |
| BASELINE_TEXT | `{decision:{message:nonempty str}, discussion:{co_judgment:null}}` | `/decision/message`→`text` | ABSENT | TEXT |
| BASELINE_DECLARE | `{decision:null, discussion:{co_judgment:{decision:"DECLARE", selected_option_id:nonempty str, claimed_role_id:nonempty str}}}` | ABSENT | `/discussion/co_judgment`、3値non-null | STRUCTURED |
| BASELINE_TEXT_DECLARE | 上記TEXTのdecisionとDECLAREのco_judgmentを同時に持つexact shape | `/decision/message`→`text` | `/discussion/co_judgment` | TEXT_AND_STRUCTURED |
| CANDIDATE_ABSENT | `{}` | ABSENT/all null | ABSENT/all null | ABSENT |
| CANDIDATE_TEXT | `{message:nonempty str}` | `/message`→`text` | ABSENT | TEXT |
| CANDIDATE_DECLARE | `{decision:"DECLARE", co_option_id:nonempty str, claimed_role_option_id:nonempty str, comment:null}` | ABSENT | root decision、3値non-null | STRUCTURED |
| CANDIDATE_COMMENT_DECLARE | CANDIDATE_DECLAREと同じ4 keys、`comment:nonempty str` | `/comment`→`comment` | root decision | TEXT_AND_STRUCTURED |

dictは表記したkey集合と完全一致する。baselineの`decision`、`discussion`、`co_judgment`も表記したkey以外を持たない。nullと欠測を同一視しない。candidateで`message`とDECLARE fieldsが同居するshape、DECLAREなしcomment、未知decision、追加keyはUNKNOWNとする。

claimで許可する `source_pointer` は次だけである。

- baseline: `/discussion/co_judgment`。`decision=DECLARE` のとき `selected_option_id` と `claimed_role_id` を読む。
- candidate: rootの `decision=DECLARE`。`co_option_id` と `claimed_role_option_id` を読む。

claimは `{decision:"DECLARE", option:<source option>, claimed_role:<source role>}` のexact三fieldへkey renameし、保存surfaceのclaim列と型・順序・件数・値をexact比較する。`claim_component.status=EXACT` ではpointer、decision、3 digestが全てnon-null、`ABSENT` では全てnullである。text componentも`EXACT`ではpointer/destination/value digestが全てnon-null、`ABSENT`では全てnullである。値は非公開digestで比較し、alias解決やrole/option catalog意味比較をしない。追加claim、欠落claim、複数claim、未知fieldに依存する対応は `ACCEPTED_SURFACE_NOT_PROVED` とする。

text、claim、kindの全componentが上記規則で一意かつexactな場合だけ `EXACT_ACCEPTED_SURFACE_WITNESS / WITNESS_PROVED`。raw finalと保存surfaceの両方を同じ値へ正規化して一致させる方法、本文の意味からDECLAREを推定する方法、旧surfaceをrawに合わせて変更する方法は禁止する。

## 5. 決定順と保全

行ごとの順序は固定する。

1. global source/preflight/root/hash/row集合不整合ならrun全体を未完成で停止。
2. 旧executionが未観測なら既存MNOを維持し、本bridge対象外。
3. gap kindと保存行が一致しない、型・ID・hash不正なら `INPUT_INTEGRITY / UNKNOWN`。
4. ROOT_BINDINGは§3の全条件を評価。不足は `SOURCE_PUBLICATION_NOT_PROVED / UNKNOWN`。
5. FINAL_SURFACEは§4の全componentを評価。不足は `ACCEPTED_SURFACE_NOT_PROVED / UNKNOWN`。
6. witness完成行は `WITNESS_PROVED` として診断artifactへ保存するだけで、projected candidateを作らない。旧V1行、packet、annotation、decisionを上書きしない。

ROOTとsurfaceの両gapを一行で同時修復しない。複数gapを検出した行はUNKNOWNとし、別設計なしに橋渡しを連鎖しない。

## 6. 公開synthetic acceptance

正例:

- ROOTの現保存artifact正例は置かない。将来capture用syntheticだけ、closed publication fieldが明示され、plan/envelope/disclose ID、owner、pointer、value、public channel recordが全て一致する場合を診断witness正例とする。
- baselineとcandidateそれぞれでtextのみ、DECLARE claimのみ、許可されたtext+DECLAREの組合せが保存surfaceへexact一致する。
- 非ability record、旧opaque ID、旧binding ID、record順がbyte-equivalentに保たれる。

負例:

- public channelが一意という理由だけでnull channelを補う。
- foreign owner、別pointer、別row/source、非public/複数channel、未選択disclose、envelope ID差。
- option/role aliasの意味一致だけ、claim件数/順序差、片側欠測、未知field、複数text候補、`True` と `1` の型混同。
- source、accepted final、surface、plan、envelope、witness、Main pinの一バイト改変。
- UNKNOWNをPASSへ変える、旧annotationを再利用する、旧V1 artifactを更新する。

Python 3.10/3.13 focused、T555/T556関連回帰、source開始終了hash、create-only、safe output非漏洩、独立tool reviewを要求する。

## 7. 次gateと未解決境界

実装前に本設計の独立APPROVEDが必要である。次にprivate値を出さないcustodian診断で、ROOT 7行を `SOURCE_PUBLICATION_NOT_PROVED` に固定し、FINAL_SURFACE 5行ごとに§4のclosed variantまたは不一致componentをcountとclosed reasonだけで固定する。値、本文、path、condition対応は公開しない。

保存accepted action/surfaceにpublication channelが無い場合、ROOT_BINDING 7行を回復するには将来captureで明示fieldを保存する必要がある。これは新しい製品/capture契約でありT557では選択しない。既存保存行への後付けは禁止し、安全な結果はUNKNOWN維持である。FINAL_SURFACE 5行も§4のexact mappingを満たさなければUNKNOWNを維持する。独立design reviewの承認は実装、private診断、再annotation、再評価の許可を代替しない。
