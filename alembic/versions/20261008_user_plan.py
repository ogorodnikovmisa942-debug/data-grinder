"""тариф пользователя: user_settings.plan и user_settings.plan_until

Revision ID: 20261008_user_plan
Revises: 20261008_job_options
Create Date: 2026-10-08 15:00:00.000000

free | paid; платный действует, пока plan_until пусто или в будущем. Пока оплаты в продукте нет, тариф выставляет администратор.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261008_user_plan"
down_revision: Union[str, Sequence[str], None] = "20261008_job_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "user_settings" not in sa.inspect(bind).get_table_names():
        return
    cols = _columns(bind, "user_settings")
    with op.batch_alter_table("user_settings") as batch_op:
        if "plan" not in cols:
            batch_op.add_column(sa.Column("plan", sa.String(length=12), nullable=True))
        if "plan_until" not in cols:
            batch_op.add_column(sa.Column("plan_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "user_settings" not in sa.inspect(bind).get_table_names():
        return
    cols = _columns(bind, "user_settings")
    with op.batch_alter_table("user_settings") as batch_op:
        for name in ("plan_until", "plan"):
            if name in cols:
                batch_op.drop_column(name)
