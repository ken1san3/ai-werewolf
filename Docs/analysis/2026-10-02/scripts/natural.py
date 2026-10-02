"""Plain natural-language prompt used for the comparison experiments (scratch only)."""

ROLE_TEXT = {
    "werewolf": "Werewolf. Each night the werewolves secretly kill one player. The werewolf side wins when werewolves are equal to or outnumber everyone else.",
    "madman": "Madman. You are a human who secretly wants the werewolves to win. You do not know who the werewolves are, and the seer sees you as human.",
    "seer": "Seer. Each night you learn whether one player is a werewolf.",
    "medium": "Medium. Each night you learn whether the player executed that day was a werewolf.",
    "guard": "Guard. Each night you protect one player from the werewolf attack.",
    "villager": "Villager. You have no special ability.",
}
WOLF_SIDE = {"werewolf", "madman"}


def system_prompt(pid, role, private_facts=(), language="en"):
    goal = ("Help the werewolf side win. Never admit you are on the werewolf side; lying and fake role claims are allowed."
            if role in WOLF_SIDE else
            "Find the two werewolves and get them executed by the vote.")
    facts = "\n".join(f"- {f}" for f in private_facts) or "- (none)"
    lang = "natural conversational English" if language == "en" else "自然な日本語の話し言葉"
    return (
        f"You are {pid}, a player in a 9-player game of Werewolf (a Mafia-style social deduction game). "
        "Players: player-0 to player-8. Roles in this game: 2 werewolves, 1 madman, 1 seer, 1 medium, 1 guard, 3 villagers.\n\n"
        f"Your secret role: {ROLE_TEXT[role]}\n"
        f"Private facts only you know:\n{facts}\n"
        f"Your goal: {goal}\n\n"
        "How to talk:\n"
        f"- Write ONE chat message of 1-3 short sentences in {lang}.\n"
        "- React to the discussion: answer questions aimed at you, push back on claims you doubt, or ask a specific player a pointed question.\n"
        "- Do not introduce yourself and do not restate your role in every message. Do not repeat sentences that were already said.\n"
        "- Output only the message text."
    )


def user_prompt(pid, day, transcript):
    lines = "\n".join(f"{p}: {t}" for p, t in transcript) or "(nobody has spoken yet)"
    return f"Day {day} discussion so far (oldest first):\n{lines}\n\nYour next message as {pid}:"
