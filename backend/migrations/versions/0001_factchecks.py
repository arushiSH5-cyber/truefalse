"""fact-check index

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "factchecks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("review_url", sa.Text, nullable=False, unique=True),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("claim_language", sa.String(16)),
        sa.Column("claimant", sa.Text),
        sa.Column("claim_date", sa.Date),
        sa.Column("publisher", sa.Text),
        sa.Column("publisher_site", sa.Text),
        sa.Column("rating", sa.Text),
        sa.Column("review_title", sa.Text),
        sa.Column("review_date", sa.Date),
        sa.Column("embedding", Vector(768)),
        sa.Column(
            "search_tsv",
            TSVECTOR,
            sa.Computed(
                "to_tsvector('simple', coalesce(claim_text, '') || ' ' || "
                "coalesce(review_title, ''))",
                persisted=True,
            ),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_factchecks_search_tsv", "factchecks", ["search_tsv"], postgresql_using="gin"
    )
    op.create_index(
        "ix_factchecks_embedding_hnsw",
        "factchecks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("factchecks")
