"""Миграция «материалы курса»: уже существующие курсы получают по одному материалу, откат возвращает схему."""
import os
import sqlite3
import tempfile

from alembic import command
from alembic.config import Config

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BEFORE = "20261004_node_kind"


def _insert(conn, table: str, **values):
    """Вставка с заполнением остальных обязательных полей заглушками (схема на старой ревизии не нужна тесту целиком)."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()          # cid, name, type, notnull, dflt_value, pk
    row = dict(values)
    for _, name, ctype, notnull, default, pk in cols:
        if name in row or pk or not notnull or default is not None:
            continue
        t = (ctype or "").upper()
        row[name] = 0 if ("INT" in t or "FLOAT" in t or "BOOL" in t or "NUMERIC" in t) else ("[]" if "JSON" in t else
                    ("2026-01-01 00:00:00" if "DATE" in t else "x"))
    names = ", ".join(row)
    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_existing_courses_get_one_source_each_and_downgrade_restores_the_schema():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    try:
        cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
        url = f"sqlite:///{os.path.abspath(path)}"
        cfg.set_section_option("alembic", "db_url", url)
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, BEFORE)

        conn = sqlite3.connect(path)
        assert "source_id" not in _columns(conn, "cards")
        _insert(conn, "knowledge_nodes", id=1, user_id="u1", subject="s1", node_key="__intro__", name="Знакомство", tier=0)
        _insert(conn, "knowledge_nodes", id=2, user_id="u1", subject="s1", node_key="a", name="A", tier=1)
        _insert(conn, "knowledge_nodes", id=3, user_id="u1", subject="s1", node_key="b", name="B", tier=2)
        _insert(conn, "knowledge_nodes", id=4, user_id="u2", subject="s2", node_key="c", name="C", tier=1)
        _insert(conn, "cards", id=1, user_id="u1", subject="s1", text="q1", translation="a1", node_id=2)
        _insert(conn, "cards", id=2, user_id="u1", subject="s1", text="q2", translation="a2", node_id=3)
        _insert(conn, "cards", id=3, user_id="u1", subject="s1", text="ручная", translation="m")                  # без узла
        _insert(conn, "cards", id=4, user_id="u2", subject="s2", text="q4", translation="a4", node_id=4)
        _insert(conn, "generation_jobs", id=1, user_id="u1", subject="s1", theme="Мой учебник", raw_text="x",
                status="completed", char_count=1234)
        conn.commit()
        conn.close()

        command.upgrade(cfg, "head")

        conn = sqlite3.connect(path)
        sources = {(r[1], r[2]): r for r in conn.execute(
            "SELECT id, user_id, subject, name, chars, nodes_count, cards_count, role FROM sources").fetchall()}
        assert set(sources) == {("u1", "s1"), ("u2", "s2")}
        s1, s2 = sources[("u1", "s1")], sources[("u2", "s2")]
        assert (s1[3], s1[4], s1[5], s1[6], s1[7]) == ("Мой учебник", 1234, 2, 2, "main")        # вводный урок в счёт узлов не идёт
        assert (s2[3], s2[4], s2[5], s2[6]) == ("Первый материал", 0, 1, 1)
        node_src = dict(conn.execute("SELECT node_key, source_id FROM knowledge_nodes").fetchall())
        assert node_src == {"__intro__": None, "a": s1[0], "b": s1[0], "c": s2[0]}
        card_src = dict(conn.execute("SELECT id, source_id FROM cards").fetchall())
        assert card_src == {1: s1[0], 2: s1[0], 3: None, 4: s2[0]}                            # ручная карточка вне материалов
        assert {"source_name", "text_hash", "replace_source_id"} <= _columns(conn, "generation_jobs")
        conn.close()

        command.downgrade(cfg, BEFORE)
        conn = sqlite3.connect(path)
        assert "source_id" not in _columns(conn, "cards") and "source_id" not in _columns(conn, "knowledge_nodes")
        assert "replace_source_id" not in _columns(conn, "generation_jobs")
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='sources'").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 4                   # данные целы
        conn.close()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
