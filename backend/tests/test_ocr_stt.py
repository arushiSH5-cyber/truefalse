"""OCR for tall screenshots, and speech-to-text language handling for any language."""

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image, ImageDraw

from app.core.models import Extraction, Part, PartKind, Platform, Submission
from app.modality.audio import FasterWhisperSTT
from app.modality.media import MAX_TILES, image_tiles, temp_workdir
from app.modality.process import MediaProviders, process_submission
from app.pipeline import Deps, run_check
from tests.fakes import FakeLLM
from tests.test_multimodal import FakeFetcher, FakeSTT, _analysis

# ---- tall screenshots ----------------------------------------------------------------------


def _chat_screenshot(path: Path, size: tuple[int, int]) -> Path:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for y in range(20, size[1], 60):
        d.text((30, y), f"message at {y}px", fill="black")
    img.save(path)
    return path


def _sizes(tiles: list[Any]) -> list[tuple[int, int]]:
    return [Image.open(io.BytesIO(base64.b64decode(t.data_b64))).size for t in tiles]


def test_normal_image_is_one_tile(tmp_path) -> None:
    assert _sizes(image_tiles(_chat_screenshot(tmp_path / "a.png", (1080, 1500)))) == [(1080, 1500)]
    [(w, h)] = _sizes(image_tiles(_chat_screenshot(tmp_path / "phone.png", (1080, 2400))))
    assert h == 1568  # an ordinary phone screenshot stays one image
    [(w, h)] = _sizes(image_tiles(_chat_screenshot(tmp_path / "b.png", (4000, 3000))))
    assert max(w, h) <= 1568


def test_tall_screenshot_is_sliced_at_readable_width(tmp_path) -> None:
    sizes = _sizes(image_tiles(_chat_screenshot(tmp_path / "chat.png", (1080, 3000))))
    assert len(sizes) == 3
    assert all(w == 1080 and h * w <= 1_150_000 for w, h in sizes)  # full width kept


def test_very_long_screenshot_capped_and_still_wide(tmp_path) -> None:
    sizes = _sizes(image_tiles(_chat_screenshot(tmp_path / "long.png", (1080, 20000))))
    assert len(sizes) == MAX_TILES
    # Shrunk only as much as needed: far wider than the ~85 px a plain thumbnail would give.
    assert all(w >= 300 for w, _ in sizes)


async def test_tall_screenshot_read_in_one_call_as_slices(tmp_path) -> None:
    llm = FakeLLM().queue("image", _analysis())
    fetcher = FakeFetcher({"s": _chat_screenshot(tmp_path / "chat.png", (1080, 3000))})
    sub = Submission(
        platform=Platform.TELEGRAM, user_hash="u", parts=[Part(kind=PartKind.IMAGE, media_ref="s")]
    )
    with temp_workdir() as wd:
        out = await process_submission(sub, MediaProviders(llm=llm, fetcher=fetcher), wd)
    [call] = llm.calls
    assert call["images"] == 3 and "slices of one tall screenshot" in call["user"]
    assert "[Text visible in the image]" in out.text and len(out.image_hashes) == 1


# ---- speech-to-text ------------------------------------------------------------------------


class FakeWhisper:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, language: str, probability: float, ranked: list[tuple[str, float]]):
        self.info = SimpleNamespace(
            language=language, language_probability=probability, all_language_probs=ranked
        )
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, path: str, language: str | None = None, **kw: Any) -> Any:
        self.calls.append({"language": language} | kw)
        info = self.info if language is None else SimpleNamespace(language=language)
        return iter([SimpleNamespace(start=0.0, end=2.0, text=" text")]), info


async def test_whisper_ignores_ui_language_when_confident() -> None:
    model = FakeWhisper("hi", 0.97, [("hi", 0.97), ("ur", 0.02)])
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", "es")  # Spanish phone, Hindi note
    assert t.language == "hi" and len(model.calls) == 1
    first = model.calls[0]
    assert first["language"] is None and first["multilingual"] is True
    assert first["condition_on_previous_text"] is False


async def test_whisper_uses_hint_only_as_tie_breaker() -> None:
    model = FakeWhisper("ur", 0.41, [("ur", 0.41), ("hi", 0.39), ("pa", 0.1)])
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", "hi-IN")
    assert t.language == "hi" and [c["language"] for c in model.calls] == [None, "hi"]


async def test_whisper_ignores_unlikely_hint_and_no_hint() -> None:
    model = FakeWhisper("sw", 0.3, [("sw", 0.3), ("en", 0.2), ("so", 0.1), ("es", 0.05)])
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", "es")
    assert t.language == "sw" and len(model.calls) == 1
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", None)
    assert t.language == "sw" and len(model.calls) == 2


async def test_speech_language_reaches_claim_extractor(tmp_path) -> None:
    from app.modality.base import Transcript, TranscriptSegment

    voice = tmp_path / "v.ogg"
    voice.write_bytes(b"fake")
    stt = FakeSTT(
        Transcript(language="ta", segments=[TranscriptSegment(start_s=0, end_s=3, text="x")])
    )
    llm = FakeLLM().queue(
        "extract",
        Extraction(
            language="ta",
            language_name="Tamil",
            script="Latin",
            romanized_or_mixed=True,
            looks_like_satire_or_opinion=False,
            claims=[],
        ),
    )
    sub = Submission(
        platform=Platform.TELEGRAM, user_hash="u", parts=[Part(kind=PartKind.AUDIO, media_ref="v")]
    )
    media = MediaProviders(llm=llm, fetcher=FakeFetcher({"v": voice}), stt=stt)
    await run_check(sub, Deps(llm, [], media=media))
    extract = next(c for c in llm.calls if c["stage"] == "extract")
    assert "<language_id_hint>ta (detected in the media)</language_id_hint>" in extract["user"]
