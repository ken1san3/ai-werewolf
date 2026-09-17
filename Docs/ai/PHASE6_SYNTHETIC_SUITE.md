# Phase 6 人工会話suite

## 目的と範囲

通常9人gameを起動せず、固定BrainInput→既存projection→共有Qwen provider→既存schema/semantic validator
の経路を1case1generationで観測する。内部推論は要求・採点・保存しない。修復generationも行わない。
製品のprompt、proxy gate、状態境界、モデル、concurrency、Logical Clockは変更しない。

fixtureはcanonical contentから役職属性を取得した合成データで、実ゲームの秘密原本を使わない。
`role_contexts`はinertなfixture objectを構築するだけで、server/network/game loopを起動しない。
他caseの能力結果は共有しない。selfはplayer-6、公開生死と本人能力結果を既存型へ入力する。

## 32caseと評価ルール

各群2case。2件目は質問文・結果値・対照条件を変える。完全一致の正解文は置かない。

| ID | 群 | 主な評価 |
|---|---|---|
| G01-1/2 | 直接質問 | player-3の質問へ理由を答えANSWER、実source/addresseeを使う |
| G02-1/2 | 告発反論 | 自分が沈黙していたという誤った前提に反論。REBUTTAL/CLAIM解釈、公開履歴参照 |
| G03-1/2 | 理由を問う | 理由のない疑い/投票提案に質問しQUESTION。異なる戦略選択は別に記録 |
| G04-1 | 意見変更 | 既存suspicion80とnot_wolfを受けた変更のprior/current/実cause整合 |
| G04-2 | 過去意見なし | 架空のpriorを作らない。not_wolfとvillage陣営を同一視しない |
| G05-1/2 | Seer結果 | not_wolf/wolf。開示時に実resultと一致。非開示のみではFAILにしない |
| G06-1/2 | Medium結果 | 処刑済targetのnot_wolf/wolf。生者の検査や逆結果を捏造しない |
| G07-1/2 | 生死 | player-2 alive/player-5 deadを優先し、公開誤情報へ追従しない |
| G08-1/2 | Werewolf秘密 | 通常会話で無利益な真の敵対役職自白をしない。騙り/沈黙は許容 |
| G09-1/2 | Madman秘密 | 無利益な真のalignment自己紹介をしない。騙りは許容 |
| G10-1/2 | claim継続 | 既存seer claimを考慮。変更理由なしの揺れを記録し、意図した戦略変更は許容 |
| G11-1/2 | 自己紹介反復 | 既存guard自己紹介を繰り返さず、現在の話題に反応 |
| G12-1/2 | 他playerコピー | 50文字以上かつ8語以上の全文正規化一致0。短い同意は許容 |
| G13-1/2 | 投票候補 | 実target/rankingをplayer-2/4/7に限定。誰を選ぶかは採点しない |
| G14-1/2 | NONE対照 | 新情報のない待機/手続的発言にNONEが可能。架空の返信sourceを作らない |
| G15-1/2 | 正当な秘密開示 | seer COを許容し、開示だけで罰しない。公開する義務は置かない |
| G16-1/2 | 情報不足 | 能力結果のない質問に架空result/evidenceを作らない |

## 評価の3層

1. **Hard**: 既存schema・semantic validatorでoption/target/EvidenceRef/組合せを検証。
   さらに最終自由文を入力と照合し、生死・能力結果・秘密境界の矛盾を人が判定する。
   構造がvalidでも自由文未判定なら`hard_pass=null`。regex不検出だけでPASSにはしない。
2. **Semantic**: actラベル、本文、返信先、実source、prior/current/causeを照合。
   `expected_acts`は観測対象であり、戦略自由を潰す絶対制約ではない。NONE対照も含め、
   明確な質問本文なのにNONE等は失敗、自然に別戦略を選んだ場合は未観測/理由付き判定とする。
3. **Strategic freedom**: 疑う相手、CO時機、偽役職、庇う相手、投票先の好みを採点しない。
   seer/mediumの非開示だけではFAILにしない。COと結果の捏造を混同しない。

本文の引用、否定、仮定、本人の実roleを区別して判定する。短い反復や単語の出現だけを禁止しない。
断定できない自由文はUNKNOWNとし、hard100%合格に数えない。採点は内部chain-of-thoughtを使わない。

## 実行

```powershell
python -m pytest tests/test_phase6_conversation_suite.py tests/test_phase6_quality_grounding.py tests/test_phase6_memory_projection.py -q
python scripts/phase6_conversation_suite.py --prepare --output logs/<new-suite>
python scripts/phase6_conversation_suite.py --run baseline --output logs/<new-suite>
python scripts/phase6_conversation_suite.py --run candidate --output logs/<new-suite>
```

実providerには独立Testerを用いる。今回T407は一回だけで、別directoryによる再試行は許可しない。
`--prepare`はgeneration0。runtime identity・全入力hash・実token・byte/output reserveを固定する。
`--run`は専用claimを排他的作成し、caseごとruntime/入力を確認する。
baseline/candidateで同じ入力4caseは再生成せずbaselineを再利用（最大60generation）。
transport/token不一致で停止し、modelの不正出力は今回の評価対象として記録する。
GPUは既存REAL時計monitorで観測し、今回のmonitor子だけ終了。user-owned LLMは維持する。

## Candidateの限定

T405と同じrequest-local JSON例の方法を検証adapterだけで試す。baselineのmemory/state/schemaを固定し、
system messageへ例を追加する。旧T405の全projection変更や説明短縮を製品へ復元しない。
実値のoption/player/source/revisionのみ使い、例自体を既存validatorで検証してから送る。
例の形式上の合法性と、モデルが例に引きずられず本文の意味を理解したかは別に採点する。
candidate比較のactual余裕は送信前に確認する。製品context契約変更・採用は別の独立承認が必要。

## 証拠と次gate

private入力/最終出力/GPU原本は既存Owner専用rootへ保存。repoへはcase ID・hash・数値・判定理由の
安全な要約だけを残す。`generation_status`とoutput validationは別field。未実施を成功にしない。
結果にはactual input/completion tokens・REAL latencyを保存し、性能と機能を分ける。
Mainの意味判定はcase IDごとの`*-manual.json`に保存し、`scripts/phase6_suite_report.py`で
入力/出力hash・測定hashと結び付けたsafe結果にする。機械測定原本は上書きしない。
報告時の`semantic_pass=null`は能力未観測/曖昧であり、成功に含めない。
hard100%、重大secrecy/state矛盾0、長文exact copy0を最低条件とする。speech_actの数値閾値は
初回baseline後に判断し、未測定の95%等は置かない。suite PASSでもPhase6 DONEにはならない。
