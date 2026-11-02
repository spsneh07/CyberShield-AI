"""add token_hash to users and user_id to incidents for authentication and ownership

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("token_hash", sa.String(64), nullable=True))
        batch_op.create_index("ix_users_token_hash", ["token_hash"], unique=False)

    with op.batch_alter_table("incidents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", name="fk_incidents_user_id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.create_index("ix_incidents_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch_op:
        batch_op.drop_index("ix_incidents_user_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_token_hash")
        batch_op.drop_column("token_hash")
