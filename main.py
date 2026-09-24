import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from jinja2 import Template

from app.api.endpoints import train, management, admin, graph, practice
from app.core.config import settings
from sqlalchemy import select, func
from app.database.session import engine, AsyncSessionLocal
from app.database.models import Base, UserSession, Card, ReviewLog
from app.database.migrations import backup_sqlite_database, run_sqlite_pragma_migrations
from app.services.notifications import notification_scheduler_loop
from app.services.generation_worker import generation_worker_loop
from app.services.frontend_bundler import bundle_modules, bundle_html

from app.core.limiter import limiter, RateLimitExceeded, _rate_limit_exceeded_handler, HAS_SLOWAPI

ADMIN_TEMPLATE_PATH = Path("app/templates/admin.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Автоматическая сборка модулей фронтенда (гарантия актуальности app.js и index.html)
    try:
        await asyncio.to_thread(bundle_modules)
        await asyncio.to_thread(bundle_html)
    except Exception as e:
        print(f"[Frontend Bundler Warning] {e}")

    # 1. Автоматический снапшот базы данных перед стартом (только для SQLite)
    if "sqlite" in engine.url.drivername:
        await asyncio.to_thread(backup_sqlite_database, "data_grinder.db")

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

# Rate limiting для защиты от abuse
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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


# Фавикон (204 No Content, чтобы не засорять логи 404-ми)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


# Веб-страница панели администратора
@app.get("/admin", response_class=HTMLResponse)
async def admin_web_page():
    curr_model = settings.DEEPSEEK_MODEL or "deepseek-flash"
    admin_token = settings.ADMIN_TOKEN
    if not admin_token:
        return HTMLResponse("<h1>ADMIN_TOKEN not configured in .env</h1>", status_code=503)
    ds_key_badge = (
        '<span style="color:#4ade80;">🔑 Ключ OK</span>'
        if settings.DEEPSEEK_API_KEY
        else '<span style="color:#f59e0b;">⚠️ Ключ не задан (.env)</span>'
    )

    total_users, part_users, total_cards, total_reviews = 0, 0, 0, 0
    try:
        async with AsyncSessionLocal() as db:
            total_users = (await db.execute(select(func.count(UserSession.id)))).scalar() or 0
            part_users = (await db.execute(select(func.count(UserSession.id)).filter(UserSession.is_experiment_participant == True))).scalar() or 0
            total_cards = (await db.execute(select(func.count(Card.id)))).scalar() or 0
            total_reviews = (await db.execute(select(func.count(ReviewLog.id)))).scalar() or 0
    except Exception as e:
        print(f"[Admin Web Notice] {e}")

    template_str = ADMIN_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered_html = Template(template_str).render(
        curr_model=curr_model,
        admin_token=admin_token,
        ds_key_badge=ds_key_badge,
        base_url=settings.DEEPSEEK_BASE_URL,
        total_users=total_users,
        part_users=part_users,
        total_cards=total_cards,
        total_reviews=total_reviews
    )
    return HTMLResponse(rendered_html)


# Статика
app.mount("/", StaticFiles(directory="app/static"), name="static")