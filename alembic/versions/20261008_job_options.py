"""параметры загрузки материала: глубина и тип (generation_jobs.depth, generation_jobs.source_kind)

Revision ID: 20261008_job_options
Revises: 20261007_node_facts
Create Date: 2026-10-08 12:00:00.000000

Глубина (compact | standard | detailed) задаёт, сколько карточек просить у материала; тип (textbook | article | notes | lecture) — если
пользователь назвал его сам (иначе тип определяет модель и код).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261008_job_options"
down_revision: Union[str, Sequence[str], None] = "20261007_node_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "generation_jobs" not in sa.inspect(bind).get_table_names():
        return
    cols = _columns(bind, "generation_jobs")
    with op.batch_alter_table("generation_jobs") as batch_op:
        if "depth" not in cols:
            batch_op.add_column(sa.Column("depth", sa.String(length=12), nullable=True))
        if "source_kind" not in cols:
            batch_op.add_column(sa.Column("source_kind", sa.String(length=16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "generation_jobs" not in sa.inspect(bind).get_table_names():
        return
    cols = _columns(bind, "generation_jobs")
    with op.batch_alter_table("generation_jobs") as batch_op:
        for name in ("source_kind", "depth"):
            if name in cols:
                batch_op.drop_column(name)
