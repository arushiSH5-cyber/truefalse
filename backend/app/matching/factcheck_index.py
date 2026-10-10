"""Hybrid (BM25 + multilingual embedding) search over published fact-checks.

Rankings are fused with Reciprocal Rank Fusion. The best cosine similarity is kept as an
absolute signal, used to decide whether a match is strong enough to rely on.
"""

from collections.abc import Hashable, Sequence
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db_models import FactCheck
from app.core.models import EvidenceDraft, SourceKind
from app.matching.bm25 import BM25, tokenize
from app.matching.claimreview import FactCheckRecord
from app.matching.embeddings import Embedder, normalize
from app.research.sources.base import clip

RRF_K = 60

# 'simple' text search config: no stemming or stopwords, so it treats every language alike.
SPARSE_SQL = text(
    "SELECT f.id, ts_rank_cd(f.search_tsv, q) AS r "
    "FROM factchecks f, websearch_to_tsquery('simple', :q) q "
    "WHERE f.search_tsv @@ q ORDER BY r DESC LIMIT :n"
)


class FactCheckMatch(BaseModel):
    record: FactCheckRecord
    cosine: float
    bm25: float
    score: float  # fused RRF score (relative; only meaningful for ranking)

    def to_evidence(self) -> EvidenceDraft:
        r = self.record
        return EvidenceDraft(
            source=SourceKind.FACTCHECK,
            tier=2,
            title=r.review_title or r.claim_text[:120],
            url=r.review_url,
            quote=clip(f'Claim reviewed: "{r.claim_text}". Rating: {r.rating}.'),
            published=r.review_date.isoformat() if r.review_date else None,
            publisher=r.publisher,
            rating=r.rating,
        )


class FactCheckIndex(Protocol):
    async def search(self, queries: Sequence[str], k: int = 5) -> list[FactCheckMatch]:
        """`queries` are alternative phrasings of one claim (e.g. original + English)."""
        ...


def rrf[K: Hashable](rankings: Sequence[Sequence[K]], k: int = RRF_K) -> dict[K, float]:
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return scores


class InMemoryFactCheckIndex:
    """For tests, demos, and small datasets. Same ranking logic as the Postgres index."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._records: list[FactCheckRecord] = []
        self._vectors = np.zeros((0, embedder.dim), dtype=np.float32)
        self._bm25: BM25 | None = None

    async def add(self, records: Sequence[FactCheckRecord]) -> None:
        if not records:
            return
        vecs = normalize(await self._embedder.embed_documents([r.claim_text for r in records]))
        self._records += list(records)
        self._vectors = np.vstack([self._vectors, vecs.astype(np.float32)])
        self._bm25 = BM25([f"{r.claim_text} {r.review_title or ''}" for r in self._records])

    async def search(self, queries: Sequence[str], k: int = 5) -> list[FactCheckMatch]:
        if not self._records or not queries:
            return []
        q = normalize(await self._embedder.embed_queries(list(queries)))
        cosine = (self._vectors @ q.T).max(axis=1)
        assert self._bm25 is not None
        bm = np.asarray(self._bm25.scores(" ".join(queries)))
        pool = max(k * 4, 20)
        dense_rank = [int(i) for i in np.argsort(-cosine)[:pool]]
        sparse_rank = [int(i) for i in np.argsort(-bm)[:pool] if bm[i] > 0]
        fused = rrf([dense_rank, sparse_rank])
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [
            FactCheckMatch(
                record=self._records[i],
                cosine=float(cosine[i]),
                bm25=float(bm[i]),
                score=fused[i],
            )
            for i in best
        ]


class PgFactCheckIndex:
    """Postgres + pgvector implementation (HNSW cosine index + GIN full-text index)."""

    def __init__(self, engine: AsyncEngine, embedder: Embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    async def upsert(self, records: Sequence[FactCheckRecord], batch_size: int = 256) -> int:
        written = 0
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            vecs = normalize(await self._embedder.embed_documents([r.claim_text for r in batch]))
            rows: list[dict[str, Any]] = [
                r.model_dump() | {"embedding": v.tolist()} for r, v in zip(batch, vecs, strict=True)
            ]
            stmt = insert(FactCheck).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[FactCheck.review_url],
                set_={c: stmt.excluded[c] for c in rows[0] if c != "review_url"},
            )
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
            written += len(batch)
        return written

    async def search(self, queries: Sequence[str], k: int = 5) -> list[FactCheckMatch]:
        if not queries:
            return []
        q = normalize(await self._embedder.embed_queries(list(queries)))
        pool = max(k * 4, 20)
        by_id: dict[int, tuple[FactCheckRecord, float, float]] = {}
        dense_rankings: list[list[int]] = []
        async with self._engine.connect() as conn:
            for vec in q:
                distance = FactCheck.embedding.cosine_distance(vec.tolist())
                rows = (
                    await conn.execute(
                        select(FactCheck, (1 - distance).label("cos"))
                        .order_by(distance)
                        .limit(pool)
                    )
                ).all()
                dense_rankings.append([row[0].id for row in rows])
                for fc, cos in rows:
                    prev = by_id.get(fc.id)
                    by_id[fc.id] = (_record(fc), max(cos, prev[1] if prev else -1.0), 0.0)

            sparse = (
                await conn.execute(SPARSE_SQL, {"q": _any_term_query(queries), "n": pool})
            ).all()
            missing = [i for i, _ in sparse if i not in by_id]
            if missing:
                for fc in (
                    await conn.execute(select(FactCheck).where(FactCheck.id.in_(missing)))
                ).scalars():
                    by_id[fc.id] = (_record(fc), -1.0, 0.0)
        for i, r in sparse:
            rec, cos, _ = by_id[i]
            by_id[i] = (rec, cos, float(r))

        fused = rrf([*dense_rankings, [i for i, _ in sparse]])
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [
            FactCheckMatch(
                record=by_id[i][0],
                cosine=by_id[i][1],
                bm25=by_id[i][2],
                score=fused[i],
            )
            for i in best
        ]


def _record(fc: FactCheck) -> FactCheckRecord:
    return FactCheckRecord(
        review_url=fc.review_url,
        claim_text=fc.claim_text,
        claim_language=fc.claim_language,
        claimant=fc.claimant,
        claim_date=fc.claim_date,
        publisher=fc.publisher,
        publisher_site=fc.publisher_site,
        rating=fc.rating,
        review_title=fc.review_title,
        review_date=fc.review_date,
    )


def _any_term_query(queries: Sequence[str]) -> str:
    """websearch_to_tsquery input matching documents that contain ANY query term."""
    terms = dict.fromkeys(t for q in queries for t in tokenize(q) if '"' not in t)
    return " or ".join(terms) or "-"
