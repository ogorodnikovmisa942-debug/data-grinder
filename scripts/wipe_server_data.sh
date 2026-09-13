#!/usr/bin/env bash
# scripts/wipe_server_data.sh
# Безопасный полный сброс базы данных Data Grinder на боевом сервере.

set -e

echo "======================================================"
echo "    DATA GRINDER: ПОЛНЫЙ СБРОС ТЕСТОВЫХ ДАННЫХ       "
echo "======================================================"

# 1. Проверяем наличие прав sudo / root
if [ "$EUID" -ne 0 ]; then
    echo "[!] Для остановки и перезапуска systemd-служб требуются права root."
    echo "    Пожалуйста, запустите: sudo bash scripts/wipe_server_data.sh"
    exit 1
fi

# 2. Остановка работающих служб приложения
echo "[1/5] Остановка фоновых служб grinder-web и grinder-bot..."
systemctl stop grinder-web grinder-bot 2>/dev/null || true
echo "[OK] Службы остановлены (файловые дескрипторы SQLite освобождены)."

# 3. Резервная копия базы перед удалением (страховка)
if [ -f "data_grinder.db" ]; then
    mkdir -p backups
    BACKUP_NAME="backups/data_grinder_wipe_backup_$(date +%Y%m%d_%H%M%S).db"
    cp data_grinder.db "$BACKUP_NAME"
    echo "[2/5] Страховочная резервная копия сохранена в $BACKUP_NAME"
else
    echo "[2/5] Файл data_grinder.db не найден (будет создан с нуля)."
fi

# 4. Удаление старой базы и временных WAL/SHM файлов
echo "[3/5] Удаление всех тестовых данных..."
rm -f data_grinder.db data_grinder.db-wal data_grinder.db-shm
echo "[OK] Файлы базы данных полностью удалены."

# 5. Запуск служб (FastAPI автоматически создаст девственно чистую структуру БД)
echo "[4/5] Запуск служб grinder-web и grinder-bot..."
systemctl start grinder-web
systemctl start grinder-bot 2>/dev/null || true

# 6. Проверка статуса
echo "[5/5] Проверка статуса сервисов..."
sleep 2

if systemctl is-active --quiet grinder-web; then
    echo "======================================================"
    echo "[SUCCESS] grinder-web успешно запущен с ЧИСТОЙ БАЗОЙ!"
    echo "======================================================"
else
    echo "[ERROR] Ошибка запуска grinder-web! Проверьте логи: journalctl -u grinder-web -n 30"
    exit 1
fi
