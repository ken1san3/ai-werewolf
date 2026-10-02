"""Extract roles, transcript and saved prompts from a Phase 6 game evidence directory (read-only)."""
import glob
import json
import os
import sys

root = sys.argv[1]
out = sys.argv[2]
rows = []
for f in glob.glob(os.path.join(root, "**", "ai.jsonl"), recursive=True):
    for line in open(f, encoding="utf-8"):
        if line.strip():
            rows.append(json.loads(line))
rows.sort(key=lambda o: o.get("recorded_at_utc", ""))

roles = {}
calls = []
for o in rows:
    pj = o.get("prompt_json")
    prompt = None
    if pj:
        try:
            prompt = json.loads(pj)
        except Exception:
            prompt = None
    user = None
    if prompt:
        for m in prompt["messages"]:
            if m["role"] == "user":
                try:
                    user = json.loads(m["content"])
                except Exception:
                    user = None
                break
    if user and "context" in user:
        roles[user["context"]["player_id"]] = {
            "role_id": user["context"].get("role_id"),
            "team": user["context"].get("team"),
            "knows_teammates": user["context"].get("knows_teammates"),
            "authorized_known_player_ids": user["context"].get("authorized_known_player_ids"),
        }
    d = o.get("decision") or {}
    calls.append({
        "t": o.get("recorded_at_utc"),
        "player": o.get("player_id"),
        "day": o.get("day"),
        "phase": o.get("phase"),
        "status": o.get("status"),
        "validation_code": o.get("validation_code"),
        "trigger": (user or {}).get("capture", {}).get("trigger", {}).get("kind") if user else None,
        "kind": d.get("kind"),
        "text": d.get("text"),
        "response_text": o.get("response_text"),
        "prompt": prompt,
        "prompt_tokens": o.get("prompt_tokens"),
    })

# accepted status rows follow decision rows; mark accepted texts
accepted = []
last_decision = {}
for c in calls:
    if c["status"] in ("DECISION", "REPAIR_SUCCEEDED") and c["text"]:
        last_decision[c["player"]] = c
    elif c["status"] == "ACCEPTED" and c["player"] in last_decision:
        d = last_decision.pop(c["player"])
        d["accepted"] = True
        accepted.append({"t": c["t"], "player": d["player"], "day": d["day"], "phase": d["phase"], "trigger": d["trigger"], "text": d["text"]})

json.dump({"roles": roles, "calls": calls, "accepted": accepted}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("roles", json.dumps(roles, ensure_ascii=False))
print("calls", len(calls), "accepted", len(accepted))
for a in accepted:
    r = roles.get(a["player"], {}).get("role_id")
    print(a["t"][11:19], a["player"], r, a["trigger"], "|", a["text"])
