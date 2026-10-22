"""Temporary media handling: everything lives in a per-request temp dir that is always deleted."""

import base64
import io
import math
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import imagehash
from PIL import Image, ImageOps

from app.core.llm import ImageInput

# Claude vision works best at <= ~1568px on the long edge; larger only costs tokens.
MAX_IMAGE_EDGE = 1568
JPEG_QUALITY = 85
# Tall screenshots are sliced so text stays legible: each slice about 1.15 MP, the size vision
# reads best; a few lines of overlap so no line is cut in half.
TILE_PIXELS = 1_150_000
TILE_OVERLAP = 48
MAX_TILES = 6
# Only images more than this many times taller than wide are sliced (a phone screenshot is ~2.2).
TALL_RATIO = 2.5


@contextmanager
def temp_workdir(prefix: str = "truefalse-") -> Iterator[Path]:
    """Raw media is deleted after processing, even on errors (privacy requirement)."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _open(path: Path) -> Image.Image:
    img: Image.Image = Image.open(path)
    img = ImageOps.exif_transpose(img)  # respect phone camera rotation
    return img.convert("RGB")


def _encode(img: Image.Image) -> ImageInput:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return ImageInput("image/jpeg", base64.b64encode(buf.getvalue()).decode())


def compress_image(path: Path, max_edge: int = MAX_IMAGE_EDGE) -> ImageInput:
    img = _open(path)
    img.thumbnail((max_edge, max_edge))
    return _encode(img)


def image_tiles(path: Path) -> list[ImageInput]:
    """A tall screenshot (long chat, thread, article) as top-to-bottom slices; else one image.

    Shrinking a 1080x8000 chat screenshot to 1568 px tall would leave it ~210 px wide and its
    text unreadable, so tall images are cut into overlapping slices at a legible width instead.
    """
    img = _open(path)
    if img.height <= TALL_RATIO * img.width:  # photos and normal phone screenshots
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        return [_encode(img)]
    if img.width > MAX_IMAGE_EDGE:
        img = img.resize((MAX_IMAGE_EDGE, round(img.height * MAX_IMAGE_EDGE / img.width)))
    tile_h = min(MAX_IMAGE_EDGE, TILE_PIXELS // max(img.width, 1))
    step = tile_h - TILE_OVERLAP
    n = math.ceil((img.height - TILE_OVERLAP) / step)
    if n > MAX_TILES:  # very long: shrink just enough to fit, keeping as much detail as possible
        height = MAX_TILES * step + TILE_OVERLAP
        img = img.resize((max(1, round(img.width * height / img.height)), height))
        n = MAX_TILES
    return [
        _encode(img.crop((0, i * step, img.width, min(img.height, i * step + tile_h))))
        for i in range(n)
    ]


def perceptual_hash(path: Path) -> str:
    """64-bit pHash as hex; robust to resizing, recompression and small edits."""
    return str(imagehash.phash(_open(path)))


def hamming(a: str, b: str) -> int:
    return int(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b))
