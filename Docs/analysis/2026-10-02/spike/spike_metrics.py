"""Label-free mechanical metrics for live spike games (scratch)."""
import json
import re
import sys
import unicodedata


def norm(t):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t).casefold()).strip()


def grams(t):
    w = re.findall(r"[\w-]+", norm(t))
    return {tuple(w[i:i + 3]) for i in range(max(len(w) - 2, 0))}


def jac(a, b):
    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


for path in sys.argv[1:]:
    d = json.load(open(path, encoding="utf-8"))
    roles = d["roles"]
    dead_from = {}
    for f in d["public_facts"]:
        m = re.match(r"Day (\d+): (player-\d) (was executed|was found dead)", f)
        if m:
            dead_from[m.group(2)] = int(m.group(1)) + 1
    chats = d["public_chat"]
    addressed_dead = 0
    near_dup = 0
    agree = 0
    questions = 0
    confess = 0
    for i, (day, ch, sp, tx) in enumerate(chats):
        for m in re.finditer(r"\bplayer-(\d)\b\s*,", tx, re.I):
            p = f"player-{m.group(1)}"
            if dead_from.get(p, 99) <= day:
                addressed_dead += 1
                break
        prev = [c[3] for c in chats[max(0, i - 15):i]]
        if any(jac(tx, r) > 0.45 for r in prev):
            near_dup += 1
        if re.match(r"\s*i (totally |completely )?agree", tx, re.I):
            agree += 1
        if "?" in tx:
            questions += 1
        if roles[sp] in ("werewolf", "madman") and re.search(r"\bI(?:'m| am) (?:a |the )?(?:were)?wolf\b|\bI(?:'m| am) (?:a |the )?madman\b|\bmy (?:fellow|partner) (?:were)?wolf", tx, re.I):
            confess += 1
    calls = [c for c in d["calls"] if c["purpose"] == "chat"]
    n = len(chats)
    print(f"{path}: game_end={d['game_end']} wall={d['wall_sec']}s chats={n} days={max(c[0] for c in chats) if chats else 0}")
    print(f"   addressed-a-dead-player={addressed_dead} ({addressed_dead/n:.0%})  near-duplicate(>0.45 vs last15)={near_dup} ({near_dup/n:.0%})"
          f"  starts-with-'I agree'={agree} ({agree/n:.0%})  with-'?'={questions}  wolf-side-confession={confess}")
    if calls:
        g = sorted(c["gen_sec"] for c in calls)
        q = sorted(c["queue_sec"] for c in calls)
        print(f"   chat gen p50={g[len(g)//2]}s max={g[-1]}s  queue p50={q[len(q)//2]}s max={q[-1]}s  rejections={len(d['rejections'])}")
