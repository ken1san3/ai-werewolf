# Phase 6 resolved subject 限定probe request

Status: DRAFT

## 仮説（未証明）

発話段のplan.subject_player_idはopaque IDのまま、同じplan.subject.valueにはcanonical player IDがある。77保存message plan全てで併記され、受理本文69件にはopaque literal混入0、選択subject literalを含む47件/他player literalだけを含む9件がある。この文字列観測を意味不一致とは採点しない。
発話段だけで既に明示選択されたsubject IDをcanonical値へexact dereferenceすると、本文と計画が合うかを新条件で検証する候補である。単なるcontext不足・モデル弱さへ原因を戻さない。

## 一因子

変更は発話段user payloadのplan.subject_player_id値だけ。nullはnull、非nullは既存subject bindingのsource scalar player IDへexact解決する。同じ選択を表示するので戦略やrefを推測しない。plan.subject object、他全field、順序、schema、system instruction、sampling、model、context8192、既存budget、guard、retry/resample policyは不変。
source/hostが保持する元plan/raw/provenanceは変更しない。製品presenter/schemaは不変。完全metadata除去、prompt注意書き追加、oneOf調整を混ぜない。

## 必要な設計判断

保存T550のchat84行（message plan77/無し7）をどう固定するか。新providerは選択済みplanの発話段だけを再現する別conditionで、計画段や非chat12を再生成しない。全96行のcoverageを保持し、19行を旧runから新runへ移植して成功扱いしない。これはplan-locked counterfactual診断であり新フルpipeline採用試験ではない。
controlは保存T550 v2 realization、canonical v1 baselineとは別名を使う。全旧raw/annotation/hashを保持する。比較に必要な新rubricは別versionでfreezeし、新旧本文をprofile-neutral blind評価する。
元protocolのresampling/seed/段budget/acceptanceとの同値が一因子で保てなければproviderを実行しない。まずsaved requests/outcomes/sealをmetadataで限定照合し、一回限りのbounded call上限を定義する。余計なprobe generationはせずutility/native countsとfocusedを先行する。

## 成功・失敗の扱い

act/message整合、本文回答、source state矛盾、copy、text completenessを別々に報告する。無効/UNKNOWN/未観測は保持し、非NONE数やsubject literal出現だけで改善としない。S4/authority/privacy保証をこのprobeの単一スコアへ混ぜない。総合Phase6採用は別gate。
必要性がofflineで支持され、D100のdesign/tool/freeze/ownedprocess/private境界/cleanup全条件を満たす場合だけ一回有限測定する。診断で因子が否定されたら無意味な調整をせず保存した結果から次仮説へ進む。

