from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse  # Импортируем для прямой отдачи HTML
from app.api.endpoints import train, management
from app.database.session import engine
from app.database.models import Base

# 1. Создание таблиц при запуске (асинхронно через lifespan) и запуск миграций
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем новые таблицы, если они не существуют
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    # Выполняем точечные миграции для sqlite, если добавлялись новые колонки
    if "sqlite" in engine.url.drivername:
        from app.database.session import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            res = await db.execute(text("PRAGMA table_info(review_logs)"))
            columns = [row[1] for row in res.fetchall()]
            
            if "has_association" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN has_association BOOLEAN DEFAULT 0"))
            if "response_time" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN response_time INTEGER"))
            if "stability" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN stability FLOAT"))
            if "difficulty" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN difficulty FLOAT"))
            if "timestamp" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN timestamp DATETIME"))
            
            # Миграции для cards
            res_cards = await db.execute(text("PRAGMA table_info(cards)"))
            columns_cards = [row[1] for row in res_cards.fetchall()]
            if "has_seen_intro" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN has_seen_intro BOOLEAN DEFAULT 0"))
            if "intro_phase" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN intro_phase INTEGER DEFAULT 0"))
            if "content_type" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN content_type VARCHAR DEFAULT 'text'"))
            if "example" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN example VARCHAR"))

            # Миграции для user_sessions (отметки уведомлений)
            res_users = await db.execute(text("PRAGMA table_info(user_sessions)"))
            columns_users = [row[1] for row in res_users.fetchall()]
            if "last_morning_sent" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN last_morning_sent VARCHAR"))
            if "last_evening_sent" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN last_evening_sent VARCHAR"))
            if "last_due_notified_at" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN last_due_notified_at DATETIME"))
            if "last_due_count" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN last_due_count INTEGER DEFAULT 0"))

            await db.commit()
            
    # Запускаем фоновый планировщик уведомлений Telegram
    import asyncio
    from app.services.notifications import notification_scheduler_loop
    asyncio.create_task(notification_scheduler_loop())
    
    yield

# 2. Инициализация FastAPI
app = FastAPI(title="Data Grinder Движок", lifespan=lifespan)

# 3. Подключаем роутеры API (префикс /api)
app.include_router(train.router, prefix="/api", tags=["Training"])
app.include_router(management.router, prefix="/api", tags=["Management"])

# 4. Отдаем главный файл index.html прямо на корневом URL (http://твой_ip:порт/)
@app.get("/")
async def read_index():
    return FileResponse("app/static/index.html")

# 5. Монтируем папку статики на корневой префикс /
# Теперь запросы фронтенда к /css/... и /js/... будут отрабатывать корректно
app.mount("/", StaticFiles(directory="app/static"), name="static")