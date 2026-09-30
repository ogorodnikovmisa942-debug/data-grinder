import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения из .env (по абсолютному пути от корня проекта и cwd)
_root_env = Path(__file__).resolve().parents[2] / ".env"
if _root_env.exists():
    load_dotenv(dotenv_path=_root_env)
load_dotenv()

def normalize_database_url(url: str) -> str:
    """
    Normalize DATABASE_URL for async drivers:
    - postgres:// -> postgresql+asyncpg://
    - postgresql:// (without driver) -> postgresql+asyncpg://
    - sqlite:// (without driver) -> sqlite+aiosqlite://
    """
    if not url:
        return "sqlite+aiosqlite:///./data_grinder.db"
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://"):]
    return url


class Settings:
    PROJECT_NAME: str = "Data Grinder"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")
    TESTING: bool = os.getenv("TESTING", "False").lower() in ("true", "1", "t")
    
    # Используем SQLite локально, но оставляем возможность переопределить через переменные окружения
    DATABASE_URL: str = normalize_database_url(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data_grinder.db")
    )
    
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "placeholder_bot_token").strip().strip('"\'')
    TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "DATAGRINDERbot").strip().strip('"\'')
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "deepseek").strip().strip('"\'').lower()
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip().strip('"\'')
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip().strip('"\'')
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().strip('"\'')
    # Пиковые цены deepseek-flash, $ за 1M токенов (вне пика — половина). Источник: api-docs.deepseek.com/quick_start/pricing
    DEEPSEEK_PRICE_CACHE_HIT: float = float(os.getenv("DEEPSEEK_PRICE_CACHE_HIT", "0.006"))
    DEEPSEEK_PRICE_CACHE_MISS: float = float(os.getenv("DEEPSEEK_PRICE_CACHE_MISS", "0.30"))
    DEEPSEEK_PRICE_OUTPUT: float = float(os.getenv("DEEPSEEK_PRICE_OUTPUT", "1.20"))

    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://datagrinder.site").strip().strip('"\'')
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "").strip().strip('"\'')
    ADMIN_TELEGRAM_ID: str = os.getenv("ADMIN_TELEGRAM_ID", "")
    EXPERIMENT_DAILY_LIMIT: int = int(os.getenv("EXPERIMENT_DAILY_LIMIT", "10"))

    # Максимальный возраст подписанного initData (Telegram кэширует WebView, поэтому с запасом)
    INIT_DATA_MAX_AGE_SECONDS: int = int(os.getenv("INIT_DATA_MAX_AGE_SECONDS", str(7 * 24 * 3600)))

    def is_dev_mode(self) -> bool:
        """Локальная разработка/тесты: только здесь допустимы неподписанные идентификаторы пользователя."""
        return bool(
            self.DEBUG
            or self.TESTING
            or not self.TELEGRAM_BOT_TOKEN
            or self.TELEGRAM_BOT_TOKEN == "placeholder_bot_token"
        )

    def __setattr__(self, name, value):
        if name == "DATABASE_URL" and isinstance(value, str):
            value = normalize_database_url(value)
        super().__setattr__(name, value)

settings = Settings()