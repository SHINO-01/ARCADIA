"""separate the real-time twin from the simulation sandbox: a `mode` on world-model state and records

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PK = {"world_states": "farm_id, mode", "weather_daily": "farm_id, mode, date", "farm_daily": "farm_id, mode, date"}


def upgrade() -> None:
    bind = op.get_bind()
    for t in ("world_states", "weather_daily", "farm_daily", "anomalies", "yields"):
        bind.execute(sa.text(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS mode VARCHAR(10) NOT NULL DEFAULT 'realtime'"))
    for t, cols in PK.items():
        bind.execute(sa.text(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey"))
        bind.execute(sa.text(f"ALTER TABLE {t} ADD PRIMARY KEY ({cols})"))
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_anomalies_farm_mode ON anomalies (farm_id, mode)"))
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_yields_farm_mode ON yields (farm_id, mode)"))


def downgrade() -> None:
    bind = op.get_bind()
    for t in ("world_states", "weather_daily", "farm_daily", "anomalies", "yields"):
        bind.execute(sa.text(f"DELETE FROM {t} WHERE mode <> 'realtime'"))
    for t, cols in PK.items():
        bind.execute(sa.text(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {t}_pkey"))
        bind.execute(sa.text(f"ALTER TABLE {t} ADD PRIMARY KEY ({cols.replace(', mode', '')})"))
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_anomalies_farm_mode"))
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_yields_farm_mode"))
    for t in ("world_states", "weather_daily", "farm_daily", "anomalies", "yields"):
        bind.execute(sa.text(f"ALTER TABLE {t} DROP COLUMN IF EXISTS mode"))
