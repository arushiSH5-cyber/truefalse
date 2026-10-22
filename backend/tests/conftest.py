import pytest

from app.core.models import (
    Claim,
    EvidenceDraft,
    Extraction,
    Part,
    PartKind,
    Platform,
    SourceKind,
    Submission,
)


@pytest.fixture
def submission() -> Submission:
    return Submission(
        platform=Platform.TELEGRAM,
        user_hash="u" * 32,
        parts=[
            Part(kind=PartKind.TEXT, text="El agua caliente con limón cura el cáncer. ¡Compártelo!")
        ],
    )


@pytest.fixture
def spanish_health_extraction() -> Extraction:
    return Extraction(
        language="es",
        language_name="Spanish",
        script="Latin",
        romanized_or_mixed=False,
        looks_like_satire_or_opinion=False,
        claims=[
            Claim(
                original="El agua caliente con limón cura el cáncer",
                english="Hot water with lemon cures cancer",
                topic="health",
                potentially_harmful=True,
                who="hot water with lemon",
                did="cures cancer",
                where=None,
                when=None,
                local_languages=[],
            )
        ],
    )


@pytest.fixture
def factcheck_draft() -> EvidenceDraft:
    return EvidenceDraft(
        source=SourceKind.FACTCHECK,
        tier=2,
        title="No, hot lemon water does not cure cancer",
        url="https://factcheck.afp.com/lemon-cancer",
        quote='Claim reviewed: "Hot lemon water cures cancer". Rating: False.',
        published="2024-03-01",
        publisher="AFP Factuel",
        rating="False",
    )


@pytest.fixture
def pubmed_draft() -> EvidenceDraft:
    return EvidenceDraft(
        source=SourceKind.PUBMED,
        tier=1,
        title="Citrus intake and cancer outcomes: a systematic review",
        url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
        quote="No clinical evidence that citrus consumption treats established cancer.",
        published="2023",
        publisher="Nutrients",
    )
