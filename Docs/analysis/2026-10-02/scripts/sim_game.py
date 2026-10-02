"""E4: minimal standalone 9-player werewolf simulation with plain prompts (scratch experiment only).

This is NOT the product.  It deliberately ignores networking/authority to answer one question:
can the same local 9B model hold a recognisable werewolf discussion when given a plain prompt and
the full public transcript?
"""
import json
import random
import re
import sys
import time

from llm import chat, free_body
from natural import ROLE_TEXT, WOLF_SIDE

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
out_path = sys.argv[2] if len(sys.argv) > 2 else f"sim_{seed}.json"
wolves_know = (sys.argv[3] if len(sys.argv) > 3 else "yes") == "yes"
rng = random.Random(seed)

ROLES = {"player-0": "werewolf", "player-2": "werewolf", "player-5": "madman", "player-4": "seer",
         "player-1": "medium", "player-3": "guard", "player-6": "villager", "player-7": "villager",
         "player-8": "villager"}
PLAYERS = sorted(ROLES)
alive = set(PLAYERS)
private = {p: [] for p in PLAYERS}
if wolves_know:
    wolves = [p for p in PLAYERS if ROLES[p] == "werewolf"]
    for w in wolves:
        private[w].append("Your fellow werewolf is " + ", ".join(x for x in wolves if x != w) + ".")
private["player-4"].append("Night 0 inspection: player-7 is NOT a werewolf.")
public_log = []        # (day, kind, player, text)
calls = []
MAX_PER_DAY = 4
MESSAGES_PER_DAY = 24


def goal(role):
    return ("Help the werewolf side win. Never admit you are on the werewolf side; lying and fake role claims are allowed."
            if role in WOLF_SIDE else "Find the two werewolves and get them executed by the vote.")


def system(p):
    facts = "\n".join(f"- {f}" for f in private[p]) or "- (none)"
    return (
        f"You are {p}, a player in a 9-player game of Werewolf (a Mafia-style social deduction game). "
        "Players: player-0 to player-8. Roles in this game: 2 werewolves, 1 madman, 1 seer, 1 medium, 1 guard, 3 villagers.\n\n"
        f"Your secret role: {ROLE_TEXT[ROLES[p]]}\nPrivate facts only you know:\n{facts}\nYour goal: {goal(ROLES[p])}\n\n"
        "How to talk:\n- Write ONE chat message of 1-3 short sentences in natural conversational English.\n"
        "- React to the discussion: answer questions aimed at you, push back on claims you doubt, or ask a specific player a pointed question.\n"
        "- Do not introduce yourself and do not restate your role in every message. Do not repeat sentences that were already said.\n"
        "- Output only the message text."
    )


def transcript_text(max_chats=40):
    lines = []
    chat_idx = [i for i, e in enumerate(public_log) if e[1] == "chat"]
    keep = set(chat_idx[-max_chats:])
    for i, (day, kind, p, text) in enumerate(public_log):
        if kind == "chat" and i not in keep:
            continue
        if kind == "chat":
            lines.append(f"[Day {day}] {p}: {text}")
        else:
            lines.append(f"[Day {day}] ** {text} **")
    return "\n".join(lines) or "(nothing yet)"


def call(p, user, schema=None, temperature=0.8, max_tokens=160):
    t0 = time.time()
    text, dt, usage, finish = chat(free_body(system(p), user, temperature=temperature, seed=rng.randint(1, 10**9),
                                             max_tokens=max_tokens, schema=schema))
    calls.append({"player": p, "sec": round(dt, 2), "prompt_tokens": usage.get("prompt_tokens"),
                  "completion_tokens": usage.get("completion_tokens"), "finish": finish})
    return text.strip()


def choose(p, question, candidates):
    schema = {"type": "object", "additionalProperties": False, "required": ["target"],
              "properties": {"target": {"enum": sorted(candidates)}}}
    user = f"Game log so far:\n{transcript_text()}\n\n{question} Alive players: {', '.join(sorted(alive))}."
    try:
        return json.loads(call(p, user, schema=schema, temperature=0.7, max_tokens=40))["target"]
    except Exception:
        return rng.choice(sorted(candidates))


def winner():
    w = sum(1 for p in alive if ROLES[p] == "werewolf")
    others = len(alive) - w
    if w == 0:
        return "village"
    if w >= others:
        return "werewolf"
    return None


day = 1
result = None
while result is None and day <= 5:
    spoken = {p: 0 for p in alive}
    next_speaker = None
    for i in range(MESSAGES_PER_DAY):
        candidates = [p for p in sorted(alive) if spoken[p] < MAX_PER_DAY]
        if not candidates:
            break
        if next_speaker in candidates:
            p = next_speaker
        else:
            least = min(spoken[c] for c in candidates)
            p = rng.choice([c for c in candidates if spoken[c] == least])
        user = f"Game log so far (oldest first):\n{transcript_text()}\n\nYour next chat message as {p}:"
        text = call(p, user)
        public_log.append((day, "chat", p, text))
        spoken[p] += 1
        mentioned = [m for m in re.findall(r"player-\d", text.lower()) if m in alive and m != p]
        next_speaker = mentioned[0] if mentioned and rng.random() < 0.7 else None
    votes = {p: choose(p, "Day vote: who do you vote to execute?", alive - {p}) for p in sorted(alive)}
    tally = {}
    for v in votes.values():
        tally[v] = tally.get(v, 0) + 1
    top = max(tally.values())
    executed = rng.choice(sorted(k for k, v in tally.items() if v == top))
    alive.discard(executed)
    public_log.append((day, "event", None, f"Votes: {json.dumps(votes)}. {executed} was executed."))
    if "player-1" in alive:
        private["player-1"].append(f"Day {day} medium result: executed {executed} was {'a WEREWOLF' if ROLES[executed] == 'werewolf' else 'NOT a werewolf'}.")
    result = winner()
    if result:
        break
    # night
    wolves_alive = [p for p in sorted(alive) if ROLES[p] == "werewolf"]
    victim = choose(wolves_alive[0], "Night: as a werewolf, choose a player to kill tonight.",
                    {p for p in alive if ROLES[p] != "werewolf"})
    guarded = choose("player-3", "Night: choose a player to protect tonight.", alive - {"player-3"}) if "player-3" in alive else None
    if "player-4" in alive:
        inspected = choose("player-4", "Night: choose a player to inspect tonight.", alive - {"player-4"})
        private["player-4"].append(f"Night {day} inspection: {inspected} is {'a WEREWOLF' if ROLES[inspected] == 'werewolf' else 'NOT a werewolf'}.")
    day += 1
    if victim != guarded:
        alive.discard(victim)
        public_log.append((day, "event", None, f"Morning: {victim} was found dead."))
    else:
        public_log.append((day, "event", None, "Morning: nobody died last night."))
    result = winner()

json.dump({"seed": seed, "wolves_know": wolves_know, "roles": ROLES, "result": result, "days": day,
           "log": public_log, "calls": calls}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("RESULT", result, "days", day, "calls", len(calls),
      "mean_sec", round(sum(c["sec"] for c in calls) / len(calls), 2),
      "max_prompt_tokens", max(c["prompt_tokens"] or 0 for c in calls))
