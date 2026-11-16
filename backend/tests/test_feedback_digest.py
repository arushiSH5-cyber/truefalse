"""Feedback, the human review queue, /wrong and the weekly digest."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.telegram import get_queue, get_reviews
from app.api.web import get_jobs, get_limiter
from app.core.config import Settings, get_settings
from app.core.jobs import InMemoryJobStore, PublicResult, PublicSource
from app.core.models import (
    CheckReport,
    Claim,
    ClaimDecision,
    ClaimJudgement,
    Confidence,
    Evidence,
    EvidenceDraft,
    Part,
    PartKind,
    Platform,
    Submission,
    Verdict,
)
from app.core.prefs import InMemoryPrefsStore
from app.core.ratelimit import InMemoryRateLimiter
from app.core.review import InMemoryReviewStore, ReviewItem, as_eval_row
from app.ingestion.handler import DIGEST_OFF, DIGEST_ON, REPORT_NOTHING, handle_inbound
from app.ingestion.telegram import parse_update
from app.main import create_app
from app.respond.digest import (
    EMPTY,
    InMemoryTrendStore,
    TrendEntry,
    compose,
    send_digests,
    trend_entries,
    week_key,
)
from app.worker import Extras, _record
from tests.fakes import FakeMessenger, FakeQueue

TOKEN = "review-secret-token"
HDR = {"X-Client-Id": "client-abc-123"}


def _claim(text: str = "Garlic cures covid") -> Claim:
    return Claim(
        original=text,
        english=text,
        topic="health",
        potentially_harmful=True,
        who=None,
        did=None,
        where=None,
        when=None,
        local_languages=[],
    )


def _evidence(draft: EvidenceDraft) -> Evidence:
    return Evidence(id="E1", **draft.model_dump())


def _report(
    draft: EvidenceDraft, verdict: Verdict = Verdict.FALSE, review: bool = False, claim: str = ""
) -> CheckReport:
    j = ClaimJudgement(
        claim_index=0,
        verdict=verdict,
        confidence=Confidence.HIGH,
        explanation="x",
        cited_evidence_ids=["E1"],
    )
    d = ClaimDecision(
        judgement=j,
        votes=[verdict] * 3,
        agreement=1.0,
        probability=0.9,
        judges_disagree=review,
        needs_human_review=review,
        review_reason="judges_disagree" if review else None,
    )
    return CheckReport(
        submission_id="s",
        language="en",
        claims=[_claim(claim or "Garlic cures covid")],
        judgements=[j],
        decisions=[d],
        overall=verdict,
        overall_confidence=Confidence.HIGH,
        evidence=[_evidence(draft)],
        warnings=[],
        reply_text="🔴 False",
        needs_human_review=review,
        review_reasons=["judges_disagree"] if review else [],
    )


@pytest.fixture
def env():
    app = create_app()
    jobs, reviews = InMemoryJobStore(), InMemoryReviewStore()
    settings = Settings(_env_file=None, review_token=TOKEN)  # type: ignore[call-arg]
    app.dependency_overrides.update(
        {
            get_settings: lambda: settings,
            get_queue: lambda: FakeQueue(),
            get_jobs: lambda: jobs,
            get_limiter: lambda: InMemoryRateLimiter(),
            get_reviews: lambda: reviews,
        }
    )
    return TestClient(app), jobs, reviews


async def _done_job(jobs: InMemoryJobStore) -> str:
    rec = await jobs.create()
    rec.status = "done"
    rec.result = PublicResult(
        verdict="false",
        verdict_label="False",
        confidence="high",
        language="en",
        reply_text="🔴 False",
        explanation="x",
        warnings=[],
        sources=[
            PublicSource(
                title="t", url="https://afp.com/x", publisher="AFP", tier=2, published=None
            )
        ],
        claims=["Call +1 415 555 0100 now, garlic cures covid"],
    )
    await jobs.update(rec)
    return rec.id


async def test_web_feedback_and_review_queue(env) -> None:
    client, jobs, reviews = env
    job_id = await _done_job(jobs)
    up = client.post(f"/api/check/{job_id}/feedback", json={"rating": "up"}, headers=HDR)
    assert up.status_code == 200 and up.json()["review_id"] is None
    down = client.post(
        f"/api/check/{job_id}/feedback",
        json={"rating": "down", "note": "WHO says otherwise, mail me at a@b.com"},
        headers=HDR,
    )
    assert down.json()["status"] == "queued_for_review"
    item = await reviews.get(down.json()["review_id"])
    assert item and item.kind == "user_report" and item.sources == ["https://afp.com/x"]
    assert "[number]" in item.claims[0] and "[email]" in (item.note or "")
    missing = client.post("/api/check/nope/feedback", json={"rating": "up"}, headers=HDR)
    assert missing.status_code == 404
    assert (
        client.post(f"/api/check/{job_id}/feedback", json={"rating": "meh"}, headers=HDR)
    ).status_code == 422

    # Reviewer endpoints need the token.
    assert client.get("/api/review").status_code == 401
    auth = {"Authorization": f"Bearer {TOKEN}"}
    listed = client.get("/api/review", headers=auth).json()
    assert [i["id"] for i in listed] == [item.id]
    assert client.get("/api/review/stats", headers=auth).json() == {
        "up": 1,
        "down": 1,
        "open_reviews": 1,
    }
    resolved = client.post(
        f"/api/review/{item.id}", json={"correct_verdict": "false"}, headers=auth
    ).json()
    assert resolved["status"] == "resolved"
    rows = client.get("/api/review/export.jsonl", headers=auth).text.splitlines()
    assert json.loads(rows[0])["expected"] == "false"
    assert client.get("/api/review", headers=auth).json() == []


def test_review_endpoints_off_without_token() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    assert TestClient(app).get("/api/review").status_code == 503


def test_eval_row_only_for_resolved_single_claims() -> None:
    item = ReviewItem(claims=["a claim"], language="hi")
    assert as_eval_row(item) is None
    item.status, item.correct_verdict = "resolved", Verdict.TRUE
    assert as_eval_row(item) == {
        "id": f"review-{item.id}",
        "language": "hi",
        "text": "a claim",
        "expected": "true",
        "source": None,
    }


def _tg(text: str) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 5, "type": "private"},
            "from": {"id": 9, "language_code": "en"},
            "text": text,
        },
    }


async def test_worker_records_and_bot_wrong_command(factcheck_draft) -> None:
    reviews, trends = InMemoryReviewStore(), InMemoryTrendStore()
    extras = Extras(reviews=reviews, trends=trends)
    inbound = parse_update(_tg("/wrong it was satire"), "salt")
    assert inbound.command == "wrong" and inbound.command_args == "it was satire"
    user = inbound.submission.user_hash
    m, q = FakeMessenger(), FakeQueue()

    assert await handle_inbound(inbound, m, q, InMemoryPrefsStore(), None, reviews) == (
        "report_nothing"
    )
    assert m.sent[-1][1] == REPORT_NOTHING

    sub = Submission(
        platform=Platform.TELEGRAM, user_hash=user, parts=[Part(kind=PartKind.TEXT, text="x")]
    )
    await _record(extras, sub, _report(factcheck_draft, review=True))
    assert [i.kind for i in reviews.items.values()] == ["auto_flag"]  # flagged by the panel
    assert await handle_inbound(inbound, m, q, InMemoryPrefsStore(), None, reviews) == "reported"
    reported = [i for i in reviews.items.values() if i.kind == "user_report"]
    assert reported[0].note == "it was satire" and not q.jobs
    # The recent answer is used once.
    assert await handle_inbound(inbound, m, q, InMemoryPrefsStore(), None, reviews) == (
        "report_nothing"
    )
    # Flagged answers never count towards the digest; confident ones do.
    assert trends.weeks == {}
    await _record(extras, sub, _report(factcheck_draft))
    assert sum(e.count for e in trends.weeks[week_key()].values()) == 1


async def test_digest_toggle_keeps_target_only_while_subscribed() -> None:
    store, m, q = InMemoryPrefsStore(), FakeMessenger(), FakeQueue()
    inbound = parse_update(_tg("/digest"), "salt")
    assert await handle_inbound(inbound, m, q, store) == "digest_toggled"
    prefs = await store.get(inbound.submission.user_hash)
    assert prefs and prefs.digest_opt_in and prefs.digest_target and m.sent[-1][1] == DIGEST_ON
    assert len(await store.digest_subscribers()) == 1
    await handle_inbound(inbound, m, q, store)
    prefs = await store.get(inbound.submission.user_hash)
    assert prefs and not prefs.digest_opt_in and prefs.digest_target is None
    assert m.sent[-1][1] == DIGEST_OFF and await store.digest_subscribers() == []


async def test_trends_skip_personal_data_and_unverified(factcheck_draft) -> None:
    assert len(trend_entries(_report(factcheck_draft))) == 1
    assert trend_entries(_report(factcheck_draft, claim="Call +1 415 555 0100 for the cure")) == []
    assert trend_entries(_report(factcheck_draft, verdict=Verdict.TRUE)) == []


async def test_send_digests_ranks_and_sends(factcheck_draft) -> None:
    trends, store, m, q = InMemoryTrendStore(), InMemoryPrefsStore(), FakeMessenger(), FakeQueue()
    week = "2026-W45"
    for _ in range(3):
        await trends.record(TrendEntry(claim="5G spreads covid", verdict=Verdict.FALSE), week)
    for _ in range(2):
        await trends.record(
            TrendEntry(
                claim="Garlic cures covid", verdict=Verdict.MISLEADING, source="https://a.b"
            ),
            week,
        )
    await trends.record(TrendEntry(claim="Only once", verdict=Verdict.FALSE), week)
    top = await trends.top(week)
    assert [e.claim for e in top] == ["5G spreads covid", "Garlic cures covid"]

    await handle_inbound(parse_update(_tg("/digest"), "salt"), m, q, store)
    m.sent.clear()
    assert await send_digests(store, trends, m, None, week) == 1
    text = m.sent[0][1]
    assert "1. 🔴 False: 5G spreads covid" in text and "https://a.b" in text
    assert "Only once" not in text
    assert compose([]).startswith(EMPTY)
