"""crop beds, roads, repair station

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds the *current* metadata; once the H3 cell table was retired (0004) a fresh
    # database never has it, so this historical step only applies to databases that do.
    if not sa.inspect(op.get_bind()).has_table("hex_cells"):
        return
    with op.batch_alter_table("hex_cells") as b:
        b.add_column(sa.Column("bed_id", sa.String(16), nullable=True))
    op.create_index("ix_hex_cells_bed", "hex_cells", ["farm_id", "bed_id"])


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("hex_cells"):
        return
    op.drop_index("ix_hex_cells_bed", table_name="hex_cells")
    with op.batch_alter_table("hex_cells") as b:
        b.drop_column("bed_id")
