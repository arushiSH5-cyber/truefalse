"""Strip personal data from text before it is sent to any external search API."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://\S+")
_HANDLE = re.compile(r"(?<![\w@])@\w{3,}")
_NUMBERISH = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")


def _phone(match: re.Match[str]) -> str:
    # Phone numbers have many digits; years, ranges and statistics do not.
    digits = sum(c.isdigit() for c in match.group(0))
    return "[number]" if digits >= 9 else match.group(0)


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _URL.sub("[link]", text)
    text = _HANDLE.sub("[handle]", text)
    return _NUMBERISH.sub(_phone, text)
