"""add access revoked fields to subscription

Revision ID: 1a3322491d67
Revises: f3c17f1d7b63
Create Date: 2026-01-15 19:33:04.857996

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a3322491d67'
down_revision = 'f3c17f1d7b63'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscription", sa.Column("access_revoked_reason", sa.String(length=255), nullable=True))
    op.add_column("subscription", sa.Column("access_revoked_anchor", sa.String(length=255), nullable=True))
    op.add_column("subscription", sa.Column("access_revoked_at", sa.DateTime(), nullable=True))

    # pass


def downgrade():
    pass
