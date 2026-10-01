"""подготовка к экзамену по билетам: планы и билеты

Revision ID: 20261001_exam_plans
Revises: 20260930_open_policy
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261001_exam_plans"
down_revision: Union[str, Sequence[str], None] = "20260930_open_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exam_plans" not in tables:
        op.create_table(
            "exam_plans",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("exam_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_exam_plans_id", "exam_plans", ["id"])
        op.create_index("ix_exam_plans_user_id", "exam_plans", ["user_id"])
        op.create_index("ix_exam_plans_user_subject", "exam_plans", ["user_id", "subject"])
    if "exam_tickets" not in tables:
        op.create_table(
            "exam_tickets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("plan_id", sa.Integer(), sa.ForeignKey("exam_plans.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("order_idx", sa.Integer(), nullable=False),
            sa.Column("question", sa.String(), nullable=False),
            sa.Column("user_answer", sa.Text(), nullable=True),
            sa.Column("node_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_exam_tickets_id", "exam_tickets", ["id"])
        op.create_index("ix_exam_tickets_plan_id", "exam_tickets", ["plan_id"])
        op.create_index("ix_exam_tickets_user_id", "exam_tickets", ["user_id"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "exam_tickets" in tables:
        op.drop_table("exam_tickets")
    if "exam_plans" in tables:
        op.drop_table("exam_plans")
