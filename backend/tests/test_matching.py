import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.core.models import ClaimJudgement, Confidence, JudgePanelOutput, SourceKind, Verdict
from app.matching.bm25 import BM25, tokenize
from app.matching.claimreview import FactCheckRecord, dedupe, from_factcheck_api, parse_feed
from app.matching.factcheck_index import InMemoryFactCheckIndex, rrf
from app.pipeline import Deps, PipelineConfig, match_factchecks, run_check
from tests.fakes import ConceptEmbedder, FakeLLM, FakeSource

FEED = Path(__file__).parent / "data" / "claimreview_feed.json"


def _rec(url: str, text: str, rating: str = "False") -> FactCheckRecord:
    return FactCheckRecord(review_url=url, claim_text=text, rating=rating, publisher="FC")


CORPUS = [
    _rec("https://fc/lemon", "Hot lemon water cures cancer"),
    _rec("https://fc/5g", "5G towers spread coronavirus"),
    _rec("https://fc/vaccine-chip", "Vaccines contain microchips"),
    _rec("https://fc/garlic", "Garlic prevents infection with the new coronavirus"),
]


def test_tokenize_handles_scripts() -> None:
    assert tokenize("Hot LEMON water!") == ["hot", "lemon", "water"]
    assert tokenize("नींबू पानी") == ["नींबू", "पानी"]
    assert tokenize("柠檬水治癌") == ["柠檬", "檬水", "水治", "治癌"]


def test_bm25_ranks_relevant_doc_first() -> None:
    scores = BM25([r.claim_text for r in CORPUS]).scores("does 5G spread the coronavirus")
    assert int(np.argmax(scores)) == 1


def test_rrf() -> None:
    fused = rrf([["a", "b", "c"], ["b", "a"]])
    assert max(fused, key=lambda key: fused[key]) in {"a", "b"}
    assert fused["c"] < fused["a"]


def test_parse_datafeed() -> None:
    records = list(parse_feed(json.loads(FEED.read_text())))
    assert len(records) == 2
    en, es = records
    assert en.rating == "False" and en.claimant == "Viral post"
    assert en.review_date == date(2023, 5, 2) and en.publisher_site == "factcheck.example.org"
    assert es.claim_language == "es" and es.rating == "Falso" and es.publisher == "Verifica Ejemplo"


def test_parse_api_and_dedupe() -> None:
    data = {
        "claims": [
            {
                "text": "c",
                "claimant": "x",
                "claimDate": "2021-01-01T00:00:00Z",
                "claimReview": [
                    {
                        "url": "https://u",
                        "publisher": {"name": "P", "site": "p.org"},
                        "textualRating": "Mostly false",
                        "languageCode": "en",
                    }
                ],
            }
        ]
    }
    records = list(from_factcheck_api(data))
    assert records[0].claim_date == date(2021, 1, 1)
    assert len(dedupe(records + records)) == 1


@pytest.fixture
async def index() -> InMemoryFactCheckIndex:
    idx = InMemoryFactCheckIndex(ConceptEmbedder())
    await idx.add(CORPUS)
    return idx


@pytest.mark.parametrize(
    ("queries", "expected"),
    [
        (["El agua caliente con limón cura el cáncer"], "https://fc/lemon"),  # Spanish
        (["गर्म नींबू पानी कैंसर ठीक करता है"], "https://fc/lemon"),  # Hindi
        (["Las antenas 5G propagan el coronavirus"], "https://fc/5g"),  # Spanish
        (["疫苗含有芯片"], "https://fc/vaccine-chip"),  # Chinese
        (["chanjo zina microchip"], "https://fc/vaccine-chip"),  # Swahili + English code-mix
    ],
)
async def test_cross_language_matching(index, queries, expected) -> None:
    hits = await index.search(queries, k=2)
    assert hits[0].record.review_url == expected
    assert hits[0].cosine > 0.9


async def test_match_stage_uses_threshold(index, spanish_health_extraction) -> None:
    drafts, matched = await match_factchecks(
        index, spanish_health_extraction.claims, PipelineConfig(match_strong_threshold=0.9)
    )
    assert matched == 1 and drafts[0].url == "https://fc/lemon"
    assert drafts[0].source is SourceKind.FACTCHECK
    none, zero = await match_factchecks(
        index, spanish_health_extraction.claims, PipelineConfig(match_strong_threshold=1.01)
    )
    assert none == [] and zero == 0


async def test_pipeline_seeds_research_with_matches(index, submission, spanish_health_extraction):
    llm = FakeLLM().queue("extract", spanish_health_extraction)
    llm.queue_panel(
        JudgePanelOutput(
            judgements=[
                ClaimJudgement(
                    claim_index=0,
                    verdict=Verdict.FALSE,
                    confidence=Confidence.HIGH,
                    explanation="e",
                    cited_evidence_ids=["E1"],
                )
            ]
        ),
    )
    llm.queue("compose", Exception("fallback to English"))
    llm.tool_script = [("search_web", {"query": f"q{i}", "language": None}) for i in range(10)]
    web = FakeSource(SourceKind.WEB, [])
    report = await run_check(
        submission, Deps(llm, [web], PipelineConfig(match_strong_threshold=0.9), index=index)
    )
    assert report.evidence[0].id == "E1" and report.evidence[0].url == "https://fc/lemon"
    assert len(web.queries) == 2  # every claim matched -> research budget capped at 2
    research_call = next(c for c in llm.calls if c["stage"] == "research")
    assert "https://fc/lemon" not in research_call["user"]  # model sees ids/quotes, not URLs
    assert '"id": "E1"' in research_call["user"]
    assert report.overall is Verdict.FALSE


@pytest.mark.ml
async def test_real_multilingual_embedder_cross_language() -> None:
    pytest.importorskip("sentence_transformers")
    from app.matching.embeddings import SentenceTransformerEmbedder

    idx = InMemoryFactCheckIndex(SentenceTransformerEmbedder())
    await idx.add(CORPUS)
    for query, expected in [
        ("El agua caliente con limón cura el cáncer", "https://fc/lemon"),
        ("5G टावर कोरोना वायरस फैलाते हैं", "https://fc/5g"),
        ("疫苗里有芯片", "https://fc/vaccine-chip"),
    ]:
        assert (await idx.search([query], k=1))[0].record.review_url == expected


@pytest.mark.integration
@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs Postgres")
async def test_postgres_index_roundtrip() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.matching.factcheck_index import PgFactCheckIndex

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE factchecks"))
    idx = PgFactCheckIndex(engine, ConceptEmbedder(dim=768))
    assert await idx.upsert(CORPUS) == 4
    assert await idx.upsert(CORPUS[:1]) == 1  # upsert is idempotent
    hits = await idx.search(
        ["El agua caliente con limón cura el cáncer", "lemon water cancer"], k=2
    )
    assert hits[0].record.review_url == "https://fc/lemon"
    assert hits[0].cosine > 0.9 and hits[0].bm25 > 0
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM factchecks"))).scalar() == 4
    await engine.dispose()
