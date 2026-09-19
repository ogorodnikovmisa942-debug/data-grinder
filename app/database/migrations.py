# app/database/migrations.py
import os
import shutil
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from app.database.session import AsyncSessionLocal


def backup_sqlite_database(db_file: str = "data_grinder.db") -> None:
    """Автоматический снапшот базы данных перед стартом (гарантия сохранности карточек)."""
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


async def run_sqlite_pragma_migrations(engine: AsyncEngine) -> None:
    """Выполняет точечные миграции для SQLite, если в схему добавлялись новые колонки."""
    if "sqlite" not in engine.url.drivername:
        return

    async with AsyncSessionLocal() as db:
        # 1. Миграции для review_logs
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
        
        # 2. Миграции для cards
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

        # 3. Миграции для user_sessions (отметки уведомлений и флаги эксперимента)
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

        # 4. Миграции для user_settings (флаги эксперимента)
        res_settings = await db.execute(text("PRAGMA table_info(user_settings)"))
        columns_settings = [row[1] for row in res_settings.fetchall()]
        if "is_experiment_participant" not in columns_settings:
            await db.execute(text("ALTER TABLE user_settings ADD COLUMN is_experiment_participant BOOLEAN DEFAULT 0"))
        if "experiment_phase" not in columns_settings:
            await db.execute(text("ALTER TABLE user_settings ADD COLUMN experiment_phase INTEGER DEFAULT 1"))

        # 5. Миграции для generation_jobs (хранение карточек для модерации в Песочнице и телеметрия)
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

        # 6. Миграция для topic_knowledge_graphs
        res_tkg = await db.execute(text("PRAGMA table_info(topic_knowledge_graphs)"))
        columns_tkg = [row[1] for row in res_tkg.fetchall()]
        if columns_tkg and "tree_data" not in columns_tkg:
            await db.execute(text("ALTER TABLE topic_knowledge_graphs ADD COLUMN tree_data JSON"))

        await db.commit()
