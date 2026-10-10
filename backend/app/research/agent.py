"""Agentic research: the LLM plans queries and calls tiered source tools.

Every result is stored as Evidence with a stable id (E1, E2, ...) that judges must cite.
Queries are PII-redacted before they leave the system.
"""

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
}

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


def build_tools(sources: Sequence[Source], store: EvidenceStore) -> list[Tool]:
    tools: list[Tool] = []
    for source in sources:
        name, description = TOOL_DESCRIPTIONS[source.kind]

        async def handler(args: dict[str, Any], _src: Source = source, _name: str = name) -> str:
            query = redact(str(args.get("query", "")))[:300]
            log.info("research_query", tool=_name)
            drafts = await _src.search(query, args.get("language"))
            return _render_for_model(store.add(drafts))

        tools.append(Tool(name, description, QUERY_SCHEMA, handler))
    return tools


def _claims_block(claims: Sequence[Claim]) -> str:
    return "\n".join(
        f"{i}. [{c.topic}] {c.english}  (original: {c.original})" for i, c in enumerate(claims)
    )


async def research(
    llm: LLMClient,
    sources: Sequence[Source],
    claims: Sequence[Claim],
    *,
    language: str,
    max_tool_calls: int,
    time_budget_s: float,
) -> list[Evidence]:
    store = EvidenceStore()
    if not sources:
        return []
    calls = await llm.run_tools(
        stage="research",
        system=prompts.render("research_agent.v1"),
        user=prompts.render("research_task.v1", claims=_claims_block(claims), language=language),
        tools=build_tools(sources, store),
        max_tool_calls=max_tool_calls,
        time_budget_s=time_budget_s,
        effort="medium",
    )
    log.info("research_done", tool_calls=calls, evidence=len(store.all()))
    return store.all()
