"""add death acknowledgement to character

Revision ID: 4237149e3f7c
Revises: 785ff21deb68
Create Date: 2026-02-11 21:19:34.742484

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '4237149e3f7c'
down_revision = '785ff21deb68'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add the column to Character
    op.add_column('character', sa.Column('death_acknowledged', sa.Boolean(), nullable=True))

    # 2. Back-fill: Assume all existing dogs (alive or dead) have been "acknowledged"
    op.execute("UPDATE character SET death_acknowledged = TRUE")

    # 3. Make it NOT NULL for consistency
    op.alter_column('character', 'death_acknowledged', nullable=False)

    # REMOVE the 'with op.batch_alter_table('user'...' block entirely
    # We want to keep those timestamps NOT NULL!


def downgrade():
    # Only remove the column we added
    op.drop_column('character', 'death_acknowledged')