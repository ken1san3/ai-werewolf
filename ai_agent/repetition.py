"""Repetition checks based on actual text, never model-produced labels."""
from collections import Counter
import re
import unicodedata


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def sentences(text):
    return [normalize(s) for s in re.split(r"(?<=[.!?。！？])\s*", text) if normalize(s)]


def similarity(a, b):
    def grams(text):
        words = re.findall(r"[\w-]+", normalize(text))
        return {tuple(words[i:i + 3]) for i in range(max(len(words) - 2, 0))}
    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x and y else 0


class RepetitionFilter:
    def __init__(self):
        self.last = {}
        self.counts = Counter()

    def allows(self, player_id, text, recent):
        parts = sentences(text)
        additions = Counter(parts)
        return bool(text.strip()) and normalize(text) != self.last.get(player_id) and (
            all(self.counts[s] + count <= 2 for s, count in additions.items())
            and not any(similarity(text, previous) > 0.45 for previous in recent[-15:])
        )

    def reserve(self, player_id, text):
        # Reserve before sending so concurrent players cannot all send the third copy.
        self.last[player_id] = normalize(text)
        self.counts.update(sentences(text))
