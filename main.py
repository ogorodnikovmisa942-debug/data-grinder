import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from jinja2 import Template

from app.api.endpoints import train, management, admin, graph, practice
from app.core.config import settings
from app.database.session import engine
from app.database.models import Base
from app.database.migrations import backup_sqlite_database, run_sqlite_pragma_migrations
from app.services.notifications import notification_scheduler_loop
from app.services.generation_worker import generation_worker_loop

ADMIN_TEMPLATE_PATH = Path("app/templates/admin.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Автоматический снапшот базы данных перед стартом (гарантия сохранности карточек)
    backup_sqlite_database("data_grinder.db")

    # 2. Создаем новые таблицы, если они не существуют
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. Выполняем точечные миграции для SQLite
    await run_sqlite_pragma_migrations(engine)

    # 4. Запускаем фоновый планировщик уведомлений Telegram и воркер нарезки карточек
    asyncio.create_task(notification_scheduler_loop())
    asyncio.create_task(generation_worker_loop())

    yield


app = FastAPI(title="Data Grinder Движок", lifespan=lifespan)


@app.middleware("http")
async def add_cache_control_header(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".html") or path.endswith(".js") or path.endswith(".css") or path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Подключаем роутеры API (префикс /api)
app.include_router(train.router, prefix="/api", tags=["Training"])
app.include_router(management.router, prefix="/api", tags=["Management"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(graph.router, prefix="/api", tags=["Knowledge Graph"])
app.include_router(practice.router, prefix="/api", tags=["Practice"])


# Главная страница MiniApp
@app.get("/")
async def read_index():
    return FileResponse(
        "app/static/index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


# Веб-страница панели администратора
@app.get("/admin", response_class=HTMLResponse)
async def admin_web_page():
    curr_model = settings.DEEPSEEK_MODEL or "deepseek-flash"
    admin_token = settings.ADMIN_TOKEN or "secret-admin-token"
    ds_key_badge = (
        '<span style="color:#4ade80;">🔑 Ключ OK</span>'
        if settings.DEEPSEEK_API_KEY
        else '<span style="color:#f59e0b;">⚠️ Ключ не задан (.env)</span>'
    )

    template_str = ADMIN_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered_html = Template(template_str).render(
        curr_model=curr_model,
        admin_token=admin_token,
        ds_key_badge=ds_key_badge,
        base_url=settings.DEEPSEEK_BASE_URL
    )
    return HTMLResponse(rendered_html)


# Статика
app.mount("/", StaticFiles(directory="app/static"), name="static")