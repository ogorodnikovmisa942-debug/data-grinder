"""материалы курса: таблица sources, source_id у узлов и карточек, поля задачи загрузки

Revision ID: 20261006_sources
Revises: 20261004_node_kind
Create Date: 2026-10-06 12:00:00.000000

Новый материал добавляется к курсу предмета и не стирает повторения. Уже существующие курсы получают один материал
на предмет («как есть»), чтобы их можно было отличать от добавленных позже.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261006_sources"
down_revision: Union[str, Sequence[str], None] = "20261004_node_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "sources" not in _tables(bind):
        op.create_table(
            "sources",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("subject", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=True),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="main"),
            sa.Column("chars", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("text_hash", sa.String(length=64), nullable=True),
            sa.Column("nodes_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cards_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_sources_id", "sources", ["id"])
        op.create_index("ix_sources_user_id", "sources", ["user_id"])
        op.create_index("ix_sources_subject", "sources", ["subject"])
        op.create_index("ix_sources_text_hash", "sources", ["text_hash"])
        op.create_index("ix_sources_user_subject", "sources", ["user_id", "subject"])

    for table in ("knowledge_nodes", "cards"):
        if "source_id" not in _columns(bind, table):
            # Как в прежних миграциях (node_id у карточек): простой ADD COLUMN, без пересоздания таблицы (SQLite не любит его на cards)
            op.add_column(table, sa.Column("source_id", sa.Integer(), nullable=True))
            op.create_index(f"ix_{table}_source_id", table, ["source_id"])
            if bind.dialect.name != "sqlite":
                op.create_foreign_key(f"fk_{table}_source_id", table, "sources", ["source_id"], ["id"], ondelete="SET NULL")

    if "generation_jobs" in _tables(bind):
        cols = _columns(bind, "generation_jobs")
        with op.batch_alter_table("generation_jobs") as batch_op:
            if "source_name" not in cols:
                batch_op.add_column(sa.Column("source_name", sa.String(), nullable=True))
            if "text_hash" not in cols:
                batch_op.add_column(sa.Column("text_hash", sa.String(length=64), nullable=True))
            if "replace_source_id" not in cols:
                batch_op.add_column(sa.Column("replace_source_id", sa.Integer(), nullable=True))

    _backfill(bind)


def _backfill(bind) -> None:
    """Каждому предмету с готовой нарезкой — один материал; узлы и карточки привязываются к нему."""
    already = bind.execute(sa.text("SELECT COUNT(*) FROM sources")).scalar() or 0
    if already:
        return
    bind.execute(sa.text("""
        INSERT INTO sources (user_id, subject, name, title, role, chars, nodes_count, cards_count, cost_usd, created_at)
        SELECT n.user_id, n.subject,
               COALESCE((SELECT g.theme FROM generation_jobs g WHERE g.user_id = n.user_id AND g.subject = n.subject
                         AND g.status = 'completed' ORDER BY g.id DESC LIMIT 1), 'Первый материал'),
               NULL, 'main',
               COALESCE((SELECT g.char_count FROM generation_jobs g WHERE g.user_id = n.user_id AND g.subject = n.subject
                         AND g.status = 'completed' ORDER BY g.id DESC LIMIT 1), 0),
               COUNT(*), 0, 0.0, MIN(n.created_at)
        FROM knowledge_nodes n
        WHERE n.node_key <> '__intro__'
        GROUP BY n.user_id, n.subject
    """))
    bind.execute(sa.text("""
        UPDATE knowledge_nodes SET source_id = (
            SELECT s.id FROM sources s WHERE s.user_id = knowledge_nodes.user_id AND s.subject = knowledge_nodes.subject
        ) WHERE node_key <> '__intro__'
    """))
    bind.execute(sa.text("""
        UPDATE cards SET source_id = (SELECT n.source_id FROM knowledge_nodes n WHERE n.id = cards.node_id)
        WHERE node_id IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE sources SET cards_count = (SELECT COUNT(*) FROM cards c WHERE c.source_id = sources.id)
    """))


def downgrade() -> None:
    bind = op.get_bind()
    if "generation_jobs" in _tables(bind):
        cols = _columns(bind, "generation_jobs")
        with op.batch_alter_table("generation_jobs") as batch_op:
            for name in ("replace_source_id", "text_hash", "source_name"):
                if name in cols:
                    batch_op.drop_column(name)
    for table in ("cards", "knowledge_nodes"):
        if "source_id" in _columns(bind, table):
            if bind.dialect.name != "sqlite":
                op.drop_constraint(f"fk_{table}_source_id", table, type_="foreignkey")
            op.drop_index(f"ix_{table}_source_id", table_name=table)
            op.drop_column(table, "source_id")
    if "sources" in _tables(bind):
        op.drop_table("sources")
