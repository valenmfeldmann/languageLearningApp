"""user curriculum stars

Revision ID: 3d737b2dab89
Revises: 672618f608c9
Create Date: 2026-01-26 22:14:41.324074

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3d737b2dab89'
down_revision = '672618f608c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_curriculum_star",
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("curriculum_id", sa.String(), sa.ForeignKey("curriculum.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_star_user", "user_curriculum_star", ["user_id"])
    op.create_index("ix_star_curriculum", "user_curriculum_star", ["curriculum_id"])


def downgrade():
    op.drop_index("ix_star_curriculum", table_name="user_curriculum_star")
    op.drop_index("ix_star_user", table_name="user_curriculum_star")
    op.drop_table("user_curriculum_star")
