"""E5: label speech acts AFTER generation with a separate tiny call (scratch experiment).

Tests whether the always-NONE speech_act is a property of the model or of the single-call schema.
"""
import collections
import json
import sys

from llm import chat, free_body

path = sys.argv[1]
out = sys.argv[2]
d = json.load(open(path, encoding="utf-8"))
chats = [(p, t) for day, k, p, t in d["log"] if k == "chat" and day == 1]

SYSTEM = ("You label one chat message from a Werewolf game discussion. Choose the single best label:\n"
          "QUESTION = asks someone something; ANSWER = answers a question someone asked earlier; "
          "REBUTTAL = disputes or pushes back on another player's claim or accusation; "
          "OPINION_CHANGE = says they changed their suspicion or view; CLAIM = asserts a view/accusation/role; "
          "NONE = none of these. If you choose ANSWER or REBUTTAL, give the earlier message number it responds to.")
schema = {"type": "object", "additionalProperties": False, "required": ["label", "responds_to"],
          "properties": {"label": {"enum": ["QUESTION", "ANSWER", "REBUTTAL", "OPINION_CHANGE", "CLAIM", "NONE"]},
                         "responds_to": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}}}
rows = []
for i, (p, t) in enumerate(chats):
    if i == 0:
        continue
    ctx = "\n".join(f"#{j+1} {q}: {s}" for j, (q, s) in enumerate(chats[max(0, i - 6):i], start=max(0, i - 6)))
    user = f"Earlier messages:\n{ctx}\n\nMessage to label:\n#{i+1} {p}: {t}"
    text, dt, usage, finish = chat(free_body(SYSTEM, user, temperature=0.0, max_tokens=40, schema=schema))
    lab = json.loads(text)
    rows.append({"n": i + 1, "player": p, "text": t, **lab, "sec": round(dt, 2)})
    print(i + 1, p, lab, flush=True)
json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(collections.Counter(r["label"] for r in rows))
