"""Repetition checks based on actual text, never model-produced labels."""
from collections import Counter
import re
import unicodedata


# Existing Japanese logs: a response adding a point scored 0.513, while a
# nearly copied refusal scored 0.786. Keep the former and reject the latter.
SIMILARITY_THRESHOLD = 0.55


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def sentences(text):
    return [normalize(s) for s in re.split(r"(?<=[.!?。！？])\s*", text) if normalize(s)]


def similarity(a, b):
    def grams(text):
        text = re.sub(r"player-\d+", "", normalize(text))
        text = "".join(char for char in text if not char.isspace()
                       and not unicodedata.category(char).startswith("P"))
        return {text[i:i + 3] for i in range(len(text) - 2)}
    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x and y else 0


class RepetitionFilter:
    def __init__(self):
        self.last = {}
        self.counts = Counter()

    def rejection_reason(self, player_id, text, recent):
        parts = sentences(text)
        additions = Counter(parts)
        previous_parts = set(sentences(self.last.get(player_id, "")))
        if previous_parts.intersection(parts):
            return "own_previous_sentence"
        if any(self.counts[s] + count > 2 for s, count in additions.items()):
            return "third_sentence"
        if any(similarity(text, previous) > SIMILARITY_THRESHOLD for previous in recent[-15:]):
            return "similarity"
        return None

    def allows(self, player_id, text, recent):
        return bool(text.strip()) and self.rejection_reason(player_id, text, recent) is None

    def reserve(self, player_id, text):
        # Reserve before sending so concurrent players cannot all send the third copy.
        self.last[player_id] = normalize(text)
        self.counts.update(sentences(text))
