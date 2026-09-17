import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

class Settings:
    PROJECT_NAME: str = "Data Grinder"
    
    # Используем SQLite локально, но оставляем возможность переопределить через переменные окружения
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data_grinder.db")
    
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "placeholder_bot_token").strip().strip('"\'')
    TELEGRAM_BOT_USERNAME: str = os.getenv("TELEGRAM_BOT_USERNAME", "DATAGRINDERbot").strip().strip('"\'')
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "deepseek").strip().strip('"\'').lower()
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip().strip('"\'')
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip().strip('"\'')
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().strip('"\'')
    
    # Xiaomi MiMo Configuration (1M context, 128K output)
    MIMO_API_KEY: str = os.getenv("MIMO_API_KEY", "").strip().strip('"\'')
    MIMO_MODEL: str = os.getenv("MIMO_MODEL", "mimo-v2.5").strip().strip('"\'')
    MIMO_BASE_URL: str = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip().strip('"\'')
    
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://datagrinder.site").strip().strip('"\'')
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "secret-admin-token").strip().strip('"\'')
    ADMIN_TELEGRAM_ID: str = os.getenv("ADMIN_TELEGRAM_ID", "")
    EXPERIMENT_DAILY_LIMIT: int = int(os.getenv("EXPERIMENT_DAILY_LIMIT", "20"))

settings = Settings()