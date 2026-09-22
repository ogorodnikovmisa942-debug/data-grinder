import importlib.util
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.core.config import normalize_database_url, settings
from app.database.session import create_db_engine, AsyncSessionLocal

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ALEMBIC_ENV_PATH = os.path.join(PROJECT_ROOT, "alembic", "env.py")

_spec = importlib.util.spec_from_file_location("alembic_env_module", ALEMBIC_ENV_PATH)
_alembic_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_alembic_env)
get_sync_url = _alembic_env.get_sync_url


class TestDialectNormalization:
    """Tests for DATABASE_URL dialect normalization in app/core/config.py."""

    def test_normalize_empty_or_none(self):
        assert normalize_database_url(None) == "sqlite+aiosqlite:///./data_grinder.db"
        assert normalize_database_url("") == "sqlite+aiosqlite:///./data_grinder.db"

    def test_normalize_postgres_prefix(self):
        url = "postgres://user:secret@localhost:5432/mydb"
        expected = "postgresql+asyncpg://user:secret@localhost:5432/mydb"
        assert normalize_database_url(url) == expected

    def test_normalize_postgresql_without_driver(self):
        url = "postgresql://prod_user:prod_pass@pg.internal:5432/production"
        expected = "postgresql+asyncpg://prod_user:prod_pass@pg.internal:5432/production"
        assert normalize_database_url(url) == expected

    def test_normalize_postgresql_with_driver_preserved(self):
        url = "postgresql+asyncpg://prod_user:prod_pass@pg.internal:5432/production"
        assert normalize_database_url(url) == url

    def test_normalize_sqlite_without_driver(self):
        url = "sqlite:///./data_grinder.db"
        expected = "sqlite+aiosqlite:///./data_grinder.db"
        assert normalize_database_url(url) == expected

    def test_normalize_sqlite_in_memory_without_driver(self):
        url = "sqlite:///:memory:"
        expected = "sqlite+aiosqlite:///:memory:"
        assert normalize_database_url(url) == expected

    def test_normalize_sqlite_with_driver_preserved(self):
        url = "sqlite+aiosqlite:///./data_grinder.db"
        assert normalize_database_url(url) == url

    def test_settings_database_url_setter_normalization(self):
        original_url = settings.DATABASE_URL
        try:
            settings.DATABASE_URL = "postgres://test:test@localhost/test"
            assert settings.DATABASE_URL == "postgresql+asyncpg://test:test@localhost/test"

            settings.DATABASE_URL = "sqlite:///./temp.db"
            assert settings.DATABASE_URL == "sqlite+aiosqlite:///./temp.db"
        finally:
            settings.DATABASE_URL = original_url


class TestEngineCreation:
    """Tests for async engine creation across SQLite and PostgreSQL."""

    def test_create_db_engine_sqlite_configuration(self):
        sqlite_url = "sqlite+aiosqlite:///:memory:"
        eng = create_db_engine(sqlite_url, debug=True)

        assert eng.dialect.name == "sqlite"
        assert eng.echo is True

    @pytest.mark.asyncio
    async def test_sqlite_pragmas_on_connect(self):
        sqlite_url = "sqlite+aiosqlite:///:memory:"
        eng = create_db_engine(sqlite_url, debug=False)

        async with eng.connect() as conn:
            # PRAGMA synchronous should be NORMAL (1)
            sync_res = await conn.execute(text("PRAGMA synchronous;"))
            sync_val = sync_res.scalar()
            # 1 corresponds to NORMAL in SQLite
            assert sync_val in (1, "1", "NORMAL")

            # PRAGMA busy_timeout should be 5000
            timeout_res = await conn.execute(text("PRAGMA busy_timeout;"))
            timeout_val = timeout_res.scalar()
            assert timeout_val == 5000

        await eng.dispose()

    def test_create_db_engine_postgresql_pooling(self):
        pg_mock_url = "postgresql+asyncpg://dbuser:dbpass@localhost:5432/mock_production_db"
        eng = create_db_engine(pg_mock_url, debug=False)

        assert eng.dialect.name == "postgresql"
        assert eng.echo is False
        assert eng.pool.size() == 20
        assert eng.pool._max_overflow == 10
        assert eng.pool._pre_ping is True
        assert eng.pool._recycle == 3600

    def test_sessionmaker_configuration(self):
        assert isinstance(AsyncSessionLocal, async_sessionmaker)
        # Verify bound class is AsyncSession
        assert issubclass(AsyncSessionLocal.class_, AsyncSession)
        # Verify expire_on_commit is False
        assert AsyncSessionLocal.kw.get("expire_on_commit") is False
        session = AsyncSessionLocal()
        assert session.sync_session.expire_on_commit is False


    @pytest.mark.asyncio
    async def test_live_session_query(self):
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1;"))
            assert result.scalar() == 1


class TestAlembicSyncUrlConversion:
    """Tests for alembic sync URL conversion."""

    def test_alembic_sqlite_conversion(self):
        assert get_sync_url("sqlite+aiosqlite:///./data_grinder.db") == "sqlite:///./data_grinder.db"
        assert get_sync_url("sqlite+aiosqlite:///:memory:") == "sqlite:///:memory:"
        assert get_sync_url("sqlite:///already_sync.db") == "sqlite:///already_sync.db"

    def test_alembic_postgresql_conversion(self):
        assert (
            get_sync_url("postgresql+asyncpg://user:pass@localhost:5432/dbname")
            == "postgresql://user:pass@localhost:5432/dbname"
        )
        assert (
            get_sync_url("postgres://user:pass@localhost:5432/dbname")
            == "postgresql://user:pass@localhost:5432/dbname"
        )
        assert (
            get_sync_url("postgresql://user:pass@localhost:5432/dbname")
            == "postgresql://user:pass@localhost:5432/dbname"
        )
        assert (
            get_sync_url("postgresql+psycopg2://user:pass@localhost:5432/dbname")
            == "postgresql+psycopg2://user:pass@localhost:5432/dbname"
        )
