"""add access_level_mult to user

Revision ID: f3c17f1d7b63
Revises: 3e93d8a7677c
Create Date: 2026-01-14 21:17:45.680582

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3c17f1d7b63'
down_revision = '3e93d8a7677c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("access_level_mult", sa.Float(), nullable=False, server_default="1.0")
    )

    # Backfill existing rows explicitly (defensive)
    op.execute("UPDATE \"user\" SET access_level_mult = 1.0 WHERE access_level_mult IS NULL")

    # Optional: remove server default if you prefer app-level defaults only
    op.alter_column("user", "access_level_mult", server_default=None)


def downgrade():
    op.drop_column("user", "access_level_mult")


