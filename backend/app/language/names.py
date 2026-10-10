"""Recognize a language *name* typed in any language ("Español", "العربية", "हिंदी", "Hindi").

Used for the language switch: a user sends just a language name and we reply in it from then
on. Names are matched after Unicode normalization and casefolding.
"""

import unicodedata

# code: (English name, autonym, other common spellings / names in other major languages)
LANGUAGES: dict[str, tuple[str, ...]] = {
    "en": (
        "English",
        "inglés",
        "inglês",
        "anglais",
        "अंग्रेज़ी",
        "अंग्रेजी",
        "الإنجليزية",
        "英语",
        "английский",
        "inggris",
        "kiingereza",
    ),
    "es": (
        "Spanish",
        "Español",
        "castellano",
        "espanhol",
        "espagnol",
        "स्पेनिश",
        "الإسبانية",
        "西班牙语",
        "испанский",
        "spanyol",
        "kihispania",
    ),
    "pt": (
        "Portuguese",
        "Português",
        "portugués",
        "portugais",
        "पुर्तगाली",
        "البرتغالية",
        "葡萄牙语",
        "португальский",
        "portugis",
        "kireno",
    ),
    "fr": (
        "French",
        "Français",
        "francés",
        "francês",
        "फ़्रेंच",
        "फ्रेंच",
        "الفرنسية",
        "法语",
        "французский",
        "prancis",
        "kifaransa",
    ),
    "de": (
        "German",
        "Deutsch",
        "alemán",
        "alemão",
        "allemand",
        "जर्मन",
        "الألمانية",
        "德语",
        "немецкий",
        "jerman",
    ),
    "it": ("Italian", "Italiano", "italien", "इतालवी", "الإيطالية", "意大利语", "итальянский"),
    "ar": (
        "Arabic",
        "العربية",
        "عربي",
        "árabe",
        "arabe",
        "अरबी",
        "阿拉伯语",
        "арабский",
        "arab",
        "kiarabu",
    ),
    "hi": ("Hindi", "हिंदी", "हिन्दी", "hindī", "हिंदि", "الهندية", "印地语", "хинди"),
    "ur": ("Urdu", "اردو", "उर्दू", "الأردية", "乌尔都语", "урду"),
    "bn": ("Bengali", "বাংলা", "Bangla", "बंगाली", "البنغالية", "孟加拉语", "бенгальский"),
    "pa": ("Punjabi", "ਪੰਜਾਬੀ", "پنجابی", "पंजाबी"),
    "gu": ("Gujarati", "ગુજરાતી", "गुजराती"),
    "mr": ("Marathi", "मराठी"),
    "ta": ("Tamil", "தமிழ்", "तमिल"),
    "te": ("Telugu", "తెలుగు", "तेलुगु"),
    "kn": ("Kannada", "ಕನ್ನಡ", "कन्नड़"),
    "ml": ("Malayalam", "മലയാളം", "मलयालम"),
    "ne": ("Nepali", "नेपाली"),
    "si": ("Sinhala", "සිංහල"),
    "zh": (
        "Chinese",
        "中文",
        "汉语",
        "漢語",
        "普通话",
        "chino",
        "chinês",
        "chinois",
        "चीनी",
        "الصينية",
        "китайский",
        "mandarin",
        "tionghoa",
        "kichina",
    ),
    "ja": (
        "Japanese",
        "日本語",
        "japonés",
        "japonais",
        "जापानी",
        "اليابانية",
        "японский",
        "jepang",
    ),
    "ko": (
        "Korean",
        "한국어",
        "coreano",
        "coréen",
        "कोरियाई",
        "الكورية",
        "韩语",
        "корейский",
        "korea",
    ),
    "ru": (
        "Russian",
        "Русский",
        "ruso",
        "russo",
        "russe",
        "रूसी",
        "الروسية",
        "俄语",
        "rusia",
        "kirusi",
    ),
    "uk": ("Ukrainian", "Українська", "украинский"),
    "pl": ("Polish", "Polski"),
    "tr": ("Turkish", "Türkçe", "turco", "turc", "التركية", "турецкий"),
    "fa": ("Persian", "فارسی", "Farsi", "الفارسية", "персидский"),
    "id": (
        "Indonesian",
        "Bahasa Indonesia",
        "Indonesia",
        "indonesio",
        "indonésien",
        "इंडोनेशियाई",
        "الإندونيسية",
        "印尼语",
        "индонезийский",
    ),
    "ms": ("Malay", "Bahasa Melayu", "Melayu"),
    "tl": ("Filipino", "Tagalog", "Wikang Filipino"),
    "vi": ("Vietnamese", "Tiếng Việt", "vietnamita", "vietnamien", "越南语", "вьетнамский"),
    "th": ("Thai", "ภาษาไทย", "ไทย", "tailandés", "泰语", "тайский"),
    "km": ("Khmer", "ភាសាខ្មែរ", "ខ្មែរ"),
    "my": ("Burmese", "မြန်မာ", "Myanmar"),
    "sw": (
        "Swahili",
        "Kiswahili",
        "suajili",
        "souahéli",
        "स्वाहिली",
        "السواحيلية",
        "斯瓦希里语",
        "суахили",
    ),
    "am": ("Amharic", "አማርኛ"),
    "ha": ("Hausa", "Harshen Hausa"),
    "yo": ("Yoruba", "Yorùbá", "Èdè Yorùbá"),
    "ig": ("Igbo", "Asụsụ Igbo"),
    "zu": ("Zulu", "isiZulu"),
    "xh": ("Xhosa", "isiXhosa"),
    "so": ("Somali", "Soomaali", "Af-Soomaali"),
    "el": ("Greek", "Ελληνικά"),
    "he": ("Hebrew", "עברית"),
    "nl": ("Dutch", "Nederlands"),
    "ro": ("Romanian", "Română"),
    "hu": ("Hungarian", "Magyar"),
    "cs": ("Czech", "Čeština"),
    "sv": ("Swedish", "Svenska"),
    "fi": ("Finnish", "Suomi"),
    "da": ("Danish", "Dansk"),
    "no": ("Norwegian", "Norsk"),
    "ka": ("Georgian", "ქართული"),
    "hy": ("Armenian", "Հայերեն"),
    "az": ("Azerbaijani", "Azərbaycan dili", "Azərbaycanca"),
    "kk": ("Kazakh", "Қазақ тілі", "Қазақша"),
    "uz": ("Uzbek", "Oʻzbekcha", "O'zbek tili"),
    "ps": ("Pashto", "پښتو"),
    "ht": ("Haitian Creole", "Kreyòl ayisyen", "Kreyòl"),
    "qu": ("Quechua", "Runa Simi"),
    "gn": ("Guarani", "Avañe'ẽ"),
}

_PREFIXES = ("in ", "en ", "em ", "auf ", "reply in ", "speak ", "habla ", "fala ")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    text = text.strip(" .!?¿¡。！？،")
    for p in _PREFIXES:
        if text.startswith(p):
            text = text[len(p) :]
    return text.strip()


_INDEX: dict[str, str] = {}
for _code, _names in LANGUAGES.items():
    for _n in _names:
        _INDEX.setdefault(_norm(_n), _code)
    _INDEX.setdefault(_code, _code)


def parse_language_name(text: str) -> str | None:
    """Language code if the whole message is just a language name, else None."""
    if len(text) > 40 or "\n" in text:
        return None
    return _INDEX.get(_norm(text))


def english_name(code: str) -> str:
    names = LANGUAGES.get(code.split("-")[0])
    return names[0] if names else code


def autonym(code: str) -> str:
    names = LANGUAGES.get(code.split("-")[0])
    return names[1] if names and len(names) > 1 else english_name(code)
