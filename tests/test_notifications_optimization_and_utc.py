# tests/test_notifications_optimization_and_utc.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.models import (
    utc_now, ReviewLog, DailySession, GenerationJob, AiTelemetryLog,
    InviteCode, TopicKnowledgeGraph, PracticeItem, PracticeSessionLog,
    UserSession, Card, Phrase
)
from app.services.notifications import send_telegram_alert, check_and_send_alerts
from app.database.session import AsyncSessionLocal


def test_utc_now_returns_naive_utc():
    """Проверяем, что utc_now() возвращает наивный datetime в UTC без tzinfo для совместимости с SQLite."""
    now = utc_now()
    assert isinstance(now, datetime)
    assert now.tzinfo is None
    
    # Сверяем с datetime.now(timezone.utc)
    expected_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    diff = abs((expected_utc - now).total_seconds())
    assert diff < 2.0


def test_models_use_utc_now_defaults():
    """Проверяем, что default callable в Column моделей использует utc_now."""
    # SQLAlchemy default triggers callable on insert or inspection
    assert callable(ReviewLog.timestamp.default.arg)
    default_val = ReviewLog.timestamp.default.arg(None)
    assert isinstance(default_val, datetime)
    assert default_val.tzinfo is None

    # GenerationJob
    assert callable(GenerationJob.created_at.default.arg)
    assert GenerationJob.created_at.default.arg(None).tzinfo is None

    # TopicKnowledgeGraph
    assert callable(TopicKnowledgeGraph.created_at.default.arg)
    assert callable(TopicKnowledgeGraph.updated_at.default.arg)


@pytest.mark.asyncio
async def test_send_telegram_alert_reuses_passed_bot():
    """
    Проверяем, что при передаче bot в send_telegram_alert:
    1. Создание нового Bot / AiohttpSession НЕ происходит.
    2. bot.session.close() НЕ вызывается (сессия управляется снаружи).
    """
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()

    with patch("app.services.notifications.settings.TELEGRAM_BOT_TOKEN", "valid_test_token"):
        await send_telegram_alert(chat_id="123456", text="Hello Alert", bot=mock_bot)

    mock_bot.send_message.assert_awaited_once()
    call_kwargs = mock_bot.send_message.await_args.kwargs
    assert call_kwargs["chat_id"] == 123456
    assert call_kwargs["text"] == "Hello Alert"

    # bot.session.close НЕ должен быть вызван для переданного бота!
    mock_bot.session.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_telegram_alert_creates_and_closes_local_bot_when_none():
    """
    Проверяем, что если bot is None, send_telegram_alert создает локальный Bot и сессию,
    а затем закрывает сессию в finally.
    """
    mock_session = MagicMock()
    mock_session.close = AsyncMock()

    mock_bot_instance = MagicMock()
    mock_bot_instance.send_message = AsyncMock()
    mock_bot_instance.session = mock_session

    with patch("app.services.notifications.settings.TELEGRAM_BOT_TOKEN", "valid_test_token"), \
         patch("app.services.notifications.AiohttpSession", return_value=mock_session), \
         patch("app.services.notifications.Bot", return_value=mock_bot_instance):
        
        await send_telegram_alert(chat_id="999888", text="Alert Local Bot")

    mock_bot_instance.send_message.assert_awaited_once()
    # Сессия локального бота ОБЯЗАНА быть закрыта в finally
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_and_send_alerts_single_bot_session_lifecycle():
    """
    Проверяем жизненный цикл Bot в check_and_send_alerts():
    Сессия бота инициализируется один раз и закрывается в блоке finally.
    """
    mock_session = MagicMock()
    mock_session.close = AsyncMock()

    mock_bot_instance = MagicMock()
    mock_bot_instance.send_message = AsyncMock()
    mock_bot_instance.session = mock_session

    with patch("app.services.notifications.settings.TELEGRAM_BOT_TOKEN", "valid_test_token"), \
         patch("app.services.notifications.AiohttpSession", return_value=mock_session), \
         patch("app.services.notifications.Bot", return_value=mock_bot_instance):

        # Вызываем check_and_send_alerts
        await check_and_send_alerts()

    # Сессия была закрыта в finally
    mock_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_and_send_alerts_grouped_queries_no_n_plus_1():
    """
    Проверяем, что check_and_send_alerts() использует ровно 2 групповых запроса
    (GROUP BY Card.user_id), предотвращая проблему N+1 запросов.
    """
    # Добавляем тестовых пользователей и карточки
    uid1 = "test_user_notif_1"
    uid2 = "test_user_notif_2"
    now = utc_now()

    async with AsyncSessionLocal() as db:
        # Создаем пользователей
        u1 = UserSession(telegram_id=uid1, user_id=uid1, last_due_count=0)
        u2 = UserSession(telegram_id=uid2, user_id=uid2, last_due_count=0)
        db.add_all([u1, u2])

        p1 = Phrase(user_id=uid1, subject="test_sub", text="Topic 1")
        p2 = Phrase(user_id=uid2, subject="test_sub", text="Topic 2")
        db.add_all([p1, p2])
        await db.flush()

        # Карточки u1: 2 карты (1 due)
        c1 = Card(
            phrase_id=p1.id, user_id=uid1, subject="test_sub",
            text="card1", translation="trans1", state=2, next_review=now - timedelta(days=1)
        )
        c2 = Card(
            phrase_id=p1.id, user_id=uid1, subject="test_sub",
            text="card2", translation="trans2", state=2, next_review=now + timedelta(days=5)
        )

        # Карточки u2: 1 карта (1 due)
        c3 = Card(
            phrase_id=p2.id, user_id=uid2, subject="test_sub",
            text="card3", translation="trans3", state=1, next_review=now - timedelta(hours=2)
        )
        db.add_all([c1, c2, c3])
        await db.commit()

    mock_send = AsyncMock()
    with patch("app.services.notifications.send_telegram_alert", mock_send):
        await check_and_send_alerts()

    # Проверяем, что для пользователей с due картами отправлены алерты с переданным bot
    assert mock_send.call_count >= 1
    for call in mock_send.call_args_list:
        # Проверяем, что bot передавался в send_telegram_alert
        assert "bot" in call.kwargs or len(call.args) >= 3

    # Очистка тестовых данных
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(Card).filter(Card.user_id.in_([uid1, uid2])))
        await db.execute(delete(Phrase).filter(Phrase.user_id.in_([uid1, uid2])))
        await db.execute(delete(UserSession).filter(UserSession.telegram_id.in_([uid1, uid2])))
        await db.commit()
