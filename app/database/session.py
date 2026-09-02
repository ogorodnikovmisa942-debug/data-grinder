import os
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.core.config import settings

# Определяем подключение к БД из настроек проекта
db_url = settings.DATABASE_URL

# Приводим к асинхронным драйверам в зависимости от используемой СУБД
if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://").replace("postgres://", "postgresql+asyncpg://")
    connect_args = {}
    is_sqlite = False
else:
    # По умолчанию используем SQLite с драйвером aiosqlite
    if db_url.startswith("sqlite:///"):
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    elif not db_url.startswith("sqlite+aiosqlite:///"):
        # Если префикс не определен, приводим к полному
        db_url = "sqlite+aiosqlite:///" + db_url.replace("sqlite://", "").lstrip("./").lstrip("/")
    connect_args = {"timeout": 30.0}
    is_sqlite = True

engine = create_async_engine(
    db_url, 
    connect_args=connect_args
)

# Настройка WAL-режима ТОЛЬКО для SQLite для конкурентной работы потоков FastAPI
if is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

# Фабрика для асинхронных сессий БД
AsyncSessionLocal = async_sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Главный класс-конструктор таблиц
Base = declarative_base()

# Асинхронный генератор подключений для эндпоинтов
async def get_db():
    async with AsyncSessionLocal() as db:
        yield db