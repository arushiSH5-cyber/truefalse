"""Writing-system detection from Unicode (no model needed; works for every script)."""

import unicodedata
from collections import Counter

# Unicode character-name prefixes -> script label.
_PREFIXES = {
    "LATIN": "Latin",
    "CYRILLIC": "Cyrillic",
    "GREEK": "Greek",
    "ARABIC": "Arabic",
    "HEBREW": "Hebrew",
    "DEVANAGARI": "Devanagari",
    "BENGALI": "Bengali",
    "GURMUKHI": "Gurmukhi",
    "GUJARATI": "Gujarati",
    "ORIYA": "Odia",
    "TAMIL": "Tamil",
    "TELUGU": "Telugu",
    "KANNADA": "Kannada",
    "MALAYALAM": "Malayalam",
    "SINHALA": "Sinhala",
    "THAI": "Thai",
    "LAO": "Lao",
    "KHMER": "Khmer",
    "MYANMAR": "Myanmar",
    "TIBETAN": "Tibetan",
    "GEORGIAN": "Georgian",
    "ARMENIAN": "Armenian",
    "ETHIOPIC": "Ethiopic",
    "HANGUL": "Hangul",
    "HIRAGANA": "Japanese",
    "KATAKANA": "Japanese",
    "CJK": "Han",
    "MONGOLIAN": "Mongolian",
    "THAANA": "Thaana",
    "SYRIAC": "Syriac",
    "NKO": "NKo",
    "TIFINAGH": "Tifinagh",
    "CHEROKEE": "Cherokee",
    "CANADIAN SYLLABICS": "Canadian Syllabics",
    "JAVANESE": "Javanese",
    "BALINESE": "Balinese",
    "OL CHIKI": "Ol Chiki",
    "VAI": "Vai",
    "ADLAM": "Adlam",
}


def char_script(ch: str) -> str | None:
    # Count combining marks too: Indic vowel signs carry a lot of the text's "weight".
    if not (ch.isalpha() or unicodedata.category(ch).startswith("M")):
        return None
    name = unicodedata.name(ch, "")
    for prefix, script in _PREFIXES.items():
        if name.startswith(prefix):
            return script
    return "Other"


def script_profile(text: str) -> Counter[str]:
    return Counter(s for s in (char_script(c) for c in text) if s)


def dominant_script(text: str) -> str | None:
    profile = script_profile(text)
    if not profile:
        return None
    # Japanese text mixes kana and Han; report it as Japanese when any kana is present.
    if profile.get("Japanese") and profile.get("Han"):
        return "Japanese"
    return profile.most_common(1)[0][0]


def is_mixed_script(text: str, threshold: float = 0.15) -> bool:
    profile = script_profile(text)
    total = sum(profile.values())
    if total == 0 or len(profile) < 2:
        return False
    second = profile.most_common(2)[1][1]
    return second / total >= threshold


# Scripts mostly used by a single language family; romanized text of these languages is common.
NATIVE_SCRIPT = {
    "hi": "Devanagari",
    "mr": "Devanagari",
    "ne": "Devanagari",
    "bn": "Bengali",
    "pa": "Gurmukhi",
    "gu": "Gujarati",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "si": "Sinhala",
    "ur": "Arabic",
    "ar": "Arabic",
    "fa": "Arabic",
    "ps": "Arabic",
    "ru": "Cyrillic",
    "uk": "Cyrillic",
    "bg": "Cyrillic",
    "sr": "Cyrillic",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "zh": "Han",
    "ja": "Japanese",
    "ko": "Hangul",
    "am": "Ethiopic",
    "ka": "Georgian",
    "hy": "Armenian",
    "km": "Khmer",
    "my": "Myanmar",
}


def looks_romanized(language: str, text: str) -> bool:
    """e.g. Hindi written in Latin letters ("nimbu pani cancer theek karta hai")."""
    native = NATIVE_SCRIPT.get(language.split("-")[0])
    return native is not None and native != "Latin" and dominant_script(text) == "Latin"
