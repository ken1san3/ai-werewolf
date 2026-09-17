# T408 構造提示のみでは会話品質を満たさない

## 症状と固定証拠

本文例を除き既存speech_act schemaの構造のみをsystemへ提示した人工suite。
新規28件/再利用4件、retry0/repair0。自白5、peer長文コピー2、死者投票1が残った。
詳細・再開条件は `Docs/ai/handoffs/tasks/T408_STRUCTURE_ONLY_PROBE.md`。
同一条件での実LLM再試行を修復手段にしない。

## 確定した局所契約差

G04-2/G10-1/G10-2は保存最終JSONに対するJSON Schema検査を通る一方、
既存object validatorが `OUTPUT_INVALID:SCHEMA` を返した。
`ClaimUpdate` はclaim識別子と根拠をpublic限定とするが、schema上の汎用EvidenceRefでprivateを表現できる。
G04-2は根拠へprivate能力結果、G10の2件はpublic CO参照のvisibilityをprivateにした。
保存出力と現コードから原因を局所化済み。新規Investigatorや実LLMでの再現は不要。

## 修正候補と制限

claim更新専用のpublic参照制約をschema/promptへ合わせる限定修正が候補。
validatorを緩めない。これは構造不適合3件への対策であり、自白・誤認・コピーの解消は別途検証が必要。
今回は候補の単発評価までで、製品修正・独立承認・実gameは未実施。
