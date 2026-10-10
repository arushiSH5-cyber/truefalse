"""Links to public posts, reels, shorts, videos, podcasts and articles.

Safety:
- Only http(s) on ports 80/443, and every redirect hop is re-validated.
- Hosts resolving to private, loopback, link-local or reserved addresses are refused (SSRF).
- yt-dlp only runs for an allow-list of public media hosts, never with cookies or logins,
  so only public content is fetched (no private groups or accounts).
"""

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel

from app.core.logging import get_logger

log = get_logger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'）)\]]+", re.IGNORECASE)
MAX_HTML_BYTES = 2_000_000
MAX_TEXT_CHARS = 8000
MAX_REDIRECTS = 5

# Hosts whose public media yt-dlp can fetch (suffix match).
MEDIA_HOSTS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "fb.watch",
    "x.com",
    "twitter.com",
    "vimeo.com",
    "dailymotion.com",
    "reddit.com",
    "v.redd.it",
    "soundcloud.com",
    "podcasts.apple.com",
    "snapchat.com",
    "twitch.tv",
    "rumble.com",
    "bilibili.com",
    "linkedin.com",
    "threads.net",
    "kwai.com",
    "likee.video",
    "sharechat.com",
    "mojapp.in",
    "dw.com",
    "anchor.fm",
    "podbean.com",
    "buzzsprout.com",
    "audioboom.com",
)
POST_PATHS = re.compile(r"/(status|statuses)/\d+")


class UnsafeURLError(Exception):
    pass


class LinkContent(BaseModel):
    url: str
    kind: Literal["media", "post", "article"]
    title: str | None = None
    text: str = ""
    uploader: str | None = None
    published: str | None = None
    duration_s: float | None = None
    media_path: str | None = None  # downloaded audio/video (temp dir, deleted after use)


def find_urls(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL_RE.findall(text)))


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")


def is_media_host(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS)


def classify(url: str) -> Literal["media", "post", "article"]:
    host = host_of(url)
    if host in ("x.com", "twitter.com", "mobile.twitter.com") and POST_PATHS.search(url):
        return "post"
    return "media" if is_media_host(url) else "article"


def _ip_allowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURLError("only http(s) URLs are allowed")
    if parsed.port not in (None, 80, 443):
        raise UnsafeURLError("non-standard port")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credentials in URL")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve {parsed.hostname}") from exc
    if not infos or not all(_ip_allowed(str(info[4][0])) for info in infos):
        raise UnsafeURLError("host resolves to a non-public address")


async def safe_get(http: httpx.AsyncClient, url: str) -> tuple[str, bytes]:
    """GET with per-hop SSRF validation and a response size cap. Returns (final_url, body)."""
    for _ in range(MAX_REDIRECTS + 1):
        await assert_public_url(url)
        async with http.stream("GET", url, follow_redirects=False) as resp:
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            resp.raise_for_status()
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > MAX_HTML_BYTES:
                    break
            return url, body
    raise UnsafeURLError("too many redirects")


class _PageParser(HTMLParser):
    """Dependency-free fallback extractor: title, og/meta description, paragraph text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.paragraphs: list[str] = []
        self._in: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in ("script", "style", "noscript", "nav", "footer", "header", "aside"):
            self._skip += 1
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                self.meta[key.lower()] = a["content"]
        elif tag in ("title", "p", "h1", "h2", "li", "blockquote") and not self._skip:
            self._in, self._buf = tag, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "nav", "footer", "header", "aside"):
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            text = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = text
            elif len(text) > 30:
                self.paragraphs.append(text)
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in and not self._skip:
            self._buf.append(data)


def extract_article(html: str, url: str) -> tuple[str | None, str, str | None]:
    """(title, main text, published date) from HTML."""
    try:
        import trafilatura

        doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
        if doc:
            data = doc if isinstance(doc, dict) else doc.as_dict()
            if data.get("text"):
                return data.get("title"), data["text"][:MAX_TEXT_CHARS], data.get("date")
    except ImportError:
        pass
    p = _PageParser()
    p.feed(html)
    title = p.meta.get("og:title") or p.title or None
    desc = p.meta.get("og:description") or p.meta.get("description") or ""
    text = "\n".join([desc, *p.paragraphs]).strip()[:MAX_TEXT_CHARS]
    published = p.meta.get("article:published_time")
    return title, text, published


async def fetch_article(http: httpx.AsyncClient, url: str) -> LinkContent:
    final_url, body = await safe_get(http, url)
    title, text, published = extract_article(body.decode("utf-8", errors="replace"), final_url)
    return LinkContent(url=final_url, kind="article", title=title, text=text, published=published)


async def fetch_x_post(http: httpx.AsyncClient, url: str) -> LinkContent:
    """Public X/Twitter post text via the public oEmbed endpoint (no login, no scraping)."""
    resp = await http.get(
        "https://publish.twitter.com/oembed", params={"url": url, "omit_script": "true"}
    )
    resp.raise_for_status()
    data = resp.json()
    p = _PageParser()
    p.feed(data.get("html", "").replace("<blockquote", "<p").replace("</blockquote>", "</p>"))
    text = " ".join(p.paragraphs) or ""
    return LinkContent(
        url=url,
        kind="post",
        title=None,
        text=text[:MAX_TEXT_CHARS],
        uploader=data.get("author_name"),
    )


class YtDlpDownloader:
    """Public media via yt-dlp (`media` extra). Short clips keep video for keyframes."""

    def __init__(
        self, max_duration_s: int = 3600, max_bytes: int = 80_000_000, keep_video_under_s: int = 180
    ) -> None:
        self.max_duration_s = max_duration_s
        self.max_bytes = max_bytes
        self.keep_video_under_s = keep_video_under_s

    def _opts(self, out_dir: Path, fmt: str) -> dict[str, Any]:
        return {
            "format": fmt,
            "outtmpl": str(out_dir / "media.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": self.max_bytes,
            "socket_timeout": 20,
            "retries": 2,
            "cookiefile": None,
        }

    def _download(self, url: str, out_dir: Path) -> LinkContent:
        from yt_dlp import YoutubeDL

        with YoutubeDL(self._opts(out_dir, "best")) as ydl:
            info = ydl.extract_info(url, download=False)
        duration = info.get("duration") or 0
        if duration and duration > self.max_duration_s:
            raise ValueError(f"media too long ({int(duration)} s)")
        fmt = (
            "best[height<=480]/best"
            if duration and duration <= self.keep_video_under_s
            else "bestaudio/best"
        )
        with YoutubeDL(self._opts(out_dir, fmt)) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
        text = "\n".join(x for x in (info.get("title"), info.get("description")) if x)
        return LinkContent(
            url=info.get("webpage_url") or url,
            kind="media",
            title=info.get("title"),
            text=text[:MAX_TEXT_CHARS],
            uploader=info.get("uploader"),
            published=info.get("upload_date"),
            duration_s=duration or None,
            media_path=path if Path(path).exists() else None,
        )

    async def fetch(self, url: str, out_dir: Path) -> LinkContent:
        if not is_media_host(url):
            raise UnsafeURLError("not an allowed media host")
        await assert_public_url(url)
        return await asyncio.wait_for(asyncio.to_thread(self._download, url, out_dir), 300)
