import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Тестовые значения секретов: без них чистая копия (без .env) не проходит тесты —
# bot.py падает при импорте без TELEGRAM_BOT_TOKEN, а админ-эндпоинты отвечают 403 при пустом ADMIN_TOKEN.
# setdefault не трогает уже заданные переменные и должен выполниться ДО импорта приложения
# (load_dotenv не перезаписывает существующие переменные окружения). Боевое поведение bot.py не меняется.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:fake")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-fake")
os.environ.setdefault("ADMIN_TOKEN", "testtoken123")

from app.core.config import settings
settings.TESTING = True
settings.DEBUG = True

import pytest


@pytest.fixture(autouse=True)
def _quality_gate_off_by_default(monkeypatch):
    """Подставные карточки тестов без цитат — «подозрительные»; отбраковку и обрезку по важности включают только свои тесты."""
    from app.services.ai_gateway import path_builder
    monkeypatch.setattr(path_builder, "QUALITY_GATE", False)
