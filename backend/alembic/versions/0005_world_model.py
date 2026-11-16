"""agronomic world model: site setup, crop plan, observations, snapshots, weather, anomalies

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

from app.core.db import Base
import app.models  # noqa: F401

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

COLUMNS = [
    "ALTER TABLE farms ADD COLUMN IF NOT EXISTS setup JSONB",
    "ALTER TABLE farms ADD COLUMN IF NOT EXISTS crop_plan JSONB",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS ndvi DOUBLE PRECISION",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS cwsi DOUBLE PRECISION",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS disease DOUBLE PRECISION",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS weed_cover DOUBLE PRECISION",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS yield_est DOUBLE PRECISION",
    "ALTER TABLE sections ADD COLUMN IF NOT EXISTS soil JSONB",
]


def upgrade() -> None:
    bind = op.get_bind()
    for sql in COLUMNS:
        bind.execute(sa.text(sql))
    for t in ("world_states", "weather_daily", "farm_daily", "anomalies"):
        Base.metadata.tables[t].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for t in ("anomalies", "farm_daily", "weather_daily", "world_states"):
        op.drop_table(t)
