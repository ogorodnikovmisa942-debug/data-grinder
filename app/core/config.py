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
    # Второй поставщик ИИ на случай сбоя DeepSeek (любой совместимый с OpenAI API: /chat/completions, JSON-режим). Пусто — выключен.
    FALLBACK_AI_BASE_URL: str = os.getenv("FALLBACK_AI_BASE_URL", "").strip().strip('"\'')
    FALLBACK_AI_API_KEY: str = os.getenv("FALLBACK_AI_API_KEY", "").strip().strip('"\'')
    FALLBACK_AI_MODEL: str = os.getenv("FALLBACK_AI_MODEL", "").strip().strip('"\'')
    DEEPSEEK_PRICE_CACHE_HIT: float = float(os.getenv("DEEPSEEK_PRICE_CACHE_HIT", "0.006"))
    DEEPSEEK_PRICE_CACHE_MISS: float = float(os.getenv("DEEPSEEK_PRICE_CACHE_MISS", "0.30"))
    DEEPSEEK_PRICE_OUTPUT: float = float(os.getenv("DEEPSEEK_PRICE_OUTPUT", "1.20"))

    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://datagrinder.site").strip().strip('"\'')
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "").strip().strip('"\'')
    ADMIN_TELEGRAM_ID: str = os.getenv("ADMIN_TELEGRAM_ID", "")
    EXPERIMENT_DAILY_LIMIT: int = int(os.getenv("EXPERIMENT_DAILY_LIMIT", "10"))

    DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip()

    # Защита от неконтролируемых трат на ИИ: размер материала и число задач на пользователя
    MAX_IMPORT_CHARS: int = int(os.getenv("MAX_IMPORT_CHARS", "1500000"))
    MAX_IMPORT_FILE_BYTES: int = int(os.getenv("MAX_IMPORT_FILE_BYTES", str(30 * 1024 * 1024)))
    MAX_IMPORT_FILES: int = int(os.getenv("MAX_IMPORT_FILES", "10"))
    MAX_ACTIVE_JOBS_PER_USER: int = int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "3"))
    MAX_JOBS_PER_HOUR: int = int(os.getenv("MAX_JOBS_PER_HOUR", "10"))

    # Ежедневные копии базы (app/services/backup.py): сколько хранить и куда класть вторую копию (папка, синхронизируемая в облако)
    BACKUP_ENABLED: bool = os.getenv("BACKUP_ENABLED", "1").lower() in ("1", "true", "yes")
    BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "14"))
    BACKUP_EXTERNAL_DIR: str = os.getenv("BACKUP_EXTERNAL_DIR", "").strip()

    # Тарифы и месячные квоты на ИИ (app/services/quota.py). По умолчанию ВЫКЛЮЧЕНЫ: включает владелец, когда появится оплата.
    # Числа из плана 2026-10-06 — гипотезы для проверки: бесплатно одна небольшая книга в ночной очереди, платно до 5 книг в месяц.
    QUOTAS_ENABLED: bool = os.getenv("QUOTAS_ENABLED", "0").lower() in ("1", "true", "yes")
    FREE_BOOK_CHARS: int = int(os.getenv("FREE_BOOK_CHARS", "50000"))
    FREE_BOOKS_PER_MONTH: int = int(os.getenv("FREE_BOOKS_PER_MONTH", "1"))
    FREE_MONTHLY_AI_USD: float = float(os.getenv("FREE_MONTHLY_AI_USD", "0.15"))
    FREE_EXAM_PLANS_PER_SUBJECT: int = int(os.getenv("FREE_EXAM_PLANS_PER_SUBJECT", "1"))
    PAID_BOOK_CHARS: int = int(os.getenv("PAID_BOOK_CHARS", "1500000"))
    PAID_BOOKS_PER_MONTH: int = int(os.getenv("PAID_BOOKS_PER_MONTH", "5"))
    PAID_MONTHLY_AI_USD: float = float(os.getenv("PAID_MONTHLY_AI_USD", "1.20"))
    PAID_AI_CHECKS_PER_MONTH: int = int(os.getenv("PAID_AI_CHECKS_PER_MONTH", "30"))   # ИИ-оценка смысла ответа на билет

    # Жёсткие потолки на платный ИИ вне нарезки. Действуют ВСЕГДА, независимо от QUOTAS_ENABLED (админ и dev без лимитов):
    # у каждой функции цена действия известна заранее, а сумма за период ограничена кодом, а не тарифом.
    AI_CHECK_ENABLED: bool = os.getenv("AI_CHECK_ENABLED", "0").lower() in ("1", "true", "yes")   # ИИ-оценка смысла ответа: выключена; проверка по тезисам (без ИИ) работает
    MNEMONIC_PER_DAY: int = int(os.getenv("MNEMONIC_PER_DAY", "10"))             # «Мнемоника» ≈ 0.01¢ за нажатие, не больше стольких в сутки на пользователя
    EXAM_PLAN_BUDGET_USD: float = float(os.getenv("EXAM_PLAN_BUDGET_USD", "0.08"))     # разбор билетов одного плана вместе с повторами и автопоиском (в ценах вне пика; в пик вдвое выше)
    EXAM_DAILY_BUDGET_USD: float = float(os.getenv("EXAM_DAILY_BUDGET_USD", "0.16"))   # разбор билетов на пользователя в сутки (в ценах вне пика)

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