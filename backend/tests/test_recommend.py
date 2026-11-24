"""For you: feed parsing, AI picks limited to real articles, fallbacks, and the endpoint."""

from fastapi.testclient import TestClient

from app.api.web import get_feeds, get_limiter
from app.core.config import Settings, get_settings
from app.core.ratelimit import InMemoryRateLimiter
from app.main import create_app
from app.recommend import FeedItem, _Pick, _Picks, parse_feed, recommend

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Lemon water does not cure cancer</title><link>https://fc.example/lemon</link>
<description>&lt;p&gt;No study shows it.&lt;/p&gt;</description>
<pubDate>Mon, 23 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>5G towers do not spread viruses</title>
<link rel="alternate" href="https://fc.example/5g"/><updated>2026-11-22T08:00:00Z</updated>
<summary>Radio waves can't carry viruses.</summary></entry></feed>"""


def _items() -> list[FeedItem]:
    return [
        *parse_feed(RSS, "Snopes", "en"),
        *parse_feed(ATOM, "Lead Stories", "en"),
        FeedItem(
            title="Election date rumor", url="https://fc.example/vote", publisher="X", language="en"
        ),
    ]


def test_parse_rss_and_atom() -> None:
    rss = parse_feed(RSS, "Snopes", "en")
    assert [i.title for i in rss] == ["Lemon water does not cure cancer"]
    assert rss[0].summary == "No study shows it." and rss[0].published
    (atom,) = parse_feed(ATOM, "Lead Stories", "en")
    assert atom.url == "https://fc.example/5g" and atom.summary.startswith("Radio waves")
    assert parse_feed("not xml", "X", "en") == []


class PickingLLM:
    def __init__(self, picks: _Picks | Exception) -> None:
        self.picks = picks
        self.user = ""

    async def structured(self, *, user: str, **_: object) -> _Picks:
        self.user = user
        if isinstance(self.picks, Exception):
            raise self.picks
        return self.picks


async def test_ai_can_only_pick_real_articles() -> None:
    llm = PickingLLM(
        _Picks(
            topics=["Health"],
            picks=[
                _Pick(index=0, why="Also about cancer"),
                _Pick(index=99, why="made up"),
                _Pick(index=0, why="dup"),
            ],
        )
    )
    out = await recommend(_items(), ["Hot lemon water cures cancer"], "en", llm)  # type: ignore[arg-type]
    assert out.topics == ["Health"]
    assert [(i.url, i.why) for i in out.items] == [
        ("https://fc.example/lemon", "Also about cancer")
    ]
    assert (
        "Hot lemon water cures cancer" in llm.user
        and "Lemon water does not cure cancer" in llm.user
    )


async def test_fallbacks_without_ai_or_history() -> None:
    failing = PickingLLM(RuntimeError("quota"))
    out = await recommend(_items(), ["5G towers spread viruses"], "en", failing)  # type: ignore[arg-type]
    assert out.items[0].url == "https://fc.example/5g"  # word overlap
    latest = await recommend(_items(), [], "en", None)
    assert latest.topics == [] and len(latest.items) == 3
    assert (await recommend([], ["x"], "en", None)).items == []


class FakeFeeds:
    http = None

    async def items(self) -> list[FeedItem]:
        return _items()


def test_endpoint_without_history_lists_latest() -> None:
    app = create_app()
    app.dependency_overrides.update(
        {
            get_settings: lambda: Settings(_env_file=None),
            get_limiter: InMemoryRateLimiter,
            get_feeds: FakeFeeds,
        }
    )
    client = TestClient(app)
    resp = client.post("/api/for-you", json={"recent": []}, headers={"X-Client-Id": "client12345"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 3
    assert client.post("/api/for-you", json={}).status_code == 422  # needs a client id
