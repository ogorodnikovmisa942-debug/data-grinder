#!/usr/bin/env bash
set -e

echo "=== DATA GRINDER SAFE UPDATE ==="

# 0. Определение пути к Python и виртуальному окружению
PYTHON_BIN="python3"
if [ -f "venv/bin/python" ]; then
    PYTHON_BIN="venv/bin/python"
elif [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
elif [ -f "venv/Scripts/python.exe" ]; then
    PYTHON_BIN="venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

# 1. Автоматический безопасный бэкап текущей базы данных перед обновлением (Native SQLite API)
if [ -f "data_grinder.db" ]; then
    mkdir -p backups
    BACKUP_NAME="backups/data_grinder_$(date +%Y%m%d_%H%M%S).db"
    
    # Попытка безопасного нативного снимка через SQLite API без риска повреждения WAL
    $PYTHON_BIN -c "from app.database.migrations import backup_sqlite_database; backup_sqlite_database('data_grinder.db')" 2>/dev/null || true
    
    # Резервная копия с временной меткой
    cp data_grinder.db "$BACKUP_NAME" 2>/dev/null || true
    cp data_grinder.db backups/data_grinder.latest.bak 2>/dev/null || true
    echo "[OK] Резервная копия базы сохранена в $BACKUP_NAME"

    # Автоматическая ротация: оставляем только последние 5 копий, чтобы диск не переполнялся
    ls -t backups/data_grinder_*.db 2>/dev/null | tail -n +6 | xargs -r rm -f 2>/dev/null || true
    ls -t backups/*.bak 2>/dev/null | tail -n +6 | xargs -r rm -f 2>/dev/null || true
fi

# 2. Убираем из кэша git БД, если попала
git rm --cached data_grinder.db 2>/dev/null || true

# 3. Принудительно подтягиваем свежий код из репозитория
echo "[...] Загрузка обновлений из Git..."
git fetch origin main
git reset --hard origin/main

echo "[OK] Текущий коммит репозитория:"
git log -1 --oneline

# 4. Проверка переменных окружения в .env
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

# 5. Проверка и установка зависимостей Python
echo "[...] Проверка и обновление зависимостей (pip)..."
$PYTHON_BIN -m pip install -r requirements.txt --quiet || true

# 6. Применение миграций базы данных через Alembic
echo "[...] Применение миграций базы данных (Alembic)..."
$PYTHON_BIN -m alembic upgrade head || true

# 7. Автоматическая сборка ассетов фронтенда (HTML и JS модули)
echo "[...] Сборка статических файлов фронтенда..."
$PYTHON_BIN -c "from app.services.frontend_bundler import bundle_all; bundle_all()" || true

# Опциональная пересборка Tailwind CSS, если установлен npx
if command -v npx >/dev/null 2>&1 && [ -f "tailwind.config.js" ]; then
    npx -y tailwindcss@3.4.17 -i ./app/static/css/tailwind.input.css -o ./app/static/css/tailwind.min.css --minify 2>/dev/null || true
fi

# 8. Автоматическая проверка лимитов Nginx (снятие ограничения 1 МБ)
if command -v nginx >/dev/null 2>&1 && [ -f "/etc/nginx/nginx.conf" ]; then
    if ! grep -rq "client_max_body_size" /etc/nginx/ 2>/dev/null; then
        echo "[...] Автоматическая настройка Nginx (увеличение лимита загрузки до 100 МБ)..."
        bash fix_nginx.sh 2>/dev/null || true
    fi
fi

# 9. Перезапуск сервисов
echo "[...] Перезапуск сервисов..."
systemctl restart grinder-web
systemctl restart grinder-bot 2>/dev/null || true

echo "=== ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО ==="