import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context

# Ensure project root is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.config import settings
from app.database.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = getattr(context, "config", None)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config is not None and config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def get_sync_url(raw_url: str = None) -> str:
    """
    Retrieve DATABASE_URL from app settings or alembic config,
    converting async drivers (aiosqlite, asyncpg) to sync drivers.
    Supports both PostgreSQL (postgresql+psycopg2:// or postgresql://) and SQLite (sqlite:///).
    """
    if raw_url:
        url = raw_url
    else:
        x_args = context.get_x_argument(as_dictionary=True)
        if "db_url" in x_args:
            url = x_args["db_url"]
        elif config.get_main_option("db_url"):
            url = config.get_main_option("db_url")
        elif config.get_main_option("sqlalchemy.url") and config.get_main_option("sqlalchemy.url") not in (
            "sqlite:///data_grinder.db",
            "sqlite:///./data_grinder.db",
            "driver://user:pass@localhost/dbname"
        ):
            url = config.get_main_option("sqlalchemy.url")
        elif getattr(settings, "DATABASE_URL", None):
            url = settings.DATABASE_URL
        else:
            url = config.get_main_option("sqlalchemy.url") or "sqlite:///data_grinder.db"

    # Normalize postgres:// prefix if present
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    # Convert async driver prefixes to sync equivalents
    if "postgresql+asyncpg://" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    elif "asyncpg" in url:
        url = url.replace("asyncpg", "psycopg2")

    if "sqlite+aiosqlite://" in url:
        url = url.replace("sqlite+aiosqlite://", "sqlite://")

    return url



def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    sync_url = get_sync_url()
    connectable = create_engine(
        sync_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


try:
    is_offline = context.is_offline_mode()
except (AttributeError, NameError):
    is_offline = None

if is_offline is True:
    run_migrations_offline()
elif is_offline is False:
    run_migrations_online()
