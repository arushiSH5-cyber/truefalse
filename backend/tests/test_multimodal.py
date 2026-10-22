"""Phase 5: images, voice, video, links. Offline; ffmpeg tests skip if ffmpeg is missing."""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from app.core.models import (
    ClaimJudgement,
    ComposedReply,
    Confidence,
    JudgePanelOutput,
    Part,
    PartKind,
    Platform,
    SourceKind,
    Submission,
    Verdict,
)
from app.modality import links
from app.modality.audio import ChunkSelection, chunk_segments, select_claim_chunks
from app.modality.base import Transcript, TranscriptSegment
from app.modality.hashes import InMemoryImageHashStore
from app.modality.image import ImageAnalysis
from app.modality.media import compress_image, hamming, perceptual_hash, temp_workdir
from app.modality.process import (
    W_AI,
    W_EDITED,
    W_MEDIA_FAILED,
    W_TRANSCRIBED,
    W_UNSUPPORTED,
    MediaProviders,
    process_submission,
)
from app.modality.video import extract_audio, extract_frames, pick_frame_times
from app.pipeline import Deps, run_check
from tests.fakes import FakeLLM, FakeSource

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_image(path: Path, text: str = "LEMON CURES CANCER", size=(640, 480)) -> Path:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 300, 200], fill="navy")
    d.ellipse([350, 220, 600, 440], fill="orange")
    d.text((50, 300), text, fill="black")
    img.save(path)
    return path


class FakeFetcher:
    def __init__(self, files: dict[str, Path]) -> None:
        self.files = files
        self.fetched: list[Path] = []

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        dest = dest_dir / self.files[part.media_ref or ""].name
        shutil.copy(self.files[part.media_ref or ""], dest)
        self.fetched.append(dest)
        return dest


class FakeSTT:
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.calls: list[tuple[str, str | None]] = []

    async def transcribe(self, audio_path: str, language_hint: str | None = None) -> Transcript:
        assert os.path.exists(audio_path)
        self.calls.append((audio_path, language_hint))
        return self.transcript


def _analysis(**kw) -> ImageAnalysis:
    base = dict(
        text_in_image="Limón cura el cáncer",
        description="A meme with a lemon.",
        language="es",
        is_screenshot_of_post=True,
        possible_editing_signs=[],
        possibly_ai_generated=False,
    )
    return ImageAnalysis(**(base | kw))


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return work


def _sub(*parts: Part, hint: str | None = "es") -> Submission:
    return Submission(
        platform=Platform.TELEGRAM, user_hash="u", language_hint=hint, parts=list(parts)
    )


# ---- images --------------------------------------------------------------------------------


def test_compress_and_hash(tmp_path) -> None:
    big = _make_image(tmp_path / "big.png", size=(4000, 3000))
    img = compress_image(big)
    assert img.media_type == "image/jpeg" and len(img.data_b64) < 400_000
    small = tmp_path / "small.jpg"
    Image.open(big).resize((800, 600)).save(small, quality=60)
    assert hamming(perceptual_hash(big), perceptual_hash(small)) <= 8  # same picture
    other = _make_image(tmp_path / "other.png", text="x")
    Image.open(other).rotate(90).save(other)
    assert hamming(perceptual_hash(big), perceptual_hash(other)) > 8


async def test_image_submission(tmp_path) -> None:
    llm = FakeLLM().queue(
        "image", _analysis(possible_editing_signs=["mismatched font"], possibly_ai_generated=True)
    )
    fetcher = FakeFetcher({"f1": _make_image(tmp_path / "meme.png")})
    hashes = InMemoryImageHashStore()
    with temp_workdir() as wd:
        out = await process_submission(
            _sub(Part(kind=PartKind.IMAGE, media_ref="f1")),
            MediaProviders(llm=llm, fetcher=fetcher, hashes=hashes),
            wd,
        )
    assert "[Text visible in the image]: Limón cura el cáncer" in out.text
    assert W_EDITED in out.warnings and W_AI in out.warnings
    assert llm.calls[0]["images"] == 1 and len(out.image_hashes) == 1
    assert not fetcher.fetched[0].exists()  # temp dir removed


async def test_seen_before_and_media_deleted_end_to_end(
    tmp_path, spanish_health_extraction, factcheck_draft
) -> None:
    hashes = InMemoryImageHashStore()
    meme = _make_image(tmp_path / "meme.png")
    await hashes.record(perceptual_hash(meme), Verdict.FALSE)
    fetcher = FakeFetcher({"f1": meme})
    llm = FakeLLM().queue("image", _analysis()).queue("extract", spanish_health_extraction)
    llm.queue_panel(
        JudgePanelOutput(
            judgements=[
                ClaimJudgement(
                    claim_index=0,
                    verdict=Verdict.FALSE,
                    confidence=Confidence.HIGH,
                    explanation="No.",
                    cited_evidence_ids=["E1"],
                )
            ]
        )
    )
    llm.queue(
        "compose",
        ComposedReply(
            verdict_label="Falso",
            explanation="No.",
            confidence_label="Alta",
            warnings=["w"],
            safety_note=None,
            sources_heading="Fuentes",
        ),
    )
    llm.tool_script = [("search_factchecks", {"query": "q", "language": None})]
    progress: list[str] = []

    async def on_progress(kind: str) -> None:
        progress.append(kind)

    report = await run_check(
        _sub(Part(kind=PartKind.IMAGE, media_ref="f1")),
        Deps(
            llm,
            [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])],
            media=MediaProviders(llm=llm, fetcher=fetcher, hashes=hashes),
        ),
        progress=on_progress,
    )
    assert progress == ["media"]
    assert any("sent to us 1 time(s) before" in w and "false" in w for w in report.warnings)
    assert "Limón cura el cáncer" in next(c for c in llm.calls if c["stage"] == "extract")["user"]
    assert not fetcher.fetched[0].exists()
    seen = await hashes.lookup(perceptual_hash(meme))
    assert seen is not None and seen.times_seen == 2


async def test_missing_providers_and_failures(tmp_path) -> None:
    llm = FakeLLM()
    out = await process_submission(
        _sub(Part(kind=PartKind.AUDIO, media_ref="a")), MediaProviders(llm=llm), tmp_path
    )
    assert out.warnings == [W_UNSUPPORTED] and out.text == ""

    class Broken:
        async def fetch(self, part, dest_dir):  # type: ignore[no-untyped-def]
            raise RuntimeError("download failed")

    out = await process_submission(
        _sub(Part(kind=PartKind.IMAGE, media_ref="x")),
        MediaProviders(llm=llm, fetcher=Broken()),
        tmp_path,
    )
    assert out.warnings == [W_MEDIA_FAILED]


# ---- audio ---------------------------------------------------------------------------------


def _segments(minutes: int) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start_s=i * 10, end_s=i * 10 + 9, text=f"sentence {i}.")
        for i in range(minutes * 6)
    ]


def test_chunking() -> None:
    chunks = chunk_segments(_segments(5))
    assert len(chunks) == 5 and chunks[1].start_s == 60 and "sentence 6." in chunks[1].text


async def test_long_audio_keeps_only_claim_chunks() -> None:
    llm = FakeLLM().queue("segment_filter", ChunkSelection(claim_chunk_indexes=[7, 2, 2, 99]))
    kept = await select_claim_chunks(llm, chunk_segments(_segments(10)))
    assert [c.index for c in kept] == [2, 7]
    short = await select_claim_chunks(FakeLLM(), chunk_segments(_segments(2)))
    assert len(short) == 2  # short audio: no filtering call


async def test_voice_note(tmp_path) -> None:
    voice = tmp_path / "v.ogg"
    voice.write_bytes(b"fake")
    stt = FakeSTT(
        Transcript(
            language="hi",
            segments=[TranscriptSegment(start_s=0, end_s=5, text="गर्म नींबू पानी कैंसर ठीक करता है")],
        )
    )
    out = await process_submission(
        _sub(Part(kind=PartKind.AUDIO, media_ref="v"), hint="hi"),
        MediaProviders(llm=FakeLLM(), fetcher=FakeFetcher({"v": voice}), stt=stt),
        _work(tmp_path),
    )
    assert "[Voice note transcript, with timestamps]:\n[0:00] गर्म नींबू" in out.text
    assert out.transcript_language == "hi" and W_TRANSCRIBED in out.warnings
    assert stt.calls[0][1] == "hi"


# ---- video (real ffmpeg) -------------------------------------------------------------------


@pytest.fixture
def sample_video(tmp_path) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


async def test_ffmpeg_audio_and_frames(sample_video, tmp_path) -> None:
    wav = await extract_audio(sample_video, tmp_path, max_seconds=60)
    assert wav is not None and wav.stat().st_size > 10_000
    frames = await extract_frames(sample_video, tmp_path, max_frames=3)
    assert len(frames) == 3 and all(Image.open(f.path).size == (320, 240) for f in frames)
    assert [f.time_s for f in frames] == sorted(f.time_s for f in frames)


async def test_video_submission(sample_video, tmp_path) -> None:
    from app.modality.image import FrameText, VideoAnalysis

    llm = FakeLLM().queue(
        "video",
        VideoAnalysis(
            on_screen_text=[FrameText(frame=2, text="AJO = CURA"), FrameText(frame=99, text="x")],
            description="Test pattern.",
            language="es",
            possible_editing_signs=[],
            possibly_ai_generated=False,
        ),
    )
    stt = FakeSTT(
        Transcript(
            language="en",
            segments=[TranscriptSegment(start_s=0, end_s=3, text="Garlic prevents covid.")],
        )
    )
    out = await process_submission(
        _sub(Part(kind=PartKind.VIDEO, media_ref="v")),
        MediaProviders(llm=llm, fetcher=FakeFetcher({"v": sample_video}), stt=stt),
        _work(tmp_path),
    )
    assert "[What the video shows]: Test pattern." in out.text
    assert "Garlic prevents covid." in out.text
    [call] = llm.calls[:1]
    assert call["stage"] == "video" and call["images"] >= 4  # one vision call for all keyframes
    assert "frame 1 at 0:00" in call["user"]
    assert "[Text on screen in the video, with timestamps]:\n[0:01] AJO = CURA" in out.text
    assert "x" not in out.text.split("timestamps]:")[1].split("\n")[2:3]  # unknown frame dropped


def test_pick_frame_times() -> None:
    # Even coverage of a 20 s clip, plus a frame just after each cut, at least 1 s apart.
    times = pick_frame_times(20.0, [3.0, 3.2, 11.0])
    assert times == [2.5, 3.5, 7.5, 11.3, 12.5, 17.5]
    assert pick_frame_times(0.0, []) == [0.0]
    many = pick_frame_times(100.0, [float(t) for t in range(1, 100, 3)], max_frames=8)
    assert len(many) == 8 and many == sorted(many)


# ---- links ---------------------------------------------------------------------------------


def test_find_and_classify_urls() -> None:
    text = "mira https://youtu.be/abc123. y https://x.com/user/status/123 y (https://news.example.com/a)"
    urls = links.find_urls(text)
    assert urls == [
        "https://youtu.be/abc123",
        "https://x.com/user/status/123",
        "https://news.example.com/a",
    ]
    assert [links.classify(u) for u in urls] == ["media", "post", "article"]
    assert links.classify("https://www.instagram.com/reel/xyz/") == "media"
    assert links.classify("https://evil-youtube.com/x") == "article"


@pytest.fixture
def fake_dns(monkeypatch):
    table = {
        "news.example.com": "93.184.216.34",
        "internal.example.com": "10.0.0.5",
        "localhost": "127.0.0.1",
        "metadata.example.com": "169.254.169.254",
    }

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]
        try:
            socket.inet_aton(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]
        except OSError:
            raise socket.gaierror("unknown host") from None

    monkeypatch.setattr(links.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://10.1.2.3/",
        "http://internal.example.com/",
        "http://metadata.example.com/latest/meta-data",
        "ftp://news.example.com/",
        "http://news.example.com:8080/",
        "http://user:pw@news.example.com/",
        "http://nosuchhost.invalid/",
    ],
)
async def test_ssrf_guard_rejects(fake_dns, url) -> None:
    with pytest.raises(links.UnsafeURLError):
        await links.assert_public_url(url)


ARTICLE_HTML = b"""<html><head><title>Site</title>
<meta property="og:title" content="Garlic does not prevent COVID-19">
<meta property="article:published_time" content="2020-03-02"></head>
<body><nav>menu menu menu menu menu menu menu menu</nav><script>var x = "ignore me please";</script>
<p>The World Health Organization says there is no evidence that eating garlic protects people.</p>
<p>Short.</p></body></html>"""


async def test_article_fetch_and_redirect_guard(fake_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://news.example.com/article"})
        if request.url.path == "/evil":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, content=ARTICLE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        art = await links.fetch_article(http, "https://news.example.com/go")
        assert art.url == "https://news.example.com/article"
        assert art.title == "Garlic does not prevent COVID-19" and art.published == "2020-03-02"
        assert "no evidence that eating garlic" in art.text
        assert "ignore me" not in art.text and "menu" not in art.text
        with pytest.raises(links.UnsafeURLError):
            await links.fetch_article(http, "https://news.example.com/evil")


async def test_x_post_via_oembed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "publish.twitter.com"
        return httpx.Response(
            200,
            json={
                "author_name": "Someone",
                "html": '<blockquote class="twitter-tweet"><p lang="en">5G towers spread the virus, '
                "share before they delete this!</p>&mdash; Someone</blockquote>",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        post = await links.fetch_x_post(http, "https://x.com/someone/status/1")
    assert post.kind == "post" and "5G towers spread the virus" in post.text


async def test_media_link_without_downloader(tmp_path) -> None:
    sub = _sub(Part(kind=PartKind.TEXT, text="is this true? https://www.tiktok.com/@a/video/1"))
    out = await process_submission(sub, MediaProviders(llm=FakeLLM()), tmp_path)
    assert W_UNSUPPORTED in out.warnings and out.text.startswith("is this true?")


async def test_ytdlp_refuses_non_media_hosts(tmp_path) -> None:
    with pytest.raises(links.UnsafeURLError):
        await links.YtDlpDownloader().fetch("https://news.example.com/a", tmp_path)


@pytest.fixture
def cuts_video(tmp_path) -> Path:
    """Three 2 s shots in different colours: cuts at 2 s and 4 s."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "cuts.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=320x240:duration=2:rate=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=320x240:duration=2:rate=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=320x240:duration=2:rate=10",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    return clip


async def test_scene_cuts_found_on_real_video(cuts_video, tmp_path) -> None:
    from app.modality.video import scene_times

    clip = cuts_video
    cuts = await scene_times(clip)
    assert [round(t) for t in cuts] == [2, 4]
    frames = await extract_frames(clip, tmp_path)
    colours = {Image.open(f.path).convert("RGB").getpixel((5, 5)) for f in frames}
    assert len(colours) == 3  # every shot is represented
