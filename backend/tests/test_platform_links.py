"""Links to social platforms: platform detection, public previews when downloads fail."""

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core.models import Part, PartKind
from app.modality import links
from app.modality.process import W_PREVIEW_ONLY, MediaProviders, process_submission
from tests.fakes import FakeLLM
from tests.test_multimodal import _analysis, _sub, fake_dns  # noqa: F401 - fixture


@pytest.mark.parametrize(
    ("url", "platform", "kind"),
    [
        ("https://www.instagram.com/p/C1a2B3c4D5e/", "instagram", "media"),
        ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=abc", "instagram", "media"),
        ("https://instagr.am/p/C1a2B3c4D5e/", "instagram", "media"),
        ("https://www.facebook.com/share/v/1AbCdEfGh/", "facebook", "media"),
        ("https://m.facebook.com/story.php?story_fbid=1&id=2", "facebook", "media"),
        ("https://fb.watch/abc123/", "facebook", "media"),
        ("https://www.tiktok.com/@someone/video/7300000000000000000", "tiktok", "media"),
        ("https://vm.tiktok.com/ZMabc123/", "tiktok", "media"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "media"),
        ("https://youtu.be/dQw4w9WgXcQ?si=x", "youtube", "media"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "media"),
        ("https://x.com/someone/status/1790000000000000000", "x", "post"),
        ("https://twitter.com/someone/status/1790000000000000000?s=20", "x", "post"),
        ("https://x.com/someone", "x", "media"),
        ("https://news.example.com/story", None, "article"),
    ],
)
def test_platform_and_kind(url: str, platform: str | None, kind: str) -> None:
    assert links.platform_of(url) == platform
    assert links.classify(url) == kind


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), "orange").save(buf, format="JPEG")
    return buf.getvalue()


INSTAGRAM_HTML = b"""<html><head><title>Instagram</title>
<meta property="og:title" content="Health Tips on Instagram">
<meta property="og:description" content="1,204 likes - healthtips: Boiled garlic water cures
diabetes in 3 days! Share with everyone">
<meta property="og:image" content="https://cdn.example.com/thumb.jpg?a=1&amp;b=2">
</head><body></body></html>"""
LOGIN_WALL_HTML = b"<html><head><title>Login - Instagram</title></head><body></body></html>"


@pytest.fixture
def social_dns(fake_dns, monkeypatch):  # noqa: F811
    import socket

    original = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host.endswith(("instagram.com", "tiktok.com", "example.com", "youtube.com")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


async def test_og_preview_for_instagram(social_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.instagram.com"
        return httpx.Response(200, content=INSTAGRAM_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await links.fetch_preview(http, "https://www.instagram.com/p/abc/")
    assert got.kind == "preview" and got.title == "Health Tips on Instagram"
    assert "garlic water cures" in got.text
    assert got.thumbnail_url == "https://cdn.example.com/thumb.jpg?a=1&b=2"


async def test_login_wall_has_no_preview(social_dns) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=LOGIN_WALL_HTML))
    ) as http:
        with pytest.raises(ValueError, match="no public preview"):
            await links.fetch_preview(http, "https://www.instagram.com/p/private/")


async def test_oembed_preview_for_tiktok(social_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.tiktok.com" and request.url.path == "/oembed"
        assert request.url.params["url"].endswith("/video/1")
        return httpx.Response(
            200,
            json={
                "title": "Drink this every morning and never get cancer #health",
                "author_name": "wellness",
                "thumbnail_url": "https://cdn.example.com/t.jpg",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        got = await links.fetch_preview(http, "https://www.tiktok.com/@wellness/video/1")
    assert got.uploader == "wellness" and "never get cancer" in got.text
    assert got.thumbnail_url == "https://cdn.example.com/t.jpg"


async def test_thumbnail_refuses_private_hosts(fake_dns, tmp_path: Path) -> None:  # noqa: F811
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=_jpeg()))
    ) as http:
        with pytest.raises(links.UnsafeURLError):
            await links.fetch_thumbnail(http, "http://10.0.0.5/t.jpg", tmp_path / "t.jpg")


class LoginWallDownloader:
    async def fetch(self, url: str, out_dir: Path) -> links.LinkContent:
        raise RuntimeError("login required")


async def test_failed_download_falls_back_to_preview_and_reads_picture(
    social_dns, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example.com":
            return httpx.Response(200, content=_jpeg())
        return httpx.Response(200, content=INSTAGRAM_HTML)

    llm = FakeLLM().queue("image", _analysis(text_in_image="GARLIC WATER CURES DIABETES"))
    sub = _sub(Part(kind=PartKind.TEXT, text="true? https://www.instagram.com/reel/abc/"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        providers = MediaProviders(llm=llm, http=http, downloader=LoginWallDownloader())  # type: ignore[arg-type]
        out = await process_submission(sub, providers, tmp_path)

    assert W_PREVIEW_ONLY in out.warnings
    assert "[Linked post (preview only): Health Tips on Instagram]" in out.text
    assert "garlic water cures" in out.text
    assert "GARLIC WATER CURES DIABETES" in out.text  # the preview picture was read
    assert len(out.image_hashes) == 1
