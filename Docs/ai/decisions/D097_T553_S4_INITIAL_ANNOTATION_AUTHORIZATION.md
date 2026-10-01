# D097: 新S4初回annotationの限定許可

Status: RECORDED
Date: 2026-10-01

ユーザーはT552終了時に提示した「保存済みbaseline/candidateへ、新S4だけ別annotationとして独立評価する」案を「承認する」と明示した。T550の旧再採点禁止に対する例外は、この新version初回評価だけである。

対象はT506製品baseline保存96行とT550判断6保存96行（固定32case × seed 4242027〜4242029）。metricは承認済み `S4_COMMON_PROVENANCE_V1` だけ。旧annotation/採否/D092 MEASUREMENT_INVALIDと他metricは変更しない。生成/provider/process操作は0。

T553で元artifact SHA、変換source SHA、rubric SHA、192行/96pair、crypto blind ID、sealed mapping、必要最小値、独立Reviewerを事前freezeする。表示・binding欠測はUNKNOWN、構造不受理はMEASUREMENT_NOT_OBSERVEDのまま分母に残す。自然文からref/subjective stateを作らない。

新annotationは旧判定の訂正でも製品採用でもない。独立評価freeze前にunblindせず、同条件再生成や同annotation再採点は行わない。新provider、game/Master Run/Phase7、model/製品変更、main/Actionsは許可外。
