"""Conservative disclosure guard using only this player's received information."""
import re

from .metrics import self_claims, unquoted
from .repetition import normalize


def disclosure_reason(state, text, roles, *, formal_claim=None):
    role = roles[state.role_id]
    names = [r.name for r in roles.values()]
    plain = unquoted(text, names)
    true_role = role.name in self_claims(state.player_id, text, [role.name]) or formal_claim == role.id
    team_claim = role.attributes.team == "wolf" and re.search(
        r"(?:私は|僕は|俺は|^|[。！？]\s*)(?:人狼|狼)陣営(?:です|に属|の一員)", plain)
    if not true_role and not team_claim:
        return None  # False COs remain free.
    if role.attributes.team == "wolf":
        supporters = {state.player_id, *state.teammates} & state.alive
        wolf_names = [r.name for r in roles.values() if r.attributes.team == "wolf"]
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
    if any(effect.id == "protect" for ability in role.abilities for effect in ability.effects):
        target = re.escape(state.player_id) + r"\s*(?:さん|君)?\s*"
        pressure = rf"{target}(?:を\s*(?:処刑|吊)|に\s*(?:投票|入れ))|(?:投票先|処刑対象|吊り先)(?:は|を|に)?\s*{target}"
        for chat in state.chats[-15:]:
            if chat["channel"] != "public" or chat["player_id"] == state.player_id or chat["player_id"] not in state.alive or chat["day"] != state.day:
                continue
            if re.search(pressure, normalize(chat["message"])) or role.name in self_claims(chat["player_id"], chat["message"], [role.name]):
                return None
        if any(f["type"] == "CO_DECLARED" and f["player_id"] != state.player_id and f["player_id"] in state.alive and f.get("claimed_role_id") == role.id
               for f in state.facts):
            return None
        return "unjustified_self_disclosure"
    return None
