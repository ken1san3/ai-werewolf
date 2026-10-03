"""Public wording checks using the player's received living/dead state."""
import re
from .claims import denied_statement, unquoted
from .strategy import STRATEGIES


def addressed_dead_players(text, dead, role_names):
    plain = unquoted(text, role_names)
    targets = set()
    for address in re.finditer(r'(?:^|[。！？.!?]\s*|@)\s*(player-\d+)\s*(?:さん|君)?\s*[、,:]', plain):
        player = address.group(1)
        scope = re.split(r'[。.!！]|player-\d+\s*(?:さん|君)?\s*[、,:]', plain[address.end():], maxsplit=1)[0]
        for opinion in re.finditer(STRATEGIES['quality_patterns']['opinion_question'], scope):
            if opinion.group(1) not in dead:
                scope = scope[:opinion.start()]
                break
        if player in dead and re.search(STRATEGIES['quality_patterns']['address_request'], scope):
            targets.add(player)
    return targets


def quality_reason(state, text, roles):
    plain = unquoted(text, [r.name for r in roles.values()])
    data = STRATEGIES['quality_patterns']
    if any(re.search(pattern, plain) for pattern in data['meta']):
        return 'meta_refusal'
    facts = [*data['self_role_unknown'], *(data['self_dead'] if state.player_id in state.alive else [])]
    contrast = r'(?<=ません)(?:が|けど|けれど)\s*[、,]?\s*|(?<=ない)(?:が|けど|けれど)\s*[、,]?\s*'
    for clause in re.split(r'[。！？.!?]|' + contrast, plain):
        if re.search(r'もし|仮に|なら|だったら|とすれば', clause):
            continue
        for pattern in facts:
            for match in re.finditer(pattern, clause):
                tail = clause[match.end():]
                if denied_statement(tail):
                    continue
                reporter = re.search(r'(player-\d+)\s*(?:さん|君)?\s*(?:は|が)\s*[、,]?\s*$', clause[:match.start()])
                reported = re.match(r'\s*(?:と|という)\s*(?:発言|主張|説明|報告|言|述)', tail)
                if reporter and reporter.group(1) != state.player_id and reported:
                    continue
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
