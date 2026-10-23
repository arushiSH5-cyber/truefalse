"""The three systems compared by the eval.

- `full`: the TrueFalse pipeline (claim extraction, research, judge panel, grounding).
- `single_judge`: one LLM call per claim, no search: the proposal's single-LLM baseline.
- `lookup_only`: fact-check database lookup only (Google Fact Check Tools API + the local
  index when enabled). The publishers' ratings decide; no hit means Unverified.

Every system hides evidence from the item's `site` (the fact-checker the label came from), so
a system can't score by finding the answer key. The verdict cache is always off.
"""

import dataclasses
import time
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from app.core import prompts
from app.core.llm import StructuredLLM
from app.core.models import Evidence, EvidenceDraft, Part, PartKind, Platform, Submission, Verdict
from app.core.trace import RunTrace, current_trace
from app.matching.factcheck_index import FactCheckIndex, FactCheckMatch
from app.pipeline import Deps, run_check
from app.research.sources.base import Source
from app.research.tiers import domain_of
from app.verify.ratings import rating_verdict, resolve_ratings
from eval.datasets import EvalItem

LOOKUP_QUERY_CHARS = 300


class Prediction(BaseModel):
    system: str
    item_id: str
    verdict: Verdict
    needs_human_review: bool = False
    cost_usd: float = 0.0
    latency_s: float = 0.0
    error: str | None = None


class System(Protocol):
    name: str

    async def predict(self, item: EvalItem) -> Prediction: ...


def _hidden(url: str, site: str | None) -> bool:
    if not site:
        return False
    host, site = domain_of(url), site.lower().removeprefix("www.")
    return host == site or host.endswith("." + site)


class HideSite:
    """A Source that drops results from one domain (the item's own fact-checker)."""

    def __init__(self, inner: Source, site: str | None) -> None:
        self._inner = inner
        self._site = site
        self.kind = inner.kind

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        return [
            d for d in await self._inner.search(query, language) if not _hidden(d.url, self._site)
        ]


class HideSiteIndex:
    def __init__(self, inner: FactCheckIndex, site: str | None) -> None:
        self._inner = inner
        self._site = site

    async def search(self, queries: Sequence[str], k: int = 5) -> list[FactCheckMatch]:
        hits = await self._inner.search(queries, k)
        return [h for h in hits if not _hidden(h.to_evidence().url, self._site)]


def _submission(item: EvalItem) -> Submission:
    return Submission(
        platform=Platform.WEB,
        user_hash="eval",
        language_hint=None,  # like a WhatsApp forward: no UI-language hint
        parts=[Part(kind=PartKind.TEXT, text=item.text)],
    )


class FullSystem:
    name = "full"

    def __init__(self, deps: Deps, prices: tuple[float, float]) -> None:
        self._deps = dataclasses.replace(deps, verdicts=None)
        self._prices = prices

    async def predict(self, item: EvalItem) -> Prediction:
        deps = dataclasses.replace(
            self._deps,
            sources=[HideSite(s, item.site) for s in self._deps.sources],
            index=HideSiteIndex(self._deps.index, item.site) if self._deps.index else None,
        )
        trace, start = RunTrace(), time.monotonic()
        report = await run_check(_submission(item), deps, trace)
        return Prediction(
            system=self.name,
            item_id=item.id,
            verdict=report.overall,
            needs_human_review=report.needs_human_review,
            cost_usd=trace.cost_usd(*self._prices),
            latency_s=round(time.monotonic() - start, 2),
        )


class SingleJudgeOutput(BaseModel):
    verdict: Verdict
    reason: str


class SingleJudgeSystem:
    name = "single_judge"

    def __init__(self, llm: StructuredLLM, prices: tuple[float, float]) -> None:
        self._llm = llm
        self._prices = prices

    async def predict(self, item: EvalItem) -> Prediction:
        trace, start = RunTrace(), time.monotonic()
        token = current_trace.set(trace)
        try:
            out = await self._llm.structured(
                stage="single_judge",
                system=prompts.render("single_judge.v1"),
                user=prompts.render("single_judge_task.v1", claim=item.text),
                output=SingleJudgeOutput,
                effort="medium",
                max_tokens=4000,
            )
        finally:
            current_trace.reset(token)
        return Prediction(
            system=self.name,
            item_id=item.id,
            verdict=out.verdict,
            cost_usd=trace.cost_usd(*self._prices),
            latency_s=round(time.monotonic() - start, 2),
        )


def lookup_verdict(evidence: Sequence[Evidence]) -> Verdict:
    """Majority of the publishers' ratings; ties go to the best-ranked rating."""
    rated = [v for e in evidence if (v := rating_verdict(e)) and v is not Verdict.UNVERIFIED]
    if not rated:
        return Verdict.UNVERIFIED
    counts = Counter(rated)
    top = max(counts.values())
    return next(v for v in rated if counts[v] == top)


class LookupOnlySystem:
    name = "lookup_only"

    def __init__(
        self,
        sources: Sequence[Source],
        index: FactCheckIndex | None,
        llm: StructuredLLM,
        prices: tuple[float, float],
    ) -> None:
        from app.core.models import SourceKind

        self._sources = [s for s in sources if s.kind is SourceKind.FACTCHECK]
        self._index = index
        self._llm = llm  # only to read ratings the keyword table doesn't know
        self._prices = prices

    @property
    def available(self) -> bool:
        return bool(self._sources or self._index)

    async def predict(self, item: EvalItem) -> Prediction:
        trace, start = RunTrace(), time.monotonic()
        query = item.text[:LOOKUP_QUERY_CHARS]
        drafts: list[EvidenceDraft] = []
        for source in self._sources:
            for lang in dict.fromkeys([item.language if item.language != "und" else None, None]):
                drafts += await HideSite(source, item.site).search(query, lang)
        if self._index is not None:
            drafts += [
                h.to_evidence() for h in await HideSiteIndex(self._index, item.site).search([query])
            ]
        evidence = [Evidence(id=f"E{i}", **d.model_dump()) for i, d in enumerate(drafts, 1)]
        token = current_trace.set(trace)
        try:
            evidence = await resolve_ratings(self._llm, evidence)
        finally:
            current_trace.reset(token)
        return Prediction(
            system=self.name,
            item_id=item.id,
            verdict=lookup_verdict(evidence),
            cost_usd=trace.cost_usd(*self._prices),
            latency_s=round(time.monotonic() - start, 2),
        )
