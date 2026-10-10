"""image perceptual hashes

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_hashes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("phash", sa.String(16), nullable=False),
        sa.Column("times_seen", sa.Integer, nullable=False, server_default="1"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verdict", sa.String(16)),
    )
    op.create_index("ix_image_hashes_phash", "image_hashes", ["phash"])
    op.create_index("ix_image_hashes_last_seen", "image_hashes", ["last_seen"])


def downgrade() -> None:
    op.drop_table("image_hashes")
