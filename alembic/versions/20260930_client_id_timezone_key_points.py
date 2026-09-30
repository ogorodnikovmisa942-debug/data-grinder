"""client_id для идемпотентных ответов, timezone пользователя, key_points открытых вопросов

Revision ID: 20260930_answers_tz_open
Revises: 20260928_knowledge_path
Create Date: 2026-09-30 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260930_answers_tz_open"
down_revision: Union[str, Sequence[str], None] = "20260928_knowledge_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "client_id" not in _columns(bind, "review_logs"):
        with op.batch_alter_table("review_logs") as batch_op:
            batch_op.add_column(sa.Column("client_id", sa.String(length=64), nullable=True))
            batch_op.create_index("ix_review_logs_client_id", ["client_id"], unique=False)
    if "timezone" not in _columns(bind, "user_settings"):
        with op.batch_alter_table("user_settings") as batch_op:
            batch_op.add_column(sa.Column("timezone", sa.String(length=64), nullable=True))
    if "key_points" not in _columns(bind, "cards"):
        with op.batch_alter_table("cards") as batch_op:
            batch_op.add_column(sa.Column("key_points", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "key_points" in _columns(bind, "cards"):
        with op.batch_alter_table("cards") as batch_op:
            batch_op.drop_column("key_points")
    if "timezone" in _columns(bind, "user_settings"):
        with op.batch_alter_table("user_settings") as batch_op:
            batch_op.drop_column("timezone")
    if "client_id" in _columns(bind, "review_logs"):
        with op.batch_alter_table("review_logs") as batch_op:
            batch_op.drop_index("ix_review_logs_client_id")
            batch_op.drop_column("client_id")
