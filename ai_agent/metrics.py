"""Transcript measurements using server truth only in the evaluator."""
import re

from .checks import speech, timing_summary
from .prompts import strip_introduction
from .repetition import normalize, similarity


def unquoted(text, role_names):
    text = normalize(text)
    # A quoted role in one's own CO is not an attributed quotation.
    for name in role_names:
        text = text.replace(f"「{name}」", name).replace(f"『{name}』", name)
    return re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"', "", text)


def self_claims(player, text, role_names):
    text = unquoted(text, role_names)
    text = re.sub(r"[^。！？]*(?:調べた|占った|護衛した|守った)(?:そう|らしい)[^。！？]*", "", text)
    claims = []
    for name in role_names:
        role_word = r"(?:人狼|狼)" if name == "人狼" else re.escape(name)
        subject = rf"(?:私|僕|俺|私たち)(?:の役職)?(?:は|が|も)\s*|(?:^|[。！？]\s*)(?:{re.escape(player)}\s*(?:は|が|も)\s*)?"
        tail = r"(?:役職)?(?:です|だ(?:[。！、\s]|$)|であり|である|として|という役職|ですが)(?!\s*(?:player-\d+|可能性|かもしれ|なら|だったら|とすれば))"
        named_intro = rf"(?:^|[。！？]\s*){role_word}の\s*{re.escape(player)}\s*です"
        body = r"(?:(?!player-\d+\s*(?:さん)?\s*(?:は|が))[^。！？.!?])*?"
        own_predicate = rf"{re.escape(player)}\s*(?:さん)?\s*(?:は|が){body}{role_word}{tail}"
        pattern = rf"(?:{subject})(?:真の|本当の|唯一の)?{role_word}{tail}|{named_intro}|{own_predicate}"
        matches = list(re.finditer(pattern, text))
        definite = [m for m in matches if not re.search(r"なら|だったら|とすれば|仮に|もし",
                    re.split(r"[。！？.!?]", text[:m.start()])[-1] + m.group())]
        if definite:
            claims.append(name)
        elif name == "人狼" and re.search(r"私(?:たち)?(?:は|が|で)[^。！？]*(?:襲撃した|襲撃しました|襲った|共同襲撃)", text):
            claims.append(name)
    return claims


def result_value(text):
    text = re.sub(r'[「」『』“”"]', "", text)
    if re.search(r"人狼で(?:は)?(?:ない|ありません)|非人狼|白(?:です|でした|と|判定|確定)|人間(?:です|でした|と)|村人(?:です|でした|と)", text):
        return "not_wolf"
    if re.search(r"人狼(?:です|でした|だった|と|だと|である|確定)|黒(?:です|でした|と|判定|確定)", text):
        return "wolf"
    return None


def measure(rows, private_results, roles, content_roles, rules, calls, *, generated=None, discards=None):
    names = [r.name for r in content_roles.values()]
    candidates = {f"Q{i}": [] for i in range(1, 7)}
    disclosures, questions, pairs, prior = [], [], [], []
    dead, day, reactions, similar, public_count, line = set(), 0, 0, 0, 0, 5
    for index, row in enumerate(rows):
        row_line = line
        line += 2 + (row["message"]["message"].count("\n") if row["kind"] == "chat" else 0)
        if row["kind"] == "PHASE_STARTED":
            day = row["payload"]["day"]
        if row["kind"] == "PLAYER_DIED":
            dead.add(row["payload"]["player_id"])
        spoken = speech(row)
        if spoken is None:
            continue
        player, text = spoken
        location = {"row": index, "line": row_line, "t": row["t"], "player_id": player, "text": text}
        role = roles.get(player)
        claims = self_claims(player, text, names)
        if row["kind"] == "CO_DECLARED":
            claimed = content_roles.get(row["payload"]["claimed_role_id"])
            if claimed:
                claims.append(claimed.name)
        if role:
            team_claim = bool(re.search(r"(?:私は|僕は|俺は|^|[。！？]\s*)(?:人狼|狼)陣営(?:です|に属|の一員)", unquoted(text, names)))
            true_role = role.name in claims
            if true_role or (role.attributes.team == "wolf" and team_claim):
                disclosures.append({**location, "role_id": role.id, "team": role.attributes.team, "day": day})
            if role.attributes.team == "wolf" and (true_role or team_claim):
                candidates["Q1"].append(location)
            if role.attributes.team == "village" and any(name != role.name for name in claims):
                candidates["Q3"].append({**location, "claimed_roles": sorted(set(claims)), "actual_role": role.name})
        if re.search(r"お断り|申し訳ありませんが|(?:ai|人工知能|言語モデル)として|このチャットへの参加|対応できません|回答できません", normalize(text)):
            candidates["Q2"].append(location)
        if normalize(strip_introduction(text, player)) != normalize(text.strip().strip('"')):
            candidates["Q6"].append(location)
        addressed = re.findall(r"(?:^|[。！？]\s*|@)(player-\d+)(?:さん|君)?\s*[,、:]", normalize(text))
        if dead.intersection(addressed):
            candidates["Q5"].append({**location, "targets": sorted(dead.intersection(addressed))})
        if role:
            issues = []
            effects = {effect.id for ability in role.abilities for effect in ability.effects}
            own = [p for p in private_results if p["player_id"] == player and p["t"] <= row["t"]]
            for clause in re.split(r"[。！？.!?\n]", unquoted(text, names)):
                can_initial = any(a.available_from_night == 0 for a in role.abilities)
                other_actor = re.search(rf"(?!{re.escape(player)}\b)player-\d+\s*(?:さん)?\s*(?:が|は)[^。！？]*(?:占った|調べた|護衛した|守った|襲撃した)", clause)
                attributed = re.search(r"(?:調べた|占った|護衛した|守った)(?:そう|らしい)|(?:と|という)(?:発言|説明|主張|報告)", clause)
                if day == 1 and not can_initial and not other_actor and not attributed and re.search(r"初夜.*(?:占った|調べた|調べました|護衛した|守った|襲撃した|判定.*(?:出た|出ました))", clause):
                    issues.append({"reason": "unavailable_initial_action"})
                if other_actor or attributed:
                    continue
                if not re.search(r"占[いっ]|霊能|霊媒|判定|調べ|私の.*結果", clause):
                    continue
                for match in re.finditer(r"player-\d+", clause):
                    target = match.group()
                    after = re.split(r"player-\d+", clause[match.end():], maxsplit=1)[0]
                    claimed_result = result_value(after)
                    target_results = [p for p in own if p["event_payload"].get("target_player_id") == target]
                    expected = {p["event_payload"]["result"] for p in target_results
                                if p["event_payload"].get("result") in {"wolf", "not_wolf"}}
                    if claimed_result and effects.intersection({"inspect", "medium_inspect"}) and expected and claimed_result not in expected:
                        issues.append({"reason": "result_mismatch", "target": target, "claimed": claimed_result, "server_results": sorted(expected)})
                    if "medium_inspect" in effects and target not in dead and re.search(r"霊能|霊媒|判定|調べ", clause) and claimed_result:
                        issues.append({"reason": "medium_living_target", "target": target})
            if issues:
                candidates["Q4"].append({**location, "issues": issues})
        if row["kind"] != "chat":
            continue
        public_count += 1
        mentioned = set(re.findall(r"player-\d+", normalize(text))) - {player}
        if mentioned.intersection(p["player_id"] for p in prior[-5:]):
            reactions += 1
        if any(similarity(text, p["text"]) > 0.45 for p in prior[-15:]):
            similar += 1
        for question in questions:
            if not question.get("answered") and question["target"] == player and 0 < row["t"] - question["t"] <= 60:
                question["answered"] = True
                pairs.append({"question_row": question["row"], "question_line": question["line"], "answer_row": index,
                              "answer_line": location["line"], "target": player, "delay_sec": round(row["t"] - question["t"], 2)})
        for clause in re.split(r"(?<=[。！？?])", text):
            if "？" in clause or "?" in clause:
                for target in set(re.findall(r"player-\d+", clause)) - {player}:
                    questions.append({**location, "target": target})
        prior.append(location)
    total_discarded = sum(discards.values()) if discards is not None else None
    tokens = [c["prompt_tokens"] for c in calls if isinstance(c.get("prompt_tokens"), int)]
    values = {key: len(value) for key, value in candidates.items()}
    values.update({"G1": public_count, "G2": reactions / public_count if public_count else 0,
                   "G3": len(pairs), "G4": total_discarded / generated if generated else None,
                   "G5_prompt_tokens_max": max(tokens, default=None),
                   "G5_generation_p95_sec": timing_summary([c for c in calls if c.get("completed", True)], "generation_sec")["p95"],
                   "similarity_over_0_45_rate": similar / public_count if public_count else 0})
    return {"metrics": values, "metric_candidates": candidates, "question_answer_pairs": pairs,
            "role_team_disclosure_candidates": disclosures,
            "speech_generations": generated, "speech_discards": discards,
            "measurement_scope": "Q4 is a text candidate check against received server results and dead/initial-action facts; Q1 includes permitted disclosures. G2/G3 count ID/time proxies, not semantic understanding. Unknown old generation telemetry stays null."}


def mechanical_conditions(checks):
    zero_lists = ("private_channel_body_matches", "authentication_token_leaks", "other_private_result_literal_matches",
                  "immediate_sentence_repetitions", "sentences_repeated_three_times")
    review = checks.get("strategic_disclosure_review", {})
    return {"completed": bool(checks.get("completed")), "server_rejections": checks.get("server_rejections") == 0,
            "crashes": checks.get("crashes") == 0, **{key: not checks[key] for key in zero_lists},
            "llm_http_errors": checks.get("llm_http_errors") == 0,
            "strategic_disclosures": review.get("status") == "complete" and review.get("leaks") == 0}
