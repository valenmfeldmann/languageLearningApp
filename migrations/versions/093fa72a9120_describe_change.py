"""describe change

Revision ID: 093fa72a9120
Revises: 70e137790ec1
Create Date: 2026-01-26 04:38:37.281554

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '093fa72a9120'
down_revision = '70e137790ec1'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Create lesson_school if missing
    op.execute("""
    CREATE TABLE IF NOT EXISTS lesson_school (
        code VARCHAR NOT NULL PRIMARY KEY,
        name VARCHAR NOT NULL,
        description TEXT,
        active BOOLEAN NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
    );
    """)

    # 2) access_asset index changes (skip if already exists)
    # Drop the old index if it exists
    op.execute("DROP INDEX IF EXISTS ix_access_asset_code;")

    # Create the new unique index if missing
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_access_asset_code ON access_asset (code);")

    # Create partial unique index if missing
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname='public'
              AND tablename='access_asset'
              AND indexname='ux_access_asset_curriculum_share'
        ) THEN
            CREATE UNIQUE INDEX ux_access_asset_curriculum_share
                ON access_asset (curriculum_id)
                WHERE asset_type = 'curriculum_share';
        END IF;
    END$$;
    """)

    # 3) Add curriculum.school_code if missing
    op.execute("ALTER TABLE curriculum ADD COLUMN IF NOT EXISTS school_code VARCHAR;")

    # Index on curriculum.school_code
    op.execute("CREATE INDEX IF NOT EXISTS ix_curriculum_school_code ON curriculum (school_code);")

    # Foreign key (named so we can check existence)
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_curriculum_school_code') THEN
            ALTER TABLE curriculum
            ADD CONSTRAINT fk_curriculum_school_code
            FOREIGN KEY (school_code) REFERENCES lesson_school(code)
            ON DELETE SET NULL;
        END IF;
    END$$;
    """)


def downgrade():
    # Best-effort downgrade (safe even if partially applied)
    op.execute("ALTER TABLE curriculum DROP CONSTRAINT IF EXISTS fk_curriculum_school_code;")
    op.execute("DROP INDEX IF EXISTS ix_curriculum_school_code;")
    op.execute("ALTER TABLE curriculum DROP COLUMN IF EXISTS school_code;")
    op.execute("DROP INDEX IF EXISTS ux_access_asset_curriculum_share;")
    op.execute("DROP INDEX IF EXISTS ux_access_asset_code;")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_access_asset_code ON access_asset (code);")
    op.execute("DROP TABLE IF EXISTS lesson_school;")
