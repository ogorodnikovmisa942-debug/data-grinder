"""политика открытых вопросов: формат предъявления и оценка проверяющего в логах, режим у пользователя

Revision ID: 20260930_open_policy
Revises: 20260930_answers_tz_open
Create Date: 2026-09-30 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260930_open_policy"
down_revision: Union[str, Sequence[str], None] = "20260930_answers_tz_open"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _columns(bind, "review_logs")
    with op.batch_alter_table("review_logs") as batch_op:
        if "answer_format" not in cols:
            batch_op.add_column(sa.Column("answer_format", sa.String(length=16), nullable=True))
        if "auto_score" not in cols:
            batch_op.add_column(sa.Column("auto_score", sa.Float(), nullable=True))
    if "open_mode" not in _columns(bind, "user_settings"):
        with op.batch_alter_table("user_settings") as batch_op:
            batch_op.add_column(sa.Column("open_mode", sa.String(length=16), server_default="auto", nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "open_mode" in _columns(bind, "user_settings"):
        with op.batch_alter_table("user_settings") as batch_op:
            batch_op.drop_column("open_mode")
    cols = _columns(bind, "review_logs")
    with op.batch_alter_table("review_logs") as batch_op:
        for name in ("auto_score", "answer_format"):
            if name in cols:
                batch_op.drop_column(name)
