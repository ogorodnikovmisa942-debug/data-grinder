# tests/test_experiment_and_telemetry.py
import unittest
import json
import csv
import io
from pathlib import Path
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
        # Лимит для участника Фазы 1 строго равен settings.EXPERIMENT_DAILY_LIMIT (10 карт)
        self.assertEqual(len(cards), settings.EXPERIMENT_DAILY_LIMIT)
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

        try:
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
        finally:
            # Всегда возвращаем систему в исходную фазу 1
            self.client.post(
                "/api/admin/experiment/switch-phase",
                headers=headers_admin,
                json={"phase": 1}
            )

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
        self.assertEqual(data["daily_new_limit"], settings.EXPERIMENT_DAILY_LIMIT)
        self.assertEqual(data["new_remaining_today"], settings.EXPERIMENT_DAILY_LIMIT) # 10 доступно (лимит 10)

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

        try:
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
        finally:
            p_file = Path("app/static/presets/sudoustroystvo.json")
            if p_file.exists():
                p_file.unlink()

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

    def test_14_spoiler_sanitizer_and_clerical_blacklist(self):
        """Проверка санитайзера спойлеров в secondary_text и фильтра канцелярского шума."""
        from app.services.ai_gateway import unpack_minified_cards, is_blacklisted_card

        # 1. Проверка санитайзера утечек ответов в 's'
        spoiler_payload = {
            "domain": "law",
            "slug": "sudoustroystvo",
            "c": [
                {
                    "t": "Какие органы в Республике Беларусь осуществляют предварительное следствие?",
                    "s": "УПК | Следственный комитет и КГБ",
                    "d": "Следственный комитет Республики Беларусь и Комитет государственной безопасности Республики Беларусь.",
                    "l": "easy"
                },
                {
                    "t": "В какой срок подается апелляционная жалоба на решение районного суда?",
                    "s": "ГПК | Срок — 10 суток",
                    "d": "В течение 10 суток со дня вынесения решения.",
                    "l": "medium"
                },
                {
                    "t": "Какой орган обладает исключительным правом осуществления правосудия?",
                    "s": "ст. 109 Конституции | Монополия судейской мантии",
                    "d": "Только суды Республики Беларусь.",
                    "l": "easy"
                }
            ]
        }
        res = unpack_minified_cards(spoiler_payload)
        cards = res["cards"]
        self.assertEqual(len(cards), 3)
        # Утечка "Следственный комитет и КГБ" должна быть отсечена
        self.assertEqual(cards[0]["secondary_text"], "УПК")
        # Утечка "Срок — 10 суток" должна быть отсечена
        self.assertEqual(cards[1]["secondary_text"], "ГПК")
        # Нейтральный концептуальный якорь должен остаться нетронутым
        self.assertEqual(cards[2]["secondary_text"], "ст. 109 Конституции | Монополия судейской мантии")

        # 2. Проверка фильтра канцелярского балласта
        clerical_card_1 = {
            "text": "Какое количество членов коллегии должно присутствовать для кворума заседания?",
            "translation": "Не менее двух третей.",
            "secondary_text": "Положение о коллегии"
        }
        is_bl, reason = is_blacklisted_card(clerical_card_1, "law")
        self.assertTrue(is_bl)
        self.assertEqual(reason, "clerical_bureaucratic_trivia")

        clerical_card_2 = {
            "text": "Какова продолжительность стажировки для претендента в адвокаты?",
            "translation": "От трех до шести месяцев.",
            "secondary_text": "Закон об адвокатуре"
        }
        is_bl2, reason2 = is_blacklisted_card(clerical_card_2, "law")
        self.assertTrue(is_bl2)
        self.assertEqual(reason2, "clerical_bureaucratic_trivia")

        clerical_card_3 = {
            "text": "Через какой минимальный срок возможна повторная сдача квалификационного экзамена?",
            "translation": "Не ранее чем через шесть месяцев.",
            "secondary_text": "Закон об адвокатуре"
        }
        is_bl3, reason3 = is_blacklisted_card(clerical_card_3, "law")
        self.assertTrue(is_bl3)
        self.assertEqual(reason3, "clerical_bureaucratic_trivia")

        valid_card = {
            "text": "В каком составе рассматриваются уголовные дела о преступлениях несовершеннолетних?",
            "translation": "Коллегией в составе судьи и двух народных заседателей.",
            "secondary_text": "ст. 32 УПК"
        }
        is_bl4, _ = is_blacklisted_card(valid_card, "law")
        self.assertFalse(is_bl4)

    def test_15_phase1_limit_10_and_slicing_lock_verification(self):
        """Проверка фиксации лимита на 10 карт и полной блокировки нарезки материалов для участников Фазы 1."""
        participant_id = "user_phase1_10_limit_tester"
        headers_user = {"X-User-Id": participant_id}

        async def _setup_p1():
            async with AsyncSessionLocal() as db:
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
                setting = UserSetting(
                    user_id=participant_id,
                    daily_limit=10,
                    is_experiment_participant=True,
                    experiment_phase=1
                )
                db.add(setting)
                await db.commit()

        self.run_async(_setup_p1())

        # 1. Проверяем /api/config: лимит строго 10, is_experiment_locked = True
        r_cfg = self.client.get("/api/config?subject=all", headers=headers_user)
        self.assertEqual(r_cfg.status_code, 200)
        cfg_data = r_cfg.json()
        self.assertEqual(cfg_data["daily_limit"], 10)
        self.assertTrue(cfg_data["is_experiment_locked"])
        self.assertTrue(cfg_data["is_experiment_participant"])
        self.assertEqual(cfg_data["experiment_phase"], 1)

        # 2. Попытка нарезки сырого текста через /api/config/import -> 403 Forbidden
        r_import = self.client.post(
            "/api/config/import",
            headers=headers_user,
            json={"text": "Статья 1. Конституция РФ имеет высшую юридическую силу.", "subject": "law"}
        )
        self.assertEqual(r_import.status_code, 403)
        self.assertIn("Действие заблокировано на период проведения научного эксперимента", r_import.json()["detail"])

        # 3. Попытка фиксации стейджинга -> 403 Forbidden
        r_commit = self.client.post(
            "/api/config/import/commit",
            headers=headers_user,
            json={"subject": "law", "theme": "Конституция", "cards": [{"text": "Вопрос", "translation": "Ответ"}]}
        )
        self.assertEqual(r_commit.status_code, 403)

        # 4. Попытка импорта пресета -> 403 Forbidden
        r_preset = self.client.post(
            "/api/config/import/preset",
            headers=headers_user,
            json={"preset_name": "law"}
        )
        self.assertEqual(r_preset.status_code, 403)

        # 5. Попытка изменения лимита пользователем -> 403 Forbidden
        r_set_limit = self.client.post(
            "/api/config",
            headers=headers_user,
            json={"daily_limit": 20}
        )
        self.assertEqual(r_set_limit.status_code, 403)

    def test_16_admin_finish_experiment_and_switch_phase(self):
        """Проверка работы переключения на Фазу 2 и полного завершения эксперимента через админку."""
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        p_user = "user_switch_phase_tester"
        headers_user = {"X-User-Id": p_user}

        # 1. Задаем участника Фазы 1
        r_set = self.client.post(
            "/api/admin/experiment/set-participant",
            headers=headers_admin,
            json={"user_id": p_user, "is_participant": True, "phase": 1}
        )
        self.assertEqual(r_set.status_code, 200)

        # 2. Переключаем на Фазу 2
        r_switch2 = self.client.post(
            "/api/admin/experiment/switch-phase",
            headers=headers_admin,
            json={"phase": 2}
        )
        self.assertEqual(r_switch2.status_code, 200)
        self.assertEqual(r_switch2.json()["phase"], 2)

        # В Фазе 2 блокировка нарезки снята (is_experiment_locked = False)
        r_cfg2 = self.client.get("/api/config?subject=all", headers=headers_user)
        self.assertEqual(r_cfg2.status_code, 200)
        self.assertFalse(r_cfg2.json()["is_experiment_locked"])
        self.assertEqual(r_cfg2.json()["experiment_phase"], 2)

        # 3. Завершаем эксперимент через /api/admin/experiment/finish
        r_finish = self.client.post(
            "/api/admin/experiment/finish",
            headers=headers_admin
        )
        self.assertEqual(r_finish.status_code, 200)
        self.assertEqual(r_finish.json()["status"], "success")

        # После завершения пользователь стал обычным (is_experiment_participant = False)
        r_cfg_final = self.client.get("/api/config?subject=all", headers=headers_user)
        self.assertEqual(r_cfg_final.status_code, 200)
        self.assertFalse(r_cfg_final.json()["is_experiment_participant"])
        self.assertFalse(r_cfg_final.json()["is_experiment_locked"])

    def test_clean_presets_and_custom_deck_distribution(self):
        """Проверка отсутствия тестовых колод-пресетов и раздачи пользовательской колоды через админку."""
        # 1. Проверяем, что в app/static/presets нет старых тестовых колод
        legacy_presets = ["hsk3.json", "law.json", "python.json", "sudoustroystvo.json"]
        presets_dir = Path("app/static/presets")
        for lp in legacy_presets:
            self.assertFalse((presets_dir / lp).exists(), f"Пресет {lp} должен быть удален из системы")

        # 2. Создаем участника эксперимента
        target_user = "exp_exclusive_student"
        headers_admin = {"X-Admin-Token": settings.ADMIN_TOKEN}
        r_user = self.client.post(
            "/api/admin/experiment/set-participant",
            headers=headers_admin,
            json={"user_id": target_user, "is_participant": True, "phase": 1}
        )
        self.assertEqual(r_user.status_code, 200)

        # 3. Раздаем уникальную пользовательскую колоду
        custom_slug = "custom_test_deck"
        custom_title = "Уникальный авторский курс"
        test_preset_file = presets_dir / f"{custom_slug}.json"
        if test_preset_file.exists():
            test_preset_file.unlink()

        try:
            payload = {
                "subject_slug": custom_slug,
                "phrase_title": custom_title,
                "target_user_id": target_user,
                "mode": "overwrite",
                "cards": [
                    {
                        "text": "Ключевое понятие 1",
                        "translation": "Точное определение 1",
                        "secondary_text": "Комментарий 1",
                        "mnemonic": "Ассоциация 1"
                    },
                    {
                        "text": "Ключевое понятие 2",
                        "translation": "Точное определение 2"
                    }
                ]
            }

            r_dist = self.client.post(
                "/api/admin/experiment/distribute-deck",
                headers=headers_admin,
                json=payload
            )
            self.assertEqual(r_dist.status_code, 200)
            data_dist = r_dist.json()
            self.assertEqual(data_dist["status"], "success")
            self.assertEqual(data_dist["total_cards_created"], 2)

            # Проверяем, что файл колоды автоматически сохранился в app/static/presets/{custom_slug}.json
            self.assertTrue(test_preset_file.exists())
            saved_content = json.loads(test_preset_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_content["subject_slug"], custom_slug)
            self.assertEqual(saved_content["phrase_title"], custom_title)
            self.assertEqual(len(saved_content["cards"]), 2)

            # Проверяем, что у участника в API видны именно эти 2 карточки
            r_cards = self.client.get(
                f"/api/data/cards?subject={custom_slug}",
                headers={"X-User-Id": target_user}
            )
            self.assertEqual(r_cards.status_code, 200)
            cards_list = r_cards.json()["cards"]
            self.assertEqual(len(cards_list), 2)
            texts = [c["text"] for c in cards_list]
            self.assertIn("Ключевое понятие 1", texts)
            self.assertIn("Ключевое понятие 2", texts)

        finally:
            if test_preset_file.exists():
                test_preset_file.unlink()



if __name__ == "__main__":
    unittest.main()


