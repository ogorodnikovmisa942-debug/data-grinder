#!/usr/bin/env bash
set -e

echo "=== DATA GRINDER SAFE UPDATE ==="

# 1. Автоматический снапшот текущей базы данных перед любыми действиями с Git
if [ -f "data_grinder.db" ]; then
    mkdir -p backups
    BACKUP_NAME="backups/data_grinder_$(date +%Y%m%d_%H%M%S).db"
    cp data_grinder.db "$BACKUP_NAME"
    cp data_grinder.db backups/data_grinder.latest.bak
    echo "[OK] База данных сохранена в $BACKUP_NAME"
fi

# 2. Убираем локальные конфликты git, если база еще отслеживалась
git rm --cached data_grinder.db 2>/dev/null || true

# 3. Подтягиваем свежий код из репозитория
git pull origin main

# 4. Перезапускаем веб-сервис
systemctl restart grinder-web

echo "=== ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО, БАЗА В БЕЗОПАСНОСТИ ==="
