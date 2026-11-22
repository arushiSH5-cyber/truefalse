#!/usr/bin/env python3
"""Draws every TrueFalse doodle and buddy emoji as SVG. Original art, drawn in code.

    python3 scripts/make_doodles.py

Edit the shapes here and rerun; don't edit the SVG files by hand.

Buddies:  scout (penguin detective), mochi (frog, True), pudding (chick, Misleading),
          berry (axolotl, False), luna (moon, Unverified)
"""

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INK = "#5b4038"
PAPER = "#fffaf3"
BLUSH = "#f58fa6"
ROSE = "#f6b8c6"
BERRY = "#d95778"
BUTTER = "#ffdc7c"
TANGERINE = "#f5a45d"
LEAF = "#7fc49a"
MINT = "#b9e8c5"
SKY = "#a9d8ee"
LILAC = "#cdb8ec"
NIGHT = "#5c5878"
WHITE = "#ffffff"

# Only what the app shows. A buddy appears where it means something, never as a gallery.
USED = (
    "scout-happy", "scout-think", "scout-sad", "scout-sleep",  # logo, checking, error, waiting
    "mochi-happy", "pudding-think", "berry-wow", "luna-sleep",  # the four verdicts
    "pudding-sleep",  # empty history
    "check", "cross", "wavy", "question", "warning",
    "book", "camera", "photo", "note", "mic", "link", "shield", "magnifier",
    "send", "speaker", "bin", "moon", "sun", "thumb", "flag", "phone", "back", "heart", "star",
)


def num(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def path(d, fill="none", width=3.2, stroke=INK, extra=""):
    line = (
        f' stroke="{stroke}" stroke-width="{num(width)}" stroke-linecap="round" stroke-linejoin="round"'
        if width
        else ""
    )
    return f'<path d="{d}" fill="{fill}"{line}{extra}/>'


def oval(x, y, rx, ry=None, fill=INK, width=0, stroke=INK, extra=""):
    line = f' stroke="{stroke}" stroke-width="{num(width)}"' if width else ""
    ry = rx if ry is None else ry
    return (
        f'<ellipse cx="{num(x)}" cy="{num(y)}" rx="{num(rx)}" ry="{num(ry)}" '
        f'fill="{fill}"{line}{extra}/>'
    )


def turn(shape, degrees, x=60, y=60):
    return f'<g transform="rotate({degrees} {x} {y})">{shape}</g>'


def star(x, y, r, fill=BUTTER, width=2.6, inner=0.52, tips=5):
    pts = []
    for i in range(tips * 2):
        radius = r if i % 2 == 0 else r * inner
        a = math.pi * i / tips - math.pi / 2
        pts.append(f"{num(x + radius * math.cos(a))} {num(y + radius * math.sin(a))}")
    return path("M" + "L".join(pts) + "Z", fill, width)


def heart(x, y, s, fill=BERRY, width=2.6):
    d = (
        f"M{num(x)} {num(y + s)}C{num(x - 1.5 * s)} {num(y)} {num(x - s)} {num(y - 1.2 * s)} "
        f"{num(x)} {num(y - 0.35 * s)}C{num(x + s)} {num(y - 1.2 * s)} {num(x + 1.5 * s)} {num(y)} "
        f"{num(x)} {num(y + s)}Z"
    )
    return path(d, fill, width)


def scallop(x, y, rx, ry, bumps, depth, fill, width=3.2):
    """A fluffy or lacy outline."""
    at = lambda i, grow=1.0: (
        x + rx * grow * math.cos(2 * math.pi * i / bumps),
        y + ry * grow * math.sin(2 * math.pi * i / bumps),
    )
    d = "M{} {}".format(*map(num, at(0)))
    for i in range(bumps):
        d += "Q{} {} {} {}".format(*map(num, (*at(i + 0.5, depth), *at(i + 1))))
    return path(d + "Z", fill, width)


def flower(x, y, r, fill=ROSE, width=2.4):
    return scallop(x, y, r * 0.7, r * 0.7, 5, 1.95, fill, width) + oval(x, y, r * 0.3, fill=BUTTER)


def bow(x, y, s, fill=ROSE):
    left = f"M{x} {y}Q{x - s * 1.6} {y - s * 1.4} {x - s * 1.6} {y}Q{x - s * 1.6} {y + s * 1.4} {x} {y}Z"
    right = f"M{x} {y}Q{x + s * 1.6} {y - s * 1.4} {x + s * 1.6} {y}Q{x + s * 1.6} {y + s * 1.4} {x} {y}Z"
    return path(left, fill, 2.6) + path(right, fill, 2.6) + oval(x, y, s * 0.34, fill=fill, width=2.6)


def face(mood, y=60, gap=15, beak=False, cheeks=True):
    """Eyes, cheeks and mouth, shared so the buddies feel like one family."""
    l, r = 60 - gap, 60 + gap
    out = ""
    if mood == "love":
        out += heart(l, y, 5.2, width=0) + heart(r, y, 5.2, width=0)
    elif mood == "sleep":
        out += path(f"M{l - 5} {y}q5 5 10 0M{r - 5} {y}q5 5 10 0", width=2.8)
    elif mood == "wow":
        for ex in (l, r):
            out += oval(ex, y, 5.2, 6) + oval(ex + 1.6, y - 2.2, 1.9, fill=WHITE)
    else:
        for ex in (l, r):
            out += oval(ex, y, 3.8, 5) + oval(ex + 1.2, y - 1.8, 1.4, fill=WHITE)
        if mood == "think":
            out += path(f"M{l - 4} {y - 11}h7M{r - 4} {y - 10}q4-7 8-3", width=2.4)
        if mood == "sad":
            out += path(f"M{l - 5} {y - 9}l8-3M{r + 5} {y - 9}l-8-3", width=2.4)
            out += path(f"M{r + 6} {y + 7}q6 9 0 11q-6-2 0-11Z", SKY, 2)
    if cheeks:
        out += oval(l - 7, y + 9, 5.4, 3.4, BLUSH, extra=' opacity=".8"')
        out += oval(r + 7, y + 9, 5.4, 3.4, BLUSH, extra=' opacity=".8"')
    m = y + 9
    if beak:
        if mood in ("wow", "love"):
            out += path(f"M53 {m}q7-8 14 0q-7 15-14 0Z", TANGERINE, 2.6)
        else:
            out += path(f"M54 {m}q6-6 12 0q-6 8-12 0Z", TANGERINE, 2.6)
    elif mood == "wow":
        out += oval(60, m + 3, 3.6, 4.6)
    elif mood == "love":
        out += path(f"M54 {m}h12q-1 10-6 10q-5 0-6-10Z", BERRY, 2.6)
    elif mood == "sad":
        out += path(f"M54 {m + 5}q6-7 12 0", width=2.8)
    elif mood == "sleep":
        out += oval(60, m + 3, 2.4, 2.8)
    elif mood == "think":
        out += path(f"M55 {m + 3}q5-3 10 1", width=2.8)
    else:
        out += path(f"M53 {m}q7 8 14 0", width=2.8)
    return out


def extras(mood):
    if mood == "love":
        return heart(104, 26, 8, ROSE) + heart(14, 40, 5, BLUSH, 2)
    if mood == "wow":
        return path("M101 34l9-7M98 24l2-10M19 34l-9-7M22 24l-2-10", width=3)
    if mood == "think":
        return path("M96 22q1-11 10-9q9 4 1 11q-5 3-4 7", width=3.4) + oval(103, 38, 2.4)
    if mood == "sleep":
        return path("M94 30h9l-9 10h9M106 12h7l-7 8h7", width=2.6)
    return ""


def scout(mood):
    body = oval(46, 108, 9, 5, TANGERINE, 3) + oval(74, 108, 9, 5, TANGERINE, 3)
    body += path("M26 72Q10 80 14 96Q22 98 30 88Z", NIGHT) + path("M94 72Q110 80 106 96Q98 98 90 88Z", NIGHT)
    body += path("M60 20C90 20 99 49 98 76C97 99 83 107 60 107C37 107 23 99 22 76C21 49 30 20 60 20Z", NIGHT)
    body += path("M60 48C64 35 88 37 88 60C88 75 78 80 72 82C83 103 37 103 48 82C42 80 32 75 32 60C32 37 56 35 60 48Z", PAPER, 0)
    body += path("M29 80Q60 92 91 80L91 88Q60 100 29 88Z", BUTTER, 2.8)
    body += path("M76 92l4 12 9-3-5-12Z", BUTTER, 2.8) + star(60, 91, 4.5, BERRY, 0)
    # a detective's beret
    body += path("M30 34C24 14 52 7 66 10C88 12 98 26 90 34C70 27 50 27 30 34Z", ROSE)
    body += path("M62 10l3-6", width=3.4) + star(80, 23, 6, PAPER, 2)
    return body + face(mood, 60, 16, beak=True) + extras(mood)


def mochi(mood):
    body = oval(38, 105, 13, 6, MINT, 3) + oval(82, 105, 13, 6, MINT, 3)
    body += path(
        "M60 33C64 33 68 34 71 35C74 22 97 22 98 43C112 58 112 104 60 104C8 104 8 58 22 43C23 22 46 22 49 35C52 34 56 33 60 33Z",
        MINT,
    )
    body += path("M36 101C32 75 88 75 84 101Q60 107 36 101Z", PAPER, 0)
    body += oval(27, 78, 3, fill=LEAF) + oval(92, 81, 2.4, fill=LEAF) + oval(87, 88, 3.4, fill=LEAF)
    body += path("M60 33q-1-9 0-15", width=3) + path("M60 21q-16 3-15-11q13-2 15 11Z", LEAF, 2.6)
    body += path("M60 19q14 1 14-11q-12-2-14 11Z", MINT, 2.6)
    return body + face(mood, 52, 24) + extras(mood)


def pudding(mood):
    body = path("M48 100v9m-5 1l5-3 5 3M72 100v9m-5 1l5-3 5 3", stroke=TANGERINE, width=3.4)
    body += path("M55 24q-9-16 3-16q3 6 2 12q4-12 12-9q2 9-8 15Z", BUTTER, 2.8)
    body += scallop(60, 63, 40, 38, 17, 1.12, BUTTER)
    body += path("M38 88q22 14 44 0", width=3, stroke="#f2c65a")
    body += path("M24 70q-12 8-5 16q8-1 10-8M96 70q12 8 5 16q-8-1-10-8", BUTTER, 2.8)
    return body + turn(bow(89, 35, 8), 22, 89, 35) + face(mood, 57, 16, beak=True) + extras(mood)


def berry(mood):
    gills = ""
    for y, tip, size in ((43, 24, 8), (60, 58, 9), (76, 89, 7)):
        gills += path(f"M28 {y}Q18 {tip} 11 {tip}", width=7, stroke=BERRY) + scallop(13, tip, size, size * 0.8, 7, 1.5, BLUSH, 2.4)
        gills += path(f"M92 {y}Q102 {tip} 109 {tip}", width=7, stroke=BERRY) + scallop(107, tip, size, size * 0.8, 7, 1.5, BLUSH, 2.4)
    body = gills + path("M60 22C90 22 101 43 101 63C101 79 92 88 81 90C88 111 32 111 39 90C28 88 19 79 19 63C19 43 30 22 60 22Z", ROSE)
    body += oval(60, 98, 11, 6, PAPER) + flower(79, 31, 10, PAPER)
    body += oval(38, 43, 2.4, fill=BLUSH) + oval(46, 36, 1.8, fill=BLUSH)
    return body + face(mood, 58, 18) + extras(mood)


def luna(mood):
    body = path("M60 18C88 18 104 39 104 63C104 88 86 106 60 106C34 106 16 88 16 63C16 39 32 18 60 18Z", LILAC)
    body += path("M60 18C34 18 16 39 16 63C16 88 34 106 60 106C43 92 36 79 36 62C36 43 45 29 60 18Z", "#bfa5e3", 0)
    body += path("M60 18C88 18 104 39 104 63C104 88 86 106 60 106C34 106 16 88 16 63C16 39 32 18 60 18Z")
    body += oval(84, 88, 6, 4, "#b79cdd") + oval(44, 94, 4, 3, "#a98fd2") + oval(95, 72, 3, fill="#b79cdd")
    body += path("M28 36C34 8 78 0 96 24Q109 30 108 50Q97 38 88 32C68 22 48 24 28 36Z", NIGHT) + star(107, 53, 9)
    body += star(54, 19, 4, PAPER, 0) + star(70, 15, 3, PAPER, 0)
    return body + face(mood, 60, 17) + extras(mood)


BUDDIES = {"scout": scout, "mochi": mochi, "pudding": pudding, "berry": berry, "luna": luna}


def objects():
    round_badge = lambda fill: scallop(60, 60, 44, 44, 12, 1.15, fill)
    house = path("M20 54L60 20L100 54", width=4) + path("M28 52V98Q28 102 32 102H88Q92 102 92 98V52L60 25Z", ROSE)
    house += path("M50 102V79q10-15 20 0v23", BUTTER) + heart(60, 52, 8, PAPER)
    book = path("M22 24Q42 16 60 28Q78 16 98 24V94Q78 86 60 98Q42 86 22 94Z", LILAC)
    book += path("M30 32Q45 27 57 35V86Q45 78 30 84ZM90 32Q75 27 63 35V86Q75 78 90 84Z", PAPER, 2.4)
    book += heart(44, 56, 8) + star(76, 56, 9)
    camera = path("M42 34l6-13h24l6 13Z", LILAC)
    camera += path("M22 34H98Q108 34 108 44V90Q108 100 98 100H22Q12 100 12 90V44Q12 34 22 34Z", ROSE)
    camera += oval(60, 67, 23, fill=PAPER, width=3.2) + oval(60, 67, 13, fill=SKY, width=3.2)
    camera += oval(64, 62, 4, fill=WHITE) + oval(93, 48, 4, fill=BUTTER, width=2.4) + heart(27, 49, 5, BERRY, 0)
    photo = turn(path("M26 22h66v76H26Z", BUTTER), 8) + turn(
        path("M24 18h66v82H24Z", PAPER) + path("M32 26h50v48H32Z", SKY, 2.4)
        + path("M32 74l16-22 12 14 8-9 14 17Z", MINT, 2.4) + oval(70, 37, 6, fill=BUTTER), -7
    )
    note = path("M26 16H94Q100 16 100 22V78L78 104H26Q20 104 20 98V22Q20 16 26 16Z", BUTTER)
    note += path("M100 78H78V104", PAPER) + path("M34 40h50M34 54h44M34 68h30", width=3)
    mic = path("M44 30C44 8 76 8 76 30V58C76 80 44 80 44 58Z", LILAC)
    mic += path("M44 36h14M44 50h14", width=2.6) + path("M30 54Q30 90 60 90Q90 90 90 54M60 90v16M44 106h32")
    link = turn(path("M56 44H30a16 16 0 0 0 0 32h26", width=14) + path("M56 44H30a16 16 0 0 0 0 32h26", width=8, stroke=ROSE)
                + path("M64 44h26a16 16 0 0 1 0 32H64", width=14) + path("M64 44h26a16 16 0 0 1 0 32H64", width=8, stroke=SKY)
                + path("M44 60h32", width=10), -24)
    magnifier = path("M76 76l28 28", width=14) + path("M78 78l24 24", width=7, stroke=ROSE)
    magnifier += oval(52, 52, 34, fill=ROSE, width=3.4) + oval(52, 52, 24, fill=SKY, width=3) + star(52, 52, 11)
    receipt = path("M28 14H92V106l-8-6-8 6-8-6-8 6-8-6-8 6-8-6-8 6Z", PAPER)
    receipt += heart(60, 35, 8) + path("M40 56h40M40 68h40M40 80h22", width=2.8)
    send = path("M14 58L106 18L86 100L56 78Z", SKY) + path("M56 78L106 18L44 70L46 98L64 84", PAPER)
    speaker = path("M18 46h20l28-24v76L38 74H18Z", MINT) + path("M80 42q16 18 0 36M92 28q28 32 0 64")
    copy = path("M44 16h52v66H44Z", LILAC) + path("M24 36h52v68H24Z", PAPER) + heart(50, 70, 11)
    bin_ = path("M30 40h60l-6 64H36Z", MINT) + path("M22 26h76v14H22ZM48 26v-9h24v9", LEAF)
    bin_ += heart(60, 70, 11, PAPER)
    cloud = path("M28 88C4 88 6 56 28 56C26 28 66 22 76 46C96 38 112 54 108 70C106 88 92 88 92 88Z", SKY)
    cloud += oval(44, 66, 3, 4) + oval(74, 66, 3, 4) + path("M53 74q6 6 12 0", width=2.6)
    moon = path("M78 16C40 10 14 34 14 62C14 94 46 112 80 102C40 92 34 36 78 16Z", BUTTER) + star(90, 50, 11, LILAC)
    sun = scallop(60, 60, 38, 38, 12, 1.4, TANGERINE) + oval(60, 60, 30, fill=BUTTER, width=3)
    sun += oval(51, 56, 2.8, 4) + oval(69, 56, 2.8, 4) + path("M53 67q7 7 14 0", width=2.6)
    cherry = path("M38 74Q52 36 72 20Q76 48 80 78", width=3.4) + path("M72 20q18-12 32 4q-18 14-32-4Z", LEAF)
    cherry += oval(36, 84, 19, fill=BERRY, width=3.2) + oval(82, 88, 18, fill=BLUSH, width=3.2) + oval(30, 78, 4, fill=WHITE)
    cake = path("M28 64h64l-10 40H38Z", LILAC) + path("M46 66l3 36M74 66l-3 36", width=2.6)
    cake += path("M26 66C10 44 36 36 40 42C36 18 84 18 80 42C86 36 110 46 94 66Q60 76 26 66Z", ROSE)
    cake += oval(60, 22, 9, fill=BERRY, width=2.8) + oval(46, 50, 2.4, fill=BUTTER) + oval(70, 54, 2.4, fill=MINT)
    planet = oval(60, 60, 32, fill=LILAC, width=3.2) + turn(path("M30 52C-8 66 10 80 60 76C110 72 126 52 90 46", width=4), -8)
    planet += oval(50, 46, 5, fill=WHITE)
    tulip = path("M60 70v36", stroke=LEAF, width=5) + path("M60 100q-30 4-30-22q24 0 30 22ZM60 96q28 0 28-22q-24 2-28 22Z", MINT)
    tulip += path("M60 72C28 72 30 34 34 24L48 38L60 18L72 38L86 24C90 34 92 72 60 72Z", ROSE)
    warning = path("M52 20q8-10 16 0l40 70q4 12-10 14H22q-14-2-10-14Z", BUTTER) + path("M60 44v26", width=6) + oval(60, 86, 4.4)
    thumb = path("M16 56h22v46H16Z", LILAC)
    thumb += path("M38 58C56 46 56 34 58 20C76 12 82 34 74 48H98C112 50 108 68 102 70C110 78 100 88 96 88C100 100 88 102 84 102H54L38 96Z", ROSE)
    flag = path("M30 106V16", width=5) + path("M30 20Q52 8 68 22T102 22V68Q84 80 66 68T30 66Z", TANGERINE) + heart(66, 45, 10, PAPER)
    phone = path("M40 10H80Q90 10 90 20V100Q90 110 80 110H40Q30 110 30 100V20Q30 10 40 10Z", LILAC)
    phone += path("M38 24h44v68H38Z", PAPER, 2.4) + path("M60 38v30m-12-11l12 12 12-12", width=4)
    back = round_badge(ROSE) + path("M84 60H38m18-18L38 60l18 18", width=6)
    shield = path("M60 12L100 28V60C100 88 80 102 60 110C40 102 20 88 20 60V28Z", SKY) + heart(60, 58, 17, PAPER)
    return {
        "check": round_badge(MINT) + path("M38 62l15 16 30-36", width=9),
        "cross": round_badge(ROSE) + path("M42 42l36 36M78 42L42 78", width=9),
        "wavy": round_badge(BUTTER) + path("M36 66q12-22 24-6t24-6", width=9),
        "question": round_badge(LILAC) + path("M45 46q2-18 18-16q20 6 4 22q-8 6-7 16", width=8) + oval(60, 85, 5),
        "home": house, "book": book, "camera": camera, "photo": photo, "note": note, "mic": mic,
        "link": link, "magnifier": magnifier, "receipt": receipt, "send": send, "speaker": speaker,
        "copy": copy, "bin": bin_, "cloud": cloud, "moon": moon, "sun": sun, "cherry": cherry,
        "cupcake": cake, "planet": planet, "tulip": tulip, "warning": warning, "thumb": thumb,
        "flag": flag, "phone": phone, "back": back, "shield": shield,
        "heart": heart(60, 58, 40, ROSE, 3.4),
        "star": star(60, 64, 48, BUTTER, 3.4, 0.56),
        "flower": flower(60, 60, 44, ROSE, 3.4),
        "bow": bow(60, 60, 30),
    }


def svg(inner):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -4 128 128">{inner}</svg>\n'


def main():
    drawn = objects()
    files = {}
    for name in USED:
        who, _, mood = name.partition("-")
        files[name] = BUDDIES[who](mood) if who in BUDDIES else drawn[name]
    web = ROOT / "web" / "public" / "doodles"
    extension = ROOT / "extension" / "doodles"
    web.mkdir(parents=True, exist_ok=True)
    extension.mkdir(parents=True, exist_ok=True)
    for name, inner in files.items():
        (web / f"{name}.svg").write_text(svg(inner))
    for name in (
        "scout-happy", "scout-think", "scout-sad", "mochi-happy", "pudding-think", "berry-wow",
        "luna-sleep", "check", "cross", "wavy", "question", "warning", "thumb", "flag", "send",
    ):
        (extension / f"{name}.svg").write_text(svg(files[name]))
    print(f"{len(files)} doodles")


if __name__ == "__main__":
    main()
