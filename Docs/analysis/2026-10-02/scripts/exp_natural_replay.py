"""E3: same T401 decision points, same information, plain natural-language prompt, free text."""
import json
import sys

from llm import chat, free_body
from natural import system_prompt, user_prompt

game = sys.argv[1]
out_path = sys.argv[2]
temperature = float(sys.argv[3]) if len(sys.argv) > 3 else 0.8
seeds = [11, 22, 33]

d = json.load(open(game, encoding="utf-8"))
roles = d["roles"]
accepted = d["accepted"]

private = {}
for c in d["calls"]:
    if not c["prompt"]:
        continue
    u = json.loads([m for m in c["prompt"]["messages"] if m["role"] == "user"][0]["content"])
    for r in u["grounding"]["ability_results"]["records"]:
        if r["event_type"] == "INSPECT_RESULT":
            fact = f"Night {r['day']} inspection: {r['target_player_id']} is {'NOT a werewolf' if r['result_id'] == 'not_wolf' else 'a WEREWOLF'}."
            private.setdefault(c["player"], set()).add(fact)

results = []
seen = set()
for c in d["calls"]:
    if c["trigger"] != "PEER_CHAT" or not c["prompt"]:
        continue
    key = (c["player"], c["day"])
    if key in seen:
        continue
    seen.add(key)
    role = roles[c["player"]]["role_id"]
    transcript = [(a["player"], a["text"]) for a in accepted if a["t"] < c["t"]]
    sysmsg = system_prompt(c["player"], role, sorted(private.get(c["player"], ())))
    usr = user_prompt(c["player"], c["day"], transcript)
    row = {"t": c["t"], "player": c["player"], "role": role, "visible": len(transcript), "logged_text": c["text"], "E3": []}
    for s in seeds:
        text, dt, usage, finish = chat(free_body(sysmsg, usr, temperature=temperature, seed=s, max_tokens=160))
        row["E3"].append({"seed": s, "message": text.strip(), "sec": round(dt, 2), "prompt_tokens": usage.get("prompt_tokens"),
                          "completion_tokens": usage.get("completion_tokens"), "finish": finish})
    results.append(row)
    print(row["t"][11:19], row["player"], role, flush=True)

json.dump({"system_example": sysmsg, "user_example": usr, "rows": results}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
