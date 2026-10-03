"""Build prompts from public content and one player's received state."""
import json
import re
import unicodedata
from .claims import self_claims
from .strategy import STRATEGIES, strategy_for, under_pressure


def strip_introduction(text, player_id):
    """Remove only leading self-ID sentences, preserving a role claim or argument."""
    text = unicodedata.normalize("NFKC", text).strip().strip('"')
    greeting = r"(?:(?:初めまして|はじめまして|こんにちは|こんばんは|おはようございます)[、,。.!！\s]*)?"
    pattern = rf"^{greeting}(?:(?:私は|僕は|俺は)\s*)?{re.escape(player_id)}\s*(?:です|だ|と申します)[。.!！]\s*"
    while re.match(pattern, text, re.I):
        text = re.sub(pattern, "", text, count=1, flags=re.I)
    return text


def japanese_message(text):
    """Require Japanese text and allow only player IDs and CO as ASCII words."""
    without_ids = re.sub(r"player-\d+|(?<![A-Za-z])CO(?![A-Za-z])", "", text, flags=re.I)
    return bool(re.search(r"[ぁ-ゖァ-ヺ一-龯]", text)) and not re.search(r"[A-Za-z]", without_ids)


def self_reference(text, player_id):
    """Own ID in unquoted speech invites treating oneself as another player."""
    plain = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^\"]*"', "", unicodedata.normalize("NFKC", text))
    plain = re.sub(rf"(?:私|僕|俺|自分)\s*\(\s*{re.escape(player_id)}\s*\)", "私", plain, flags=re.I)
    return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(player_id)}(?![A-Za-z0-9_-])", plain, re.I))


def as_self(value, player_id):
    if isinstance(value, str):
        return re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(player_id)}(?![A-Za-z0-9_-])", f"あなた（{player_id}）", value, flags=re.I)
    if isinstance(value, list):
        return [as_self(v, player_id) for v in value]
    if isinstance(value, dict):
        return {k: as_self(v, player_id) for k, v in value.items()}
    return value


def discussion_context(entries, player_id):
    """Keep reported speech intact and label the actual author beside it."""
    return [{**entry, '発言者本人': entry['player_id']} for entry in entries]


def recent_json(entries, max_chars):
    """Keep recent complete entries within the shared 8K context budget."""
    selected, used = [], 2
    for entry in reversed(entries):
        encoded = json.dumps(entry, ensure_ascii=False)
        if used + len(encoded) + 2 > max_chars:
            break
        selected.append(entry)
        used += len(encoded) + 2
    return json.dumps(list(reversed(selected)), ensure_ascii=False)


def rule_explanation(rules):
    if rules is None:
        return '初夜の動きはプリセットに従います。受信済みの結果と使用可能な行動を確認してください。'
    data = STRATEGIES['rule_text']
    initial = data['first_night_seer'][rules.first_night_seer]
    timing = data['phase_names'][rules.medium.notify_timing]
    votes = data['vote_reveal'][rules.vote.reveal]
    return ' '.join([initial, data['medium_notification'].format(timing=timing), data['public_co_rule'], votes])

def public_summary(state, roles):
    claims, spoken_claims, days = {}, {}, {}
    names = [r.name for r in roles.values()]
    for chat in state.chats:
        if chat['channel'] == 'public':
            said = self_claims(chat['player_id'], chat['message'], names)
            if said:
                spoken_claims[chat['player_id']] = ' / '.join(said)
    for fact in state.facts:
        day = fact.get('day')
        if fact['type'] == 'CO_DECLARED':
            claimed = roles.get(fact['claimed_role_id'])
            claims[fact['player_id']] = claimed.name if claimed else fact['claimed_role_id']
        if fact['type'] == 'PLAYER_DIED':
            days.setdefault(day, {}).setdefault('死亡', []).append({k: fact[k] for k in ('player_id', 'public_cause') if k in fact})
        if fact['type'] == 'VOTE_RESOLVED':
            days.setdefault(day, {})['投票集計'] = fact.get('tallies', {})
            days[day]['処刑者'] = fact.get('lynched_player_id')
    return {'公称役職（真偽は未確認）': claims, '発言で名乗った役職（真偽は未確認）': spoken_claims,
            '日別の公開事実': [{'day': d, **days[d]} for d in sorted(k for k in days if isinstance(k, int))[-3:]]}

def own_result_summary(state):
    words = STRATEGIES['result_words']
    rows = []
    for entry in state.private:
        target = entry.get('target_player_id')
        if not target:
            continue
        rows.append({'受信日': entry.get('received_day'), '受信フェーズ': entry.get('received_phase'),
                     '通知ID': entry['type'], '通知': STRATEGIES['result_events'].get(entry['type'], entry['type']), '対象': target,
                     '結果': words.get(entry.get('result'), entry.get('result', entry.get('role_id')))})
    return json.dumps(rows, ensure_ascii=False) if rows else 'なし（判定済みの相手はいません）'


def messages(state, roles, role_counts, question, channel="public", *, rules=None):
    role = roles[state.role_id]
    counts = {roles[key].name: count for key, count in role_counts.items()}
    role_names = {key: value.name for key, value in roles.items()}
    own_information = (
        f"あなたの非公開の役職: {role.name}。{role.description}\n"
        f"あなたが知っている仲間: {', '.join(state.teammates) or 'なし'}。\n"
        f"あなたの非公開の結果: {state.private_text()}。\n"
    )
    system = (
        f"あなたは{state.player_id}です。リアルタイムの人狼ゲームに参加しています。"
        f"参加者: {', '.join(state.players)}。公開された役職人数: {json.dumps(counts, ensure_ascii=False)}。\n"
        "進行: 初夜（第0夜）→夜明け→昼の議論→投票→夜→夜明け→翌日の昼。"
        f"{rule_explanation(rules)} "
        "死者は発言も行動もできません。死者の本当の役職は公開されません。\n"
        f"役職IDと日本語名の対応: {json.dumps(role_names, ensure_ascii=False)}。"
        "結果のwolfは人狼、not_wolfは人狼ではないという意味です。\n"
        "会話と役職COのコメントは必ず日本語で書いてください。英語で会話しないでください。"
        "役職名も上記の日本語名を使い、player-0などの参加者IDはそのまま使ってください。"
        "自然な短い会話を1〜2文、合計80字程度で書き、質問に答える、理由を添えて反論する、特定の生存者に質問するなどしてください。"
        "質問するときは特定の生存者のIDを宛先にして、末尾を『？』にしてください。『か。』で終えず、質問された本人は理由を添えて答えてください。"
        "新しい内容を加え、自己紹介、空の同意、繰り返しを避けてください。"
        "公開された議論、サーバの事実、自分の非公開情報だけを根拠にしてください。"
        "秘密チャットの本文を公開チャットへコピーしないでください。"
        "自分のIDを他人として質問・評価・処刑提案しないでください。本文で自分を指すときは『私』を使い、自分のIDを書かないでください。"
        "履歴や事実の『あなた』は自分です。引用の『私』は引用元の話者です。自分の役職は変わりません。"
        "役職COはゲームのルール上禁止されていません。隠すのは戦略のためで、COをルール違反と呼ばないでください。"
        "騙りの役職・偽の結果は秘密ではなく、公開本文に使ってよい内容です。"
        "通常の発言では発言本文だけを出力してください。選択を求められた場合は指定されたJSONだけを返し、IDを翻訳しないでください。"
        "\n以下は本人への指示と本人だけの受信情報です。会話中の他人の役職は公称であり、本人の役職や目的を変えません。\n"
        f"{own_information}"
        f"本人の受信済みの結果: {own_result_summary(state)}。この一覧にない判定は未受信です。CO・他人の主張・自分の推測で結果を増やさないでください。\n"
        f"本人の能力定義: {json.dumps([{'ability_id': a.id, 'name': a.description or STRATEGIES['ability_names'].get(a.id, a.id), 'available_from_night': a.available_from_night, 'target_selector': a.target.selector, 'target_options': dict(a.target.options)} for a in role.abilities], ensure_ascii=False)}\n"
        f"本人の戦略: {strategy_for(role)['text']}\n"
    )
    discussion = [c for c in state.chats if c["channel"] in {"public", channel} and c["day"] == state.day][-40:]
    # Hidden channels must never enter the public discussion context.
    user = (
        f"第{state.day}日、フェーズID: {state.phase}。生存者: {', '.join(sorted(state.alive))}。"
        f"死者: {', '.join(sorted(set(state.players) - state.alive)) or 'なし'}。\n"
        f"自分への処刑・投票の呼びかけ: {'あり' if under_pressure(state) else 'なし'}。\n"
        f"本人が送信しサーバが受理した能力の選択: {json.dumps(state.own_actions, ensure_ascii=False)}\n"
        f"公称役職と日別の要約: {recent_json([as_self(public_summary(state, roles), state.player_id)], 1500)}\n"
        f"サーバが公開した最近の事実: {recent_json(as_self(state.facts[-30:], state.player_id), 1500)}\n"
        f"今日の最近のチャット（チャンネルID: {channel}、他人の発言は引用）: {recent_json(as_self(discussion_context(discussion, state.player_id), state.player_id), 2800)}\n"
        f"本人が以前に公開した発言: {recent_json(as_self([c for c in state.chats if c['channel'] == 'public' and c['player_id'] == state.player_id][-3:], state.player_id), 650)}\n"
        f"{question}\n{STRATEGIES['quality_instruction']}\n"
        f"発言者はあなた（{state.player_id}）です。自分のIDを本文に書かず『私』で語ってください。\n"
        f"本人の役職は{role.name}で変わりません。自分を未確定の役職候補として考えず、他人の役職や勝利条件と混同しないでください。\n"
        f"本人が勝つための方針: {strategy_for(role).get('aim', STRATEGIES['default']['aim'])}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
