#!/usr/bin/env bash
set -e

echo "=== DATA GRINDER SAFE UPDATE ==="

# 1. Автоматический бэкап текущей базы данных перед обновлением
if [ -f "data_grinder.db" ]; then
    mkdir -p backups
    BACKUP_NAME="backups/data_grinder_$(date +%Y%m%d_%H%M%S).db"
    cp data_grinder.db "$BACKUP_NAME"
    cp data_grinder.db backups/data_grinder.latest.bak
    echo "[OK] Резервная копия базы сохранена в $BACKUP_NAME"
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

# 5. Перезапуск сервисов
echo "[...] Перезапуск сервисов..."
systemctl restart grinder-web
systemctl restart grinder-bot 2>/dev/null || true

echo "=== ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО ==="