"""HEIC/HEIF photos (iPhone's default format), decoded with ffmpeg because Pillow can't read them.

iPhone photos are stored as a grid of 512 px tiles. ffmpeg 8+ joins them; older versions return
a single tile, so the output size is checked and a partial photo is never passed on as the photo.
"""

from pathlib import Path

from PIL import Image

from app.modality.video import MediaToolError, run_tool

BRANDS = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
)
MAX_PIXELS = 100_000_000  # above a 48 MP iPhone photo; refuses decompression bombs
SIZE_TOLERANCE = 16  # cropping to even dimensions can shave a few pixels
DECODE_TIMEOUT_S = 30


class HeicError(Exception):
    pass


def is_heic(head: bytes) -> bool:
    if head[4:8] != b"ftyp":
        return False
    # AVIF can also list mif1, and Pillow reads AVIF by itself.
    return head[8:12] in BRANDS and b"avif" not in head[8:32]


def declared_size(data: bytes) -> tuple[int, int] | None:
    """The photo's size from its `ispe` boxes (the largest: the others are tiles and thumbnails)."""
    sizes = []
    at = data.find(b"ispe")
    while at != -1:
        box = data[at + 8 : at + 16]
        if len(box) == 8:
            sizes.append((int.from_bytes(box[:4], "big"), int.from_bytes(box[4:], "big")))
        at = data.find(b"ispe", at + 4)
    return max(sizes, key=lambda s: (max(s), s[0] * s[1]), default=None)


async def readable_image(path: Path) -> Path:
    """`path` itself, or for HEIC an upright PNG copy next to it (same temp dir, also deleted)."""
    with path.open("rb") as f:
        if not is_heic(f.read(32)):
            return path
        f.seek(0)
        size = declared_size(f.read())
    if size is None or size[0] * size[1] > MAX_PIXELS:
        raise HeicError("HEIC size is missing or too large")
    out = path.with_name(f"{path.name}.decoded.png")
    try:
        await run_tool(
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file",
            "-f",
            "mov",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-y",
            str(out),
            timeout_s=DECODE_TIMEOUT_S,
        )
        with Image.open(out) as img:
            got = sorted(img.size)
    except (MediaToolError, OSError) as exc:
        raise HeicError("ffmpeg could not decode the HEIC file") from exc
    # Sorted, because a rotated photo swaps width and height.
    if any(abs(a - b) > SIZE_TOLERANCE for a, b in zip(got, sorted(size), strict=True)):
        raise HeicError(f"decoded {got}, expected {size}: ffmpeg 8+ is needed for tiled HEIC")
    return out
