# app/api/endpoints/management.py
"""
Фасадный модуль API управления, импорта, карточек и аналитики.
Декомпозирован на специализированные роутеры для повышения надежности:
- app.api.endpoints.cards: CRUD, перемещение, удаление, экспорт, шеринг карточек и предметов.
- app.api.endpoints.imports: ИИ-конвейер импорта (текст/файлы), песочница (staging), очередь задач и библиотеки.
- app.api.endpoints.stats: Аналитика, дашборд, таймер отдыха, опрос ежедневных сессий.
- app.api.endpoints.settings_router: Конфигурация пользователя, дневные лимиты, статус ИИ-провайдера.
- app.services.card_db_sync: Сервисный слой атомарной синхронизации карточек, графа и практики в БД.
"""
from fastapi import APIRouter

# 1. Подмодули роутеров
from app.api.endpoints import cards, imports, stats, settings_router

# 2. Реэкспорт сервисных функций для обратной совместимости (тесты, бот, админка)
from app.services.card_db_sync import (
    is_admin_or_dev,
    check_experiment_lock,
    save_cards_to_database,
    append_or_sync_cards_to_database,
    sync_subject_knowledge_and_practice,
)
from app.services.generation_worker import is_deepseek_offpeak

# 3. Реэкспорт Pydantic-схем и функций карточек
from app.api.endpoints.cards import (
    ShareDeckIn,
    ManualCardIn,
    CardUpdateIn,
    CardMoveIn,
    BulkCardMoveIn,
    BulkCardDeleteIn,
    RegenerateMnemonicIn,
    SubjectRenameIn,
    get_all_cards,
    export_cards_json,
    share_cards_deck,
    create_manual_card,
    update_single_card,
    move_card,
    delete_card,
    regenerate_mnemonic,
    bulk_move_cards,
    bulk_delete_cards,
    get_subjects_details,
    rename_subject,
    delete_subject_all,
)

# 4. Реэкспорт схем и функций импорта и стейджинга
from app.api.endpoints.imports import (
    ImportIn,
    PresetImportIn,
    CardStagingItem,
    StagingCommitIn,
    import_raw_text,
    commit_staging_cards,
    import_file_at_code_level,
    get_staging_job_cards,
    delete_staging_job,
    get_import_queue,
    cancel_import_queue_job,
    import_preset_library,
)

# 5. Реэкспорт схем и функций статистики и таймера
from app.api.endpoints.stats import (
    DailySessionIn,
    get_analytics,
    start_rest_session,
    get_timer_status,
    log_daily_session,
)

# 6. Реэкспорт схем и функций конфигурации
from app.api.endpoints.settings_router import (
    ConfigUpdate,
    get_config,
    update_config,
    get_public_ai_provider,
)

# 7. Единый агрегированный роутер для подключения в FastAPI
router = APIRouter()
router.include_router(cards.router)
router.include_router(imports.router)
router.include_router(stats.router)
router.include_router(settings_router.router)