# Запуск Data Grinder на сервере с нуля

Гайд для чистого сервера Ubuntu 24.04. Как устроен прод:

- `grinder-web` — FastAPI (uvicorn) на `127.0.0.1:8000`. Внутри него работают воркер нарезки, уведомления и ежедневные копии базы.
- `grinder-bot` — Telegram-бот (aiogram, polling). Ставит кнопку меню, которая открывает WebApp.
- nginx принимает HTTPS на 443 и проксирует на 8000.
- База — SQLite-файл `data_grinder.db` в папке проекта.

Скрипты проекта (`safe_update.sh`, `scripts/wipe_server_data.sh`) рассчитаны на папку `/root/GRINDER` и сервисы с именами `grinder-web` и `grinder-bot`. Ниже используются те же пути и имена.

---

## 0. Что нужно заранее

| Что | Где взять |
|---|---|
| VPS с Ubuntu 24.04, 1 ГБ RAM и больше, доступ root по SSH | любой хостинг |
| Домен, A-запись которого указывает на IP сервера | регистратор домена |
| Токен Telegram-бота | @BotFather → `/newbot` |
| Ключ DeepSeek API с пополненным балансом | platform.deepseek.com |
| Ваш Telegram ID (число) | @userinfobot |

Telegram открывает Mini App только по **HTTPS**, поэтому без домена и сертификата приложение в Telegram не откроется.

Ниже домен везде записан как `datagrinder.site` — замените его на свой.

---

## П. Подключение с Windows через PuTTY

Все команды этого гайда вводятся в окне PuTTY, то есть на сервере. Команды, которые выполняются **на вашем компьютере** (в «Командной строке» Windows), помечены отдельно.

### П.1. Установка

Скачайте установщик PuTTY (64-bit `.msi`) с официального сайта `putty.org` → «Download PuTTY» и установите. Вместе с PuTTY ставятся:

- **PuTTYgen** — делает SSH-ключи;
- **pscp** — копирует файлы с сервера и на сервер из командной строки Windows.

Для перетаскивания файлов мышкой удобно поставить ещё **WinSCP** (`winscp.net`). Он понимает те же ключи `.ppk`.

### П.2. Первое подключение (по паролю от хостинга)

1. Запустите PuTTY.
2. В поле **Host Name** введите `root@IP_СЕРВЕРА`, **Port** `22`, **Connection type** SSH.
3. Чтобы соединение не обрывалось при простое: слева **Connection** → **Seconds between keepalives** = `30`.
4. Сохраните сессию: слева снова **Session** → в поле **Saved Sessions** напишите `grinder` → **Save**. В следующий раз — двойной клик по `grinder`.
5. Нажмите **Open**. При первом входе PuTTY спросит про ключ сервера — **Accept**.
6. Введите пароль из письма хостинга. При вводе пароля символы не отображаются, это нормально.

### П.3. Вход по ключу вместо пароля (можно пропустить)

Если вы вошли по логину и паролю — вы уже подключены по SSH: PuTTY всегда работает через SSH. Этот шаг только заменяет пароль на ключ-файл, это безопаснее. Его можно сделать потом и сразу перейти к разделу 1.

1. Запустите **PuTTYgen** → тип ключа **EdDSA (Ed25519)** → **Generate**, поводите мышью.
2. Нажмите **Save private key** и сохраните, например, `C:\keys\grinder.ppk`. Пароль на ключ (passphrase) — по желанию.
3. Скопируйте всю строку из поля **Public key for pasting into OpenSSH authorized_keys file** (начинается с `ssh-ed25519`).
4. В PuTTY на сервере выполните, вставив свою строку между кавычек:

   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   echo "ssh-ed25519 AAAA...ваш_ключ... grinder" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

5. В PuTTY загрузите сессию `grinder`. Слева **Connection** → **SSH** → **Auth** → **Credentials** → **Private key file**: выберите `grinder.ppk`. Вернитесь в **Session** и нажмите **Save**.
6. Откройте новое окно PuTTY с сессией `grinder` и убедитесь, что вход проходит без пароля. Только после этого можно отключить вход по паролю:

   ```bash
   sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null
   systemctl restart ssh
   ```

   Многие хостинги кладут свою настройку в `/etc/ssh/sshd_config.d/`, поэтому команда правит и её. Старое окно PuTTY не закрывайте, пока не проверите вход из нового: если ошиблись, через старое можно всё вернуть.

### П.4. Как работать в окне PuTTY

| Действие | Как |
|---|---|
| Вставить команду из гайда | **правая кнопка мыши** в окне PuTTY (Ctrl+V не работает) |
| Скопировать текст из окна | просто выделить мышью — текст уже в буфере |
| Прервать команду | Ctrl+C |
| Редактор `nano`: сохранить | Ctrl+O, затем Enter |
| Редактор `nano`: выйти | Ctrl+X |
| Повторить прошлую команду | стрелка вверх |

Многострочные блоки (например, `cat > ... <<'EOF'` в разделе 5) вставляются целиком одним нажатием правой кнопки. После вставки проверьте, что последняя строка `EOF` выполнилась: должна появиться обычная строка приглашения `root@...:~#`. Если курсор ждёт ввода, нажмите Enter.

### П.5. Чтобы обрыв связи не прервал установку или обновление

Если PuTTY отключится посреди `pip install` или `safe_update.sh`, команда на сервере оборвётся вместе с окном. Долгие команды запускайте внутри `tmux` — он продолжает работать, даже когда PuTTY закрыт:

```bash
apt install -y tmux        # если ещё нет
tmux new -s grinder        # открыть рабочее окно
```

Если связь пропала, переподключитесь в PuTTY и вернитесь в то же окно: `tmux attach -t grinder`. Выйти из `tmux`, оставив его работать: Ctrl+B, затем D.

Сервисы `grinder-web` и `grinder-bot` от закрытия PuTTY не зависят: их держит systemd.

---

## 1. Подготовка сервера

Подключитесь к серверу через PuTTY (раздел П) и выполните:

```bash
apt update && apt upgrade -y
apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx sqlite3
apt install -y tesseract-ocr tesseract-ocr-bel tesseract-ocr-rus tesseract-ocr-ukr   # распознавание текста на слайдах-картинках
timedatectl set-timezone Europe/Moscow     # чтобы время в логах совпадало с вашим
```

Файрвол: открыть только SSH и веб.

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
```

Порт 8000 наружу **не открывать**: к нему ходит только nginx.

---

## 2. Код

```bash
cd /root
git clone https://github.com/ogorodnikovmisa942-debug/data-grinder.git GRINDER
cd /root/GRINDER
```

Если репозиторий приватный, понадобится доступ. Проще всего deploy key:

1. На сервере создайте ключ (на все вопросы — Enter) и выведите его публичную часть:

   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
   cat ~/.ssh/id_ed25519.pub
   ```

2. Выделите выведенную строку мышью в окне PuTTY — она уже скопирована.
3. Вставьте её в GitHub: репозиторий → Settings → Deploy keys → Add deploy key.
4. Клонируйте через SSH-адрес: `git clone git@github.com:ogorodnikovmisa942-debug/data-grinder.git GRINDER`.

---

## 3. Python-окружение

Установка пакетов идёт несколько минут. Чтобы обрыв PuTTY её не прервал, сначала откройте «окно, которое живёт без PuTTY» (`tmux`):

```bash
apt install -y tmux
tmux new -s grinder
```

Внизу появится зелёная полоска — вы внутри `tmux`. Если связь оборвётся, переподключитесь и наберите `tmux attach -t grinder`, установка продолжится там же.

```bash
cd /root/GRINDER
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

Проверка: `venv/bin/python -c "import fastapi, aiogram, pypdf; print('ok')"`.

---

## 4. Настройки `.env`

```bash
cp .env.example .env
nano .env
```

Откроется редактор `nano`. Мышь в нём не работает: двигайтесь стрелками, стирайте Backspace, вставляйте правой кнопкой мыши. Сохранить — **Ctrl+O**, затем **Enter**; выйти — **Ctrl+X**.

Обязательные поля (значения в кавычках замените на свои):

```dotenv
TELEGRAM_BOT_TOKEN="123456:ABC..."          # от @BotFather
DEEPSEEK_API_KEY="sk-..."                   # ключ DeepSeek
WEBAPP_URL="https://datagrinder.site"       # ваш домен, обязательно https
ADMIN_TELEGRAM_ID="123456789"               # ваш Telegram ID (через запятую, если админов несколько)
ADMIN_TOKEN="..."                           # случайная строка, см. ниже
DATABASE_URL="sqlite+aiosqlite:///./data_grinder.db"
```

`ADMIN_TOKEN` сгенерировать так: `openssl rand -hex 32`.

> **Важно для безопасности:** на сервере **не** ставьте `DEBUG=true` и `TESTING=true` и не оставляйте пустой или заглушечный `TELEGRAM_BOT_TOKEN`. В этих режимах приложение пускает любого пользователя по неподписанному `?tg_id=…`.

Необязательные поля (значения по умолчанию разумные):

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `QUOTAS_ENABLED` | `0` | тарифы и месячные квоты пользователей; `1` — включить |
| `BACKUP_ENABLED` / `BACKUP_KEEP` | `1` / `14` | ежедневная копия базы в `backups/`, хранить 14 штук |
| `BACKUP_EXTERNAL_DIR` | пусто | вторая папка для копий (например, примонтированный диск) |
| `MAX_IMPORT_CHARS` | `1500000` | потолок размера материала (~600 страниц) |
| `PATH_BUDGET_USD` | `0.60` | аварийный потолок расходов на одну книгу |
| `FALLBACK_AI_BASE_URL` / `_API_KEY` / `_MODEL` | пусто | запасной ИИ-провайдер на случай сбоя DeepSeek |
| `DEFAULT_TIMEZONE` | `Europe/Moscow` | часовой пояс для «дня» пользователя |

Закройте файл от чужих глаз: `chmod 600 .env`.

---

## 5. Сервисы systemd

Веб-приложение:

```bash
cat > /etc/systemd/system/grinder-web.service <<'EOF'
[Unit]
Description=Data Grinder web (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/root/GRINDER
ExecStart=/root/GRINDER/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

`--workers 1` обязателен. Воркер нарезки и уведомления живут внутри процесса веб-приложения, и с несколькими воркерами одна книга нарезалась бы дважды, а SQLite получал бы параллельные записи.

Бот:

```bash
cat > /etc/systemd/system/grinder-bot.service <<'EOF'
[Unit]
Description=Data Grinder Telegram bot
After=network-online.target grinder-web.service
Wants=network-online.target

[Service]
WorkingDirectory=/root/GRINDER
ExecStart=/root/GRINDER/venv/bin/python bot.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

Запуск:

```bash
systemctl daemon-reload
systemctl enable --now grinder-web
sleep 3 && systemctl status grinder-web --no-pager
curl -sI http://127.0.0.1:8000/ | head -1          # ждём HTTP/1.1 200 OK
```

При первом старте приложение само соберёт фронтенд (`app.js`, `index.html`) и создаст все таблицы базы.

---

## 6. База данных: отметка миграций

Таблицы на чистой базе уже созданы по текущим моделям. Нужно сообщить Alembic, что схема свежая, иначе при обновлениях он попытается накатить старые миграции поверх готовых таблиц:

```bash
cd /root/GRINDER
venv/bin/alembic stamp head
```

Дальше миграции накатывает `safe_update.sh` (раздел 10).

---

## 7. nginx и HTTPS

Сначала простой HTTP-конфиг, затем certbot сам добавит HTTPS и редирект:

```bash
cat > /etc/nginx/sites-available/datagrinder.conf <<'EOF'
server {
    listen 80;
    server_name datagrinder.site www.datagrinder.site;

    # Книги, PDF и презентации до 100 МБ
    client_max_body_size 100M;
    # Загрузка и разбор больших файлов идут долго
    proxy_connect_timeout 300s;
    proxy_send_timeout    300s;
    proxy_read_timeout    300s;
    send_timeout          300s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/datagrinder.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Сертификат Let's Encrypt (домен уже должен указывать на сервер):

```bash
certbot --nginx -d datagrinder.site -d www.datagrinder.site --redirect -m you@example.com --agree-tos -n
```

`you@example.com` замените на свою настоящую почту: на неё Let's Encrypt пишет, если сертификат не продлился. Если у домена нет DNS-записи для `www`, уберите из команды `-d www.datagrinder.site`, иначе certbot не выдаст сертификат.

Продление сертификата настраивается автоматически, проверить можно так: `certbot renew --dry-run`.

Проверка: открыть `https://datagrinder.site` в браузере. Должна загрузиться страница приложения; без Telegram она попросит авторизацию, это нормально.

В репозитории есть готовый пример конфига `nginx/datagrinder.conf`: это тот же конфиг, но сразу с блоком 443. Использовать его стоит только после того, как появятся сертификаты.

---

## 8. Бот

```bash
systemctl enable --now grinder-bot
journalctl -u grinder-bot -n 20 --no-pager
```

В логе должна быть строка `Кнопка меню WebApp успешно настроена: https://...`. Бот сам ставит кнопку меню с вашим `WEBAPP_URL`, в BotFather для этого ничего делать не нужно.

Проверка: напишите боту `/start` и нажмите «[КАРТОЧКИ]» — откроется приложение. Чтобы в приложении появился первый курс, загрузите в нём материал: вставьте текст или прикрепите файл PDF / DOCX / EPUB / FB2.

---

## 9. Проверка, что всё живо

```bash
systemctl is-active grinder-web grinder-bot nginx     # три раза active
journalctl -u grinder-web -f                           # живой лог (Ctrl+C — выйти)
venv/bin/python scripts/check_balance.py               # остаток на балансе DeepSeek
```

Что видно в логе `grinder-web` при нарезке: `[Generation Worker] Задача #N ...`, затем строки `[Path Builder] ...` со стоимостью. Книга в 450 страниц стоит около 20¢, если DeepSeek работает со скидкой. Скидка действует всё время, кроме будних дней 04:00–07:00 и 09:00–13:00 по Москве.

---

## 10. Обновления

> **Если сервер ставился до ноября 2026 года**, один раз поставьте распознавание слайдов: `apt install -y tesseract-ocr tesseract-ocr-bel tesseract-ocr-rus tesseract-ocr-ukr`. Без него презентации в PDF читаются только по текстовому слою — слайды, сохранённые картинками, пропадают. Проверка: `tesseract --list-langs` должен показать `bel` и `rus`.

Всё обновление — одна команда из папки проекта. Запускайте её внутри `tmux`, чтобы обрыв PuTTY не прервал обновление на середине:

```bash
tmux new -s update
cd /root/GRINDER && bash safe_update.sh
```

Когда в конце появится `ОБНОВЛЕНИЕ УСПЕШНО ЗАВЕРШЕНО`, выйдите из `tmux` командой `exit`.

Скрипт делает следующее:

1. Копирует базу в `backups/`.
2. Делает `git fetch` и **`git reset --hard origin/main`**. Ручные правки файлов на сервере при этом пропадут; `.env` и база не трогаются, они не в git.
3. Ставит зависимости.
4. Накатывает миграции Alembic.
5. Собирает фронтенд.
6. Перезапускает `grinder-web` и `grinder-bot` и показывает, поднялись ли сервисы.

Код на сервер попадает только из ветки `main` в GitHub. Сначала `git push origin main` со своего компьютера, потом `safe_update.sh` на сервере.

---

## 11. Резервные копии и восстановление

- Автоматически: раз в сутки снимок `data_grinder.db` уходит в `backups/` (хранится `BACKUP_KEEP` штук). Плюс снимок при каждом старте и при каждом `safe_update.sh`.
- Копии на том же диске не спасут, если умрёт сам сервер. Хотя бы раз в неделю забирайте свежую копию к себе на компьютер. Способов два.
  - **WinSCP:** подключитесь с теми же адресом и ключом `.ppk`, откройте `/root/GRINDER/backups/` и перетащите файл `data_grinder.latest.bak` в папку на компьютере.
  - **pscp:** на вашем компьютере (Win+R → `cmd`) выполните:

    ```bat
    pscp -i C:\keys\grinder.ppk root@IP_СЕРВЕРА:/root/GRINDER/backups/data_grinder.latest.bak C:\backups\data_grinder.db
    ```

  Если входите по паролю, уберите `-i C:\keys\grinder.ppk`: pscp спросит пароль.

Отправить файл на сервер (например, копию базы для восстановления) — так же, только в обратную сторону:

```bat
pscp -i C:\keys\grinder.ppk C:\backups\data_grinder.db root@IP_СЕРВЕРА:/root/GRINDER/backups/restore.db
```

Восстановление из копии:

```bash
systemctl stop grinder-web grinder-bot
cp /root/GRINDER/data_grinder.db /root/GRINDER/data_grinder.broken.db      # на всякий случай
cp /root/GRINDER/backups/НУЖНАЯ_КОПИЯ.db /root/GRINDER/data_grinder.db
rm -f /root/GRINDER/data_grinder.db-wal /root/GRINDER/data_grinder.db-shm
systemctl start grinder-web grinder-bot
```

---

## 12. Если что-то не работает

| Симптом | Где смотреть / что сделать |
|---|---|
| `grinder-web` не стартует | `journalctl -u grinder-web -n 50 --no-pager` — чаще всего не установлены зависимости или ошибка в `.env` |
| Сайт отдаёт 502 Bad Gateway | веб-сервис не запущен: `systemctl restart grinder-web`, затем лог |
| Загрузка файла падает с 413 | лимит nginx: `bash fix_nginx.sh` (ставит 100 МБ) |
| Mini App в Telegram пишет об ошибке авторизации | `WEBAPP_URL` не совпадает с доменом, или токен бота в `.env` не от этого бота |
| Нарезка висит в очереди | задачи, отложенные «до скидки», ждут ближайшего часа вне пика (пик — будни 04:00–07:00 и 09:00–13:00 по Москве), остальные стартуют сразу. Лог: `journalctl -u grinder-web | grep "Generation Worker"` |
| Нарезка падает с ошибкой ИИ | баланс DeepSeek: `venv/bin/python scripts/check_balance.py` |
| Бот молчит на `/start` | `journalctl -u grinder-bot -n 50` — неверный токен или бот запущен где-то ещё (два polling-процесса на один токен мешают друг другу) |
| PuTTY пишет «Network error: Connection timed out» | сервер выключен, неверный IP или файрвол закрыл SSH. Через веб-консоль хостинга выполните `ufw allow OpenSSH` |
| PuTTY пишет «No supported authentication methods available» | вход по паролю отключён, а ключ `.ppk` не указан в сессии (раздел П.3, шаг 5) |
| В окне PuTTY «кракозябры» вместо русских букв | Window → Translation → **UTF-8**, сохраните сессию |
| Изменения кода не видны в Telegram | Telegram кэширует WebApp: закройте и откройте приложение заново. Версии `?v=` у `main.css` и `app.js` поднимаются при правках в `index_template.html` |

Полезно знать:

- `scripts/clean_server_junk.sh` чистит старые логи и кэши, если место на диске кончается.
- `scripts/wipe_server_data.sh` **стирает всю базу** — это для тестового сервера, не для боевого.
