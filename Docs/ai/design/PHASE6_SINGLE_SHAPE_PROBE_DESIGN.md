# Phase 6 single-shape speech act 最小実験設計

Task: T449
Status: DRAFT

## 1. 目的と境界

今回の候補S1は、baselineの`discussion.speech_act.oneOf`だけを、全fieldを持つ単一closed objectへ置き換えるtest-only 1call実験である。仮説は、7枝を同時に選ばせるgrammar負担を減らすと、本文とspeech actの対応が改善する可能性がある、という限定的なものとする。

I1 Intent-first、K1 kind-first、C1 proposal順、C2 NONE reasonの変換を呼ばず、field順やenvelopeを組み合わせない。I1と旧baseline/C1/C2/K1のraw、結果、意味判定は再生成・再採点しない。製品schema、parser、authority、game state、prompt本文、model、sampling、concurrency、scale、privacy境界は変更しない。通常game、Master Run、Phase7は対象外である。

S1はschema、strict JSON、single-shape validator、可逆adapterだけを持つ。send/state適用APIは持たず、offlineの副作用は0とする。成功しても製品採用ではなく、D077の製品gateと独立reviewを別に必要とする。

## 2. 出力interface

root、`decision`、`discussion`、updates、reaction、CO、pre-voteはbaselineと同じkey集合・制約・順序を保持する。変更するのは`$defs.speech_act`だけである。候補speech actは次の17 keyを全てrequiredとし、`additionalProperties=false`とする。

```text
kind,
addressee_player_id, causes, confidence, current, dimension, evidence,
in_reply_to, prior, relation, source, source_interpretation,
source_player_id, stance, subject_player_id, target_player_id, topic
```

生成順は`kind`を先頭、その後の16 keyを上記のalphabetical順とする。全fieldを省略不可にすることで、欠落と非該当を区別する。`kind`以外の各property schemaは、元oneOf枝にある同名property schemaの値・array順をcopyし、`null`を追加したものとする。既知差分である`source_interpretation`だけは、ANSWERの`const: QUESTION`とREBUTTALの`const: CLAIM`を確認したうえでcandidate schemaを閉じた`enum: [QUESTION, CLAIM]`へ合併する。strict validatorはkindごとに元constを再適用する。それ以外の同名fieldの非null schemaが枝間でJSON値同一でなければ`SCHEMA_SHAPE_CHANGED`で停止し、勝手にunionや緩和をしない。remote refは追加しない。元`$defs`は値・array順を含め保持する。

schema段階では`kind`を7値enumとし、他16 fieldは対応する旧制約またはnullを許す。kind別の適用・非該当規則は次節のstrict validatorで検査する。JSON Schemaの`if/then`や新しいoneOfへ規則を移さない。単一shapeという実験変数を保ち、受理境界はadapter前validatorと旧validatorの二段で閉じる。静的field合併ではNONE 16、CLAIM 12、QUESTION 12、ANSWER 10、REBUTTAL 10、OPINION_CHANGE 11、RELATION_HYPOTHESIS 11個の非該当nullが増える。このcompletion増加を測定対象とし、`max_tokens=512`は据え置く。

## 3. kind別field map

`A`は適用され非null必須、`N`は適用されnullも正当、`-`は非該当でnull必須を表す。配列の`A`は配列自体が非nullという意味で、空配列の可否は旧schemaをそのまま使う。

| field | NONE | CLAIM | QUESTION | ANSWER | REBUTTAL | OPINION_CHANGE | RELATION_HYPOTHESIS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| addressee_player_id | - | - | A | A | A | - | - |
| causes | - | - | - | - | - | A | - |
| confidence | - | - | - | - | - | - | A |
| current | - | - | - | - | - | A | - |
| dimension | - | - | - | - | - | A | - |
| evidence | - | A | - | A | A | - | A |
| in_reply_to | - | - | - | A | A | - | - |
| prior | - | - | - | - | - | A | - |
| relation | - | - | - | - | - | - | A |
| source | - | - | N | - | - | - | - |
| source_interpretation | - | - | - | A | A | - | - |
| source_player_id | - | - | - | - | - | - | A |
| stance | - | A | - | A | A | - | - |
| subject_player_id | - | A | N | - | - | A | - |
| target_player_id | - | - | - | - | - | - | A |
| topic | - | A | A | A | A | - | - |

追加の固定値は旧枝をそのまま継承する。candidate schema上の`source_interpretation`は`QUESTION`または`CLAIM`だが、strict mapではANSWERに`QUESTION`だけ、REBUTTALに`CLAIM`だけを許す。逆組合せや未知値をadapter前に拒否する。NONEは16 fieldすべてnullである。QUESTIONの`subject_player_id`と`source`は適用fieldなので、nullでも圧縮後の旧objectにkeyを残す。それ以外の非該当nullとはmapで区別する。

validatorは全17 keyのexact集合、`kind`の既知7値、表の`A`非null、`N`旧nullable制約、`-`のexact nullを検査する。unknown kind、field欠落、追加field、非該当の非null、適用fieldの誤型、配列nullを拒否する。型強制、default、trim、空値補完、field推測、sort、deduplicateは行わない。

## 4. schema構築とfail-closed条件

入力はbaseline bodyと元projectionだけで、新しいstateや秘密を取得しない。

1. baseline bodyを既存canonical serializerでplain JSON化する。
2. 元schema top-level、`decision.oneOf`、`discussion.oneOf`、`$defs.speech_act.oneOf`の既知shapeとlocal refだけを検査する。未知keyword、未知枝、枝の追加・欠落、duplicate kind、required/key集合不一致、open object、remote refは`SCHEMA_SHAPE_CHANGED`で停止する。
3. speech act 7枝を元順で読み、kindが`NONE, CLAIM, QUESTION, ANSWER, REBUTTAL, OPINION_CHANGE, RELATION_HYPOTHESIS`の一意な集合であることを確認する。
4. 16 fieldごとに旧枝のproperty schemaを収集する。QUESTIONの`subject_player_id`と`source`だけは既存`anyOf`から一意なnull枝を除いて非null制約を抽出する。それ以外の旧propertyにnull枝があれば停止する。同名fieldが複数枝にある場合は、この正規化後の非null schemaがJSON値同一であることを原則とする。唯一の例外として`source_interpretation`は、出現枝がANSWERとREBUTTALだけで、各schemaがそれぞれexact `{"const":"QUESTION"}`と`{"const":"CLAIM"}`であることを要求し、candidateの非null schemaを`{"enum":["QUESTION","CLAIM"]}`とする。枝の追加、const値の変更、第三の値、別keywordがあれば停止する。各非null schemaをdeep-copyし、candidateでは`anyOf: [non_null_schema, {type:null}]`を一度だけ構築する。nullable QUESTIONも同じcandidate表現に正規化し、null枝を重ねない。
5. `$defs.speech_act`だけをsingle closed objectへ置換する。他のschema node、messages、canonical input、grounding、limitsはJSON値同一を要求する。
6. `Draft202012Validator.check_schema`で検査する。対象runtimeの公式reference converterで全32 schemaを非LLM変換し、未対応ならprovider前に停止する。

candidate schema builderは未知top-level/branch keywordを黙ってdropしない。candidate validatorも、生成したsingle-shapeのexact key集合、required、property schema、順序対象名を再確認する。任意の外部schemaを渡してfieldを通す経路を作らない。

## 5. 可逆adapterと旧受理境界

処理順は次のとおり固定する。

```text
rawをprivate保存
→ duplicate key / NaN / Infinity / float overflow / 構文不正をstrict JSONで拒否
→ candidate schema全体検査
→ kind別single-shape strict validator
→ copy上で旧speech_actへ圧縮
→ 元baseline projectionのparse_llm_output
```

圧縮は`kind`と表で適用されるfieldだけを、値を変えず旧objectへcopyする。非該当nullは捨てる。QUESTIONの適用nullable 2fieldはnullでも残す。adapterはspeech act以外の値・key・array順を変更しない。原rawも変更しない。

inverse mappingは旧speech act枝を元schemaで検査後、全16 fieldを追加し、非該当fieldだけnullで埋める。適用fieldの値はnullを含めそのままcopyする。合法な旧出力について`decode(encode(old)) == old`、candidate受理値について`encode(decode(candidate)) == candidate`を全7 kindで要求する。

candidate schema不合格またはkind map不合格ではadapterを実行せず、`legacy_contract_pass=null`とする。candidate合格後に旧validatorが拒否した場合は`candidate_schema_pass=true`、`single_shape_pass=true`、`legacy_contract_pass=false`を別保存する。adapter成功やEvidenceRef実在を意味品質PASSにしない。

旧`parse_llm_output`が引き続き次を決定する: offered option/target/role、actionとproposalのkind/option、current player、EvidenceRefのcapture/memory同一性とvisibility、ANSWER/REBUTTAL actor/addressee、claim speaker、OPINION_CHANGEの実priorと新cause、trigger/reaction、CO/pre-vote、更新重複、distinct evidence上限、text/proposal UTF-8 bytes。co_report/context-free parserをPhase6へ拡張しない。

したがって意図する受理集合は次の双方向同値である。

```text
candidate schema + kind map + adapter + legacy validator が受理
⇔ 対応する旧oneOf値をlegacy validatorが受理
```

## 6. grounding、秘密、atomic境界

candidate body化の前後でmessages、canonical input、capture、grounding、allowed EvidenceRef、alive/dead、自己に許可されたability result、limitsをJSON値同一とする。他player用投影へowner-only resultが現れないことをfocusedで確認する。single shapeは情報を追加せず、fieldの候補値は元`$defs`と元projectionからだけ得る。

実在refが本文を支持するか、自由文に捏造・秘密・state/ability矛盾があるか、本文とactが一致するかはschemaでは確定しない。regexで意味PASSにせず、独立意味評価へ残す。合法NONE、正当な非開示、真のCOをしない選択、戦略的な騙りを一律拒否しない。

候補全体が不正なら部分受理しない。offlineでstate更新・sendは0。将来の製品統合は今回のscope外であり、既存epoch/revision/phase/deadline/freshnessとatomic admissionを弱めない。

## 7. wire、入力、runtime、予算

baseline bodyを一度canonical化し、新しい`$defs.speech_act.properties`だけを`kind`→残16field alphabeticalへ戻し、`sort_keys=false`でwire化する。それ以外のproperties、required array、`$defs`順、messagesはcanonical baseline順を維持する。元body hash、messages hash、candidate schema/body hash、実wire hashを別々にfreezeする。apply-templateとgenerationへprivate保存済みの同一immutable request bytesを渡し、MockTransportで保存・両HTTP content一致を検査する。

最初の32生成からspeech_actの17 key順だけを固定名・位置で記録する。順序相違は`ORDER_NOT_REALIZED`として品質採用判定を保留し、追加callでやり直さない。順序は内部思考や因果の証明ではない。

既存32case、Qwen3.5-9B、seed4242026、context8192、max_tokens512、temperature0.2、top_k40、top_p0.95、min_p0.05、penalty/cache_prompt=false、thinking=false、ngl99、native template、slot1/同時1を維持する。request60/load180/model1200（load含む）/cleanup各25/outer1320 REAL秒、retry0、repair0、新規生成最大32とする。rendered actual tokens + 512 + 1 <= 8192を送信前後に検査し、proxyとactualを混同しない。single shapeでschema/outputが膨張してもmax_tokens/context/根拠を即興で変えない。収まらなければ候補を不適合として次cycleへ進む。

runnerは新experiment名`single_shape_v1`だけを追加し、S1専用helper/test/source allowlistをfreezeする。I1 helperをimport・呼出しせず、I1 source deltaをS1へ含めない。baseline対照は保存済みartifact/hashを使い、旧rawを開かず、messagesと元body復元側の一致を確認する。raw/stdout/stderr/request bytesはprivateに保持し、公開rowへ自由文・新field値・秘密を出さない。

## 8. focused acceptance

非LLM focusedは最低限次を満たす。

- 全32caseでschema構築、baseline messages/input/grounding/limits不変、speech act以外のschema leaf不変、公式converter 32/32、wire freeze、private保存とtransport bytes一致。
- 全7 kindについて、全17 key、A/N/- map、unknown/missing/extra、非該当非null、適用null、誤型、配列null、nullable QUESTIONを検査し、両方向roundtrip exact equal。`source_interpretation`はcandidateの閉enum 2値、ANSWER/QUESTIONとREBUTTAL/CLAIMの正組合せ、逆組合せ拒否、未知値拒否、元枝const・枝数・keyword差分のfail-closedを個別に検査する。
- 全5 actionと全trigger、合法NONE、PRE_VOTE棄権、CO、ability 0/1 targetを含め、旧validatorのaccept/rejectと一致。
- authority-closed positive matrix 13件を旧parserとcandidate adapter後で比較し、ParsedDiscussionOutputを完全一致させる。
- option/target/role、alive/dead、own ability result、EvidenceRef存在/visibility、actor/addressee/claim speaker、prior/new cause、reaction/CO/pre-vote、更新重複・上限、distinct evidence、text scalar/UTF-8/proposal bytesの代表negativeを両経路で拒否。invalidを成功扱いしない。
- strict JSONのduplicate key、NaN、Infinity、overflow、構文不正をadapter前に拒否。原raw不変、candidate不合格時adapter未実行、raw語彙を公開rowへ出さず、副作用0。
- source/input/wire/runtime mismatch、claim再利用、private保存失敗、transport timeout、所有process cleanupの既存mock regression。非所有process停止0。

独立Reviewerはfield map、nullable QUESTION、schema copy、可逆性、受理同値、秘密境界、wire差分allowlistを確認する。実装者のfocusedだけでtool承認にしない。

## 9. 人工suiteと採否

設計独立承認→実装→独立tool審査→実行freeze→独立Testerによる32call一回→独立意味評価の順を守る。32件すべてを分母にし、未生成・schema不合格・strict validator不合格・旧validator不合格を除外しない。retry、repair、追加seed、同条件再実行は0。

相対採用条件は保存baselineに対して、act/text mismatch `<23`、HARD fail `<=14`、FABRICATED_EVIDENCE `<=0`、SECRET `<=5`、STATE_CONTRADICTION `<=3`、ABILITY_CONTRADICTION `<=0`を個別に満たし、各UNKNOWNを増やさず、合法NONEを損なわないこととする。非NONE数や他指標の改善で未達条件を相殺しない。D077の製品候補gateは別にHARD 100%、重大秘密/状態矛盾0、長文exact copy0を要求する。

通常の候補不採用やfocused失敗は6時間/6cycle全体の停止条件ではない。失敗原因を保存し、同条件retryせず、残り上限内で設計承認済みの次の安全なcycleへ進む。重要な製品規則選択、権限衝突、破壊操作、外部認可欠落だけを停止・ユーザー判断対象とする。本設計の失敗時に同run中でprompt追加、2-call化、予算拡大へ切り替えない。

## 10. 既知限界

single shapeはgrammarの枝選択を減らす一方、常に16個のnullまたは値を出すためcompletion tokenと誤field出力を増やす可能性がある。strict validatorは非該当fieldを確実に拒否できるが、生成途中の計画順や自然文の真偽を保証しない。1 seed/32件の結果はこの候補・model・条件だけの有限比較であり、single-shape一般やmodel能力の結論にはしない。
