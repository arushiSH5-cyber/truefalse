"""OCR for tall screenshots, and speech-to-text language handling for any language."""

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw

from app.core.models import Extraction, Part, PartKind, Platform, Submission
from app.modality import audio as audio_mod
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
    """Stands in for faster_whisper.WhisperModel. `audio` is a list of per-sample language codes,
    so each slice's "detected" language is simply its majority code."""

    def __init__(self, probability: float = 0.95, ranked: list[tuple[str, float]] | None = None):
        self.probability = probability
        self.ranked = ranked
        self.transcribed: list[tuple[int, str | None, bool]] = []

    def detect_language(self, audio: list[str], **kw: Any) -> Any:
        lang = max(set(audio), key=audio.count)
        return lang, self.probability, self.ranked or [(lang, self.probability)]

    def transcribe(self, audio: list[str], language: str | None = None, **kw: Any) -> Any:
        self.transcribed.append((len(audio), language, kw["multilingual"]))
        assert kw["condition_on_previous_text"] is False
        text = f" <{language or 'auto'}:{len(audio)}>"
        return iter([SimpleNamespace(start=0.0, end=len(audio) / 16000, text=text)]), None


SR = 16000


@pytest.fixture
def fake_audio(monkeypatch):
    """Install a fake decoded clip (and its speech spans) for FasterWhisperSTT."""

    def install(pieces: list[tuple[str, float]], spans: list[tuple[float, float]] | None = None):
        audio: list[str] = []
        for lang, seconds in pieces:
            audio += [lang] * int(seconds * SR)
        monkeypatch.setattr(audio_mod, "_decode", lambda path: audio)
        found = spans or [(0.0, len(audio) / SR)]
        monkeypatch.setattr(
            audio_mod, "_speech_spans", lambda a: [(int(s * SR), int(e * SR)) for s, e in found]
        )
        return audio

    return install


async def test_whisper_ignores_ui_language_when_confident(fake_audio) -> None:
    fake_audio([("hi", 5)])
    model = FakeWhisper()
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", "es")  # Spanish phone, Hindi note
    assert t.language == "hi" and model.transcribed == [(5 * SR, "hi", False)]


async def test_whisper_uses_hint_only_as_tie_breaker(fake_audio) -> None:
    fake_audio([("ur", 5)])
    unsure = FakeWhisper(0.41, [("ur", 0.41), ("hi", 0.39), ("pa", 0.1)])
    t = await FasterWhisperSTT(model=unsure).transcribe("x.ogg", "hi-IN")
    assert t.language == "hi"
    t = await FasterWhisperSTT(model=unsure).transcribe("x.ogg", "es")  # not a likely guess
    assert t.language == "ur"
    t = await FasterWhisperSTT(model=FakeWhisper(0.41)).transcribe("x.ogg", None)
    assert t.language == "ur"


async def test_code_switched_note_transcribed_per_language(fake_audio) -> None:
    # Spanish sentence, pause, English sentence: each must be decoded in its own language,
    # never translated into the other.
    fake_audio([("es", 4), ("en", 3)], spans=[(0.0, 2.5), (2.5, 4.0), (4.0, 7.0)])
    model = FakeWhisper()
    t = await FasterWhisperSTT(model=model).transcribe("x.ogg", None)
    assert [(lang, multi) for _, lang, multi in model.transcribed] == [
        ("es", False),
        ("en", False),
    ]
    assert [round(s.start_s, 1) for s in t.segments] == [0.0, 4.0]  # timestamps offset
    assert "<es:" in t.segments[0].text and "<en:" in t.segments[1].text


async def test_long_audio_lets_whisper_detect_per_window(fake_audio) -> None:
    fake_audio([("es", 200), ("en", 100)])
    model = FakeWhisper()
    await FasterWhisperSTT(model=model).transcribe("x.ogg", None)
    assert model.transcribed == [(300 * SR, None, True)]


def test_merge_short_and_group() -> None:
    from app.modality.audio import group_by_language, merge_short

    assert merge_short([(0, 10), (12, 13), (20, 40), (41, 42)], 5) == [(0, 13), (20, 42)]
    assert merge_short([(0, 1), (3, 20)], 5) == [(0, 20)]
    groups = group_by_language([(0, 5), (6, 9), (10, 20)], ["es", None, "en"], "es")
    assert groups == [(0, 9, "es"), (10, 20, "en")]


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


def test_real_audio_decode_and_vad(tmp_path) -> None:
    """Real faster-whisper decoding + VAD on an OGG/Opus voice note (no model download).
    Catches dependency breaks such as PyAV 19 failing every decode."""
    import importlib.util
    import shutil
    import subprocess

    if not importlib.util.find_spec("faster_whisper") or not shutil.which("ffmpeg"):
        pytest.skip("needs the media extra and ffmpeg")
    note = tmp_path / "note.ogg"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=3",
            "-c:a",
            "libopus",
            str(note),
        ],
        check=True,
    )
    audio = audio_mod._decode(str(note))
    assert abs(len(audio) / audio_mod.SAMPLE_RATE - 3.0) < 0.2
    assert isinstance(audio_mod._speech_spans(audio), list)  # a pure tone: no speech is fine
