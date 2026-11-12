"""Agentic research: the LLM plans queries and calls tiered source tools.

Before the agent runs, code searches the fact-check database for every claim in English and in
the message's language, so that lookup never depends on the model remembering to do it.
Every result is stored as Evidence with a stable id (E1, E2, ...) that judges must cite.
Queries are PII-redacted before they leave the system.
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from app.core import prompts
from app.core.llm import LLMClient, Tool
from app.core.logging import get_logger
from app.core.models import Claim, Evidence, EvidenceDraft, SourceKind
from app.research.sources.base import Source
from app.safety.pii_redact import redact

log = get_logger(__name__)

TOOL_DESCRIPTIONS = {
    SourceKind.FACTCHECK: (
        "search_factchecks",
        "Search published fact-checks (ClaimReview) from IFCN fact-checkers. Try this first. "
        "Use short keyword queries. Optionally pass a language code to find local fact-checks.",
    ),
    SourceKind.SEMANTIC_SCHOLAR: (
        "search_papers",
        "Search peer-reviewed scientific papers (Semantic Scholar). English keyword queries.",
    ),
    SourceKind.PUBMED: (
        "search_pubmed",
        "Search PubMed biomedical literature. Use for any health, medicine, or cure claim. "
        "English keyword queries; boolean operators allowed.",
    ),
    SourceKind.WEB: (
        "search_web",
        "General web search. Add site: filters to target trusted sources, e.g. "
        "'site:who.int', 'site:reuters.com', 'site:apnews.com', or a national health agency.",
    ),
    SourceKind.NEWS: (
        "search_news",
        "Search recent news coverage from many countries and languages. Use for events, quotes, "
        "disasters, elections and anything recent. To find local coverage, write the query in "
        "the local language and pass its language code.",
    ),
}
BASELINE_TIMEOUT_S = 15.0

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query"},
        "language": {
            "type": ["string", "null"],
            "description": "Optional BCP-47 language code to search in, else null",
        },
    },
    "required": ["query", "language"],
    "additionalProperties": False,
}


class EvidenceStore:
    def __init__(self, max_items: int = 40) -> None:
        self._items: dict[str, Evidence] = {}
        self._by_url: dict[str, str] = {}
        self._max = max_items

    def add(self, drafts: Sequence[EvidenceDraft]) -> list[Evidence]:
        added: list[Evidence] = []
        for d in drafts:
            if d.url in self._by_url:
                added.append(self._items[self._by_url[d.url]])
                continue
            if len(self._items) >= self._max:
                break
            ev = Evidence(id=f"E{len(self._items) + 1}", **d.model_dump())
            self._items[ev.id] = ev
            self._by_url[ev.url] = ev.id
            added.append(ev)
        return added

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def all(self) -> list[Evidence]:
        return list(self._items.values())


def _render_for_model(items: Sequence[Evidence]) -> str:
    if not items:
        return "No results."
    return json.dumps(
        [
            {
                "id": e.id,
                "tier": e.tier,
                "title": e.title,
                "publisher": e.publisher,
                "date": e.published,
                "rating": e.rating,
                "quote": e.quote,
            }
            for e in items
        ],
        ensure_ascii=False,
    )


async def search_all(
    sources: Sequence[Source], query: str, language: str | None
) -> list[EvidenceDraft]:
    """Query every source of one kind; a failing provider is skipped unless all of them fail."""
    results = await asyncio.gather(
        *(s.search(query, language) for s in sources), return_exceptions=True
    )
    drafts: list[EvidenceDraft] = []
    errors: list[BaseException] = []
    for r in results:
        if isinstance(r, BaseException):
            errors.append(r)
            log.warning("source_failed", error=str(r))
        else:
            drafts += r
    if errors and len(errors) == len(results):
        raise errors[0]
    return drafts


def build_tools(sources: Sequence[Source], store: EvidenceStore) -> list[Tool]:
    """One tool per source kind; a tool with several providers (e.g. news) queries them all."""
    by_kind: dict[SourceKind, list[Source]] = {}
    for source in sources:
        by_kind.setdefault(source.kind, []).append(source)
    tools: list[Tool] = []
    for kind, group in by_kind.items():
        name, description = TOOL_DESCRIPTIONS[kind]

        async def handler(
            args: dict[str, Any], _group: list[Source] = group, _name: str = name
        ) -> str:
            query = redact(str(args.get("query", "")))[:300]
            log.info("research_query", tool=_name)
            drafts = await search_all(_group, query, args.get("language"))
            return _render_for_model(store.add(drafts))

        tools.append(Tool(name, description, QUERY_SCHEMA, handler))
    return tools


async def baseline_searches(
    sources: Sequence[Source], claims: Sequence[Claim], language: str, store: EvidenceStore
) -> None:
    """Fact-check lookups for every claim, in English and in the message's own language."""
    factcheck = [s for s in sources if s.kind is SourceKind.FACTCHECK]
    if not factcheck:
        return
    queries: list[tuple[str, str | None]] = []
    base = language.split("-")[0].lower()
    for c in claims:
        queries.append((c.english, None))  # no language filter: finds reviews in any language
        if base != "en" and c.original.strip() and c.original.strip() != c.english.strip():
            queries.append((c.original, base))
    queries = list(dict.fromkeys((redact(q)[:300], lang) for q, lang in queries))
    try:
        async with asyncio.timeout(BASELINE_TIMEOUT_S):
            results = await asyncio.gather(
                *(search_all(factcheck, q, lang) for q, lang in queries), return_exceptions=True
            )
    except TimeoutError:
        log.warning("baseline_search_timeout")
        return
    for r in results:
        if isinstance(r, BaseException):
            log.warning("baseline_search_failed", error=str(r))
        else:
            store.add(r)
    log.info("baseline_searches", queries=len(queries), evidence=len(store.all()))


def _claims_block(claims: Sequence[Claim]) -> str:
    lines = []
    for i, c in enumerate(claims):
        line = f"{i}. [{c.topic}] {c.english}  (original: {c.original})"
        if c.frame():
            line += f"\n   parts: {c.frame()}"
        if c.local_languages:
            line += f"\n   local languages: {', '.join(c.local_languages)}"
        lines.append(line)
    return "\n".join(lines)


async def research(
    llm: LLMClient,
    sources: Sequence[Source],
    claims: Sequence[Claim],
    *,
    language: str,
    max_tool_calls: int,
    time_budget_s: float,
    seed: Sequence[EvidenceDraft] = (),
) -> list[Evidence]:
    store = EvidenceStore()
    store.add(seed)
    if not sources:
        return store.all()
    await baseline_searches(sources, claims, language, store)
    if max_tool_calls == 0:
        return store.all()
    found = store.all()
    calls = await llm.run_tools(
        stage="research",
        system=prompts.render("research_agent.v2"),
        user=prompts.render(
            "research_task.v2",
            claims=_claims_block(claims),
            language=language,
            already_found=_render_for_model(found) if found else "Nothing yet.",
        ),
        tools=build_tools(sources, store),
        max_tool_calls=max_tool_calls,
        time_budget_s=time_budget_s,
        effort="medium",
    )
    log.info("research_done", tool_calls=calls, evidence=len(store.all()))
    return store.all()
