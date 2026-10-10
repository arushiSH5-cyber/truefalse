"""SQLAlchemy ORM tables. Schema changes go through Alembic migrations (backend/migrations)."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, Date, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 768  # multilingual-e5-base and LaBSE both produce 768-d vectors


class Base(DeclarativeBase):
    pass


class FactCheck(Base):
    """One published fact-check (ClaimReview) of one claim."""

    __tablename__ = "factchecks"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_url: Mapped[str] = mapped_column(Text, unique=True)
    claim_text: Mapped[str] = mapped_column(Text)
    claim_language: Mapped[str | None] = mapped_column(String(16))
    claimant: Mapped[str | None] = mapped_column(Text)
    claim_date: Mapped[date | None] = mapped_column(Date)
    publisher: Mapped[str | None] = mapped_column(Text)
    publisher_site: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[str | None] = mapped_column(Text)
    review_title: Mapped[str | None] = mapped_column(Text)
    review_date: Mapped[date | None] = mapped_column(Date)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # 'simple' config: no stemming/stopwords, so it works for every language.
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(claim_text, '') || ' ' || coalesce(review_title, ''))",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_factchecks_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_factchecks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ImageHash(Base):
    """Perceptual hashes of checked images (never the images themselves)."""

    __tablename__ = "image_hashes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phash: Mapped[str] = mapped_column(String(16), index=True)
    times_seen: Mapped[int] = mapped_column(default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_verdict: Mapped[str | None] = mapped_column(String(16))
