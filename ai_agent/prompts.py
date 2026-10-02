"""Build prompts from public content and one player's received state."""
import json


def messages(state, roles, role_counts, question, channel="public"):
    role = roles[state.role_id]
    system = (
        f"You are {state.player_id}, playing a real-time game of Werewolf. "
        f"Players: {', '.join(state.players)}. Public role counts: {json.dumps(role_counts)}.\n"
        "Flow: Night 0, dawn, day discussion, vote, night, dawn, next day. "
        "Night 0 gives the seer a server-selected non-wolf result before Day 1. "
        "Dead players cannot speak or act; their actual roles are not publicly revealed.\n"
        f"Your secret role: {role.description or role.name}.\n"
        f"Your known teammates: {', '.join(state.teammates) or 'none'}.\n"
        f"Your private results: {state.private_text()}.\n"
        "Write one natural conversational English message of 1-3 short sentences. "
        "Answer questions, challenge a claim with a reason, or ask a specific living player a question. "
        "Add something new. Avoid introductions, empty agreement and repetitions. "
        "Use only facts in the supplied public discussion, server facts or your private information. "
        "Do not copy hidden channel messages into public chat. Output only the requested text."
    )
    discussion = [c for c in state.chats if c["channel"] == channel and c["day"] == state.day][-40:]
    # Hidden channels must never enter the public discussion context.
    user = (
        f"Day {state.day}, phase {state.phase}. Alive: {', '.join(sorted(state.alive))}. "
        f"Dead: {', '.join(sorted(set(state.players) - state.alive)) or 'none'}.\n"
        f"Public server facts: {json.dumps(state.facts[-30:], ensure_ascii=False)}\n"
        f"Today's {channel} discussion: {json.dumps(discussion, ensure_ascii=False)}\n"
        f"{question}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
