import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
settings.TESTING = True
settings.DEBUG = True

import pytest


@pytest.fixture(autouse=True)
def _quality_gate_off_by_default(monkeypatch):
    """Подставные карточки тестов без цитат — «подозрительные»; отбраковку и обрезку по важности включают только свои тесты."""
    from app.services.ai_gateway import path_builder
    monkeypatch.setattr(path_builder, "QUALITY_GATE", False)
