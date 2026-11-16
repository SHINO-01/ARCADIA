"""controlled-traffic layout: bed sections

Revision ID: 0003
Revises: 0002
"""
from alembic import op

from app.core.db import Base
import app.models  # noqa: F401

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["sections"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table("sections")
