from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse  # Импортируем для прямой отдачи HTML
from app.api.endpoints import train, management, admin, graph, practice
from app.core.config import settings
from app.database.session import engine
from app.database.models import Base

# 1. Создание таблиц при запуске (асинхронно через lifespan) и запуск миграций
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Автоматический снапшот базы данных перед стартом (гарантия сохранности карточек)
    import os
    import shutil
    from datetime import datetime
    
    db_file = "data_grinder.db"
    if os.path.exists(db_file) and os.path.getsize(db_file) > 0:
        try:
            os.makedirs("backups", exist_ok=True)
            shutil.copy2(db_file, "backups/data_grinder.latest.bak")
            today_bak = f"backups/data_grinder_{datetime.now().strftime('%Y%m%d')}.bak"
            if not os.path.exists(today_bak):
                shutil.copy2(db_file, today_bak)
            print("[SAFE-BACKUP] Автоматический бэкап базы успешно сохранен в backups/")
        except Exception as b_err:
            print(f"[WARNING] Не удалось создать автобэкап базы: {b_err}")

    # 1. Создаем новые таблицы, если они не существуют
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
            if "is_outlier" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN is_outlier BOOLEAN DEFAULT 0"))
            if "is_cram" not in columns:
                await db.execute(text("ALTER TABLE review_logs ADD COLUMN is_cram BOOLEAN DEFAULT 0"))
            
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
            if "topological_rank" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN topological_rank INTEGER DEFAULT 0"))
            if "organ_slug" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN organ_slug VARCHAR"))
            if "layer" not in columns_cards:
                await db.execute(text("ALTER TABLE cards ADD COLUMN layer INTEGER DEFAULT 1"))

            # Миграции для user_sessions (отметки уведомлений и флаги эксперимента)
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
            if "is_experiment_participant" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN is_experiment_participant BOOLEAN DEFAULT 0"))
            if "experiment_phase" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN experiment_phase INTEGER DEFAULT 1"))
            if "username" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN username VARCHAR"))
            if "full_name" not in columns_users:
                await db.execute(text("ALTER TABLE user_sessions ADD COLUMN full_name VARCHAR"))

            # Миграции для user_settings (флаги эксперимента)
            res_settings = await db.execute(text("PRAGMA table_info(user_settings)"))
            columns_settings = [row[1] for row in res_settings.fetchall()]
            if "is_experiment_participant" not in columns_settings:
                await db.execute(text("ALTER TABLE user_settings ADD COLUMN is_experiment_participant BOOLEAN DEFAULT 0"))
            if "experiment_phase" not in columns_settings:
                await db.execute(text("ALTER TABLE user_settings ADD COLUMN experiment_phase INTEGER DEFAULT 1"))

            # Миграции для generation_jobs (хранение карточек для модерации в Песочнице и телеметрия)
            res_jobs = await db.execute(text("PRAGMA table_info(generation_jobs)"))
            columns_jobs = [row[1] for row in res_jobs.fetchall()]
            if "result_cards_json" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN result_cards_json TEXT"))
            if "is_deferred" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN is_deferred BOOLEAN DEFAULT 0"))
            if "char_count" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN char_count INTEGER"))
            if "fallback_used" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN fallback_used BOOLEAN DEFAULT 0"))
            if "json_repair_applied" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN json_repair_applied BOOLEAN DEFAULT 0"))
            if "execution_time_ms" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN execution_time_ms INTEGER"))
            if "error_trace" not in columns_jobs:
                await db.execute(text("ALTER TABLE generation_jobs ADD COLUMN error_trace TEXT"))

            # Миграция для topic_knowledge_graphs
            res_tkg = await db.execute(text("PRAGMA table_info(topic_knowledge_graphs)"))
            columns_tkg = [row[1] for row in res_tkg.fetchall()]
            if columns_tkg and "tree_data" not in columns_tkg:
                await db.execute(text("ALTER TABLE topic_knowledge_graphs ADD COLUMN tree_data JSON"))

            await db.commit()
            
    # Запускаем фоновый планировщик уведомлений Telegram и воркер нарезки карточек
    import asyncio
    from app.services.notifications import notification_scheduler_loop
    from app.services.generation_worker import generation_worker_loop
    asyncio.create_task(notification_scheduler_loop())
    asyncio.create_task(generation_worker_loop())
    
    yield

# 2. Инициализация FastAPI
app = FastAPI(title="Data Grinder Движок", lifespan=lifespan)

# 3. Подключаем роутеры API (префикс /api)
app.include_router(train.router, prefix="/api", tags=["Training"])
app.include_router(management.router, prefix="/api", tags=["Management"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(graph.router, prefix="/api", tags=["Knowledge Graph"])
app.include_router(practice.router, prefix="/api", tags=["Practice"])

# 4. Отдаем главный файл index.html прямо на корневом URL (http://твой_ip:порт/)
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

@app.get("/admin", response_class=HTMLResponse)
async def admin_web_page():
    curr_provider = getattr(settings, "AI_PROVIDER", "deepseek").lower()
    curr_model = settings.MIMO_MODEL if curr_provider == "mimo" else settings.DEEPSEEK_MODEL
    admin_token = settings.ADMIN_TOKEN or "secret-admin-token"
    ds_key_badge = '<span style="color:#4ade80;">🔑 Ключ OK</span>' if settings.DEEPSEEK_API_KEY else '<span style="color:#f59e0b;">⚠️ Ключ не задан</span>'
    mimo_key_badge = '<span style="color:#4ade80;">🔑 Ключ OK</span>' if settings.MIMO_API_KEY else '<span style="color:#f59e0b;">⚠️ Ключ не задан</span>'
    
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Grinder | Панель управления</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" />
    <style>
        body {{ margin: 0; padding: 24px; font-family: 'Inter', sans-serif; background: #0a0a0a; color: #f1f5f9; display: flex; justify-content: center; align-items: center; min-height: 100vh; }}
        .card {{ max-width: 580px; width: 100%; background: #141414; border: 1px solid #262626; border-radius: 20px; padding: 28px; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }}
        h1 {{ font-family: 'Space Grotesk', sans-serif; font-size: 20px; margin: 0 0 16px; text-transform: uppercase; letter-spacing: 1px; display: flex; align-items: center; gap: 8px; }}
        p {{ font-size: 13px; color: #94a3b8; line-height: 1.6; margin: 0 0 16px; }}
        .code-box {{ background: #0a0a0a; border: 1px solid #262626; padding: 12px 16px; border-radius: 12px; font-family: 'JetBrains Mono', monospace; font-size: 13px; color: #38bdf8; margin-bottom: 20px; word-break: break-all; }}
        .btn {{ display: flex; align-items: center; justify-content: center; gap: 8px; padding: 12px 20px; background: #f8fafc; color: #0f172a; font-weight: 700; font-size: 13px; text-decoration: none; border-radius: 12px; transition: all 0.15s; border: none; cursor: pointer; }}
        .btn:hover {{ background: #e2e8f0; }}
        .badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; background: #1e293b; border-radius: 8px; font-size: 11px; font-family: 'JetBrains Mono', monospace; color: #94a3b8; }}
        .ai-panel {{ background: #1a1a1a; border: 1px solid #2e2e2e; border-radius: 16px; padding: 20px; margin-bottom: 20px; }}
        .ai-title {{ font-size: 14px; font-weight: 700; color: #f8fafc; display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }}
        .switcher-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }}
        .switch-card {{ padding: 14px; border-radius: 12px; border: 2px solid #2e2e2e; background: #121212; cursor: pointer; transition: all 0.2s; text-align: left; }}
        .switch-card.active {{ border-color: #38bdf8; background: rgba(56, 189, 248, 0.08); }}
        .switch-card:hover:not(.active) {{ border-color: #475569; }}
        .switch-name {{ font-weight: 700; font-size: 14px; color: #f8fafc; margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }}
        .switch-sub {{ font-size: 11px; color: #94a3b8; line-height: 1.4; }}
        .switch-specs {{ margin-top: 8px; font-size: 10px; font-family: 'JetBrains Mono', monospace; color: #38bdf8; }}
        .status-pill {{ font-size: 10px; padding: 2px 6px; border-radius: 6px; font-weight: 600; text-transform: uppercase; }}
        .status-pill.active {{ background: #0369a1; color: #e0f2fe; }}
        .status-pill.inactive {{ background: #262626; color: #64748b; }}
        .pricing-table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 12px; }}
        .pricing-table th, .pricing-table td {{ padding: 6px 8px; text-align: left; border-bottom: 1px solid #262626; }}
        .pricing-table th {{ color: #94a3b8; font-weight: 600; }}
        .toast {{ display: none; padding: 10px 14px; border-radius: 8px; font-size: 12px; margin-top: 10px; text-align: center; }}
        .toast.success {{ display: block; background: #064e3b; color: #6ee7b7; border: 1px solid #059669; }}
        .toast.error {{ display: block; background: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }}
    </style>
</head>
<body>
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <span class="badge"><span class="material-symbols-outlined" style="font-size:14px;">terminal</span> DATA GRINDER ADMIN</span>
            <span class="badge" id="ai-active-badge" style="color:#38bdf8; font-weight:bold;">ИИ: {curr_provider.upper()}</span>
        </div>
        <h1><span class="material-symbols-outlined" style="color:#38bdf8;">admin_panel_settings</span> Панель администратора</h1>
        
        <!-- СЕКЦИЯ ПЕРЕКЛЮЧЕНИЯ ИИ-ПРОВАЙДЕРА -->
        <div class="ai-panel">
            <div class="ai-title">
                <span style="display:flex; align-items:center; gap:6px;">
                    <span class="material-symbols-outlined" style="color:#38bdf8; font-size:18px;">psychology</span>
                    Переключатель ИИ-провайдера
                </span>
                <span id="current-model-tag" style="font-size:11px; font-family:'JetBrains Mono', monospace; color:#94a3b8;">
                    {curr_model}
                </span>
            </div>

            <div class="switcher-grid">
                <!-- DeepSeek Card -->
                <div class="switch-card {'active' if curr_provider == 'deepseek' else ''}" id="card-deepseek" onclick="selectProvider('deepseek')">
                    <div class="switch-name">
                        DeepSeek
                        <span class="status-pill {'active' if curr_provider == 'deepseek' else 'inactive'}" id="pill-deepseek">
                            {'АКТИВЕН' if curr_provider == 'deepseek' else 'ВЫБРАТЬ'}
                        </span>
                    </div>
                    <div class="switch-sub">deepseek-chat (V3)<br>Экономичный стандарт</div>
                    <div class="switch-specs">
                        Контекст: 64K | Вывод: 8K<br>
                        {ds_key_badge}
                    </div>
                </div>

                <!-- Xiaomi MiMo Card -->
                <div class="switch-card {'active' if curr_provider == 'mimo' else ''}" id="card-mimo" onclick="selectProvider('mimo')">
                    <div class="switch-name">
                        Xiaomi MiMo
                        <span class="status-pill {'active' if curr_provider == 'mimo' else 'inactive'}" id="pill-mimo">
                            {'АКТИВЕН' if curr_provider == 'mimo' else 'ВЫБРАТЬ'}
                        </span>
                    </div>
                    <div class="switch-sub">mimo-v2.5 / pro<br>Сверхдлинный контекст</div>
                    <div class="switch-specs">
                        Контекст: 1M | Вывод: 128K<br>
                        {mimo_key_badge}
                    </div>
                </div>
            </div>

            <div style="display:flex; gap:8px; align-items:center;">
                <input type="password" id="admin-token-input" value="{admin_token}" placeholder="X-Admin-Token" style="flex:1; background:#0a0a0a; border:1px solid #2e2e2e; border-radius:8px; padding:8px 12px; color:#f1f5f9; font-size:12px; font-family:'JetBrains Mono', monospace;">
                <button class="btn" style="padding:8px 16px; font-size:12px;" onclick="applySwitch()">Применить</button>
            </div>
            <div id="switch-toast" class="toast"></div>

            <table class="pricing-table">
                <thead>
                    <tr>
                        <th>Провайдер</th>
                        <th>Контекст</th>
                        <th>Кэш-хит ($/1M)</th>
                        <th>Кэш-мисс ($/1M)</th>
                        <th>Вывод ($/1M)</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="color:#38bdf8; font-weight:600;">DeepSeek</td>
                        <td>64,000</td>
                        <td>$0.014 <span style="color:#4ade80;">(-90%)</span></td>
                        <td>$0.14</td>
                        <td>$0.28</td>
                    </tr>
                    <tr>
                        <td style="color:#a855f7; font-weight:600;">Xiaomi MiMo</td>
                        <td>1,000,000</td>
                        <td>$0.0035 <span style="color:#4ade80;">(-99%)</span></td>
                        <td>$0.42</td>
                        <td>$0.84</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <p>Управление экспериментом, участниками, фазами и выгрузкой датасетов также доступно через Telegram-бота:</p>
        <div class="code-box">
            /admin {admin_token}
        </div>
        
        <div style="display:flex; gap:10px; margin-top:20px;">
            <a href="/" class="btn" style="flex:1; background:#262626; color:#f1f5f9;"><span class="material-symbols-outlined" style="font-size:16px;">arrow_back</span> В консоль</a>
            <a href="/api/admin/export/experiment-dataset" class="btn" style="flex:1;"><span class="material-symbols-outlined" style="font-size:16px;">download</span> Датасет CSV</a>
        </div>
    </div>

    <script>
        let selectedProvider = "{curr_provider}";

        function selectProvider(prov) {{
            selectedProvider = prov;
            document.querySelectorAll('.switch-card').forEach(c => c.classList.remove('active'));
            document.getElementById('card-' + prov).classList.add('active');
        }}

        async function applySwitch() {{
            const token = document.getElementById('admin-token-input').value.trim();
            const toast = document.getElementById('switch-toast');
            toast.className = 'toast';
            toast.style.display = 'none';

            try {{
                const res = await fetch('/api/admin/switch-ai-provider', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Admin-Token': token
                    }},
                    body: JSON.stringify({{ provider: selectedProvider }})
                }});
                const data = await res.json();
                if (res.ok) {{
                    if (data.warning) {{
                        toast.className = 'toast error';
                        toast.innerText = data.message || ('ИИ переключен. ' + data.warning);
                    }} else {{
                        toast.className = 'toast success';
                        toast.innerText = 'Успешно: ' + (data.message || 'ИИ переключен на ' + selectedProvider.toUpperCase());
                    }}
                    document.getElementById('ai-active-badge').innerText = 'ИИ: ' + selectedProvider.toUpperCase();
                    document.getElementById('current-model-tag').innerText = data.model || selectedProvider;
                    
                    document.getElementById('pill-deepseek').className = selectedProvider === 'deepseek' ? 'status-pill active' : 'status-pill inactive';
                    document.getElementById('pill-deepseek').innerText = selectedProvider === 'deepseek' ? 'АКТИВЕН' : 'ВЫБРАТЬ';
                    document.getElementById('pill-mimo').className = selectedProvider === 'mimo' ? 'status-pill active' : 'status-pill inactive';
                    document.getElementById('pill-mimo').innerText = selectedProvider === 'mimo' ? 'АКТИВЕН' : 'ВЫБРАТЬ';
                }} else {{
                    toast.className = 'toast error';
                    toast.innerText = 'Ошибка (' + res.status + '): ' + (data.detail || JSON.stringify(data));
                }}
            }} catch (err) {{
                toast.className = 'toast error';
                toast.innerText = 'Сетевой сбой при переключении: ' + err.message;
            }}
        }}
    </script>
</body>
</html>"""
    )

# 5. Монтируем папку статики на корневой префикс /
# Теперь запросы фронтенда к /css/... и /js/... будут отрабатывать корректно
app.mount("/", StaticFiles(directory="app/static"), name="static")