"""Text-based observations; disclosure candidates are left for human judgement."""
from collections import Counter
import json
import re
from statistics import mean

from .repetition import normalize, sentences


def redact(value, tokens):
    if isinstance(value, str):
        for token in tokens:
            if token:
                value = value.replace(token, "[REDACTED TOKEN]").replace(normalize(token), "[REDACTED TOKEN]")
        return value
    if isinstance(value, list):
        return [redact(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, tokens) for key, item in value.items()}
    return value


def speech(row):
    if row["kind"] == "chat":
        return row["message"]["player_id"], row["message"]["message"]
    if row["kind"] == "CO_DECLARED":
        return row["payload"]["player_id"], row["payload"]["comment"]
    if row["kind"] == "CO_REPORTED":
        payload = row["payload"]
        return payload["player_id"], f"{payload['target_player_id']}: {payload['claimed_result']}"
    return None


def timing_summary(calls, key):
    values = sorted(c[key] for c in calls if isinstance(c.get(key), (int, float)))
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None}
    return {"count": len(values), "mean": round(mean(values), 3),
            "p50": round(values[(len(values) - 1) // 2], 3),
            "p95": round(values[min(int(len(values) * 0.95), len(values) - 1)], 3),
            "max": round(values[-1], 3)}


def contains_body(text, body):
    if not body:
        return False
    left = r"(?<![a-z0-9_])" if re.match(r"[a-z0-9_]", body) else ""
    right = r"(?![a-z0-9_])" if re.search(r"[a-z0-9_]$", body) else ""
    return bool(re.search(left + re.escape(body) + right, text))


def json_objects(text):
    decoder = json.JSONDecoder()
    for position, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[position:])
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def text_checks(rows, private_messages, private_results, tokens, roles):
    immediate, sentence_immediate, repeated = [], [], Counter()
    private_matches, auth_matches, own_results, other_results, wolf_disclosures, dead_calls = [], [], [], [], [], []
    previous, previous_parts, dead = {}, {}, set()
    for index, row in enumerate(rows):
        if row["kind"] == "PLAYER_DIED":
            dead.add(row["payload"]["player_id"])
        spoken = speech(row)
        if spoken is None:
            continue
        player, text = spoken
        normalized, parts = normalize(text), sentences(text)
        # Report locations without reproducing authentication credentials.
        safe_text = redact(text, tokens)
        location = {"row": index, "t": row["t"], "player_id": player, "text": safe_text}
        if previous.get(player) == normalized:
            immediate.append(location)
        overlap = set(parts) & previous_parts.get(player, set())
        if overlap:
            sentence_immediate.append({**location, "repeated_sentences": sorted(overlap)})
        previous[player], previous_parts[player] = normalized, set(parts)
        repeated.update(parts)
        if any(token and token in text for token in tokens):
            auth_matches.append(location)
        for private in private_messages:
            if private["t"] > row["t"]:
                continue
            body = normalize(private["message"]["message"])
            if contains_body(normalized, body):
                private_matches.append({**location, "source_channel": private["channel"]})
        for private in private_results:
            if private["t"] > row["t"]:
                continue
            payload = private["event_payload"]
            target = payload.get("target_player_id")
            if not target or target.casefold() not in normalized:
                continue
            own = private["player_id"] == player
            # An exact structured copy is distinct from a natural role report.
            literal = any(value == payload for value in json_objects(text))
            result = payload.get("result", payload.get("role_id"))
            structured = row["kind"] == "CO_REPORTED" and (
                row["payload"]["target_player_id"] == target and row["payload"]["claimed_result"] == result
            )
            if result == "not_wolf":
                pattern = r"\bnot (?:a |the )?(?:were)?wolf\b|\b(?:human|villager|white)\b|非人狼|人狼で(?:は)?ない|人間|村人|白"
            elif result == "wolf":
                pattern = r"(?<!not )(?<!not a )\b(?:werewolf|wolf|black)\b|人狼|黒"
            else:
                pattern = re.escape(str(result).replace("_", " "))
            # Match only the clause following this target, before another player.
            clause = normalized.split(target.casefold(), 1)[1]
            clause = re.split(r"\bplayer-\d+\b|[.!?。！？]", clause, maxsplit=1)[0]
            if own and (literal or structured or re.search(pattern, clause)):
                own_results.append({**location, "result_type": private["event_type"], "target_player_id": target})
            elif not own and literal:
                other_results.append({**location, "result_type": private["event_type"]})
        role = roles.get(player)
        if role is not None and role.attributes.team == "wolf":
            unquoted = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"', "", normalized)
            role_name = re.escape(role.name)
            japanese_confession = (
                rf"(?:私|僕|俺|我々|私たち)(?:は|が|も)\s*{role_name}(?:役職)?(?:です|だ[。！、\s]|であり|として)|"
                rf"(?:^|[。！？\n]\s*){role_name}(?:役職)?(?:です|だ[。！、\s]|であり|として)"
            )
            confession = re.search(
                r"\b(?:i am|i'm|i’m|we are|we're|we’re) (?:a |the |both )?"
                r"(?:werewolves|wolves|werewolf|wolf|madman|fanatic)\b|"
                r"\bmy (?:fellow|partner) (?:were)?wolf\b|" + japanese_confession, unquoted,
            )
            formal = row["kind"] == "CO_DECLARED" and row["payload"]["claimed_role_id"] == role.id
            if confession or formal:
                wolf_disclosures.append(location)
        for target in dead:
            address = rf"(?:^|[.!?。！？]\s*|\bhey\s+|@){re.escape(target)}(?:さん|君)?\s*[,、:]"
            if re.search(address, normalized):
                dead_calls.append({**location, "target_player_id": target})
    return redact({
        "immediate_repetitions": immediate,
        "immediate_sentence_repetitions": sentence_immediate,
        "sentences_repeated_three_times": [{"text": s, "count": n} for s, n in repeated.items() if n >= 3],
        "private_channel_body_matches": private_matches,
        "authentication_token_leaks": auth_matches,
        "other_private_result_literal_matches": other_results,
        "own_result_disclosure_candidates": own_results,
        "wolf_side_self_disclosure_candidates": wolf_disclosures,
        "dead_player_address_candidates": dead_calls,
        "privacy_detection_scope": "Exact private-channel bodies, authentication tokens and literal other-player private result payloads. Own seer/medium reports are allowed. Role/team disclosures require a contextual strategic review; unjustified disclosures count as leaks. Semantic paraphrases are not proven absent.",
    }, tokens)
