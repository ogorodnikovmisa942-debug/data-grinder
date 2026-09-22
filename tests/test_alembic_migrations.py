import os
import tempfile
import pytest
from alembic.config import Config
from alembic import command
import sqlite3
from app.database.models import Base

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ALEMBIC_INI_PATH = os.path.join(PROJECT_ROOT, "alembic.ini")


def test_alembic_ini_exists():
    """Verify that alembic.ini exists and script_location is configured."""
    assert os.path.exists(ALEMBIC_INI_PATH), "alembic.ini should exist in project root"
    cfg = Config(ALEMBIC_INI_PATH)
    assert cfg.get_main_option("script_location") is not None


def test_alembic_migration_lifecycle_on_fresh_db():
    """
    Test running upgrade head and downgrade base on a temporary SQLite database
    to ensure all models are created and clean rollback works.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        cfg = Config(ALEMBIC_INI_PATH)
        # Use absolute path for sqlite url
        sync_url = f"sqlite:///{os.path.abspath(tmp_db_path)}"
        cfg.set_section_option("alembic", "db_url", sync_url)
        cfg.set_main_option("sqlalchemy.url", sync_url)

        # 1. Upgrade to head
        command.upgrade(cfg, "head")

        # Verify all tables from Base.metadata were created in the fresh database
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        created_tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected_tables = set(Base.metadata.tables.keys())
        missing_tables = expected_tables - created_tables
        assert not missing_tables, f"Tables missing after upgrade head: {missing_tables}"

        # 2. Downgrade to base
        command.downgrade(cfg, "base")

        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables_after_downgrade = {row[0] for row in cursor.fetchall()} - {"alembic_version", "sqlite_sequence"}
        conn.close()

        assert len(tables_after_downgrade) == 0, f"Expected all application tables dropped, found: {tables_after_downgrade}"

    finally:
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


def test_alembic_current_on_project_db():
    """Verify that alembic current on project database reports stamped head revision without error."""
    cfg = Config(ALEMBIC_INI_PATH)
    # command.current should succeed without raising
    command.current(cfg, verbose=False)

