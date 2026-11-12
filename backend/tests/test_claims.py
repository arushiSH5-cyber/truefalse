"""Claim splitter: structured {who, did, where, when} frame and cleanup."""

from app.claims.extract import extract_claims
from app.core.models import Claim, Extraction
from tests.fakes import FakeLLM


def _claim(english: str, langs: list[str], **frame: str | None) -> Claim:
    return Claim(
        original=english,
        english=english,
        topic="election",
        potentially_harmful=False,
        who=frame.get("who"),
        did=frame.get("did"),
        where=frame.get("where"),
        when=frame.get("when"),
        local_languages=langs,
    )


async def test_extract_keeps_frame_dedupes_and_cleans_languages() -> None:
    a = _claim(
        "The election commission moved voting day to Sunday",
        ["sw", " en", "sw", "", "fr", "de", "it"],
        who="the election commission",
        did="moved voting day to Sunday",
        where="Kenya",
        when=None,
    )
    llm = FakeLLM().queue(
        "extract",
        Extraction(
            language="sw",
            language_name="Swahili",
            script="Latin",
            romanized_or_mixed=False,
            looks_like_satire_or_opinion=False,
            claims=[
                a,
                a.model_copy(
                    update={"english": "  the election commission moved voting day to sunday "}
                ),
            ],
        ),
    )
    out = await extract_claims(
        llm, "Tume imehamisha siku ya kupiga kura hadi Jumapili", max_claims=3
    )
    assert len(out.claims) == 1
    c = out.claims[0]
    assert c.local_languages == ["sw", "en", "fr", "de"]
    assert c.frame() == (
        "who: the election commission; did: moved voting day to Sunday; where: Kenya"
    )
    assert "claim_extract.v2" not in llm.calls[0]["system"]  # rendered text, not the name
    assert "local_languages" in llm.calls[0]["system"]


def test_frame_empty_when_nothing_known() -> None:
    assert _claim("x", []).frame() == ""
