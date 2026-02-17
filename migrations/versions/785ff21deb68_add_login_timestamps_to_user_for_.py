"""add login timestamps to user for companion tracking

Revision ID: 785ff21deb68
Revises: 36d22a90d015
Create Date: 2026-02-11 05:06:53.238214

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '785ff21deb68'
down_revision = '36d22a90d015'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add the columns as nullable first
    op.add_column('user', sa.Column('last_login_at', sa.DateTime(), nullable=True))
    op.add_column('user', sa.Column('current_login_at', sa.DateTime(), nullable=True))

    # 2. Back-fill: Set existing users to the current time
    op.execute("UPDATE \"user\" SET last_login_at = NOW(), current_login_at = NOW()")

    # 3. Now make them NOT NULL (Optional, but recommended for consistency)
    op.alter_column('user', 'last_login_at', nullable=False)
    op.alter_column('user', 'current_login_at', nullable=False)
    # ### end Alembic commands ###



def downgrade():
    # Fix: Change 'user_curriculum_star' to 'user'
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('current_login_at')
        batch_op.drop_column('last_login_at')

    # ### end Alembic commands ###
