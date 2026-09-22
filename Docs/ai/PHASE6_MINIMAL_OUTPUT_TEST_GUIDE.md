# Minimal output：ローカル検査の準備

## 今回の位置づけ

23時以降の作成対象は公開syntheticによるoffline検査であり、LLM品質の測定ではない。
テストは実行しない。今夜の設計・作成・static reviewと、後日の実行結果を区別する。
通常game、Master Run、provider、モデル操作、Actionsは対象外。

## 一コマンド入口

repository root `C:\AIwolf` で、依存関係を導入済みのPythonから実行する。

```powershell
python -m pytest tests/test_phase6_minimal_output_applicability.py -q
```

この文書にコマンドがあることは、実行済みやPASSの証拠ではない。
今回の実装範囲・独立review・未実行項目はT493 handoffに記録する。

作成物はpure helper、手作りの公開13ケース、パラメータ化した負例である。
ケース構築とhelper呼出しはtest関数から行う。テストには通信・プロセス起動・provider・
state store構築/commitを拒否するガードを付けた。ガード自体を含め実行結果は未確認。

## 結果を読むときの境界

- 機械検査：JSON/shape、action/option/target、参照の存在・visibility・actor、trigger整合、
  text bound、永続private updateを生成しないこと。
- 未測定：LLMの質問への回答、act/text一致、秘密開示の妥当性、根拠が主張を支えるか、自然さ。
- 合法なNONEや騙りを一律に禁止しない。機械的に本文から意味labelを後付けしない。
- 既存32ケースは保持し、今夜は再生成・再採点しない。metadata不足をUNRESOLVEDで残す。
  公開toyケースのPASSを、既存32件の意味評価PASSへ置き換えない。
- 32件のmetadataは公開sourceから静的に記したID/trigger一覧だけで、実際のprojection取得adapter
  は未実装。全32件UNRESOLVEDであり、元suiteとの実行時同値は未確認。
- `assessment_updates / claim_updates / relation_updates / strategy_update`を省く実験であり、
  戦略的な主観判断をLLMから取り上げる設計ではない。

## 後続gate

ローカル実行結果を確認した後でも、providerを自動起動しない。人工suite接続には、
未実装の32件adapter、所有process、source/hash/一回許可、独立測定・意味評価のgateを別に満たす。
製品baseline、モデル設定、ゲーム規則、private境界は現状維持。

`grounding[]`は既存speech refのpurpose付き重複表現であり、baselineに無い生成負荷も加える。
今夜はそのclosed contractのケースを準備しただけで、永続update削除の単一要因実験ではない。
providerを検討する前にこの交絡とschema/token負荷を別に確認する必要がある。
helperのJSON Schemaはtriggerとoffered option/target/role/count/abstentionでdecisionを閉じる。
参照原像やactor等のdynamic authority条件はvalidatorが追加検査する。
そのままmodel-readyなprovider schema/promptとして利用しない。
