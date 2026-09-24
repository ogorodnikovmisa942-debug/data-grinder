#!/usr/bin/env bash
set -e

# === DATA GRINDER SAFE UPDATE ===

# Фаза 1: При первом запуске синхронизируем код с Git и перезапускаем обновленный скрипт
if [ -z "$GRINDER_UPDATE_PHASE2" ]; then
    echo "=== [1/2] DATA GRINDER SAFE UPDATE: Загрузка свежего кода ==="
    
    # Резервная копия БД перед операциями с Git
    if [ -f "data_grinder.db" ]; then
        mkdir -p backups
        cp data_grinder.db "backups/data_grinder_prepull_$(date +%Y%m%d_%H%M%S).db" 2>/dev/null || true
    fi

    # Исключаем БД из индекса Git, если попала
    git rm --cached data_grinder.db 2>/dev/null || true

    echo "[...] Синхронизация с Git origin/main..."
    git fetch origin main
    git reset --hard origin/main

    echo "[OK] Актуальный коммит: $(git log -1 --oneline)"

    # Передаем управление обновленному коду скрипта
    export GRINDER_UPDATE_PHASE2=1
    exec bash safe_update.sh
fi

echo "=== [2/2] DATA GRINDER SAFE UPDATE: Применение обновлений ==="

# 1. Автоматический поиск нужного интерпретатора Python и виртуального окружения
PYTHON_BIN=""

# 1.1. Определение пути к Python из рабочей конфигурации systemd сервиса grinder-web
if command -v systemctl >/dev/null 2>&1; then
    SERVICE_CMD=$(systemctl show grinder-web -p ExecStart --value 2>/dev/null || true)
    EXEC_FILE=$(echo "$SERVICE_CMD" | tr ' ' '\n' | grep -E '(uvicorn|python)' | head -n 1 | sed 's/path=//' | sed 's/;.*//')
    if [ -n "$EXEC_FILE" ] && [ -f "$EXEC_FILE" ]; then
        EXEC_DIR=$(dirname "$EXEC_FILE")
        if [ -f "$EXEC_DIR/python" ]; then
            PYTHON_BIN="$EXEC_DIR/python"
        elif [ -f "$EXEC_DIR/python3" ]; then
            PYTHON_BIN="$EXEC_DIR/python3"
        fi
    fi
fi

# 1.2. Если через systemctl не нашли, ищем по стандартным путям виртуальных окружений
if [ -z "$PYTHON_BIN" ] || [ ! -f "$PYTHON_BIN" ]; then
    if [ -f "venv/bin/python" ]; then
        PYTHON_BIN="venv/bin/python"
    elif [ -f ".venv/bin/python" ]; then
        PYTHON_BIN=".venv/bin/python"
    elif [ -f "/root/GRINDER/venv/bin/python" ]; then
        PYTHON_BIN="/root/GRINDER/venv/bin/python"
    elif [ -f "venv/Scripts/python.exe" ]; then
        PYTHON_BIN="venv/Scripts/python.exe"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        PYTHON_BIN="python3"
    fi
fi

echo "[i] Используется интерпретатор Python: $PYTHON_BIN"

# 2. Безопасный бэкап текущей базы данных (Native SQLite API + файловая копия)
if [ -f "data_grinder.db" ]; then
    mkdir -p backups
    BACKUP_NAME="backups/data_grinder_$(date +%Y%m%d_%H%M%S).db"
    
    # Попытка безопасного нативного снимка через SQLite API без риска повреждения WAL
    $PYTHON_BIN -c "from app.database.migrations import backup_sqlite_database; backup_sqlite_database('data_grinder.db')" 2>/dev/null || true
    
    cp data_grinder.db "$BACKUP_NAME" 2>/dev/null || true
    cp data_grinder.db backups/data_grinder.latest.bak 2>/dev/null || true
    echo "[OK] Резервная копия базы сохранена в $BACKUP_NAME"

    # Автоматическая ротация: оставляем только последние 5 копий
    ls -t backups/data_grinder_*.db 2>/dev/null | tail -n +6 | xargs -r rm -f 2>/dev/null || true
    ls -t backups/*.bak 2>/dev/null | tail -n +6 | xargs -r rm -f 2>/dev/null || true
fi

# 3. Проверка переменных окружения в .env
if [ -f ".env" ]; then
    if ! grep -q "DEEPSEEK_API_KEY" .env || grep -q "DEEPSEEK_API_KEY=$" .env || grep -q 'DEEPSEEK_API_KEY=""' .env; then
        echo ""
        echo "========================================================"
        echo "[!] ВНИМАНИЕ: В файле .env не задан DEEPSEEK_API_KEY!"
        echo "Чтобы генерация работала, укажите ключ DeepSeek в .env:"
        echo "nano .env  ->  добавьте: DEEPSEEK_API_KEY=sk-ваш_ключ"
        echo "========================================================"
        echo ""
    fi
else
    echo "[!] Файл .env не найден! Создайте его из .env.example"
fi

# 4. Проверка и установка зависимостей Python
PIP_FLAGS=""
if $PYTHON_BIN -m pip install --help 2>&1 | grep -q -- "--break-system-packages"; then
    PIP_FLAGS="--break-system-packages"
fi

echo "[...] Проверка и установка зависимостей (pip)..."
$PYTHON_BIN -m pip install -r requirements.txt $PIP_FLAGS

# Проверка критических библиотек
echo "[...] Проверка критических импортов..."
$PYTHON_BIN -c "import fastapi, uvicorn; print('[OK] FastAPI и Uvicorn готовы к работе.')"
$PYTHON_BIN -c "import slowapi; print('[OK] Модуль rate-limiting (slowapi) активен.')" 2>/dev/null || echo "[i] slowapi работает в режиме встроенного fallback."

# 5. Применение миграций базы данных через Alembic
echo "[...] Проверка и применение миграций базы данных (Alembic)..."
$PYTHON_BIN -c "
import sqlite3
from alembic.config import Config
from alembic import command

try:
    conn = sqlite3.connect('data_grinder.db')
    c = conn.cursor()
    c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='ai_telemetry_logs'\")
    has_tables = c.fetchone() is not None

    c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'\")
    has_alembic = c.fetchone() is not None
    alembic_ver = None
    if has_alembic:
        c.execute(\"SELECT version_num FROM alembic_version\")
        row = c.fetchone()
        if row:
            alembic_ver = row[0]
    conn.close()

    cfg = Config('alembic.ini')
    if has_tables and not alembic_ver:
        command.stamp(cfg, '1926b71611fb')
        command.upgrade(cfg, 'head')
        print('[Alembic] Существующая схема базы данных синхронизирована с 1926b71611fb и обновлена до head.')
    else:
        command.upgrade(cfg, 'head')
        print('[Alembic] Миграции успешно применены.')
except Exception as e:
    print(f'[Alembic Notice] {e}')
" || true

# 6. Автоматическая сборка ассетов фронтенда (HTML и JS модули)
echo "[...] Сборка статических файлов фронтенда..."
$PYTHON_BIN -c "from app.services.frontend_bundler import bundle_all; bundle_all()" || true

# Опциональная пересборка Tailwind CSS, если установлен npx
if command -v npx >/dev/null 2>&1 && [ -f "tailwind.config.js" ]; then
    npx -y tailwindcss@3.4.17 -i ./app/static/css/tailwind.input.css -o ./app/static/css/tailwind.min.css --minify 2>/dev/null || true
fi

# 7. Автоматическая проверка лимитов Nginx (снятие ограничения 1 МБ)
if command -v nginx >/dev/null 2>&1 && [ -f "/etc/nginx/nginx.conf" ]; then
    if ! grep -rq "client_max_body_size" /etc/nginx/ 2>/dev/null; then
        echo "[...] Автоматическая настройка Nginx (увеличение лимита загрузки до 100 МБ)..."
        bash fix_nginx.sh 2>/dev/null || true
    fi
fi

# 8. Перезапуск сервисов
echo "[...] Перезапуск сервисов..."
systemctl restart grinder-web
systemctl restart grinder-bot 2>/dev/null || true

# 9. Проверка доступности и работоспособности сервисов
echo "[...] Проверка статуса сервисов..."
sleep 2

WEB_RUNNING=false
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet grinder-web; then
        WEB_RUNNING=true
        echo "[SUCCESS] Сервис grinder-web успешно запущен и работает!"
    else
        echo "[ERROR] Ошибка запуска grinder-web! Журнал ошибок (последние 25 строк):"
        journalctl -u grinder-web -n 25 --no-pager || true
    fi

    if systemctl is-active --quiet grinder-bot; then
        echo "[SUCCESS] Сервис grinder-bot успешно запущен и работает!"
    else
        echo "[i] Сервис grinder-bot сейчас не активен (проверьте настройки бота при необходимости)."
    fi
fi

if [ "$WEB_RUNNING" = true ] || ! command -v systemctl >/dev/null 2>&1; then
    echo "=== ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО ==="
else
    echo "=== ОБНОВЛЕНИЕ ЗАВЕРШИЛОСЬ С ОШИБКОЙ ==="
    exit 1
fi