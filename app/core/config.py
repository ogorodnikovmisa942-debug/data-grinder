import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

class Settings:
    PROJECT_NAME: str = "Data Grinder"
    
    # Используем SQLite локально, но оставляем возможность переопределить через переменные окружения
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data_grinder.db")
    
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "placeholder_bot_token")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "placeholder_gemini_key")
    WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://datagrinder.site")

settings = Settings()