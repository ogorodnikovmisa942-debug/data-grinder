import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings, normalize_database_url


def create_db_engine(url: str = None, debug: bool = None):
    """
    Create an async SQLAlchemy engine configured for PostgreSQL or SQLite.
    """
    target_url = url or getattr(settings, "DATABASE_URL", None) or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data_grinder.db")
    target_url = normalize_database_url(target_url)
    is_debug = settings.DEBUG if debug is None else debug

    if target_url.startswith("postgresql") or target_url.startswith("postgres"):
        return create_async_engine(
            target_url,
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=is_debug,
        )
    elif target_url.startswith("sqlite"):
        eng = create_async_engine(
            target_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=is_debug,
        )

        # Apply WAL PRAGMA on connect
        @event.listens_for(eng.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

        return eng
    else:
        return create_async_engine(target_url, echo=is_debug)


# Read DATABASE_URL from settings.DATABASE_URL (or os.getenv("DATABASE_URL", ...))
database_url = getattr(settings, "DATABASE_URL", None) or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data_grinder.db")
engine = create_db_engine(database_url, debug=settings.DEBUG)

# Фабрика для асинхронных сессий БД
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Главный класс-конструктор таблиц
Base = declarative_base()

# Асинхронный генератор подключений для эндпоинтов
async def get_db():
    async with AsyncSessionLocal() as db:
        yield db