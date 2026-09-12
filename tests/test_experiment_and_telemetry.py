# tests/test_experiment_and_telemetry.py
import unittest
import json
import csv
import io
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import select, delete, text
from main import app
from app.core.config import settings
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, UserSetting, Card, Phrase, ReviewLog, DailySession, AiTelemetryLog, GenerationJob, InviteCode
from app.services.ai_gateway import (
    extract_json_payload_with_telemetry,
    extract_json_payload,
    record_ai_telemetry
)
from app.services.fsrs_core import calculate_intervals

class TestExperimentAndTelemetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Инициализируем TestClient с lifespan контекстом для выполнения миграций
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_01_json_repair_and_truncation_detection(self):
        """Phase 2: Тестирование восстановления обрезанного JSON и флагов телеметрии."""
        # 1. Валидный неповрежденный JSON
        valid_json = '{"domain":"law","slug":"sudoustroystvo","title":"Судоустройство","c":[{"t":"Судья","s":"ст. 1","d":"Носитель судебной власти","e":"Пример","l":"easy"}]}'
        data, is_truncated, repair_successful = extract_json_payload_with_telemetry(valid_json)
        self.assertFalse(is_truncated)
        self.assertFalse(repair_successful)
        self.assertEqual(len(data["c"]), 1)

        # 2. Обрезанный на полуслове JSON (симуляция лимита токенов)
        truncated_json = (
            '{"domain":"law","slug":"sudoustroystvo","title":"Судоустройство","c":['
            '{"t":"Судья","s":"ст. 1","d":"Носитель судебной власти","e":"Пример","l":"easy"},'
            '{"t":"Прокурор","s":"ст. 2","d":"Гособвинитель в суде'
        )
        data_rep, is_trunc_rep, rep_succ = extract_json_payload_with_telemetry(truncated_json)
        self.assertTrue(is_trunc_rep)
        self.assertTrue(rep_succ)
        self.assertEqual(len(data_rep["c"]), 1)
        self.assertEqual(data_rep["c"][0]["t"], "Судья")

        # 3. Полностью невалидный JSON
        with self.assertRaises(ValueError):
            extract_json_payload_with_telemetry("Совершенно не JSON ответ от модели")

    def test_02_ai_telemetry_logging_in_db(self):
        """Phase 2: Запись телеметрии обращения к ИИ в AiTelemetryLog."""
        async def _cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(AiTelemetryLog).filter(AiTelemetryLog.job_id == "test-job-42"))
                await db.commit()
        self.run_async(_cleanup())

        async def _log_test():
            await record_ai_telemetry(
                job_id="test-job-42",
                user_id="user_research_1",
                model_requested="deepseek-chat",
                model_resolved="deepseek-chat",
                input_chars=1250,
                prompt_tokens=450,
                completion_tokens=220,
                cache_hit=True,
                is_truncated=True,
                repair_successful=True,
                cards_generated=14,
                duration_ms=1850,
                status="success"
            )

            async with AsyncSessionLocal() as db:
                stmt = select(AiTelemetryLog).filter(AiTelemetryLog.job_id == "test-job-42")
                res = await db.execute(stmt)
                log = res.scalars().first()
                self.assertIsNotNone(log)
                self.assertEqual(log.user_id, "user_research_1")
                self.assertTrue(log.cache_hit)
                self.assertTrue(log.is_truncated)
                self.assertTrue(log.repair_successful)
                self.assertEqual(log.cards_generated, 14)
                self.assertEqual(log.duration_ms, 1850)

        self.run_async(_log_test())

    def test_03_experiment_phase1_blocks_modification_and_import(self):
        """Phase 1: Проверка 403 Forbidden для участника эксперимента Фазы 1."""
        participant_id = "participant_p1_test"

        # Настраиваем участника Фазы 1
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        set_resp = self.client.post(
            "/api/admin/experiment/set-participant",
            headers=headers_admin,
            json={"user_id": participant_id, "is_participant": True, "phase": 1}
        )
        self.assertEqual(set_resp.status_code, 200)

        headers_user = {"X-User-Id": participant_id}

        # 1. Попытка создать карточку вручную -> 403
        r_create = self.client.post(
            "/api/management/cards",
            headers=headers_user,
            json={"subject": "sudoustroystvo", "text": "Тест", "translation": "Определение"}
        )
        self.assertEqual(r_create.status_code, 403)
        self.assertIn("Действие заблокировано на период проведения научного эксперимента", r_create.json()["detail"])

        # 2. Попытка редактировать карточку -> 403
        r_update = self.client.put(
            "/api/management/cards/999",
            headers=headers_user,
            json={"text": "Новый", "translation": "Определение"}
        )
        self.assertEqual(r_update.status_code, 403)

        # 3. Попытка удалить карточку -> 403
        r_delete = self.client.delete("/api/management/cards/999", headers=headers_user)
        self.assertEqual(r_delete.status_code, 403)

        # 4. Попытка массового удаления -> 403
        r_bulk_del = self.client.post(
            "/api/data/cards/delete",
            headers=headers_user,
            json={"card_ids": [1, 2]}
        )
        self.assertEqual(r_bulk_del.status_code, 403)

        # 5. Попытка удалить предмет -> 403
        r_del_sub = self.client.delete("/api/data/subjects/sudoustroystvo", headers=headers_user)
        self.assertEqual(r_del_sub.status_code, 403)

        # 6. Попытка импорта текста -> 403
        r_import = self.client.post(
            "/api/config/import",
            headers=headers_user,
            json={"text": "Некоторый юридический текст", "subject": "sudoustroystvo"}
        )
        self.assertEqual(r_import.status_code, 403)

        # 7. Попытка фиксации из песочницы -> 403
        r_commit = self.client.post(
            "/api/config/import/commit",
            headers=headers_user,
            json={"subject": "sudoustroystvo", "theme": "Тема", "cards": [{"text": "Фронт", "translation": "Бэк"}]}
        )
        self.assertEqual(r_commit.status_code, 403)

        # 8. Попытка импорта готового пресета -> 403
        r_preset = self.client.post(
            "/api/config/import/preset",
            headers=headers_user,
            json={"preset_name": "law"}
        )
        self.assertEqual(r_preset.status_code, 403)

        # 9. Попытка изменить настройки -> 403
        r_cfg = self.client.post(
            "/api/config",
            headers=headers_user,
            json={"daily_limit": 50}
        )
        self.assertEqual(r_cfg.status_code, 403)

    def test_04_experiment_session_limits_and_subject_lock(self):
        """Phase 1: При запросе /api/session предмет принудительно sudoustroystvo, лимит новых карт = 20."""
        participant_id = "participant_session_test"

        # Настраиваем карточки и участника в БД
        async def _prepare():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).filter(Card.user_id == participant_id))
                await db.execute(delete(Phrase).filter(Phrase.user_id == participant_id))
                await db.execute(delete(UserSession).filter(UserSession.user_id == participant_id))
                await db.execute(delete(UserSetting).filter(UserSetting.user_id == participant_id))
                await db.commit()

                sess = UserSession(
                    telegram_id=participant_id,
                    user_id=participant_id,
                    is_experiment_participant=True,
                    experiment_phase=1
                )
                db.add(sess)

                # Добавляем 25 новых карт по судоустройству
                p = Phrase(text="Судоустройство", subject="sudoustroystvo", user_id=participant_id)
                db.add(p)
                await db.flush()

                for i in range(25):
                    c = Card(
                        phrase_id=p.id,
                        user_id=participant_id,
                        subject="sudoustroystvo",
                        text=f"Термин {i}",
                        translation=f"Определение {i}",
                        state=0,
                        next_review=datetime.utcnow()
                    )
                    db.add(c)
                await db.commit()

        self.run_async(_prepare())

        headers = {"X-User-Id": participant_id}
        # Запрашиваем session с subject=all
        resp = self.client.get("/api/session?subject=all&mode=new", headers=headers)
        self.assertEqual(resp.status_code, 200)
        cards = resp.json()
        # Лимит для участника Фазы 1 строго равен 20 (режим 20 карт)
        self.assertEqual(len(cards), 20)
        # Все карточки принадлежат исключительно предмету sudoustroystvo
        for card in cards:
            self.assertEqual(card["subject"], "sudoustroystvo")

    def test_05_timing_sanitization_and_outliers(self):
        """Phase 1: Проверка санитарии таймингов (<600 мс понижение Easy -> Good, >30000 мс FSRS безопасное 15000 мс, cram=True логирование)."""
        user_id = "timing_test_user"

        card_id = None
        async def _setup_card():
            nonlocal card_id
            async with AsyncSessionLocal() as db:
                await db.execute(delete(ReviewLog).filter(ReviewLog.user_id == user_id))
                await db.execute(delete(Card).filter(Card.user_id == user_id))
                await db.execute(delete(Phrase).filter(Phrase.user_id == user_id))
                await db.execute(delete(UserSession).filter(UserSession.user_id == user_id))
                await db.execute(delete(UserSetting).filter(UserSetting.user_id == user_id))
                await db.commit()

                sess = UserSession(telegram_id=user_id, user_id=user_id, is_experiment_participant=True, experiment_phase=1)
                db.add(sess)
                p = Phrase(text="Основы права", subject="sudoustroystvo", user_id=user_id)
                db.add(p)
                await db.flush()
                c = Card(
                    phrase_id=p.id,
                    user_id=user_id,
                    subject="sudoustroystvo",
                    text="Конституция",
                    translation="Основной закон государства",
                    state=0,
                    next_review=datetime.utcnow()
                )
                db.add(c)
                await db.commit()
                await db.refresh(c)
                card_id = c.id

        self.run_async(_setup_card())

        headers = {"X-User-Id": user_id}

        # 1. Миссклик: response_time = 300 мс, rating = 4 (Easy)
        # Рейтинг должен быть понижен до 3 (Good), а is_outlier = True
        r_misclick = self.client.post(
            "/api/answer",
            headers=headers,
            json={"card_id": card_id, "rating": 4, "response_time": 300}
        )
        self.assertEqual(r_misclick.status_code, 200)

        async def _check_misclick():
            async with AsyncSessionLocal() as db:
                stmt = select(ReviewLog).filter(ReviewLog.card_id == card_id, ReviewLog.response_time == 300)
                res = await db.execute(stmt)
                log = res.scalars().first()
                self.assertIsNotNone(log)
                self.assertEqual(log.rating, 3)  # Понижено с 4 до 3!
                self.assertTrue(log.is_outlier)   # Зафиксирован выброс!

        self.run_async(_check_misclick())

        # 2. Долгое зависание: response_time = 45000 мс (> 30000 мс)
        r_delay = self.client.post(
            "/api/answer",
            headers=headers,
            json={"card_id": card_id, "rating": 3, "response_time": 45000}
        )
        self.assertEqual(r_delay.status_code, 200)

        async def _check_delay():
            async with AsyncSessionLocal() as db:
                stmt = select(ReviewLog).filter(ReviewLog.card_id == card_id, ReviewLog.response_time == 45000)
                res = await db.execute(stmt)
                log = res.scalars().first()
                self.assertIsNotNone(log)
                self.assertEqual(log.response_time, 45000) # Реальное время сохранено
                self.assertTrue(log.is_outlier)            # Помечен выбросом

        self.run_async(_check_delay())

        # 3. Штурм: is_cram = True
        r_cram = self.client.post(
            "/api/answer",
            headers=headers,
            json={"card_id": card_id, "rating": 3, "response_time": 4000, "is_cram": True}
        )
        self.assertEqual(r_cram.status_code, 200)

        async def _check_cram():
            async with AsyncSessionLocal() as db:
                stmt = select(ReviewLog).filter(ReviewLog.card_id == card_id, ReviewLog.is_cram == True)
                res = await db.execute(stmt)
                log = res.scalars().first()
                self.assertIsNotNone(log)
                self.assertTrue(log.is_cram)
                self.assertFalse(log.is_outlier)

        self.run_async(_check_cram())

    def test_06_admin_export_dataset_csv(self):
        """Проверка эндпоинта выгрузки датасета для Pandas/R (/api/admin/export/experiment-dataset)."""
        # 1. Проверка авторизации: без токена -> 403
        r_unauth = self.client.get("/api/admin/export/experiment-dataset")
        self.assertEqual(r_unauth.status_code, 403)

        # 2. С валидным X-Admin-Token
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        r_export = self.client.get("/api/admin/export/experiment-dataset", headers=headers_admin)
        self.assertEqual(r_export.status_code, 200)
        self.assertIn("text/csv", r_export.headers.get("content-type", ""))
        self.assertIn('filename="grinder_experiment_phase1.csv"', r_export.headers.get("content-disposition", ""))

        # Проверяем, что контент начинается с UTF-8 BOM (\ufeff)
        content_bytes = r_export.content
        self.assertTrue(content_bytes.startswith(b"\xef\xbb\xbf"))

        # Проверяем парсинг CSV
        text_content = content_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text_content))
        expected_fields = [
            "user_id", "log_id", "card_id", "subject_id", "rating",
            "response_time", "is_outlier", "is_cram", "stability",
            "difficulty", "elapsed_days", "scheduled_days",
            "mental_effort", "perceived_retention", "true_retention",
            "session_duration", "review_time"
        ]
        self.assertEqual(reader.fieldnames, expected_fields)
        rows = list(reader)
        self.assertGreater(len(rows), 0)

    def test_07_admin_switch_phase(self):
        """Административное переключение фазы (POST /api/admin/experiment/switch-phase) и снятие блокировок."""
        participant_id = "participant_p1_test"
        headers_user = {"X-User-Id": participant_id}
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}

        # Гарантируем, что перед тестом участник в фазе 1
        self.client.post(
            "/api/admin/experiment/set-participant",
            headers=headers_admin,
            json={"user_id": participant_id, "is_participant": True, "phase": 1}
        )

        # До переключения: создание карты заблокировано (403)
        r_blocked = self.client.post(
            "/api/management/cards",
            headers=headers_user,
            json={"subject": "sudoustroystvo", "text": "Тест", "translation": "Определение"}
        )
        self.assertEqual(r_blocked.status_code, 403)

        # Переключаем фазу на 2
        r_switch = self.client.post(
            "/api/admin/experiment/switch-phase",
            headers=headers_admin,
            json={"phase": 2}
        )
        self.assertEqual(r_switch.status_code, 200)
        self.assertEqual(r_switch.json()["phase"], 2)

        # После переключения в фазу 2 блокировка снята (не 403)
        r_allowed = self.client.post(
            "/api/management/cards",
            headers=headers_user,
            json={"subject": "sudoustroystvo", "text": "Свободная карта", "translation": "Определение"}
        )
        self.assertEqual(r_allowed.status_code, 200)
        self.assertEqual(r_allowed.json()["status"], "success")

    def test_08_admin_export_ai_telemetry(self):
        """Проверка выгрузки инженерной телеметрии ИИ в форматах JSON и CSV."""
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}

        # 1. JSON
        r_json = self.client.get("/api/admin/export/ai-telemetry?format=json", headers=headers_admin)
        self.assertEqual(r_json.status_code, 200)
        data = r_json.json()
        self.assertEqual(data["status"], "success")
        self.assertIsInstance(data["telemetry"], list)

        # 2. CSV
        r_csv = self.client.get("/api/admin/export/ai-telemetry?format=csv", headers=headers_admin)
        self.assertEqual(r_csv.status_code, 200)
        self.assertTrue(r_csv.content.startswith(b"\xef\xbb\xbf"))
        text_csv = r_csv.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text_csv))
        self.assertIn("cache_hit", reader.fieldnames)
        self.assertIn("is_truncated", reader.fieldnames)
        self.assertIn("repair_successful", reader.fieldnames)

    def test_09_session_button_counters(self):
        """Проверка динамических счетчиков для кнопок сессии (/api/stats/dashboard)."""
        user_id = "test_button_counters_user"
        headers_user = {"X-User-Id": user_id}

        async def _setup():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).filter(Card.user_id == user_id))
                await db.execute(delete(Phrase).filter(Phrase.user_id == user_id))
                await db.execute(delete(UserSession).filter(UserSession.user_id == user_id))
                await db.execute(delete(UserSetting).filter(UserSetting.user_id == user_id))
                await db.commit()

                sess = UserSession(telegram_id=user_id, user_id=user_id, is_experiment_participant=True, experiment_phase=1)
                db.add(sess)
                p = Phrase(text="Судоустройство", subject="sudoustroystvo", user_id=user_id)
                db.add(p)
                await db.flush()

                # Добавляем 5 карточек на повторение (state=2, next_review <= now)
                now_utc = datetime.utcnow()
                for i in range(5):
                    c = Card(
                        phrase_id=p.id, user_id=user_id, subject="sudoustroystvo",
                        text=f"Карта повтора {i}", translation=f"Определение {i}",
                        state=2, next_review=now_utc
                    )
                    db.add(c)
                # Добавляем 10 новых карточек (state=0)
                for i in range(10):
                    c = Card(
                        phrase_id=p.id, user_id=user_id, subject="sudoustroystvo",
                        text=f"Новая карта {i}", translation=f"Определение {i}",
                        state=0, next_review=now_utc
                    )
                    db.add(c)
                await db.commit()

        self.run_async(_setup())

        r = self.client.get("/api/stats/dashboard?subject=sudoustroystvo", headers=headers_user)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # Проверяем наличие всех счетчиков
        self.assertIn("due_reviews_now", data)
        self.assertIn("new_remaining_today", data)
        self.assertIn("daily_new_limit", data)
        self.assertIn("total_cards", data)

        self.assertEqual(data["due_reviews_now"], 5)
        self.assertEqual(data["total_cards"], 15)
        self.assertEqual(data["daily_new_limit"], 20)
        self.assertEqual(data["new_remaining_today"], 10) # 10 доступно (лимит 20)

    def test_10_invites_and_participants_list(self):
        """Проверка генерации инвайтов и детального списка участников с @username."""
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}

        # 1. Генерация инвайта
        r_gen = self.client.post("/api/admin/invites/generate", headers=headers_admin, json={"created_by": "test_admin"})
        self.assertEqual(r_gen.status_code, 200)
        invite_code = r_gen.json()["code"]
        self.assertTrue(invite_code.startswith("INV-"))

        # 2. Проверка в списке инвайтов
        r_invites = self.client.get("/api/admin/invites", headers=headers_admin)
        self.assertEqual(r_invites.status_code, 200)
        inv_list = r_invites.json()["invites"]
        found = any(i["code"] == invite_code and not i["is_used"] for i in inv_list)
        self.assertTrue(found)

        # 3. Симуляция активации участником с username
        async def _activate():
            async with AsyncSessionLocal() as db:
                stmt = select(InviteCode).filter(InviteCode.code == invite_code)
                inv = (await db.execute(stmt)).scalar_one_or_none()
                inv.is_used = True
                inv.used_by_user_id = "test_student_tg"
                inv.used_by_username = "@test_law_student"
                inv.used_at = datetime.utcnow()

                stmt_sess = select(UserSession).filter(UserSession.telegram_id == "test_student_tg")
                sess = (await db.execute(stmt_sess)).scalar_one_or_none()
                if not sess:
                    sess = UserSession(
                        telegram_id="test_student_tg",
                        user_id="test_student_tg"
                    )
                    db.add(sess)
                sess.username = "test_law_student"
                sess.full_name = "Иван Юрист"
                sess.is_experiment_participant = True
                sess.experiment_phase = 1
                await db.commit()

        self.run_async(_activate())

        # 4. Проверка в /api/admin/participants
        r_part = self.client.get("/api/admin/participants", headers=headers_admin)
        self.assertEqual(r_part.status_code, 200)
        participants = r_part.json()["participants"]
        p_match = next((p for p in participants if p["telegram_id"] == "test_student_tg"), None)
        self.assertIsNotNone(p_match)
        self.assertEqual(p_match["username"], "test_law_student")
        self.assertEqual(p_match["full_name"], "Иван Юрист")
        self.assertEqual(p_match["experiment_phase"], 1)

    def test_11_distribute_deck_append_preserves_fsrs_state(self):
        """Проверка безопасной дозагрузки карточек (Append): сохранение state, stability и прогресса FSRS."""
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        user_id = "test_append_student"

        async def _prepare():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).filter(Card.user_id == user_id))
                await db.execute(delete(Phrase).filter(Phrase.user_id == user_id))
                await db.execute(delete(UserSession).filter(UserSession.user_id == user_id))
                await db.commit()

                sess = UserSession(
                    telegram_id=user_id,
                    user_id=user_id,
                    is_experiment_participant=True,
                    experiment_phase=1
                )
                db.add(sess)

                p = Phrase(text="Основы судебной власти", subject="sudoustroystvo", user_id=user_id)
                db.add(p)
                await db.flush()

                # Существующая изученная карточка: FSRS state=2, stability=9.5, difficulty=3.8, lapses=1
                card_old = Card(
                    phrase_id=p.id,
                    user_id=user_id,
                    subject="sudoustroystvo",
                    text="Правосудие",
                    secondary_text="ст. 118 КРФ",
                    translation="Старое определение",
                    state=2,
                    stability=9.5,
                    difficulty=3.8,
                    lapses=1,
                    has_seen_intro=True,
                    next_review=datetime.utcnow()
                )
                db.add(card_old)
                await db.commit()

        self.run_async(_prepare())

        # 1. Запускаем раздачу в режиме append: обновляем формулировку "Правосудие" и добавляем "Судья"
        payload_append = {
            "subject_slug": "sudoustroystvo",
            "phrase_title": "Судоустройство: Основной курс",
            "target_user_id": user_id,
            "mode": "append",
            "cards": [
                {
                    "text": "Правосудие",
                    "secondary_text": "ст. 118 Конституции РФ",
                    "translation": "Новое уточненное определение правосудия",
                    "example": "Пример осуществления правосудия"
                },
                {
                    "text": "Судья",
                    "secondary_text": "ст. 119 Конституции РФ",
                    "translation": "Носитель судебной власти",
                    "example": "Судьей может быть гражданин РФ..."
                }
            ]
        }

        r_append = self.client.post(
            "/api/admin/experiment/distribute-deck",
            headers=headers_admin,
            json=payload_append
        )
        self.assertEqual(r_append.status_code, 200)
        data_append = r_append.json()
        self.assertEqual(data_append["status"], "success")
        self.assertEqual(data_append["mode"], "append")
        self.assertEqual(data_append["total_cards_created"], 1) # Добавлена 1 новая
        self.assertEqual(data_append["total_cards_updated"], 1) # Обновлена 1 старая

        # Проверяем в БД: существующая карточка сохранила прогресс FSRS, но обновила текст!
        async def _verify_append():
            async with AsyncSessionLocal() as db:
                stmt = select(Card).filter(Card.user_id == user_id, Card.subject == "sudoustroystvo")
                res = await db.execute(stmt)
                cards = res.scalars().all()
                self.assertEqual(len(cards), 2)

                cards_by_text = {c.text: c for c in cards}
                old_c = cards_by_text["Правосудие"]
                self.assertEqual(old_c.translation, "Новое уточненное определение правосудия")
                self.assertEqual(old_c.secondary_text, "ст. 118 Конституции РФ")
                # FSRS параметры полностью сохранены!
                self.assertEqual(old_c.state, 2)
                self.assertAlmostEqual(old_c.stability, 9.5)
                self.assertAlmostEqual(old_c.difficulty, 3.8)
                self.assertEqual(old_c.lapses, 1)
                self.assertTrue(old_c.has_seen_intro)

                new_c = cards_by_text["Судья"]
                self.assertEqual(new_c.state, 0)
                self.assertFalse(new_c.has_seen_intro)
                self.assertEqual(new_c.translation, "Носитель судебной власти")

        self.run_async(_verify_append())

        # 2. Проверяем режим overwrite: полный сброс
        payload_overwrite = {
            "subject_slug": "sudoustroystvo",
            "phrase_title": "Судоустройство: Сброс",
            "target_user_id": user_id,
            "mode": "overwrite",
            "cards": [
                {"text": "Новая единственная карта", "translation": "Определение"}
            ]
        }
        r_over = self.client.post(
            "/api/admin/experiment/distribute-deck",
            headers=headers_admin,
            json=payload_overwrite
        )
        self.assertEqual(r_over.status_code, 200)
        data_over = r_over.json()
        self.assertEqual(data_over["mode"], "overwrite")
        self.assertEqual(data_over["total_cards_created"], 1)

        async def _verify_overwrite():
            async with AsyncSessionLocal() as db:
                stmt = select(Card).filter(Card.user_id == user_id, Card.subject == "sudoustroystvo")
                cards = (await db.execute(stmt)).scalars().all()
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0].text, "Новая единственная карта")
                self.assertEqual(cards[0].state, 0)

        self.run_async(_verify_overwrite())

    def test_12_export_cards_json(self):
        """Проверка эндпоинта экспорта колоды пользователя в формате JSON пресета (/api/data/cards/export)."""
        user_id = "test_export_cards_user"
        headers_user = {"X-User-Id": user_id}

        async def _prepare():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Card).filter(Card.user_id == user_id))
                await db.execute(delete(Phrase).filter(Phrase.user_id == user_id))
                await db.commit()

                p = Phrase(text="Судебная система", subject="sudoustroystvo", user_id=user_id)
                db.add(p)
                await db.flush()

                c = Card(
                    phrase_id=p.id,
                    user_id=user_id,
                    subject="sudoustroystvo",
                    text="Конституционный Суд",
                    secondary_text="ст. 125 КРФ",
                    translation="Орган конституционного контроля",
                    example="КС РФ разрешает дела о соответствии Конституции",
                    state=2,
                    next_review=datetime.utcnow()
                )
                db.add(c)
                await db.commit()

        self.run_async(_prepare())

        r = self.client.get("/api/data/cards/export?subject=sudoustroystvo", headers=headers_user)
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers.get("content-type", ""))
        payload = r.json()
        self.assertEqual(payload["subject_slug"], "sudoustroystvo")
        self.assertEqual(payload["total_cards"], 1)
        self.assertEqual(len(payload["cards"]), 1)
        self.assertEqual(payload["cards"][0]["text"], "Конституционный Суд")
        self.assertEqual(payload["cards"][0]["secondary_text"], "ст. 125 КРФ")
        self.assertEqual(payload["cards"][0]["translation"], "Орган конституционного контроля")

    def test_13_deepseek_prompt_caching_and_atomic_rules(self):
        """Phase 2: Проверка соответствия системного промпта DeepSeek порогу кэширования (>1024 токенов) и правилам атомарности."""
        from app.services.ai_gateway import DEEPSEEK_CACHED_SYSTEM_PROMPT, unpack_minified_cards

        # 1. Проверка длины системного промпта для DeepSeek Context Caching (порог > 1024 токенов)
        # В русско-английском тексте 1 слово = 1.3-2.0 токена. При >1400 словах токенов гарантированно >1800.
        word_count = len(DEEPSEEK_CACHED_SYSTEM_PROMPT.split())
        char_count = len(DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertGreaterEqual(word_count, 1200, f"Промпт содержит {word_count} слов, что может быть недостаточно для порога 1024 токенов.")
        self.assertGreaterEqual(char_count, 9000, f"Длина промпта в символах ({char_count}) должна быть >= 9000.")

        # 2. Проверка наличия ключевых когнитивных законов и анти-списочных директив
        self.assertIn("Minimum Information Principle", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Absolute Prohibition of Lists & Enumerations", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Narrow the Question, Never Mutilate the Answer", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Syntactic Completeness Guarantee", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Binary Qualification for High-Dimension Categorical Sets", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Zero-Duplication & Deck Cannibalization Guard", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("1.5–3.5 seconds", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("CONTRAST CASE 1", DEEPSEEK_CACHED_SYSTEM_PROMPT)

        # 3. Проверка распаковки атомарных юридических карточек функцией unpack_minified_cards
        mock_deepseek_output = {
            "domain": "law",
            "slug": "sudoustroystvo",
            "title": "Судоустройство РФ",
            "c": [
                {
                    "t": "Каков минимальный возраст для кандидата в судьи районного суда?",
                    "s": "ст. 4 Закона о статусе судей",
                    "d": "25 лет.",
                    "e": "24-летний кандидат получит отказ квалифколлегии.",
                    "l": "easy",
                    "h": "Требования к судьям"
                },
                {
                    "t": "Срок подачи кассационной жалобы составляет [...] со дня вступления приговора в силу.",
                    "s": "ст. 401.3 УПК РФ",
                    "d": "[6 месяцев].",
                    "e": "Пропуск срока влечет возврат жалобы без рассмотрения.",
                    "l": "medium",
                    "h": "Кассационное производство"
                }
            ]
        }
        unpacked = unpack_minified_cards(mock_deepseek_output)
        self.assertEqual(unpacked["subject_domain"], "law")
        self.assertEqual(unpacked["subject_slug"], "sudoustroystvo")
        self.assertEqual(len(unpacked["cards"]), 2)
        
        card1 = unpacked["cards"][0]
        self.assertEqual(card1["text"], "Каков минимальный возраст для кандидата в судьи районного суда?")
        self.assertEqual(card1["translation"], "25 лет.")
        self.assertEqual(card1["theme"], "Требования к судьям")

        card2 = unpacked["cards"][1]
        self.assertEqual(card2["translation"], "[6 месяцев].")
        self.assertEqual(card2["theme"], "Кассационное производство")

        # 5. Проверка защитного шлюза от порчи карточек пользовательскими инструкциями
        from app.services.ai_gateway import build_granularity_prompt
        safe_prompt = build_granularity_prompt(
            granularity_mode="detailed",
            custom_instruction="Сделай подробно все 10 признаков и напиши простыню текста",
            density="high",
            volume="auto"
        )
        self.assertIn("NON-NEGOTIABLE SAFETY CONSTRAINT", safe_prompt)
        self.assertIn("USER THEMATIC FOCUS", safe_prompt)
        self.assertNotIn("HIGHEST PRIORITY", safe_prompt)

if __name__ == "__main__":
    unittest.main()

