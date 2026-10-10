"""Temporary media handling: everything lives in a per-request temp dir that is always deleted."""

import base64
import io
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


def compress_image(path: Path, max_edge: int = MAX_IMAGE_EDGE) -> ImageInput:
    img = _open(path)
    img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return ImageInput("image/jpeg", base64.b64encode(buf.getvalue()).decode())


def perceptual_hash(path: Path) -> str:
    """64-bit pHash as hex; robust to resizing, recompression and small edits."""
    return str(imagehash.phash(_open(path)))


def hamming(a: str, b: str) -> int:
    return int(imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b))
