"""initial schema + TimescaleDB hypertables

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

from app.core.db import Base
import app.models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    # Time-series: convert to hypertables when the extension exists (timescale image), else plain tables.
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            bind.execute(sa.text("SELECT create_hypertable('telemetry','time', if_not_exists => TRUE, migrate_data => TRUE)"))
            bind.execute(sa.text("SELECT create_hypertable('energy_log','time', if_not_exists => TRUE, migrate_data => TRUE)"))
            bind.execute(sa.text("ALTER TABLE telemetry SET (timescaledb.compress, timescaledb.compress_segmentby = 'robot_id')"))
            bind.execute(sa.text("SELECT add_compression_policy('telemetry', INTERVAL '1 day', if_not_exists => TRUE)"))
    except Exception as e:  # noqa: BLE001
        print(f"[alembic] TimescaleDB not available, using plain tables: {e}")


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
