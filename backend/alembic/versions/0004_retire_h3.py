"""retire the H3 hex index: bed sections are the only spatial unit

Revision ID: 0004
Revises: 0003

Every statement is idempotent: a database created after this change never had the H3 columns
(0001 builds the current metadata), an older one drops them here.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATEMENTS = [
    "DROP TABLE IF EXISTS hex_cells CASCADE",
    "ALTER TABLE farms DROP COLUMN IF EXISTS h3_resolution",
    "ALTER TABLE farms DROP COLUMN IF EXISTS cell_count",
    "ALTER TABLE sections DROP COLUMN IF EXISTS h3_index",
    "ALTER TABLE infrastructure DROP COLUMN IF EXISTS h3_index",
    "ALTER TABLE missions DROP COLUMN IF EXISTS target_h3",
    "ALTER TABLE missions ADD COLUMN IF NOT EXISTS target_section VARCHAR(16)",
    "DROP INDEX IF EXISTS ix_yields_h3_index",
    "ALTER TABLE yields DROP COLUMN IF EXISTS h3_index",
    "ALTER TABLE yields ADD COLUMN IF NOT EXISTS section_id VARCHAR(16)",
    "CREATE INDEX IF NOT EXISTS ix_yields_section_id ON yields (section_id)",
]


def upgrade() -> None:
    bind = op.get_bind()
    for sql in STATEMENTS:
        bind.execute(sa.text(sql))
    # telemetry is a compressed hypertable on TimescaleDB; dropping a column there can be refused
    # on older extension versions. The column is nullable and unused, so leaving it is harmless.
    try:
        with bind.begin_nested():
            bind.execute(sa.text("ALTER TABLE telemetry DROP COLUMN IF EXISTS h3_index"))
    except Exception as e:  # noqa: BLE001
        print(f"[alembic] kept telemetry.h3_index ({e})")


def downgrade() -> None:
    raise NotImplementedError("the H3 index was retired; restore from a backup to go back")
