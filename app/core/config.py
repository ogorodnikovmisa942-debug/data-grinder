import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

class Settings:
    PROJECT_NAME: str = "Data Grinder"
    
    # Используем SQLite локально, но оставляем возможность переопределить через переменные окружения
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data_grinder.db")
    
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "placeholder_bot_token")
    AI_PROVIDER: str = "deepseek"
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://datagrinder.site")
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "secret-admin-token")
    ADMIN_TELEGRAM_ID: str = os.getenv("ADMIN_TELEGRAM_ID", "")
    EXPERIMENT_DAILY_LIMIT: int = int(os.getenv("EXPERIMENT_DAILY_LIMIT", "20"))

settings = Settings()