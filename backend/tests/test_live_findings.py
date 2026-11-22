"""Bugs found by the first live check: unbacked verdicts, off-topic quotes, secrets in logs."""

import logging

import httpx

from app.core.logging import configure_logging
from app.core.models import (
    ClaimDecision,
    ClaimJudgement,
    Confidence,
    Evidence,
    GroundedExplanation,
    GroundedSentence,
    GroundingOutput,
    SourceKind,
    Verdict,
)
from app.research.sources.factcheck_api import GoogleFactCheckSource
from app.research.sources.wikipedia import WikipediaSource
from app.safety.grounding_check import ground
from tests.fakes import FakeLLM


async def test_verdict_with_no_supported_sentence_becomes_unverified() -> None:
    evidence = Evidence(
        id="E1",
        source=SourceKind.WEB,
        tier=3,
        title="List",
        url="https://x.org/a",
        quote="Off topic.",
    )
    decision = ClaimDecision(
        judgement=ClaimJudgement(
            claim_index=0,
            verdict=Verdict.FALSE,
            confidence=Confidence.LOW,
            explanation="Garlic does not prevent it.",
            cited_evidence_ids=["E1"],
        ),
        votes=[Verdict.FALSE] * 3,
        agreement=1.0,
        probability=0.6,
        judges_disagree=False,
    )
    none = GroundedSentence(index=0, supported=False, evidence_ids=[])
    output = GroundingOutput(explanations=[GroundedExplanation(claim_index=0, sentences=[none])])
    (out,) = await ground(FakeLLM().queue("ground", output), [decision], {"E1": evidence})
    assert out.judgement.verdict is Verdict.UNVERIFIED and out.judgement.cited_evidence_ids == []


async def test_wikipedia_quotes_the_passage_about_the_query() -> None:
    filler = "\n".join(
        f"Paragraph {i} is about other unproven methods in general." for i in range(40)
    )
    article = (
        f"Intro about many unproven methods.\n{filler}\nEating garlic has no proven effect on it."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"pageid": 7}]}})
        page = {
            "title": "Methods",
            "fullurl": "https://en.wikipedia.org/wiki/M",
            "extract": article,
        }
        return httpx.Response(200, json={"query": {"pages": [page]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        (found,) = await WikipediaSource(http).search("garlic unproven methods")
    assert "garlic has no proven effect" in found.quote


async def test_factcheck_key_travels_in_a_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await GoogleFactCheckSource(http, "secret-key").search("garlic")
    assert "secret-key" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "secret-key"


def test_request_urls_are_not_logged() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)  # what a root logger at INFO gives it
    configure_logging("INFO")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_reply_writer_is_told_the_script() -> None:
    """A Devanagari Hindi message got its answer in Latin letters: compose never saw the script."""
    from app.core.models import Extraction
    from app.pipeline import reply_language_name

    hindi = Extraction(
        language="hi",
        language_name="Hindi",
        script="Devanagari",
        romanized_or_mixed=False,
        looks_like_satire_or_opinion=False,
        claims=[],
    )
    assert reply_language_name(hindi, None) == "Hindi, written in Devanagari script"
    romanized = hindi.model_copy(update={"script": "Latin", "romanized_or_mixed": True})
    assert reply_language_name(romanized, None) == "Hindi, romanized or mixed like the message"
    assert reply_language_name(hindi, "es") == "Spanish"  # the person picked a reply language
