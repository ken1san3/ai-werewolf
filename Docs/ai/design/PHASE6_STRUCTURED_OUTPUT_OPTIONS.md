# 構造化出力 A〜E 比較設計

Task: T437
Status: DRAFT

比較と将来の境界を定義する文書。B〜Eの製品実装・実LLM実験は選択／承認していない。まず別文書のkind-first単独probeを行い、その結果とMainのconverter分析を合わせて次の範囲を判断する。

## 共通のauthorityと採否

LLM出力は提案でありserverだけがゲーム状態の正本。どの案もrole分岐、権限拡張、根拠の自動補完、本文からのラベル捏造を行わない。入力は同じ許可済みsnapshot/dynamic options/evidence。kind、参照、option、target、base_revisionと更新内容を検証し、最終結果が揃うまで送信・state適用を行わない。自由文とラベルの意味一致はschemaだけでは保証できない。

候補比較は元T427 qw9の32入力・T428独立評価を再利用し、HARD/SEMANTIC/STYLEを別軸にする。相対改善はact/text不一致23件より減少、HARD fail<=14、捏造<=0、秘密<=5、STATE_CONTRADICTION<=3、ABILITY_CONTRADICTION<=0を個別に満たし、各軸UNKNOWNを増やさないこと。数値は既存集計へhash束縛し、1軸の悪化を他の改善で相殺しない。合法NONE/非開示、正当なCO、戦略的な騙り、短い同意を不当に禁止しない。

これは次の研究候補を残す条件であり製品gateではない。D077のHARD100%、重大秘密/状態矛盾0、長文exact copy0と独立reviewを維持する。実game/Master Run/Phase7は未許可。32件1seedの結果から統計的優劣や唯一原因を主張しない。

## 比較表

| 案 | 出力interface | call/判断 | 期待する効果 | 主な弱点・変更scope |
| --- | --- | --- | --- | --- |
| A 現行 | decision＋discussion、speech_actはclosed oneOf | 1 | 現validator・groundingをそのまま使用 | 本文とactの不一致。kind-firstは同じJSON値のwire順だけを試すA内の最小probe。 |
| B intent-first | closed intentを先、本文と更新を後に出す1つの応答 | 1 | 本文をact/対象/根拠と合わせて生成しやすくする | 新experimental envelopeと厳密adapterが必要。1call内順序が意味上の計画を保証しない。 |
| C 単一shape | speech_actを共通field集合＋kindにし、非該当fieldはnull | 1 | grammarの枝選択と必須fieldの非対称を減らす | syntax上の受理が広がり、strict validatorへの負荷増。null誤用・長文化の可能性。 |
| D 2call | 構造intentを検証後、別callで本文・更新を生成 | 最大2 | intentを固定して実現段階へ渡せる | 待ち時間、入力再送、admission、deadline、stale/cancel管理が増える。 |
| E regex後付け | 現出力本文から機械的にactを推定 | 追加0 | 実装や見かけのラベル数だけは小さな費用で変えられる | 意味・否定・引用・騙り・参照を保証できず非推奨。自動採用しない。 |

call数が同じでも出力token、grammar処理、遅延が同じとは限らない。Dも正確に時間・tokenが2倍になるとは主張せず測定対象とする。

## A: 現行とkind-firstの境界

現行parser/validatorと根拠契約を維持する。`PHASE6_KIND_FIRST_PROBE_DESIGN.md` はspeech_act各枝propertiesのkindだけを先頭へ置き、他fieldはalphabetical、proposal順・枝順・required・値は不変とする。C1/C2の結果をそのまま再実行せず新しい単一因子だけ測定する。

JSON値/canonical hashがbaseline同一、実wire hashが異なることをfreezeし、保存wireとMockTransport contentを照合する。converterの静的対応は別証拠であり、生成品質を代替しない。kind-firstで改善しても本文/actの意味検証と製品採用gateを省略しない。

## B: intent-first 1call（製品未選択）

将来のexperimental envelope案は `intent` → `realization` → `discussion_delta`。intentには既存decisionから本文だけを除いた構造（kind/option/target等）と既存closed speech_actを持たせる。realizationには当該decisionで許されるmessage/commentだけ、discussion_deltaには残りの既存proposal fieldを置く。思考過程や自由文rationaleは要求しない。

adapterは明示されたfieldを既存decision/discussionへ機械的に再配置し、decision_kind/option_id等の重複は同じ構造値からだけ再構成する。本文を読んでkindを決めたり欠けた根拠を推定したりしない。未知field、欠落、本文fieldが許されないactionでの本文、ID不一致を拒否する。candidate全schemaの検査と旧validatorの検査を別に記録する。

一回のcapture→一回のadmission/request→応答全体検証→freshness再確認→既存送信経路、というlifecycleは維持可能。stream途中のintentで予約やstateを確定しない。失敗は既存失敗扱いで、Aへの隠れたfallback再生成はしない。

実装前に必要なのは、全decision kindの本文分離map、closed envelope、各fieldの一対一変換表、unused field拒否、schema/token予算の詳細設計と独立承認。新prompt説明を足すならschema順変更だけの実験とは分けてfreezeする。既存product parserへ新envelopeを受け入れる変更は今回許可しない。

## C: 単一shape＋strict validator（製品未選択）

変更対象はspeech_actだけ。kindは現行7種の閉enum、その他は現行枝のfield集合を共通shapeへ統合し、全keyをrequiredとする。選んだkindで非該当のfieldはnull、該当fieldの型・値・根拠は従来契約を維持する。current/prior、subject/target、source/in_reply_to等の意味を統合しない。

test-only validatorはkind別の該当field表に従い、必要fieldのnull、非該当fieldの非null、空根拠、未提供参照、reply元とactの不整合等を拒否する。許された非該当nullだけを除いて旧closed unionへ変換し、旧validatorも通す。削除可能fieldの表は明示し、余分な値を黙って捨てない。NONEでも全非該当fieldはnullなので、合法NONEは残るが出力費用は増え得る。

callとlifecycleはA同様。受理条件をpromptだけへ移さず、strict validatorが旧受理集合を回復することを証明する必要がある。grammarで省いた制約を検証しないまま「単一shapeで成功」と扱うことは禁止。生のcandidateと変換後旧契約を別採点する。kind全7種の合法例、各fieldの欠落/null/extra、不正組合せ・捏造参照対照が実装前の必須設計項目。

## D: 2call（費用・lifecycle再設計が必要）

第1callは許可済みsnapshotからclosed intentだけを生成し、option/根拠/targetを検証する。第2callは同じsnapshotと検証済みintentを受け取り、本文・残り更新を生成する。intentは内部推論ではなく、実行候補の外形的な構造値に限定する。第2callがintentを書き換えた場合は拒否し、両方のrawを保存する。

部分成功を送信やstateに適用しない。第1call後、第2call前、最終適用前でphase/base_revision/deadlineのfreshnessを検査し、staleなら全体を破棄する。ひとつの判断に一つのterminal outcomeを持たせ、cancelで双方の所有処理を終端する。provider permitは各callで既存管理を使い、二段目のためにpermitを保持して他clientを塞がない。新schedulerやretryを暗黙追加しない。

admission/attempt accountingを「判断1件」と「provider call最大2件」で分離する必要がある。上限、失敗後のattempt消費、共有deadline、再入、cancel raceをArchitectが別途定義・独立承認するまで実装しない。比較するなら32判断最大64生成の新許可・freezeが必要で、今回の32call枠では実行しない。60秒を各callへ無条件に倍付与せず、将来設計で判断全体の予算と残時間を明示する。単独suiteの速度から9client共有queue性能を推定しない。

## E: regexの許容範囲

疑問符や表現の一致はscreen flagとして独立評価の手掛かりにできる。ただし引用・否定・皮肉・役職推理と自己開示の区別、誰への回答か、正しいEvidenceRefを保証できない。speech_actや参照の生成・上書き・stateへの反映には使わない。追加call0という利点でauthority/意味保証の欠落を相殺しない。

## 次の選択と独立token ticket

今はAのkind-firstだけを詳細設計済み。結果が不足ならB/Cは上記必要設計と計測コストを比較してから一方を選び、新packetで独立承認する。B/C/Dを同時導入しない。既存baseline reuseは入力fixture/model/sampling/runtimeと独立評価のbindingが維持できる場合だけで、契約差を隠さない。

token accountingは別ticket候補: proxy、rendered prompt tokens、provider usage、max output、grammar処理費用、KV/slot memoryを別の量として整理し、ズレの再現条件と責任範囲を調べる。実験中のbudget/計数式変更や1935実tokenの旧結果再調査はしない。別ticketの設計・実装許可は本書から発生しない。
