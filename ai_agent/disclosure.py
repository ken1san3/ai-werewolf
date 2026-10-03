"""Conservative disclosure guard using only this player's received information."""
import re

from .claims import self_claims, team_claim, unquoted
from .strategy import STRATEGIES, strategy_for, under_pressure


def disclosure_reason(state, text, roles, *, formal_claim=None, role_counts=None):
    role = roles[state.role_id]
    names = [r.name for r in roles.values()]
    plain = unquoted(text, names)
    if role.attributes.team == 'wolf' and team_claim(text, names, STRATEGIES['public_disruption_patterns']):
        return 'unjustified_self_disclosure'
    true_role = role.name in self_claims(state.player_id, text, [role.name]) or formal_claim == role.id
    claimed = roles.get(formal_claim)
    wolf_names = [r.name for r in roles.values() if r.attributes.team == "wolf"]
    claimed_team = role.attributes.team == "wolf" and (
        self_claims(state.player_id, text, wolf_names) or (claimed and claimed.attributes.team == "wolf") or team_claim(text, names))
    known_buddy = False
    for clause in re.split(r'[。！？.!?]', plain):
        if re.search(r'もし|仮に|なら|だったら|とすれば', clause):
            continue
        for buddy in state.teammates:
            for pattern in STRATEGIES['teammate_claim_patterns']:
                match = re.search(pattern.format(teammate=re.escape(buddy)), clause)
                if match and not re.match(r'\s*(?:では(?:ない|ありません)|ではなく)', clause[match.end():]):
                    known_buddy = True
    if not true_role and not claimed_team and not known_buddy:
        return None  # Claims that disclose neither the true role nor army remain free.
    policy = strategy_for(role)["disclosure"]
    if policy == "hidden":
        return "unjustified_self_disclosure"
    if policy == "endgame_vote":
        supporters = {state.player_id, *state.teammates} & state.alive
        known_helpers = set()
        for chat in state.chats:
            player = chat["player_id"]
            claims = self_claims(player, chat['message'], wolf_names)
            if chat["channel"] == "public" and player in state.alive and claims:
                supporters.add(player)
                if any(r.name in claims and r.attributes.team == 'wolf' and r.attributes.count_as == 'village' for r in roles.values()):
                    known_helpers.add(player)
        for fact in state.facts:
            claimed = roles.get(fact.get("claimed_role_id"))
            if fact["type"] == "CO_DECLARED" and claimed and claimed.attributes.team == "wolf" and fact["player_id"] in state.alive:
                supporters.add(fact["player_id"])
                if claimed.attributes.count_as == 'village':
                    known_helpers.add(fact['player_id'])
        # In the current nine-player preset a living human wolf-side member
        # can initiate a final-three PP: an ongoing game still has a wolf.
        late_helper = role.attributes.count_as == "village" and len(state.alive) <= 3
        possible_helpers = sum(count for role_id, count in (role_counts or {}).items()
                               if roles[role_id].attributes.team == 'wolf' and roles[role_id].attributes.count_as == 'village')
        possible_helpers = max(0, possible_helpers - len(known_helpers))
        possible_pp = role.attributes.count_as == 'wolf' and len(supporters) + possible_helpers > len(state.alive) / 2
        if late_helper or possible_pp or len(supporters) > len(state.alive) / 2:
            return None
        return "unjustified_self_disclosure"
    if policy == "pressure":
        if under_pressure(state):
            return None
        for chat in state.chats:
            if chat["channel"] != "public" or chat["player_id"] == state.player_id or chat["player_id"] not in state.alive or chat["day"] != state.day:
                continue
            if role.name in self_claims(chat["player_id"], chat["message"], [role.name]):
                return None
        if any(f["type"] == "CO_DECLARED" and f["player_id"] != state.player_id and f["player_id"] in state.alive and f.get("claimed_role_id") == role.id
               for f in state.facts):
            return None
        return "unjustified_self_disclosure"
    return None
