# R16採否と次回の観測手順

2026-09-15。ユーザー「採用すべき点を採用してください」に基づく文書限定改訂。対象はR16のU1〜U3。実行結果や実装承認の追加ではない。

## 採否

| 指摘 | 採用する内容 | 採用しない断定 |
|---|---|---|
| U1 | T316とT330/T344の差は未解明と明記し、次回providerの実構成を保存・比較する。 | CLIのthinking指定差が原因と確定すること、次回argvだけで過去の差が解明できること。 |
| U2 | 取得できる終了理由・生成token数・本文状態・validation_codeを分けて診断する。 | HTTP200なら正常、空本文なら必ずOUTPUT_INVALID、reasoningが原因と無観測で確定すること。 |
| U3 | 次回preflightで実際のGGUFのSHA-256を必須確認し、同じ実行のprovider identityに結合する。 | 現在のhashからT316の過去モデルが同じだったと確定すること。 |

T316の記録はDECISION 8件、OUTPUT_INVALID 26件。34件を「正常成功」と呼ばない。35 provider terminal中34 PROVEN_TERMINAL / 1 UNKNOWNという別の集計も、出力妥当性率と同一視しない。根拠はT318/T320 handoff。T316の実argvとGGUF hashの欠測は、現物の新観測で過去へ補完しない。

T348 handoffと `logs/t348-sampler/private-inspection.json` の保存template検査では、thinking無効時にも空のthink blockが残る。R16の「enable_thinking=falseならprefixを出さない」という仮説は採用しない。修正後HTTP200/schema-validは未検証であり、今回の文書採用で実証済みへ変えない。

## U1/U3：次回preflight

1. T328のR14 S1の手順で、その実行時点のPID・所有者・creationと実command観測を結合する。既存helperのredacted private原本、公開 `command_observation` / `command_canonical_sha256` / `environment_observation` と独立照合を使う。providerを起動停止しない。
2. 同じ正規化方式で得た新command hashをT344の承認済み観測hashと比較し、一致・不一致・比較不能を明記する。比較元はT345 handoffの `command_canonical_sha256=7d42b2161351cbeb9fd5316acdd91bf638fc4b035f0ad903c460ce54b1776281`。T330との比較は当該保存証拠を照合できた場合だけ行う。commandの一致は環境・template・モデル・request全体の一致を意味しない。
3. 実際に使用されるGGUFについて、既存preflight方式でSHA-256、size、モデルidentity、serving build/ファイルidentity、PID/creationとの対応を記録する。複数shardなら全構成fileを対象にする。設定上のモデル名だけで同一性を認定しない。保存後に独立Reviewerがbindingとhashを照合し、必要なidentityが確認不能ならlaunchを保留する。既存T330のGGUF照合を次回の必須手順として継承する。
4. 比較ではCLI設定と今回送るrequestの `reasoning_format=deepseek` / boolean `enable_thinking=false` を分ける。既存の安全な設定・fingerprintとserving template identityの証拠を使い、private promptやraw argvを公開しない。現ファイルのhashだけで既にロード中のbytesを証明せず、実providerへのbindingが確認できない場合はUNKNOWNとする。
5. 実測が成功しても「観測された今回構成で修正後経路が成功」と報告する。T316との差の原因確定は、比較可能な当時の証拠が得られた場合に限る。未解明のT316を解くための追加runは自動で行わない。

## U2：次回の結果判定

B10の追加診断として、同一run・attemptの既存private generation原本を使う。`OUTPUT_INVALID` / `REPAIR_FAILED` は `completion_tokens` と `finish_reason` を先に確認し、`response_text` / `response_bytes` / `validation_code` と合わせて診断する。backend failureとresponseありの失敗は別に数える。

- **本文なし・空の観測**：実responseの許可された保存証拠がある場合だけ判定する。現backendは空文字・null・非文字列contentを `RESPONSE_ENVELOPE_INVALID` としてusage/finish_reason取得より前に拒否する。同codeは他のenvelope不正でも生じるため、codeだけでは空本文の証拠にならない。generationの `response_text=None` やerror時bytes=0も空本文の証明ではない。原本がなければ「本文状態UNKNOWN」と記録する。
- **出力上限到達**：観測された `finish_reason=length` と不採用を記録する。JSONの途中切れは実本文で確認できた場合に別記し、lengthだけでJSON不正やthinkingによる使い切りを断定しない。
- **形式・値の不適合**：保存本文と既存validation結果から判定する。`VALUE_NOT_OFFERED` 等を空本文・途中切れへまとめない。停止理由だけでschema適合を認定しない。
- **欠測・相関不能**：fieldごとの欠測数、観測可能な件数と母数、UNKNOWNを残す。token欠測を0にせず、accepted text長を失敗responseの長さへ代用しない。reasoning_contentを新規取得・本文へ代入しない。

上記は手動の診断ラベルであり、新runtime fieldや排他的なstatus enumではない。各観測軸は重なり得るため加算せず、generation/attemptとprovider callの母数を分ける。公開は安全なcount/分布/hashのみ。既存B01〜B11のoracle、mandatory式、閾値、B10必須四測定を変更しない。追加診断の欠測だけを新blockerにしない。

## 適用境界

次回担当packetはT328のR14条件と本書を継承する。実行前確認と結果診断は別の工程であり、今回の採用はprovider観測・推論probe・ゲームの開始指示ではない。新実行の有限条件・許可と独立Tester/Reviewerを維持する。P3保留、Q2後続設計、ゲームロジック・schema・timeout方針は変更しない。旧原本と外部レビュー本文はそのまま保持する。
