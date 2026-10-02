"""Build prompts from public content and one player's received state."""
import json
import re
import unicodedata


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


def messages(state, roles, role_counts, question, channel="public", *, include_private=True):
    role = roles[state.role_id]
    counts = {roles[key].name: count for key, count in role_counts.items()}
    role_names = {key: value.name for key, value in roles.items()}
    own_information = (
        f"あなたの非公開の役職: {role.name}。{role.description}\n"
        f"あなたが知っている仲間: {', '.join(state.teammates) or 'なし'}。\n"
        f"あなたの非公開の結果: {state.private_text()}。\n"
    ) if include_private else (
        "公開議論だけを根拠に、役職・所属・自分の能力行動を名乗らずに会話してください。"
        "正式COのコメントを求められた場合だけ、依頼された公称の役職に合わせてください。\n"
    )
    system = (
        f"あなたは{state.player_id}です。リアルタイムの人狼ゲームに参加しています。"
        f"参加者: {', '.join(state.players)}。公開された役職人数: {json.dumps(counts, ensure_ascii=False)}。\n"
        "進行: 初夜（第0夜）→夜明け→昼の議論→投票→夜→夜明け→翌日の昼。"
        "初夜には、第1日の前に占い師だけがサーバの選んだ人狼ではない相手の占い結果を受け取ります。"
        "死者は発言も行動もできません。死者の本当の役職は公開されません。\n"
        f"{own_information}"
        f"役職IDと日本語名の対応: {json.dumps(role_names, ensure_ascii=False)}。"
        "結果のwolfは人狼、not_wolfは人狼ではないという意味です。\n"
        "会話と役職COのコメントは必ず日本語で書いてください。英語で会話しないでください。"
        "役職名も上記の日本語名を使い、player-0などの参加者IDはそのまま使ってください。"
        "自然な短い会話を1〜2文、合計80字程度で書き、質問に答える、理由を添えて反論する、特定の生存者に質問するなどしてください。"
        "新しい内容を加え、自己紹介、空の同意、繰り返しを避けてください。"
        "公開された議論、サーバの事実、自分の非公開情報だけを根拠にしてください。"
        "秘密チャットの本文を公開チャットへコピーしないでください。"
        "自分の本当の役職を公表する場合は、今の盤面で必要な理由があるときだけです。"
        "人狼陣営は、序盤に本当の役職・陣営・仲間・自分の襲撃を公表せず、村側として議論し騙ってください。"
        "役職COはゲームのルール上禁止されていません。隠すのは戦略のためで、COをルール違反と呼ばないでください。"
        "終盤、公開の仲間COと通知された生存する仲間で投票の過半数を取れるなら、名乗って票を合わせられます。"
        "狂人など人間の人狼陣営は、生存3人以下の終盤なら名乗って人狼に票合わせを呼びかけられます。"
        "狩人は、他人から自分への処刑・投票提案が出た場合や対抗狩人COがある場合に限り、COして処刑回避や対抗できます。"
        "占い師・霊能者は自分の役職と受け取った結果を公表できます。"
        "通常の発言では発言本文だけを出力してください。選択を求められた場合は指定されたJSONだけを返し、IDを翻訳しないでください。"
    )
    discussion = [c for c in state.chats if c["channel"] == channel and c["day"] == state.day][-40:]
    # Hidden channels must never enter the public discussion context.
    user = (
        f"第{state.day}日、フェーズID: {state.phase}。生存者: {', '.join(sorted(state.alive))}。"
        f"死者: {', '.join(sorted(set(state.players) - state.alive)) or 'なし'}。\n"
        f"サーバが公開した最近の事実: {recent_json(state.facts[-30:], 2000)}\n"
        f"今日の最近のチャット（チャンネルID: {channel}）: {recent_json(discussion, 3500)}\n"
        f"{question}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
