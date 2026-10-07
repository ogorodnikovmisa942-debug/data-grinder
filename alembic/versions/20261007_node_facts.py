"""конспект темы: knowledge_nodes.facts (список фактов, которые карточки не спрашивают)

Revision ID: 20261007_node_facts
Revises: 20261006_sources
Create Date: 2026-10-07 19:00:00.000000

Факты хранятся готовым списком и показываются после урока и из графа; урок их не пересказывает.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261007_node_facts"
down_revision: Union[str, Sequence[str], None] = "20261006_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "facts" not in _columns(bind, "knowledge_nodes"):
        op.add_column("knowledge_nodes", sa.Column("facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "facts" in _columns(bind, "knowledge_nodes"):
        with op.batch_alter_table("knowledge_nodes") as batch_op:
            batch_op.drop_column("facts")
