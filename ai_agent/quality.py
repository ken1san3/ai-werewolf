"""Public wording checks using the player's received living/dead state."""
import re
from .claims import unquoted
from .strategy import STRATEGIES


def addressed_dead_players(text, dead, role_names):
    plain = unquoted(text, role_names)
    targets = set()
    for address in re.finditer(r'(?:^|[。！？.!?]\s*|@)\s*(player-\d+)\s*(?:さん|君)?\s*[、,:]', plain):
        player = address.group(1)
        scope = re.split(r'[。.!！]|player-\d+\s*(?:さん|君)?\s*[、,:]', plain[address.end():], maxsplit=1)[0]
        if player in dead and re.search(STRATEGIES['quality_patterns']['address_request'], scope):
            targets.add(player)
    return targets


def quality_reason(state, text, roles):
    plain = unquoted(text, [r.name for r in roles.values()])
    data = STRATEGIES['quality_patterns']
    if any(re.search(pattern, plain) for pattern in data['meta']):
        return 'meta_refusal'
    if any(re.search(pattern, plain) for pattern in data['self_role_unknown']):
        return 'self_fact_confusion'
    if state.player_id in state.alive:
        for clause in re.split(r'[。！？.!?]', plain):
            if not re.search(r'もし|仮に|なら|だったら|とすれば|(?:では|して)(?:ない|いない|ありません)', clause):
                if any(re.search(pattern, clause) for pattern in data['self_dead']):
                    return 'self_fact_confusion'
    if re.fullmatch(data['empty_agreement'], plain.strip()):
        return 'empty_agreement'
    dead = set(state.players) - state.alive
    if addressed_dead_players(text, dead, [r.name for r in roles.values()]):
        return 'dead_player_address'
    for player in dead:
        for clause in re.split(r'[。！？.!?]', plain):
            if re.search(r'もし|仮に|なら|だったら|とすれば|すべきではない|しません|しない', clause):
                continue
            for pattern in data['dead_vote']:
                if re.search(pattern.format(player=re.escape(player)), clause):
                    return 'dead_player_address'
    return None
