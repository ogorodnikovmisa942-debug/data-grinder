#!/usr/bin/env bash
set -e

echo "=== АВТОМАТИЧЕСКАЯ НАСТРОЙКА NGINX ДЛЯ DATA GRINDER ==="

# Проверка прав суперпользователя (root)
if [ "$EUID" -ne 0 ]; then
  echo "[!] Ошибка: скрипт должен быть запущен с правами sudo."
  echo "    Выполните: sudo bash fix_nginx.sh"
  exit 1
fi

NGINX_CONF="/etc/nginx/nginx.conf"

if [ ! -f "$NGINX_CONF" ]; then
    echo "[!] Файл $NGINX_CONF не найден. Nginx установлен?"
    exit 1
fi

# 1. Создаем резервную копию конфига
BACKUP_FILE="${NGINX_CONF}.bak_$(date +%Y%m%d_%H%M%S)"
cp "$NGINX_CONF" "$BACKUP_FILE"
echo "[OK] Резервная копия конфига Nginx сохранена в $BACKUP_FILE"

# 2. Удаляем старые дубликаты и прописываем чистый лимит 100M в nginx.conf
sed -i '/client_max_body_size/d' "$NGINX_CONF"
sed -i '/proxy_read_timeout/d' "$NGINX_CONF"
sed -i '/http {/a \    client_max_body_size 100M;\n    proxy_connect_timeout 300s;\n    proxy_send_timeout 300s;\n    proxy_read_timeout 300s;' "$NGINX_CONF"
echo "[OK] Установлен client_max_body_size 100M и таймауты в секцию http {} файла $NGINX_CONF"

# 3. Проверяем также сайты в sites-enabled и conf.d
for conf in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*; do
    if [ -f "$conf" ] && grep -q "client_max_body_size" "$conf"; then
        sed -i -E 's/client_max_body_size\s+[0-9]+[kKmMgG]?;/client_max_body_size 100M;/g' "$conf"
        echo "[OK] Обновлен client_max_body_size на 100M в $conf"
    fi
done

# 4. Проверка корректности синтаксиса Nginx
echo "[...] Проверка конфигурации Nginx (nginx -t)..."
nginx -t

# 5. Применение изменений без остановки работы сервера
echo "[...] Применение конфигурации (systemctl reload nginx)..."
systemctl reload nginx

echo ""
echo "================================================================"
echo "[SUCCESS] Ограничение успешно снято! Разрешены файлы до 100 МБ."
echo "================================================================"
