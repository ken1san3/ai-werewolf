"""Conservative disclosure guard using only this player's received information."""
import re

from .metrics import self_claims, unquoted
from .repetition import normalize
from .strategy import strategy_for, under_pressure


def disclosure_reason(state, text, roles, *, formal_claim=None):
    role = roles[state.role_id]
    names = [r.name for r in roles.values()]
    plain = unquoted(text, names)
    true_role = role.name in self_claims(state.player_id, text, [role.name]) or formal_claim == role.id
    claimed = roles.get(formal_claim)
    wolf_names = [r.name for r in roles.values() if r.attributes.team == "wolf"]
    team_claim = role.attributes.team == "wolf" and (
        self_claims(state.player_id, text, wolf_names) or (claimed and claimed.attributes.team == "wolf") or re.search(
        r"(?:私は|僕は|俺は|^|[。！？]\s*)(?:人狼|狼)陣営(?:です|に属|の一員)", plain))
    if not true_role and not team_claim:
        return None  # Claims that disclose neither the true role nor army remain free.
    policy = strategy_for(role)["disclosure"]
    if policy == "hidden":
        return "unjustified_self_disclosure"
    if policy == "endgame_vote":
        supporters = {state.player_id, *state.teammates} & state.alive
        for chat in state.chats:
            player = chat["player_id"]
            if chat["channel"] == "public" and player in state.alive and self_claims(player, chat["message"], wolf_names):
                supporters.add(player)
        for fact in state.facts:
            claimed = roles.get(fact.get("claimed_role_id"))
            if fact["type"] == "CO_DECLARED" and claimed and claimed.attributes.team == "wolf" and fact["player_id"] in state.alive:
                supporters.add(fact["player_id"])
        # In the current nine-player preset a living human wolf-side member
        # can initiate a final-three PP: an ongoing game still has a wolf.
        late_helper = role.attributes.count_as == "village" and len(state.alive) <= 3
        if late_helper or len(supporters) > len(state.alive) / 2:
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
