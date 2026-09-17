# T396 英語会話品質 A–I 原因・修正対応表

ユーザーが提示したT389本文観測を使用し、T394の確定事項を再調査しない。
保護原本の本文を追加表示せず、現在codeと受信済みtyped viewを確認した。

| 問題 | 原因箇所・現在の確認 | 修正対象 |
|---|---|---|
| A 秘密自白 | system promptはJSON/長さ/値の一般指示のみ。自role/team/win contextは既存投影あり | role名分岐を作らずcontextに基づく秘密保護/戦略指示 |
| B 毎回自己紹介 | 毎回COを要求するsystem指示はないが、通常会話とCOの選び方も説明なし | 会話継続、毎turn自己紹介回避、COは選択的 |
| C claim変化 | cognitive strategyは投影されるがImportantEventはCOのclaimed_role_idを落としcommentのみ保持 | 受信済み本人CO履歴を構造化して投影 |
| D 他者コピー | T394確定: 3群は別player間。旧guardは自己直前文だけ | 本人受信PUBLIC他者全文をNFKC/casefold/空白正規化。50文字かつ8words以上の完全一致をstage前に拒否。短い同意・抜粋を含む新文は許容 |
| E speech act | T394確定: 全167の生成段階から候補0、peer sourceは投影済み | 各actの意味/選択条件/本文対応を明示 |
| F 能力結果 | WorldのAbilityResultRecordはresult_id/event_type/revealed_role_idを持つがImportantEventにはtargetのみ | 本人受信済みAbilityResultViewをallowlist投影し実結果を保持 |
| G 生死 | WorldSnapshot.players/deaths/phaseとVoteResultRecordはあるがPhase6 inputにはcurrent IDs/phase程度 | snapshot由来alive/dead/公開死亡・処刑と既存action候補を明示 |
| H schema不一致 | T394確定: 167全件送信schema適合、局所84FAIL。ref、null相関、offered target等 | schema主要分岐と最終投影allowed一覧、semanticは非緩和 |
| I 中途切断 | `_validate_generated_text` は超長拒否のみ。生成textのhard slice経路は関連codeではなし。確認したsliceはinput memory/repair excerpt | 短い完結文を生成指示、上限近傍未完結を安全に拒否/既存repair |

Iの発生原因をhard truncateと断定しない。maxLength制約とモデル生成による未完結は、
コードでの検出対策と実LLMの新証拠を分けて扱う。
F/Gの情報はサーバの他player秘密から取得せず、既存の本人受信World viewに限定する。
T397設計SHA `720afafdfe536ca9df36dbc4dc681c8b212157657fe2a4a52a01dec0bfb9db12` はT398独立APPROVED。
Mainが4製品fileへ限定実装。ImportantEvent/World reducer/ゲーム本体/時間/モデルは変更なし。
F/G/Cは同一immutable request由来のgrounding、Hはtrigger別closed oneOfと最終memoryのallowed refs、
A/B/C/E/Iは英語指示、Iは190charsまたは570bytes以上の文末欠落をTEXT_BOUND→既存同一lease一回repairへ送る。
生成文の切断/書換えは行わない。新focused53件（標準9人×5triggerを含む）、関連回帰、公式offline grammarを検証。
T398指摘により、棄権不可PRE_VOTEのnone提示を除去し、CO判断と実decisionの合法組合せを明示した。
測定原本と範囲は `Docs/ai/handoffs/tasks/T396_ENGLISH_QUALITY_REPAIR.md`。実LLM品質改善率は未測定。

## T401観測後の限定修正
T401の16受理発言でA（Werewolf自白3）/B（反復自己紹介7人）/E（意味質問2・反論5・意見変更5に対しNONE16）が残った。
MainはT402でpromptだけ短く再構成し、私有戦略情報と公開自己紹介の区別、秘密保護の理由、
同じ会話の継続、本文とactの具体的な照合例を強めた。推論/CO/騙りを固定しない。
C/D/H/IはT401観測範囲で違反0、F/Gは機会なし。完全コピー/文末欠落各0。
最終T402の実品質効果は再実行しておらず未証明。T389との改善率を断定しない。
