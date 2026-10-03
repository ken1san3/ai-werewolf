"""Role strategy data and observations of public vote pressure."""
from pathlib import Path
import re

import yaml

from .repetition import normalize


STRATEGIES = yaml.safe_load((Path(__file__).resolve().parents[1] / "content/ai_strategies.yaml").read_text(encoding="utf-8"))


def strategy_for(role):
    return STRATEGIES["roles"].get(role.id, STRATEGIES["default"])


def vote_pressure(player_id, text):
    target = re.escape(player_id) + r"(?![A-Za-z0-9_-])\s*(?:さん|君)?\s*"
    for clause in re.split(r"[。！？.!?]", normalize(text)):
        if re.search(r"処刑(?:しない|するべきではない|すべきではない|された)|吊(?:らない|った|られ)|投票(?:しない|した|しました)", clause):
            continue
        if re.search(rf"{target}(?:を\s*(?:今日|優先|即座に|即刻|先に|まず|早く)*\s*(?:処刑|吊|排除)|に\s*(?:投票|票を|入れ))|(?:投票先|処刑対象|吊り先)(?:は|を|に)?\s*{target}", clause):
            return True
    return False


def under_pressure(state):
    return any(c.get("channel") == "public" and c.get("day") == state.day and
               c.get("player_id") != state.player_id and c.get("player_id") in state.alive and
               vote_pressure(state.player_id, c["message"]) for c in state.chats)
