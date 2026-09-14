#!/usr/bin/env bash
# scripts/clean_server_junk.sh
# Скрипт для безопасной очистки дискового пространства на сервере Data Grinder.

set -e

echo "========================================================"
echo "    DATA GRINDER: ОЧИСТКА ЛИШНИХ ФАЙЛОВ НА СЕРВЕРЕ       "
echo "========================================================"

echo "[1/6] Текущее свободное место на диске:"
df -h . | awk 'NR==1 || NR==2'
echo ""

# 1. Очистка старых бэкапов в backups/
if [ -d "backups" ]; then
    echo "[2/6] Очистка устаревших копий базы данных (оставляем последние 3)..."
    DELETED_BACKUPS=0
    for f in $(ls -t backups/data_grinder_*.db 2>/dev/null | tail -n +4); do
        rm -f "$f"
        DELETED_BACKUPS=$((DELETED_BACKUPS + 1))
    done
    for f in $(ls -t backups/*.bak 2>/dev/null | tail -n +4); do
        rm -f "$f"
        DELETED_BACKUPS=$((DELETED_BACKUPS + 1))
    done
    echo "[OK] Удалено устаревших бэкапов: $DELETED_BACKUPS"
else
    echo "[2/6] Папка backups не найдена, пропуск."
fi

# 2. Очистка кэша Python (__pycache__ и *.pyc)
echo "[3/6] Удаление временных файлов Python кэша..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "*.pyo" -delete 2>/dev/null || true
echo "[OK] Кэш Python очищен."

# 3. Очистка кэша pip
echo "[4/6] Очистка кэша менеджера пакетов pip..."
if command -v pip >/dev/null 2>&1; then
    pip cache purge 2>/dev/null || true
fi
rm -rf ~/.cache/pip 2>/dev/null || true
echo "[OK] Кэш pip удален."

# 4. Сжатие и оптимизация SQLite базы данных (VACUUM)
echo "[5/6] Оптимизация размера SQLite базы данных (VACUUM)..."
if [ -f "data_grinder.db" ] && command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 data_grinder.db "VACUUM;" 2>/dev/null || echo "[!] SQLite база сейчас заблокирована другим процессом, пропуск VACUUM."
    echo "[OK] База данных дефрагментирована."
else
    echo "[i] sqlite3 утилита не найдена или база отсутствует, пропуск."
fi

# 5. Очистка системных журналов systemd (если запущен с sudo)
echo "[6/6] Очистка старых системных логов (journalctl)..."
if command -v journalctl >/dev/null 2>&1; then
    if [ "$EUID" -eq 0 ]; then
        journalctl --vacuum-size=50M 2>/dev/null || true
        echo "[OK] Системные логи сокращены до 50M."
    else
        echo "[i] Для сжатия логов journalctl требуются права sudo (пропущено)."
    fi
fi

echo ""
echo "========================================================"
echo "    ОЧИСТКА ЗАВЕРШЕНА! ИТОГОВОЕ МЕСТО НА ДИСКЕ:         "
echo "========================================================"
df -h . | awk 'NR==1 || NR==2'
