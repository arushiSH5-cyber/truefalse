"""Shareable correction card (1080x1080 PNG) with verdict, short explanation and top source.

Polite, non-shaming design: no "FAKE!" stamps, no mention of who shared it.
Non-Latin scripts need Noto fonts (Docker installs them); Arabic/Indic shaping needs libraqm.
"""

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

from app.language.scripts import dominant_script

SIZE = 1080
PAD = 72

ASSETS = Path(__file__).parent / "assets"  # the buddy for each verdict
PAPER = "#fffaf4"
INK = "#4a332d"
SOFT = "#725950"

COLORS = {  # background band, text on band (same palette as the web app)
    "true": ("#c3ecce", INK),
    "misleading": ("#ffe8a3", INK),
    "false": ("#f9d2dc", INK),
    "unverified": ("#decff4", INK),
}
EMOJI_FREE_LABEL = {
    "true": "TRUE",
    "misleading": "MISLEADING",
    "false": "FALSE",
    "unverified": "UNVERIFIED",
}

# Noto font file per script (Debian/Ubuntu fonts-noto-core + fonts-noto-cjk layout).
SCRIPT_FONTS = {
    "Latin": "NotoSans-Regular.ttf",
    "Cyrillic": "NotoSans-Regular.ttf",
    "Greek": "NotoSans-Regular.ttf",
    "Arabic": "NotoSansArabic-Regular.ttf",
    "Hebrew": "NotoSansHebrew-Regular.ttf",
    "Devanagari": "NotoSansDevanagari-Regular.ttf",
    "Bengali": "NotoSansBengali-Regular.ttf",
    "Gurmukhi": "NotoSansGurmukhi-Regular.ttf",
    "Gujarati": "NotoSansGujarati-Regular.ttf",
    "Tamil": "NotoSansTamil-Regular.ttf",
    "Telugu": "NotoSansTelugu-Regular.ttf",
    "Kannada": "NotoSansKannada-Regular.ttf",
    "Malayalam": "NotoSansMalayalam-Regular.ttf",
    "Sinhala": "NotoSansSinhala-Regular.ttf",
    "Thai": "NotoSansThai-Regular.ttf",
    "Khmer": "NotoSansKhmer-Regular.ttf",
    "Myanmar": "NotoSansMyanmar-Regular.ttf",
    "Ethiopic": "NotoSansEthiopic-Regular.ttf",
    "Georgian": "NotoSansGeorgian-Regular.ttf",
    "Armenian": "NotoSansArmenian-Regular.ttf",
    "Han": "NotoSansCJK-Regular.ttc",
    "Japanese": "NotoSansCJK-Regular.ttc",
    "Hangul": "NotoSansCJK-Regular.ttc",
}
WIDE_SCRIPTS = {"Han", "Japanese", "Hangul"}  # no spaces between words: wrap by characters


def _font(
    font_dir: Path, script: str | None, size: int
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = SCRIPT_FONTS.get(script or "Latin", "NotoSans-Regular.ttf")
    candidates = [
        font_dir / name,
        font_dir.parent / "opentype" / "noto" / name,
        Path("/usr/share/fonts/opentype/noto") / name,
    ]
    layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size, layout_engine=layout)
    return ImageFont.load_default(size=size)


def _wrap(text: str, script: str | None, width: int) -> list[str]:
    if script in WIDE_SCRIPTS:
        return [text[i : i + width // 2] for i in range(0, len(text), width // 2)]
    return textwrap.wrap(text, width=width)


def render_card(
    *,
    verdict: str,
    verdict_label: str,
    claim: str,
    explanation: str,
    source: str | None,
    font_dir: Path,
) -> bytes:
    band, band_text = COLORS.get(verdict, COLORS["unverified"])
    script = dominant_script(claim + explanation) or "Latin"
    rtl = script in ("Arabic", "Hebrew")
    img = Image.new("RGB", (SIZE, SIZE), PAPER)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, SIZE, 230], fill=band)
    d.line([0, 230, SIZE, 230], fill=INK, width=4)
    label = verdict_label.upper()[:28]
    label_script = dominant_script(verdict_label)
    size = 84
    room = SIZE - 2 * PAD - 220  # the buddy sits on the right
    while size > 36 and d.textlength(label, font=_font(font_dir, label_script, size)) > room:
        size -= 6
    d.text((PAD, 112), label, font=_font(font_dir, label_script, size), fill=band_text, anchor="lm")
    buddy = ASSETS / f"{verdict if verdict in COLORS else 'unverified'}.png"
    if buddy.exists():
        with Image.open(buddy) as sticker:
            rgba = sticker.convert("RGBA")
            img.paste(rgba, (SIZE - PAD - rgba.width + 24, 5), rgba)

    y = 260
    small = _font(font_dir, script, 34)
    for line in _wrap("“" + claim[:220] + "”", script, 52)[:4]:
        y += 46
        d.text(
            (SIZE - PAD if rtl else PAD, y),
            line,
            font=small,
            fill=SOFT,
            anchor="ra" if rtl else "la",
        )
    y += 40

    body = _font(font_dir, script, 40)
    for line in _wrap(explanation[:420], script, 44)[:9]:
        y += 54
        d.text(
            (SIZE - PAD if rtl else PAD, y),
            line,
            font=body,
            fill=INK,
            anchor="ra" if rtl else "la",
        )

    d.line([PAD, SIZE - 190, SIZE - PAD, SIZE - 190], fill="#e8d5cc", width=3)
    meta = _font(font_dir, "Latin", 30)
    if source:
        d.text((PAD, SIZE - 160), f"Source: {source[:60]}", font=meta, fill=INK)
    d.text(
        (PAD, SIZE - 100),
        "Checked with TrueFalse · always verify before you forward",
        font=meta,
        fill=SOFT,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
