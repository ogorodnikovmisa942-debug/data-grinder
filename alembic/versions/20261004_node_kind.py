"""узлы пути: kind (core | background) для справочных разделов

Revision ID: 20261004_node_kind
Revises: 20261001_exam_plans
Create Date: 2026-10-04 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261004_node_kind"
down_revision: Union[str, Sequence[str], None] = "20261001_exam_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "kind" not in _columns(bind, "knowledge_nodes"):
        with op.batch_alter_table("knowledge_nodes") as batch_op:
            batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="core"))


def downgrade() -> None:
    bind = op.get_bind()
    if "kind" in _columns(bind, "knowledge_nodes"):
        with op.batch_alter_table("knowledge_nodes") as batch_op:
            batch_op.drop_column("kind")
