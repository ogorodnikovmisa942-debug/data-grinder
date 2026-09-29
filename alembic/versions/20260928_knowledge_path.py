"""knowledge_path_schema_and_full_wipe

Путь знаний: узлы/связи/прогресс, привязка карточек и практики к узлам,
полная очистка данных старого конвейера (решение владельца: пользователей нет).

Revision ID: 20260928_knowledge_path
Revises: 20260924_phase1_limit_10
Create Date: 2026-09-28 22:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260928_knowledge_path'
down_revision: Union[str, Sequence[str], None] = '20260924_phase1_limit_10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Порядок важен: сначала зависимые таблицы
WIPE_TABLES = (
    "node_progress",
    "review_logs",
    "practice_items",
    "practice_session_logs",
    "cards",
    "phrases",
    "categories",
    "knowledge_edges",
    "knowledge_nodes",
    "generation_jobs",
    "ai_telemetry_logs",
    "daily_sessions",
    "user_settings",
    "user_sessions",
    "invite_codes",
)


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    # create_all при старте приложения мог уже создать таблицы — миграция идемпотентна
    if "knowledge_nodes" not in tables:
        op.create_table(
            "knowledge_nodes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=False),
            sa.Column("node_key", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("tier", sa.Integer(), nullable=False),
            sa.Column("parent_key", sa.String(), nullable=True),
            sa.Column("prereq_keys", sa.JSON(), nullable=False),
            sa.Column("order_idx", sa.Integer(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("source_hint", sa.String(), nullable=True),
            sa.Column("lesson", sa.JSON(), nullable=True),
            sa.Column("lesson_status", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "subject", "node_key", name="uq_knowledge_nodes_user_subject_key"),
        )
        with op.batch_alter_table("knowledge_nodes", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_knowledge_nodes_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_knowledge_nodes_user_id"), ["user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_knowledge_nodes_subject"), ["subject"], unique=False)
            batch_op.create_index("ix_knowledge_nodes_user_subject", ["user_id", "subject"], unique=False)

    if "knowledge_edges" not in tables:
        op.create_table(
            "knowledge_edges",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=False),
            sa.Column("source_key", sa.String(), nullable=False),
            sa.Column("target_key", sa.String(), nullable=False),
            sa.Column("relation", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("knowledge_edges", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_knowledge_edges_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_knowledge_edges_user_id"), ["user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_knowledge_edges_subject"), ["subject"], unique=False)
            batch_op.create_index("ix_knowledge_edges_user_subject", ["user_id", "subject"], unique=False)

    if "node_progress" not in tables:
        op.create_table(
            "node_progress",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("node_id", sa.Integer(), nullable=False),
            sa.Column("lesson_done", sa.Boolean(), nullable=False),
            sa.Column("checkpoint_score", sa.Integer(), nullable=False),
            sa.Column("lesson_done_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["node_id"], ["knowledge_nodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "node_id", name="uq_node_progress_user_node"),
        )
        with op.batch_alter_table("node_progress", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_node_progress_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_node_progress_user_id"), ["user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_node_progress_node_id"), ["node_id"], unique=False)

    # Колонки добавляем нативным ALTER TABLE (без пересоздания таблиц в batch-режиме:
    # старые боевые схемы SQLite содержат DEFAULT-выражения, которые batch не воспроизводит)
    card_cols = _columns(bind, "cards")
    if "node_id" not in card_cols:
        op.add_column("cards", sa.Column("node_id", sa.Integer(), nullable=True))
        op.create_index("ix_cards_node_id", "cards", ["node_id"], unique=False)
    if "answer_type" not in card_cols:
        op.add_column("cards", sa.Column("answer_type", sa.String(), nullable=True))
    if "distractors" not in card_cols:
        op.add_column("cards", sa.Column("distractors", sa.JSON(), nullable=True))

    if "node_id" not in _columns(bind, "practice_items"):
        op.add_column("practice_items", sa.Column("node_id", sa.Integer(), nullable=True))
        op.create_index("ix_practice_items_node_id", "practice_items", ["node_id"], unique=False)

    tel_cols = _columns(bind, "ai_telemetry_logs")
    if "cache_hit_tokens" not in tel_cols:
        op.add_column("ai_telemetry_logs", sa.Column("cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"))
    if "cost_usd" not in tel_cols:
        op.add_column("ai_telemetry_logs", sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"))

    # Старый граф знаний заменён узлами «Пути знаний»
    if "topic_knowledge_graphs" in _tables(bind):
        op.drop_table("topic_knowledge_graphs")

    # Полная очистка данных старого конвейера
    existing = _tables(bind)
    for table in WIPE_TABLES:
        if table in existing:
            bind.execute(sa.text(f"DELETE FROM {table}"))


def _drop_index_if_exists(bind, table: str, name: str) -> None:
    if name in {i["name"] for i in sa.inspect(bind).get_indexes(table)}:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    if "node_id" in _columns(bind, "practice_items"):
        _drop_index_if_exists(bind, "practice_items", "ix_practice_items_node_id")
        op.drop_column("practice_items", "node_id")

    card_cols = _columns(bind, "cards")
    if "node_id" in card_cols:
        _drop_index_if_exists(bind, "cards", "ix_cards_node_id")
        op.drop_column("cards", "node_id")
    for col in ("answer_type", "distractors"):
        if col in card_cols:
            op.drop_column("cards", col)

    tel_cols = _columns(bind, "ai_telemetry_logs")
    for col in ("cache_hit_tokens", "cost_usd"):
        if col in tel_cols:
            op.drop_column("ai_telemetry_logs", col)

    tables = _tables(bind)
    for table in ("node_progress", "knowledge_edges", "knowledge_nodes"):
        if table in tables:
            op.drop_table(table)

    if "topic_knowledge_graphs" not in tables:
        op.create_table(
            "topic_knowledge_graphs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=False),
            sa.Column("graph_data", sa.JSON(), nullable=False),
            sa.Column("tree_data", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "subject", name="uq_topic_knowledge_graphs_user_subject"),
        )
        with op.batch_alter_table("topic_knowledge_graphs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_topic_knowledge_graphs_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_topic_knowledge_graphs_subject"), ["subject"], unique=False)
            batch_op.create_index(batch_op.f("ix_topic_knowledge_graphs_user_id"), ["user_id"], unique=False)
            batch_op.create_index("ix_topic_knowledge_graphs_user_subject", ["user_id", "subject"], unique=False)
