"""Transcript measurements using server truth only in the evaluator."""
import re

from .checks import speech, timing_summary
from .prompts import self_reference, strip_introduction
from .repetition import normalize, similarity
from .strategy import STRATEGIES, vote_pressure


def unquoted(text, role_names):
    text = normalize(text)
    # A quoted role in one's own CO is not an attributed quotation.
    for name in role_names:
        text = text.replace(f"「{name}」", name).replace(f"『{name}』", name)
    return re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"', "", text)


def self_claims(player, text, role_names):
    text = unquoted(text, role_names)
    text = re.sub(r"(私たち|私|僕|俺)\s*\(\s*player-\d+\s*\)\s*", r"\1", text)
    text = re.sub(r"[^。！？]*(?:調べた|占った|護衛した|守った)(?:そう|らしい)[^。！？]*", "", text)
    claims = []
    for name in role_names:
        role_word = r"(?:人狼|狼)" if name == "人狼" else re.escape(name)
        subject = rf"(?:私たち|私|僕|俺|自分)(?:の役職)?(?:は|が|も)\s*|{re.escape(player)}\s*(?:さん)?\s*(?:は|が|も)\s*"
        tail = r"\s*(?:(?:の\s*)?co\s*)?(?:役職)?(?:です|でした|だった|します|しました|しています|している|だ(?:[。！、\s]|$)|であり|である|なので|なんです|だが|だけど|の一人|として|という役職|(?=[。！]|$))(?!\s*(?:可能性|かもしれ|なら|だったら|とすれば))"
        named_intro = rf"(?:^|(?<=[。！？]))\s*{role_word}の\s*{re.escape(player)}\s*です"
        body = r"(?:(?!player-\d+\s*(?:さん)?\s*(?:は|が))[^。！？.!?])*?"
        own_predicate = rf"{re.escape(player)}\s*(?:さん)?\s*(?:は|が){body}{role_word}{tail}"
        # An explicit self subject may be followed by an action target ID;
        # only an unqualified role phrase excludes a following other actor.
        bare = rf"(?:^|(?<=[。！？]))\s*(?:真の|本当の|唯一の)?{role_word}{tail}(?!\s*player-\d+)"
        inverse = rf"{role_word}(?:である|としての|の)(?:私たち|私|僕|俺|自分|{re.escape(player)})"
        alternatives = '|'.join(re.escape(n) for n in role_names)
        changed_role = rf"(?:{subject})(?:{alternatives})ではなく\s*{role_word}{tail}"
        pattern = rf"(?:{subject})(?:真の|本当の|唯一の)?{role_word}{tail}|{bare}|{named_intro}|{own_predicate}|{inverse}|{changed_role}"
        matches = list(re.finditer(pattern, text))
        definite = []
        for match in matches:
            context = re.split(r"[。！？.!?]", text[:match.start()])[-1] + match.group()
            conditional = re.search(r"なら|だったら|とすれば|仮に|もし", context)
            role_as = re.search(rf"{role_word}(?:役職)?として", match.group())
            self_hypothesis = re.search(rf"(?:もし|仮に)\s*(?:私|僕|俺|{re.escape(player)})", context)
            if not conditional or re.fullmatch(inverse, match.group()) or (role_as and not self_hypothesis):
                definite.append(match)
        if definite:
            claims.append(name)
        elif name == "人狼":
            for clause in re.split(r"[。！？.!?]", text):
                if re.search(r"なら|だったら|とすれば|仮に|もし", clause):
                    continue
                attack = re.search(r"(?:私(?:たち)?|自分)(?:は|が|で)[^。！？]*(?:襲撃(?:した|しました)(?!\s*(?:狼|人狼|者|相手))|襲撃します|襲撃する|襲った(?!\s*(?:狼|人狼|者|相手))|襲う|共同襲撃|player-\d+\s*(?:さん)?\s*(?:への|を)\s*襲撃で)", clause)
                other_subject = attack and re.search(rf"(?!{re.escape(player)}\b)player-\d+\s*(?:さん)?\s*(?:は|が)", attack.group())
                if attack and not other_subject and not re.search(r"(?:襲撃した|襲撃する|襲った)(?:の)?(?:では(?:ない|ありません)|とは言っていない)", clause[attack.start():]):
                    claims.append(name)
                    break
        elif name == "狩人":
            for clause in re.split(r"[。！？.!?]", text):
                if re.search(r"なら|だったら|とすれば|仮に|もし", clause):
                    continue
                explicit = re.search(r"(?:私|僕|俺|自分)(?:は|が)[^。！？]*護衛(?:した|しました|します|する)", clause)
                night = re.search(r"(?:^|(?:私|僕|俺|自分)(?:は|が))[^。！？]*(?:初夜|昨夜|今夜|本夜)[^。！？]*player-\d+[^。！？]*(?:護衛(?:した|しました|します)|守(?:った|りました|ります))", clause)
                action = explicit or night
                other_subject = action and re.search(rf"(?!{re.escape(player)}\b)player-\d+\s*(?:さん)?\s*(?:は|が)", action.group())
                if action and not other_subject:
                    claims.append(name)
                    break
    return claims


def result_value(text):
    text = re.sub(r'[「」『』“”"]', "", text)
    if re.search(r"(?:人狼|狼)で(?:は)?(?:ない|ありません)|非人狼|無狼|(?:清白|白)(?:です|でした|と|に|判定|確定)|人間(?:です|でした|と)|村人(?:です|でした|と)", text):
        return "not_wolf"
    if re.search(r"(?:人狼|狼)(?:です|でした|だった|と|だと|である|確定)|黒(?:です|でした|と|判定|確定)|(?:人狼|狼|黒)(?=[、,]|\s*$)", text):
        return "wolf"
    return None


def measure(rows, private_results, roles, content_roles, rules, calls, *, generated=None, discards=None):
    names = [r.name for r in content_roles.values()]
    candidates = {f"Q{i}": [] for i in range(1, 7)}
    disclosures, questions, pairs, prior = [], [], [], []
    dead, day, reactions, similar, public_count, line = set(), 0, 0, 0, 0, 5
    first_day_at = next((r['t'] for r in rows if r['kind'] == 'PHASE_STARTED' and
                         r['payload'].get('day') == 1 and r['payload'].get('phase') == 'day'), None)
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
        confused_id = self_reference(strip_introduction(text, player), player)
        claims = self_claims(player, text, names)
        if row["kind"] == "CO_DECLARED":
            claimed = content_roles.get(row["payload"]["claimed_role_id"])
            if claimed:
                claims.append(claimed.name)
        if role:
            team_claim = bool(re.search(r"(?:私は|僕は|俺は|^|[。！？]\s*)(?:人狼|狼)陣営(?:です|に属|の一員)", unquoted(text, names)))
            true_role = role.name in claims
            wolf_claim = role.attributes.team == "wolf" and (team_claim or any(r.name in claims for r in content_roles.values() if r.attributes.team == "wolf"))
            if (true_role or wolf_claim) and not confused_id:
                disclosures.append({**location, "role_id": role.id, "team": role.attributes.team, "day": day})
            if wolf_claim and not confused_id:
                candidates["Q1"].append(location)
            if role.attributes.team == "village" and any(name != role.name for name in claims):
                candidates["Q3"].append({**location, "claimed_roles": sorted(set(claims)), "actual_role": role.name})
        if confused_id:
            if candidates['Q3'] and candidates['Q3'][-1]['row'] == index:
                candidates['Q3'][-1]['self_id_confusion'] = True
            else:
                candidates['Q3'].append({**location, 'self_id_confusion': True})
        if re.search(r"お断り|申し訳ありませんが|(?:ai|人工知能|言語モデル)として|このチャットへの参加|対応できません|回答できません", normalize(text)):
            candidates["Q2"].append(location)
        if normalize(strip_introduction(text, player)) != normalize(text.strip().strip('"')):
            candidates["Q6"].append(location)
        addressed = re.findall(r"(?:^|[。！？]\s*|@)(player-\d+)\s*(?:さん|君)?\s*[,、:]", normalize(text))
        if dead.intersection(addressed):
            candidates["Q5"].append({**location, "targets": sorted(dead.intersection(addressed))})
        if role:
            issues = []
            effects = {effect.id for ability in role.abilities for effect in ability.effects}
            own = [p for p in private_results if p["player_id"] == player and p["t"] <= row["t"]]
            # Quoted atomic results are often the speaker's own report, not
            # a quotation of another participant's whole utterance.
            result_text = re.sub(r"[「『](人狼ではない|人狼|狼|白|黒)[」』]", r"\1", text)
            for clause in re.split(r"[。！？.!?\n]", unquoted(result_text, names)):
                can_initial = any(a.available_from_night == 0 for a in role.abilities)
                actor_body = r"(?:(?!(?:私|僕|俺|自分)(?:は|が)|player-\d+\s*(?:さん)?\s*(?:は|が))[^。！？])*?"
                other_actor = re.search(rf"(?:(?!{re.escape(player)}\b)player-\d+\s*(?:さん)?|あなた)\s*(?:が|は){actor_body}(?:占った|調べた|護衛した|守った|襲撃した|判定した)", clause)
                attributed = re.search(r"(?:調べた|占った|護衛した|守った)(?:そう|らしい)|(?:と|という)(?:発言|説明|主張|報告)", clause)
                attributed = attributed or re.search(r"player-\d+\s*(?:さん|君)?\s*(?:は|が)\s*(?:私|僕|俺|自分)を[^。！？]*(?:断定|判定|疑)", clause)
                hypothetical = re.search(r"もし|なら|場合|仮に|だったら|かもしれ", clause)
                initial_action = re.search(r"(?:初夜|第0夜).*(?:占った|調べた|調べました|護衛した|守った|襲撃した|判定した|判定しました|判定.*(?:出た|出ました)|(?:調査|占い)(?:で|の結果).*(?:確認した|確認しました|判定した|確定した|判明した))", clause)
                actual_ability = any(re.search(STRATEGIES['ability_report_words'].get(effect, r'(?!)'), clause) for effect in effects)
                if actual_ability and not can_initial and not other_actor and not attributed and not hypothetical and initial_action:
                    issues.append({"reason": "unavailable_initial_action"})
                if other_actor or attributed or hypothetical:
                    continue
                own_report = role.name in claims and re.search(r"結果|確定|判定", text)
                if not re.search(r"占[いっ]|霊能|霊媒|判定|調べ|私の.*結果", clause) and not own_report:
                    continue
                for match in re.finditer(r"player-\d+", clause):
                    target = match.group()
                    after = re.split(r"player-\d+", clause[match.end():], maxsplit=1)[0]
                    claimed_result = result_value(after)
                    if 'inspect' in effects and re.search(r'初夜|第0夜', clause) and claimed_result:
                        initial = [p for p in own if first_day_at is not None and p['t'] < first_day_at and
                                   p['event_payload'].get('target_player_id') == target]
                        if rules.first_night_seer == 'none' or (rules.first_night_seer == 'random_white' and claimed_result == 'wolf'):
                            issues.append({'reason': 'initial_result_not_allowed_by_preset', 'target': target})
                        elif first_day_at is not None and not initial:
                            issues.append({'reason': 'initial_unreceived_result', 'target': target})
                    target_results = [p for p in own if p["event_payload"].get("target_player_id") == target]
                    expected = {p["event_payload"]["result"] for p in target_results
                                if p["event_payload"].get("result") in {"wolf", "not_wolf"}}
                    if claimed_result and effects.intersection({"inspect", "medium_inspect"}) and expected and claimed_result not in expected:
                        issues.append({"reason": "result_mismatch", "target": target, "claimed": claimed_result, "server_results": sorted(expected)})
                    explicit_own_report = own_report or re.match(rf"\s*{re.escape(role.name)}として", clause) or re.search(
                        r"(?:私|僕|俺|自分)(?:は|が|の)[^。！？]*(?:占った|占って|調べた|判定|結果)", clause)
                    if claimed_result and effects.intersection({"inspect", "medium_inspect"}) and own and not expected and explicit_own_report and not hypothetical:
                        issues.append({"reason": "unreceived_result", "target": target, "claimed": claimed_result})
                    if "medium_inspect" in effects and target not in dead and (re.search(r"霊能|霊媒|判定|調べ", clause) or own_report) and claimed_result:
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
        addressees = set()
        for clause in re.split(r"(?<=[。！？?])", text):
            direct = set(re.findall(r"(player-\d+)\s*(?:さん|君)?\s*[,、:]", normalize(clause)))
            if direct:
                addressees = direct
            if "？" in clause or "?" in clause:
                subject = set(re.findall(r"(player-\d+)\s*(?:さん|君)?\s*は\s*(?=どう|誰|何|なぜ|どの)", normalize(clause)))
                for target in (direct or subject or addressees) - {player}:
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
            "strategy_metrics": strategy_metrics(rows, roles, content_roles),
            "speech_generations": generated, "speech_discards": discards,
            "measurement_scope": "Q4 is a text candidate check against received server results and dead/initial-action facts; Q1 includes permitted disclosures. G2/G3 count ID/time proxies, not semantic understanding. Unknown old generation telemetry stays null."}


def strategy_metrics(rows, roles, content_roles):
    guards = {p for p, role in roles.items() if any(e.id == 'protect' for a in role.abilities for e in a.effects)}
    wolves = {p for p, role in roles.items() if role.attributes.count_as == 'wolf'}
    pressured, revealed, failures = set(), set(), []
    day, phase, dead, public, wolf_public = 0, None, set(), 0, 0
    votes = {str(d): {p: 0 for p in sorted(wolves)} for d in (1, 2)}
    for index, row in enumerate(rows):
        if row['kind'] == 'PHASE_STARTED':
            day, phase = row['payload']['day'], row['payload']['phase']
        if row['kind'] == 'VOTE_RESOLVED' and day in (1, 2):
            for p in wolves:
                votes[str(day)][p] += row['payload']['tallies'].get(p, 0)
        spoken = speech(row)
        if spoken:
            player, text = spoken
            own_role = player in guards and re.search(rf'(?:私|僕|俺|自分)(?:は|が|も|こそが|[、,])\s*{re.escape(roles[player].name)}(?:です|で|として|[。！])', unquoted(text, [roles[player].name]))
            if player in guards and (own_role or roles[player].name in self_claims(player, text, [roles[player].name]) or
               row['kind'] == 'CO_DECLARED' and row['payload']['claimed_role_id'] == roles[player].id):
                revealed.add(player)
            if row['kind'] == 'chat':
                public += 1
                wolf_public += player in wolves
            if phase == 'day':
                for p in guards - dead - {player}:
                    if vote_pressure(p, text):
                        pressured.add((day, p))
        if row['kind'] == 'PLAYER_DIED':
            p = row['payload']['player_id']
            if row['payload'].get('public_cause') == 'lynched' and (day, p) in pressured and p not in revealed:
                failures.append({'row': index, 'day': day, 'player_id': p})
            dead.add(p)
    return {'pressure_no_co_lynches': len(failures), 'pressure_no_co_candidates': failures,
            'wolf_public_messages': wolf_public, 'wolf_public_speech_share': wolf_public / public if public else 0,
            'wolf_votes_days1_2': votes,
            'note': 'Vote pressure is a text candidate; votes sum all resolved rounds in each day. Role truth is evaluator-only.'}


def mechanical_conditions(checks):
    zero_lists = ("private_channel_body_matches", "authentication_token_leaks", "other_private_result_literal_matches",
                  "immediate_sentence_repetitions", "sentences_repeated_three_times")
    review = checks.get("strategic_disclosure_review", {})
    result = {"completed": bool(checks.get("completed")), "server_rejections": checks.get("server_rejections") == 0,
            "crashes": checks.get("crashes") == 0, **{key: not checks[key] for key in zero_lists},
            "llm_http_errors": checks.get("llm_http_errors") == 0}
    if checks.get('require_strategic_disclosures'):
        result['strategic_disclosures'] = review.get('status') == 'complete' and review.get('leaks') == 0
    return result
