"""File metadata that names an AI generator: IPTC digital-source-type in XMP, EXIF Software,
and the generation settings Stable Diffusion UIs and ComfyUI write into PNG text chunks.

Free and local. Metadata is easy to strip (messaging apps usually do), so absence means
nothing; presence of a generator's own marker is strong.
"""

import asyncio
from pathlib import Path

from PIL import ExifTags, Image

from app.ai_detect.signals import AISignal, MediaKind, Strength

# Read at most this much of the file looking for an XMP packet (it sits near the start).
XMP_SCAN_BYTES = 4_000_000
AI_SOURCE_MARKERS = (
    b"trainedAlgorithmicMedia",
    b"compositeWithTrainedAlgorithmicMedia",
)
# Generators and AI editors that write their name into EXIF/XMP/PNG metadata (lower case).
AI_TOOLS = (
    "dall-e",
    "dall·e",
    "midjourney",
    "stable diffusion",
    "stablediffusion",
    "sdxl",
    "adobe firefly",
    "firefly",
    "imagen",
    "gemini",
    "ideogram",
    "leonardo.ai",
    "flux",
    "comfyui",
    "automatic1111",
    "novelai",
    "sora",
    "grok",
    "meta ai",
    "chatgpt",
    "gpt-4o",
    "openai",
    "runway",
    "kling",
    "pika",
    "veo",
    "bing image creator",
    "designer",
)
# PNG text keys that only image generators write (A1111 "parameters", ComfyUI "prompt"/"workflow").
GENERATION_KEYS = ("parameters", "prompt", "workflow", "sd-metadata", "invokeai_metadata")


def _tool_in(text: str) -> str | None:
    low = text.lower()
    return next((t for t in AI_TOOLS if t in low), None)


def scan(path: Path) -> list[tuple[str, str]]:
    """[(kind, value)] of AI markers found. kind: iptc | exif | png (tool name) | params."""
    found: list[tuple[str, str]] = []
    with path.open("rb") as f:
        head = f.read(XMP_SCAN_BYTES)
    for marker in AI_SOURCE_MARKERS:
        if marker in head:
            found.append(("iptc", marker.decode()))
            break
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            for tag in (ExifTags.Base.Software, ExifTags.Base.ImageDescription, ExifTags.Base.Make):
                value = exif.get(tag)
                if isinstance(value, str) and (tool := _tool_in(value)):
                    found.append(("exif", tool))
            for key, value in (img.info or {}).items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                if key.lower() in GENERATION_KEYS and len(value) > 20:
                    found.append(("params", key))
                elif key.lower() in ("software", "comment", "description") and (
                    tool := _tool_in(value)
                ):
                    found.append(("png", tool))
    except Exception:  # not an image Pillow can open (e.g. video): byte scan only
        pass
    return found


class MetadataCheck:
    async def check(self, path: Path, media: MediaKind) -> list[AISignal]:
        found = await asyncio.to_thread(scan, path)
        if not found:
            return []
        kinds = {k for k, _ in found}
        tool = next((v for k, v in found if k in ("exif", "png")), None)
        named = f" ({tool.title()})" if tool else ""
        if kinds & {"iptc", "params"}:
            detail = f"This {media}'s file information says it was made by an AI tool{named}."
            strength = Strength.STRONG
        else:
            # A program name alone: some (e.g. Firefly, Designer) are also ordinary editors.
            detail = f"This {media}'s file information names an AI tool{named}."
            strength = Strength.MODERATE
        return [
            AISignal(
                detector="metadata",
                media=media,
                finding="ai_generated",
                strength=strength,
                detail=detail,
            )
        ]
