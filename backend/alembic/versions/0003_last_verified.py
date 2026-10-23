"""knowledge_documents: last_verified (date a person or fetch script last confirmed the source is current)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("last_verified", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "last_verified")
