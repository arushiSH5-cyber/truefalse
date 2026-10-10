from datetime import UTC, datetime

import pytest

from app.core import prompts
from app.core.models import ClaimJudgement, Confidence, Evidence, Extraction, SourceKind, Verdict
from app.safety import topic_rules
from app.safety.pii_redact import redact

ALL_PROMPTS = [
    "claim_extract.v1",
    "research_agent.v1",
    "research_task.v1",
    "judge.v1",
    "judge_task.v1",
    "compose.v1",
    "compose_task.v1",
]


@pytest.mark.parametrize("name", ALL_PROMPTS)
def test_prompts_render(name: str) -> None:
    values = {k: "X" for k in prompts.placeholders(name)}
    out = prompts.render(name, **values)
    assert "{{" not in out


def test_render_rejects_missing_and_keeps_braces_in_values() -> None:
    with pytest.raises(prompts.PromptError):
        prompts.render("compose_task.v1", language="es")
    out = prompts.render(
        "compose_task.v1", language="es", language_name="Spanish", payload='{"a": "{{language}}"}'
    )
    assert '{"a": "{{language}}"}' in out


def test_redact() -> None:
    assert redact("mail me at x.y@z.org") == "mail me at [email]"
    assert "[number]" in redact("WhatsApp +91 98765 43210 now")
    assert redact("Between 2019-2021, 45% rose") == "Between 2019-2021, 45% rose"
    assert redact("see https://t.co/abc") == "see [link]"


def _extraction(**kw) -> Extraction:
    base = dict(
        language="en",
        language_name="English",
        script="Latin",
        romanized_or_mixed=False,
        looks_like_satire_or_opinion=False,
        claims=[],
    )
    return Extraction(**(base | kw))


def test_warnings_old_evidence_and_satire() -> None:
    ev = {
        "E1": Evidence(
            id="E1",
            source=SourceKind.WEB,
            tier=2,
            title="t",
            url="u",
            quote="q",
            published="2015-01-01",
        )
    }
    j = [
        ClaimJudgement(
            claim_index=0,
            verdict=Verdict.FALSE,
            confidence=Confidence.MEDIUM,
            explanation="e",
            cited_evidence_ids=["E1"],
        )
    ]
    w = topic_rules.warnings(
        _extraction(looks_like_satire_or_opinion=True, romanized_or_mixed=True),
        j,
        ev,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(w) == 3 and "2015" in w[2]


def test_safety_note_health(spanish_health_extraction) -> None:
    claims = spanish_health_extraction.claims
    false = [
        ClaimJudgement(
            claim_index=0,
            verdict=Verdict.FALSE,
            confidence=Confidence.HIGH,
            explanation="e",
            cited_evidence_ids=["E1"],
        )
    ]
    true = [false[0].model_copy(update={"verdict": Verdict.TRUE})]
    assert topic_rules.DANGER_NOTE in topic_rules.safety_note(claims, false)
    note = topic_rules.safety_note(claims, true)
    assert topic_rules.DANGER_NOTE not in note and topic_rules.HEALTH_NOTE in note
