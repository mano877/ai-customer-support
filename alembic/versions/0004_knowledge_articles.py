"""Knowledge base articles

Revision ID: 0004_knowledge_articles
Revises: 0003_products
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_knowledge_articles"
down_revision: Union[str, None] = "0003_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("view_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_articles_slug", "knowledge_articles", ["slug"], unique=True)
    op.create_index("ix_knowledge_articles_title", "knowledge_articles", ["title"])
    op.create_index("ix_knowledge_articles_category", "knowledge_articles", ["category"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_articles_category", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_title", table_name="knowledge_articles")
    op.drop_index("ix_knowledge_articles_slug", table_name="knowledge_articles")
    op.drop_table("knowledge_articles")
