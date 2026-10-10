"""Language-agnostic tokenization + Okapi BM25 (used by the in-memory index and for fusion)."""

import math
import unicodedata
from collections import Counter
from collections.abc import Sequence


def _is_word_char(ch: str) -> bool:
    # Letters, numbers and combining marks. Python's \w misses marks such as Devanagari,
    # Tamil and Bengali vowel signs, which would split words apart.
    return unicodedata.category(ch)[0] in "LNM"


def _words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for ch in text:
        if _is_word_char(ch):
            current.append(ch)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def _is_unspaced_script(ch: str) -> bool:
    # Han, Hiragana/Katakana, Thai, Lao, Khmer, Myanmar: written without spaces between words.
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3040 <= cp <= 0x30FF
        or 0x0E00 <= cp <= 0x0EFF
        or 0x1780 <= cp <= 0x17FF
        or 0x1000 <= cp <= 0x109F
    )


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for word in _words(text):
        if any(_is_unspaced_script(c) for c in word):
            # Character bigrams are a robust, dictionary-free fallback for unspaced scripts.
            chars = [c for c in word if not c.isspace()]
            tokens += (
                chars
                if len(chars) < 2
                else ["".join(p) for p in zip(chars, chars[1:], strict=False)]
            )
        elif len(word) > 1 or word.isdigit():
            tokens.append(word)
    return tokens


class BM25:
    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._tfs = [Counter(tokenize(d)) for d in docs]
        self._lens = [sum(tf.values()) for tf in self._tfs]
        self._avg = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        df: Counter[str] = Counter()
        for tf in self._tfs:
            df.update(tf.keys())
        n = len(self._tfs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out: list[float] = []
        for tf, length in zip(self._tfs, self._lens, strict=True):
            s = 0.0
            for term in q:
                f = tf.get(term)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * length / (self._avg or 1))
                s += self._idf[term] * f * (self.k1 + 1) / denom
            out.append(s)
        return out
