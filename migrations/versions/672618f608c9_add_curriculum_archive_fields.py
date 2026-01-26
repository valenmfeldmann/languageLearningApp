"""add curriculum archive fields

Revision ID: 672618f608c9
Revises: 412ec4de14d9
Create Date: 2026-01-26 05:52:38.243842

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '672618f608c9'
down_revision = '412ec4de14d9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("curriculum", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_curriculum_is_archived", ["is_archived"], unique=False)

    # optional: remove the server_default after existing rows are backfilled
    op.alter_column("curriculum", "is_archived", server_default=None)


def downgrade():
    with op.batch_alter_table("curriculum", schema=None) as batch_op:
        batch_op.drop_index("ix_curriculum_is_archived")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("is_archived")
