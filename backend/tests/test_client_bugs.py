"""Regressions: HEIC photos, image links, and one verdict per claim for the web and extension."""

import io
import re
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import web
from app.api.telegram import get_queue
from app.core.jobs import InMemoryJobStore, public_result
from app.core.models import (
    CheckReport,
    Claim,
    ClaimJudgement,
    Confidence,
    Part,
    PartKind,
    Platform,
    Submission,
    Verdict,
)
from app.core.ratelimit import InMemoryRateLimiter
from app.main import create_app
from app.modality import links, process
from app.modality.image import ImageAnalysis
from app.modality.process import W_MEDIA_FAILED, MediaProviders, process_submission
from tests.fakes import FakeLLM, FakeQueue

DATA = Path(__file__).parent / "data"
IMG_URL = "https://cdn.example.com/photo"


def _ffmpeg_major() -> int:
    if not shutil.which("ffmpeg"):
        return 0
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
    found = re.search(r"ffmpeg version n?(\d+)\.", out)
    return int(found[1]) if found else 0


needs_tile_grid = pytest.mark.skipif(_ffmpeg_major() < 8, reason="HEIC needs ffmpeg 8+")


def _llm() -> FakeLLM:
    return FakeLLM().queue(
        "image",
        ImageAnalysis(
            text_in_image="LEMON CURES CANCER",
            description="A poster.",
            language="en",
            is_screenshot_of_post=False,
            possible_editing_signs=[],
            possibly_ai_generated=False,
        ),
    )


class _Fetcher:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def fetch(self, part: Part, dest_dir: Path) -> Path:
        return self.path


def _submission(part: Part) -> Submission:
    return Submission(platform=Platform.WEB, user_hash="u", parts=[part])


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "navy").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        ip = "127.0.0.1" if host == "localhost" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(links.socket, "getaddrinfo", getaddrinfo)


async def _check_url(handler, llm: FakeLLM, tmp_path: Path, url: str = IMG_URL):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await process_submission(
            _submission(Part(kind=PartKind.TEXT, text=url)),
            MediaProviders(llm, http=http),
            tmp_path,
        )


# ---- image links (right-click image on another site's CDN) ---------------------------------


async def test_image_link_is_read_as_an_image(public_dns, tmp_path) -> None:
    out = await _check_url(lambda r: httpx.Response(200, content=_png()), _llm(), tmp_path)
    assert "LEMON CURES CANCER" in out.text and W_MEDIA_FAILED not in out.warnings


async def test_image_link_larger_than_html_cap(public_dns, tmp_path) -> None:
    body = _png() + b"\0" * (links.MAX_HTML_BYTES + 1000)
    out = await _check_url(lambda r: httpx.Response(200, content=body), _llm(), tmp_path)
    assert "LEMON CURES CANCER" in out.text


async def test_image_link_too_large_is_reported(public_dns, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(links, "MAX_IMAGE_BYTES", 2000)
    body = _png() + b"\0" * 4000
    out = await _check_url(lambda r: httpx.Response(200, content=body), FakeLLM(), tmp_path)
    assert W_MEDIA_FAILED in out.warnings


async def test_image_link_redirect_to_private_host_is_refused(public_dns, tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host != "localhost"
        return httpx.Response(302, headers={"location": "http://localhost/secret.png"})

    out = await _check_url(handler, FakeLLM(), tmp_path)
    assert W_MEDIA_FAILED in out.warnings


async def test_unreadable_link_is_never_silent(public_dns, tmp_path) -> None:
    out = await _check_url(lambda r: httpx.Response(200, content=b""), FakeLLM(), tmp_path)
    assert W_MEDIA_FAILED in out.warnings


# ---- HEIC ----------------------------------------------------------------------------------


@needs_tile_grid
@pytest.mark.parametrize(
    ("name", "size", "red_corner"),
    [("grid.heic", (1100, 700), (100, 100)), ("grid_rotated.heic", (700, 1100), (600, 100))],
)
async def test_heic_photo_is_read_whole_and_upright(tmp_path, name, size, red_corner) -> None:
    src = Path(shutil.copy(DATA / name, tmp_path / name))
    llm = _llm()
    out = await process_submission(
        _submission(Part(kind=PartKind.IMAGE, media_ref="k")),
        MediaProviders(llm, fetcher=_Fetcher(src)),
        tmp_path,
    )
    assert "LEMON CURES CANCER" in out.text and W_MEDIA_FAILED not in out.warnings
    with Image.open(next(tmp_path.glob("*.decoded.png"))) as img:
        assert img.size == size
        red, green, blue = img.convert("RGB").getpixel(red_corner)
        assert red > 200 and green < 60 and blue < 60


async def test_heic_never_accepts_a_single_tile(tmp_path, monkeypatch) -> None:
    """Older ffmpeg returns one 512 px tile of the photo. That must not be checked as the photo."""
    from app.modality import heic

    async def one_tile(*args: str, **kwargs: object) -> bytes:
        Image.new("RGB", (512, 512)).save(args[-1])
        return b""

    monkeypatch.setattr(heic, "run_tool", one_tile)
    src = Path(shutil.copy(DATA / "grid.heic", tmp_path / "p.heic"))
    out = await process_submission(
        _submission(Part(kind=PartKind.IMAGE, media_ref="k")),
        MediaProviders(FakeLLM(), fetcher=_Fetcher(src)),
        tmp_path,
    )
    assert process.W_HEIC_FAILED in out.warnings


@pytest.mark.parametrize("mime", ["", "application/octet-stream", "image/heic"])
def test_heic_upload_is_accepted_whatever_mime_the_browser_sends(mime) -> None:
    app, queue, jobs = create_app(), FakeQueue(), InMemoryJobStore()
    app.dependency_overrides.update(
        {
            get_queue: lambda: queue,
            web.get_jobs: lambda: jobs,
            web.get_limiter: lambda: InMemoryRateLimiter(),
        }
    )
    res = TestClient(app).post(
        "/api/check",
        files={"file": ("IMG_0001.HEIC", (DATA / "grid.heic").read_bytes(), mime)},
        headers={"X-Client-Id": "client-abc-123"},
    )
    assert res.status_code == 202
    assert queue.jobs[0][1]["submission"]["parts"][0]["kind"] == "image"


# ---- one verdict per claim -----------------------------------------------------------------


def _report() -> CheckReport:
    claims = [
        Claim(
            original=text,
            english=text,
            topic="health",
            potentially_harmful=False,
            who=None,
            did=None,
            where=None,
            when=None,
            local_languages=[],
        )
        for text in ("Soap removes germs", "Garlic cures covid")
    ]
    judgements = [
        ClaimJudgement(
            claim_index=i,
            verdict=verdict,
            confidence=Confidence.HIGH,
            explanation="x",
            cited_evidence_ids=[],
        )
        for i, verdict in ((1, Verdict.FALSE), (0, Verdict.TRUE))  # order must not matter
    ]
    return CheckReport(
        submission_id="s",
        language="en",
        claims=claims,
        judgements=judgements,
        overall=Verdict.FALSE,
        overall_confidence=Confidence.HIGH,
        evidence=[],
        warnings=[],
        reply_text="🔴 False",
    )


def test_each_claim_keeps_its_own_verdict() -> None:
    result = public_result(_report())
    assert [(c.claim, c.verdict) for c in result.claim_results] == [
        ("Soap removes germs", "true"),
        ("Garlic cures covid", "false"),
    ]


async def test_share_card_quotes_the_claim_that_matches_the_verdict(monkeypatch) -> None:
    jobs = InMemoryJobStore()
    job = await jobs.create()
    job.status, job.result = "done", public_result(_report())
    await jobs.update(job)
    shown: dict[str, str] = {}
    monkeypatch.setattr(web, "render_card", lambda **kw: shown.update(claim=kw["claim"]) or b"png")
    app = create_app()
    app.dependency_overrides[web.get_jobs] = lambda: jobs
    assert TestClient(app).get(f"/api/check/{job.id}/card.png").status_code == 200
    assert shown["claim"] == "Garlic cures covid"


# ---- production wiring ---------------------------------------------------------------------


async def test_build_deps_hands_every_platform_its_media_fetcher() -> None:
    """Without this, every uploaded photo, voice note and video was answered "can't be checked"."""
    from app.core.config import Settings
    from app.core.container import build_deps, build_platforms

    settings = Settings(_env_file=None, gemini_api_key="made-up", verdict_cache_enabled=False)
    async with httpx.AsyncClient() as http:
        _, fetchers = build_platforms(settings, http, InMemoryJobStore())
        deps = build_deps(settings, http, fetchers=fetchers)
    assert deps.media is not None and deps.media.fetchers == fetchers
