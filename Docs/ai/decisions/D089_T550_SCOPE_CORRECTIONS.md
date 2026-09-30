# D089: T550の限定scope訂正

Status: ACCEPTED
Date: 2026-09-30

ユーザーが他エージェントへ許可し、commit 73597e3のT550追加判断4・5をMainがremote一致で確認した。その後ユーザーが実行を明示した。D088原本は変更しない。

- B1: ai_client/discussion/generation_v2.pyのprior受理域をexact int 0..100へ限定訂正する。保存80/20を丸めない。currentは0/25/50/75/100の順序を維持し、priorと同値だけ除く。範囲外、bool、float等は拒否する。該当テスト以外の製品変更は許可しない。
- B2: 28chatをT→P、G13の2件を既存PRE_VOTE、G15の2件を既存CO_OPPORTUNITYで扱う。元入力と32case×3seedの96分母を維持する。
- 唯一のWP1設計へ反映し独立承認後WP2。両Python focusedと独立tool review後のみPF1/2/3、2行probe、条件合格時96行一回、独立blind評価へ進む。
- T515/T549停止、製品v1既定、privacy/authority、main/Actions/game/Phase7禁止を維持。追加設計一本または同一成果物review3回目が必要ならpacketどおり停止する。
