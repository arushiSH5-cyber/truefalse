"""Evidence retrieval across languages: news sources, tool fan-out, guaranteed fact-check lookups."""

import httpx
import pytest

from app.core.models import Claim, EvidenceDraft, SourceKind
from app.research import agent
from app.research.agent import EvidenceStore, baseline_searches, build_tools, research
from app.research.sources.news import GdeltNewsSource, parse_brave_news, parse_gdelt
from tests.fakes import FakeLLM, FakeSource


def _draft(url: str, kind: SourceKind = SourceKind.NEWS) -> EvidenceDraft:
    return EvidenceDraft(source=kind, tier=3, title="t", url=url, quote="q")


def _claim(english: str, original: str, **kw: object) -> Claim:
    return Claim.model_validate(
        {
            "original": original,
            "english": english,
            "topic": "emergency",
            "potentially_harmful": True,
            "who": None,
            "did": None,
            "where": None,
            "when": None,
            "local_languages": [],
        }
        | kw
    )


# ---- parsers -------------------------------------------------------------------------------


def test_parse_brave_news() -> None:
    data = {
        "results": [
            {
                "title": "Dam did not burst, officials say",
                "url": "https://www.reuters.com/world/x",
                "description": "Authorities denied rumors...",
                "age": "2 days ago",
                "meta_url": {"hostname": "www.reuters.com"},
            },
            {"title": "", "description": "", "url": "https://empty"},
            {"title": "no url"},
        ]
    }
    [d] = parse_brave_news(data)
    assert d.source is SourceKind.NEWS and d.tier == 2 and d.publisher == "www.reuters.com"


def test_parse_gdelt() -> None:
    data = {
        "articles": [
            {
                "url": "https://www.nation.africa/kenya/news/x",
                "title": "Tume yakanusha uvumi wa siku ya kura",
                "seendate": "20261020T091500Z",
                "domain": "nation.africa",
                "language": "Swahili",
                "sourcecountry": "Kenya",
            },
            {"url": "https://x", "title": ""},
        ]
    }
    [d] = parse_gdelt(data)
    assert d.published == "2026-10-20" and d.publisher == "nation.africa"
    assert "Kenya, Swahili" in d.quote and d.quote.startswith("Headline: Tume")


async def test_gdelt_language_filter_uses_language_name() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"articles": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        src = GdeltNewsSource(client)
        src.MIN_INTERVAL_S = 0
        await src.search("siku ya kura", "sw")
        await src.search("vote day", "xx-unknown")
    assert seen[0].params["query"] == "siku ya kura sourcelang:swahili"
    assert seen[1].params["query"] == "vote day"  # unknown language: no filter, never an error


# ---- tools ---------------------------------------------------------------------------------


async def test_one_tool_per_kind_fans_out_and_survives_one_failure() -> None:
    ok = FakeSource(SourceKind.NEWS, [_draft("https://a")])
    broken = FakeSource(SourceKind.NEWS, error=RuntimeError("rate limited"))
    web = FakeSource(SourceKind.WEB, [_draft("https://w", SourceKind.WEB)])
    tools = build_tools([ok, broken, web], EvidenceStore())
    assert [t.name for t in tools] == ["search_news", "search_web"]
    out = await tools[0].handler({"query": "dam burst", "language": "hi"})
    assert '"id": "E1"' in out
    assert ok.queries == broken.queries == [("dam burst", "hi")]


async def test_tool_raises_when_every_provider_fails() -> None:
    a = FakeSource(SourceKind.NEWS, error=RuntimeError("a"))
    b = FakeSource(SourceKind.NEWS, error=RuntimeError("b"))
    (tool,) = build_tools([a, b], EvidenceStore())
    with pytest.raises(RuntimeError):
        await tool.handler({"query": "x", "language": None})


# ---- guaranteed fact-check lookups ---------------------------------------------------------


async def test_baseline_searches_english_and_original_language() -> None:
    fc = FakeSource(SourceKind.FACTCHECK, [_draft("https://fc", SourceKind.FACTCHECK)])
    web = FakeSource(SourceKind.WEB)
    store = EvidenceStore()
    claims = [
        _claim("The dam burst", "बांध टूट गया"),
        _claim("Call +91 98765 43210 for help", "मदद के लिए +91 98765 43210 पर कॉल करें"),
    ]
    await baseline_searches([fc, web], claims, "hi", store)
    assert fc.queries[:2] == [("The dam burst", None), ("बांध टूट गया", "hi")]
    assert all("98765" not in q for q, _ in fc.queries)  # PII redacted
    assert web.queries == []  # only the fact-check database is guaranteed
    assert len(store.all()) == 1  # same URL deduplicated


async def test_baseline_searches_english_message_single_query_and_failure_ok() -> None:
    fc = FakeSource(SourceKind.FACTCHECK, error=RuntimeError("down"))
    store = EvidenceStore()
    await baseline_searches([fc], [_claim("The dam burst", "The dam burst")], "en", store)
    assert fc.queries == [("The dam burst", None)]
    assert store.all() == []


async def test_research_runs_baseline_even_with_zero_agent_budget(monkeypatch) -> None:
    monkeypatch.setattr(agent, "BASELINE_TIMEOUT_S", 5.0)
    fc = FakeSource(SourceKind.FACTCHECK, [_draft("https://fc", SourceKind.FACTCHECK)])
    llm = FakeLLM()
    evidence = await research(
        llm, [fc], [_claim("x", "y")], language="es", max_tool_calls=0, time_budget_s=10
    )
    assert [e.url for e in evidence] == ["https://fc"]
    assert not [c for c in llm.calls if c["stage"] == "research"]


async def test_research_prompt_lists_claim_parts_and_local_languages() -> None:
    llm = FakeLLM()
    claim = _claim(
        "Voting day moved to Sunday",
        "Siku ya kura imehamishwa hadi Jumapili",
        who="election commission",
        did="moved voting day to Sunday",
        where="Kenya",
        local_languages=["sw", "en"],
    )
    await research(
        llm,
        [FakeSource(SourceKind.NEWS)],
        [claim],
        language="sw",
        max_tool_calls=2,
        time_budget_s=10,
    )
    user = next(c for c in llm.calls if c["stage"] == "research")["user"]
    assert "where: Kenya" in user and "local languages: sw, en" in user
