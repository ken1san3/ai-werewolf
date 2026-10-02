"""Dump AI decisions from a game evidence directory (read-only analysis helper)."""
import collections
import glob
import json
import os
import sys


def load(root):
    rows = []
    for f in glob.glob(os.path.join(root, "**", "ai.jsonl"), recursive=True):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                rows.append({"_bad": True})
    rows = [r for r in rows if not r.get("_bad")]
    rows.sort(key=lambda o: o.get("recorded_at_utc", ""))
    return rows


def text_of(o):
    d = o.get("decision") or {}
    return d.get("text")


def main(root, verbose):
    rows = load(root)
    print("ROOT", root, "rows", len(rows))
    models = collections.Counter(o.get("provider_model") or (o.get("backend") or {}).get("model") for o in rows)
    print("models", dict(models))
    print("status", collections.Counter((o.get("status"), o.get("validation_code"), o.get("backend_error_code")) for o in rows).most_common(15))
    print("finish", collections.Counter(o.get("finish_reason") for o in rows))
    kinds = collections.Counter((o.get("decision") or {}).get("kind") for o in rows)
    print("kinds", dict(kinds))
    texts = [(o.get("player_id"), text_of(o)) for o in rows if text_of(o)]
    uniq = collections.Counter(t for _, t in texts)
    print("text decisions", len(texts), "unique", len(uniq))
    print("top repeated:")
    for t, c in uniq.most_common(8):
        print("   ", c, repr(t))
    lat = sorted(o.get("latency_microseconds") or 0 for o in rows)
    if lat:
        print("latency ms p50/p90/max", lat[len(lat) // 2] // 1000, lat[int(len(lat) * 0.9)] // 1000, lat[-1] // 1000)
    pt = sorted(o.get("prompt_tokens") or 0 for o in rows)
    ct = sorted(o.get("completion_tokens") or 0 for o in rows)
    if pt:
        print("prompt_tokens p50/max", pt[len(pt) // 2], pt[-1], "completion p50/max", ct[len(ct) // 2], ct[-1])
    if verbose:
        for o in rows:
            d = o.get("decision") or {}
            print(o.get("recorded_at_utc", "")[11:19], o.get("player_id"), o.get("day"), o.get("phase"), o.get("status"),
                  o.get("validation_code") or "", d.get("kind"), repr(d.get("text")) if d.get("text") else "",
                  d.get("claimed_role_id") or "", d.get("vote_target_player_id") or "", d.get("ability_target_player_ids") or "")


if __name__ == "__main__":
    main(sys.argv[1], len(sys.argv) > 2)
