# D072 — Phase6の出力profileと完了条件の再設定

Status: ACCEPTED（2026-09-14、ユーザーによる方向転換指示）

ユーザーの「外部エージェントにテストなどを整理させました。確認して実行して」により
`PHASE6_REDIRECTION_INSTRUCTION.md` を実行仕様として採用する。
原文SHA256: bb15b1b4903c547e22a9b9e563077517bd433e2cd1bd897079748f2509b27b42。
数値とscopeはユーザー判断であり、原文中の原因推測や測定の十分性まで事実認定しない。
T283の78–510は代表content計測で、512が全応答/非content費用を保証するという意味ではない。
32文字上限だけが過去の会話不成立の原因だったという因果も未証明のまま保持する。

## 採用する製品境界

Phase6の初期profileは200chars/600UTF-8bytes、text token誘導20–120、whole-response512。
Phase5/Q8の96/80/96/5–30は維持。day180、vote/night60は初期据置き。
length-finish拒否、同一leaseでrepair最大1回、player/phaseのCHAT最大2、CO別経路、
structured validation、server truth、privacy/authorization/linkageは維持する。
ai_client/llmの型・profile変更は原文§6.5の明示範囲として許可される。

Phase6 closureはROADMAPに記す5条件のみ。機械判定可能性はactual codeと独立審査で確認する。
特にschema/authorization PASSだけで自然言語private情報漏洩0を推定せず、必要なprivate原文審査を行う。
旧quality checklistの追加項目を新closureの必須条件へ戻さず、新条件の独立判定と対応を設計する。

調整実行と受入実行は別系列。調整はパラメータ探索で受入PASSにならず、最大5回。
token512超、day600超の必要、または5回で条件2/3が一度も成立しない場合は停止してユーザーへ報告。
受入実行は確定パラメータで一回、実起動直前のユーザー明示承認、raw保全、独立Tester/fresh Reviewerを維持。
別model/35B/fallback/soak/自動受入retryは不可。

現在の実行境界は先のユーザー指示「今回の監査修正と必要な独立検証が完了した時点で停止」。
今回は§6の5修正と独立検証まで進め、G/H全体・調整実game・受入実gameへ自動続行しない。
原文§2.2の将来調整実行許可は記録するが、この停止境界を越える起動には使わない。

## 変更・保留範囲

診断category/codeをredacted stderrへ出す。新private evidenceはgame/syntheticのpathで分ける。
private CHATのliteral oracleを追加し、global512超過は件数とreasonを安全に記録して停止する。
上限変更/分割/件数事前証明、追加provenance/rename/ACL設計、tokenizer追加調査、
数値予算追加審査、CI分割の追加は今回対象外。T288/T290/T284/T286/T285をCANCELLED。
既存変更・成果・FAIL/UNKNOWNを保存。T289の静的結果を保持して終了。
既存windows_private marker/unsupported guard等の継承bytesは保存するが未検証を承認へ読み替えない。
synthetic単体失敗は同packet内で原因を確認して限定修正・同テスト再実行可。初回raw不変、
D068のobjective単位の3失敗後経路再評価、独立gate、Design Gate、自認承認禁止は維持。

AGENTS/INDEX/WORKFLOW/roles/ai_status/check_docsおよびD051–D071は変更しない。
OPERATIONSは指定raw保留文のみ置換。今回のprofile変更の技術設計は独立承認が必要であり、
取り消したT284の数値審査やT288の追加provenance設計を復活させない。

## backup採用記録

外部作成 `D:\AIwolf_backup\20260914T041928Z_full\repository` を新snapshot復旧元として採用。
Mainは指示のmanifest SHA256 c47a78a156b8a62404a98bf7b70f2f88b71ce5fb4388584e94656d3c524f845eの
現物一致、保存済み両側照合8,620/8,620・変化/欠落/余剰/read error0、robocopy失敗0を確認。
新rootのprotected DACL、Owner/SYSTEM/Administratorsのみをread-only確認した。
8,620全fileをMainが再hashしたとは主張せず、外部実行結果として採用する。再コピーしない。
このsnapshot以後の変更は自動的にbacked upとはしない。
旧T287部分backupはsource changed before copyで失敗、初回記録/部分原本は不変で復旧元にしない。
保存済みprefixのMain照合では1,299file/4,502,340bytes（うち.git内1,298file）。次の対象は.git objectで、
指示書の「.gitを含まない」は正確ではない。途中で変化した主体・理由は未確認。部分backupが不完全という判断は同じ。
今後はコピー後に両側hashを照合し、途中変化を個別記録する。未一致を全一致と報告しない。
T252旧FAIL/原本欠落は未解消の履歴であり、今回backupで復元された扱いにしない。
