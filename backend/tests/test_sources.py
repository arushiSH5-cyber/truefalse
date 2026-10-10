import httpx
import pytest

from app.core.http import HttpError, get_json
from app.research.sources import factcheck_api, pubmed, semantic_scholar, web_search

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
  <Journal><Title>BMJ</Title><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue></Journal>
  <ArticleTitle>Vitamin C and <i>cancer</i></ArticleTitle>
  <Abstract><AbstractText Label="RESULTS">No survival benefit.</AbstractText>
   <AbstractText>High dose was safe.</AbstractText></Abstract>
  <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
 </Article></MedlineCitation></PubmedArticle>
 <PubmedArticle><MedlineCitation><PMID>222</PMID><Article>
  <ArticleTitle>Retracted</ArticleTitle><Abstract><AbstractText>x</AbstractText></Abstract>
  <PublicationTypeList><PublicationType>Retracted Publication</PublicationType></PublicationTypeList>
 </Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_factcheck_parse() -> None:
    data = {
        "claims": [
            {
                "text": "5G spreads COVID",
                "claimReview": [
                    {
                        "publisher": {"name": "Reuters"},
                        "url": "https://reuters.com/fc",
                        "title": "Fact check",
                        "reviewDate": "2020-04-10T00:00:00Z",
                        "textualRating": "False",
                    },
                    {"publisher": {"name": "NoUrl"}},
                ],
            }
        ]
    }
    (ev,) = factcheck_api.parse(data)
    assert ev.rating == "False" and ev.published == "2020-04-10" and ev.tier == 2


def test_semantic_scholar_parse_prefers_tldr() -> None:
    data = {
        "data": [
            {
                "title": "P",
                "url": "https://s2/p",
                "year": 2021,
                "tldr": {"text": "Short."},
                "abstract": "Long abstract",
                "venue": "Nature",
            },
            {"title": "No text", "url": "https://s2/q"},
        ]
    }
    (ev,) = semantic_scholar.parse(data)
    assert ev.quote == "Short." and ev.published == "2021" and ev.tier == 1


def test_pubmed_parse_skips_retracted() -> None:
    (ev,) = pubmed.parse_efetch(PUBMED_XML)
    assert ev.url == "https://pubmed.ncbi.nlm.nih.gov/111/"
    assert ev.title == "Vitamin C and cancer"
    assert ev.quote == "No survival benefit. High dose was safe."
    assert ev.published == "2022" and ev.publisher == "BMJ"


def test_brave_parse_tiers_by_domain() -> None:
    data = {
        "web": {
            "results": [
                {"title": "WHO", "url": "https://www.who.int/a", "description": "d1"},
                {
                    "title": "Blog",
                    "url": "https://blog.example/b",
                    "description": "d2",
                    "age": "2 days",
                },
            ]
        }
    }
    a, b = web_search.parse_brave(data)
    assert (a.tier, b.tier) == (1, 3) and a.publisher == "who.int"


async def test_get_json_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await get_json(client, "https://x", max_retries=2, backoff_s=0) == {"ok": 1}
    assert calls["n"] == 3


async def test_get_json_does_not_retry_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HttpError):
            await get_json(client, "https://x", backoff_s=0)
    assert calls["n"] == 1
