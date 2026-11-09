"""тип мнемоники в задаче генерации (generation_jobs.assoc_preference)

Revision ID: 20261109_job_assoc_pref
Revises: 20261008_user_plan
Create Date: 2026-11-09 21:15:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261109_job_assoc_pref"
down_revision: Union[str, Sequence[str], None] = "20261008_user_plan"
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
        if "assoc_preference" not in cols:
            batch_op.add_column(sa.Column("assoc_preference", sa.String(length=16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "generation_jobs" not in sa.inspect(bind).get_table_names():
        return
    cols = _columns(bind, "generation_jobs")
    with op.batch_alter_table("generation_jobs") as batch_op:
        if "assoc_preference" in cols:
            batch_op.drop_column("assoc_preference")
