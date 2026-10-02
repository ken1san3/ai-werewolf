"""Mechanical, label-free metrics over simulated transcripts (no semantic judging)."""
import collections
import json
import re
import sys
import unicodedata


def norm(t):
    t = unicodedata.normalize("NFKC", t).casefold()
    return re.sub(r"\s+", " ", t).strip()


for path in sys.argv[1:]:
    d = json.load(open(path, encoding="utf-8"))
    roles = d["roles"]
    chats = [(day, p, t) for day, k, p, t in d["log"] if k == "chat"]
    sentences = collections.Counter()
    for _, p, t in chats:
        for s in re.split(r"(?<=[.!?])\s+", t):
            if len(s) > 15:
                sentences[norm(s)] += 1
    last = {}
    self_repeat = 0
    for _, p, t in chats:
        if last.get(p) == norm(t):
            self_repeat += 1
        last[p] = norm(t)
    exact_msgs = collections.Counter(norm(t) for _, p, t in chats)
    wolf_confess = [t for _, p, t in chats if roles[p] in ("werewolf", "madman")
                    and re.search(r"\bI(?:'m| am) (?:a |the )?(?:were)?wolf\b|\bI(?:'m| am) (?:a |the )?madman\b|\bwe wolves\b|\bmy fellow (?:were)?wolf\b", t, re.I)]
    intro = sum(1 for _, p, t in chats if re.match(r"\s*I(?:'m| am) player-\d", t, re.I))
    questions = sum(1 for _, p, t in chats if "?" in t)
    addressed = sum(1 for _, p, t in chats if re.match(r"\s*player-\d", t, re.I))
    lat = [c["sec"] for c in d["calls"]]
    print(f"{path}: result={d['result']} days={d['days']} chats={len(chats)} calls={len(lat)} mean_sec={sum(lat)/len(lat):.2f} max_sec={max(lat):.2f}")
    print(f"   exact self-repeat(prev msg)={self_repeat}  duplicate whole msgs={sum(c-1 for c in exact_msgs.values() if c>1)}"
          f"  sentences seen>=3 times={sum(1 for c in sentences.values() if c>=3)}")
    print(f"   wolf-side self-disclosure={len(wolf_confess)}  'I am player-N' intros={intro}  msgs with '?'={questions}  msgs opening by addressing a player={addressed}")
    for t in wolf_confess:
        print("     CONFESS?", t)
    for s, c in sentences.most_common(3):
        if c >= 3:
            print(f"     x{c}: {s}")
