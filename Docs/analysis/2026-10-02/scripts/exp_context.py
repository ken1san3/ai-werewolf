"""E1/E2: replay saved product prompts as-is vs. with the omitted public chat restored.

Read-only use of saved evidence; generations go to the local scratch llama-server only.
"""
import copy
import json
import sys

from llm import chat, product_body

game = sys.argv[1]          # t401.json / t404.json
out_path = sys.argv[2]
seeds = [11, 22, 33]

d = json.load(open(game, encoding="utf-8"))
roles = d["roles"]
accepted = d["accepted"]

# text -> order map from every saved prompt memory
order_of = {}
for c in d["calls"]:
    if not c["prompt"]:
        continue
    u = json.loads([m for m in c["prompt"]["messages"] if m["role"] == "user"][0]["content"])
    for r in u.get("memory", {}).get("records", []):
        if r["source"]["record_kind"] == "chat" and r.get("text_excerpt"):
            order_of[r["text_excerpt"]] = r["source"]["order"]

# assign orders to accepted chats; unknown ones get interpolated values
prev = 4
for a in accepted:
    o = order_of.get(a["text"])
    if o is None:
        o = prev + 1
        while o in order_of.values():
            o += 1
    a["order"] = o
    prev = o


def chat_record(a):
    return {
        "actor_player_ids": [a["player"]], "channel_id": "public", "day": a["day"], "importance": 50,
        "phase": a["phase"], "remembered_after_world_eviction": False,
        "schema_version": "aiwolf.important-event.v1",
        "source": {"order": a["order"], "record_kind": "chat", "visibility": "PUBLIC"},
        "target_player_ids": [], "text_excerpt": a["text"], "text_original_scalars": len(a["text"]),
        "text_original_utf8_bytes": len(a["text"].encode("utf-8")), "text_truncated": False,
    }


def augmented(prompt, t):
    p = copy.deepcopy(prompt)
    idx = [i for i, m in enumerate(p["messages"]) if m["role"] == "user"][0]
    u = json.loads(p["messages"][idx]["content"])
    have = {r["source"]["order"] for r in u["memory"]["records"]}
    added = 0
    for a in accepted:
        if a["t"] < t and a["order"] not in have:
            u["memory"]["records"].append(chat_record(a))
            u["grounding"]["allowed_evidence_refs"].append({"order": a["order"], "record_kind": "chat", "visibility": "PUBLIC"})
            added += 1
    u["memory"]["records"].sort(key=lambda r: r["source"]["order"])
    u["grounding"]["allowed_evidence_refs"].sort(key=lambda r: r["order"])
    u["memory"]["included_records"] = len(u["memory"]["records"])
    u["memory"]["omitted_records"] = max(0, u["memory"]["omitted_records"] - added)
    u["memory"]["token_proxy_exhausted"] = False
    p["messages"][idx]["content"] = json.dumps(u, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return p, added


results = []
seen_first = set()
for c in d["calls"]:
    if c["trigger"] != "PEER_CHAT" or not c["prompt"]:
        continue
    key = (c["player"], c["trigger"], c["day"])
    if key in seen_first:      # first attempt per player/trigger only (skip repair prompts)
        continue
    seen_first.add(key)
    visible = sum(1 for a in accepted if a["t"] < c["t"])
    aug, added = augmented(c["prompt"], c["t"])
    row = {"t": c["t"], "player": c["player"], "role": roles.get(c["player"], {}).get("role_id"),
           "accepted_before": visible, "added_records": added, "logged_text": c["text"], "E1": [], "E2": []}
    for s in seeds:
        for name, prompt in (("E1", c["prompt"]), ("E2", aug)):
            try:
                text, dt, usage, finish = chat(product_body(prompt, seed=s))
                try:
                    msg = json.loads(text)["decision"].get("message")
                    act = json.loads(text)["discussion"]["speech_act"]["kind"]
                except Exception:
                    msg, act = "<PARSE_FAIL>", None
                row[name].append({"seed": s, "message": msg, "speech_act": act, "sec": round(dt, 2),
                                  "prompt_tokens": usage.get("prompt_tokens"), "finish": finish})
            except Exception as e:
                row[name].append({"seed": s, "error": repr(e)})
    results.append(row)
    print(json.dumps(row, ensure_ascii=False, indent=1), flush=True)

json.dump(results, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
