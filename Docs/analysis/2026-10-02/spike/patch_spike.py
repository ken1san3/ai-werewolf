"""Apply the prompt/memory changes to spike_live.py (scratch). Fails loudly if an anchor is missing."""
from pathlib import Path

p = Path("spike_live.py")
s = p.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    if s.count(old) != 1:
        raise SystemExit(f"anchor not found exactly once: {old[:60]!r} (count={s.count(old)})")
    s = s.replace(old, new)


# A. game-flow rules in the system prompt
sub('f"Your secret role: {ROLE_TEXT[self.role]}',
    '"Game flow: Night 0 -> Day 1 discussion -> Day 1 vote -> Night 1 -> Day 2 discussion -> Day 2 vote -> ... "\n'
    '                "Night 0 has ALREADY happened before Day 1: on night 0 the seer received one free result (a random player who is NOT a werewolf). "\n'
    '                "Each day the discussion ends with a vote, and the most-voted player is executed. "\n'
    '                "Each night the werewolves kill one player, the seer inspects one player, the guard protects one player, "\n'
    '                "and the medium learns whether the previous day\'s executed player was a werewolf. Dead players\' roles are not revealed.\\n\\n"\n'
    '                f"Your secret role: {ROLE_TEXT[self.role]}')

# B. no empty agreement
sub('"- Do not introduce yourself and do not restate your role in every message. Do not repeat what was already said.\\n"',
    '"- Do not introduce yourself and do not restate your role in every message. Do not repeat what was already said.\\n"\n'
    '                "- Every message must add something new: a suspicion with a reason, a direct question to one player, an answer, a role claim, or a vote proposal. No empty agreement.\\n"')

# C. today's chat only, dead players marked, time left, earlier days summarised by the public facts
sub('        lines = [f"[Day {d}] {sp}: {tx}" for d, ch, sp, tx in self.transcript if ch == channel][-40:]\n'
    '        chat = "\\n".join(lines) or "(nobody has spoken yet today)"\n',
    '        dead_set = set(dead)\n'
    '        lines = [f"{sp}{\' (now dead)\' if sp in dead_set else \'\'}: {tx}" for d, ch, sp, tx in self.transcript if ch == channel and d == self.day][-40:]\n'
    '        chat = "\\n".join(lines) or "(nobody has spoken yet today)"\n'
    '        earlier = sum(1 for d, ch, sp, tx in self.transcript if ch == channel and d < self.day)\n'
    '        left = int(max(self.seconds_left(), 0))\n')
sub('''({'daytime discussion' if self.phase == 'day' else self.phase})''',
    '''({f"daytime discussion, about {left} seconds left before today's vote" if self.phase == 'day' else self.phase})''')
sub('                f"Chat so far (oldest first):\\n{chat}")',
    '                f"Earlier days had {earlier} chat messages (not shown); rely on the public facts for what happened.\\n"\n'
    '                "Dead players cannot speak, vote or use abilities. Never ask a dead player for anything.\\n\\n"\n'
    '                f"Today\'s chat so far (oldest first):\\n{chat}")')

# D. near-duplicate filter against the last 15 public messages
sub('                if candidate and norm(candidate) != (self.last_text or "")',
    '                recent = [tx for d, ch, sp, tx in self.transcript if ch == "public"][-15:]\n'
    '                if any(trigram_jaccard(candidate, r) > 0.45 for r in recent):\n'
    '                    continue\n'
    '                if candidate and norm(candidate) != (self.last_text or "")')
sub('class Shared:',
    'def trigram_jaccard(a: str, b: str) -> float:\n'
    '    def grams(t: str) -> set:\n'
    '        w = re.findall(r"[\\w-]+", norm(t))\n'
    '        return {tuple(w[i:i + 3]) for i in range(max(len(w) - 2, 0))}\n'
    '    x, y = grams(a), grams(b)\n'
    '    return len(x & y) / len(x | y) if x and y else 0.0\n\n\n'
    'class Shared:')

p.write_text(s, encoding="utf-8")
print("patched OK")
