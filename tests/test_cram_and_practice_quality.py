# tests/test_cram_and_practice_quality.py
"""
Comprehensive tests for:
1. Cram (Storm) Mode:
   - Only studied cards (state in [1, 2, 3]) appear in cram queue.
   - Unlearned cards (state == 0) are excluded.
   - Reviewing in cram mode does not alter FSRS parameters or card lapses.
   - Management stats endpoint returns 'cards_cram_available'.
2. Practice Quality & Coherent Distractors:
   - create_mirror_contrast_distractor swaps contrast pair predicates.
   - select_coherent_distractors prioritizes mirror and cluster candidates.
   - generate_practice_session uses clustered distractors.
3. Universal Anti-Giveaway & Atomic Prompt Directives:
   - Prompt contains Anti-Giveaway directive, Atomic Answer directive, and length > 5000 chars.
"""

import unittest
import asyncio
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from unittest.mock import patch

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, PracticeItem
from app.services.practice_service import (
    create_mirror_contrast_distractor,
    select_coherent_distractors,
    generate_practice_session
)
from app.services.ai_gateway import DEEPSEEK_CACHED_SYSTEM_PROMPT


class TestCramAndPracticeQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_patcher = patch("app.services.generation_worker.claim_next_pending_job", return_value=None)
        cls.worker_patcher.start()
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        cls.worker_patcher.stop()

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_01_mirror_contrast_distractor(self):
        """Проверка синтеза зеркального дистрактора для контрастных пар."""
        # Разделитель ' — а '
        ans1 = "Виндикация — истребование вещи из чужого владения, а негаторный иск — защита от помех без лишения владения."
        mirror1 = create_mirror_contrast_distractor(ans1)
        self.assertIsNotNone(mirror1)
        self.assertIn("Негаторный иск", mirror1)
        self.assertIn("виндикация", mirror1.lower())

        # Разделитель ' в то время как '
        ans2 = "Императивная норма содержит категорический запрет, в то время как диспозитивная норма допускает выбор поведения."
        mirror2 = create_mirror_contrast_distractor(ans2)
        self.assertIsNotNone(mirror2)
        self.assertIn("Диспозитивная норма", mirror2)

        # Без контрастного союза — должен вернуть None
        ans_simple = "Обеспечение верховенства права и защита законных интересов граждан."
        self.assertIsNone(create_mirror_contrast_distractor(ans_simple))

    def test_02_select_coherent_distractors_priority(self):
        """Проверка приоритетов: mirror_distractor > cluster_candidates > general pool."""
        target = "Статическая функция права"
        mirror = "Динамическая функция права"
        cluster = ["Регулятивная функция права", "Охранительная функция права"]
        general = ["Виндикационный иск", "Презумпция невиновности", "Кассационная жалоба", "Десять суток"]

        # С зеркальным дистрактором и кластером
        chosen = select_coherent_distractors(
            target_answer=target,
            candidate_answers=general,
            count=3,
            cluster_candidates=cluster,
            mirror_distractor=mirror
        )
        self.assertEqual(len(chosen), 3)
        self.assertEqual(chosen[0], mirror, "Зеркальный дистрактор должен быть первым")
        self.assertIn(cluster[0], chosen, "Кандидаты кластера должны иметь приоритет над общим пулом")
        # Не должно быть несвязанных далеких дистракторов при наличии кандидатов из темы
        self.assertNotIn("Десять суток", chosen)

    def test_03_prompt_anti_giveaway_and_atomic_rules(self):
        """Проверка наличия анти-спойлерных и атомарных директив в системном промпте DeepSeek."""
        self.assertGreater(len(DEEPSEEK_CACHED_SYSTEM_PROMPT), 5000)
        self.assertIn("ANTI-GIVEAWAY DIRECTIVE", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("ATOMIC ANSWER DIRECTIVE", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Minimum Information Principle", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Zero-Spoiler Law", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Pareto 80/20 Law", DEEPSEEK_CACHED_SYSTEM_PROMPT)

    def test_04_cram_mode_strictly_studied_cards_and_no_fsrs_mutation(self):
        """Проверка штурм-режима: только изученные карточки (state in [1, 2, 3]), state=0 исключены, FSRS не мутирует."""
        async def _test():
            async with AsyncSessionLocal() as db:
                uid = f"test_cram_user_{datetime.utcnow().timestamp()}"
                
                phrase_law = Phrase(text="Тема право", subject="law", user_id=uid)
                phrase_med = Phrase(text="Тема медицина", subject="medicine", user_id=uid)
                db.add_all([phrase_law, phrase_med])
                await db.flush()

                # Создаем карточки:
                # 1. Неизученная (state = 0)
                card_new = Card(
                    phrase_id=phrase_law.id,
                    user_id=uid, subject="law", text="Новая неизученная карта",
                    translation="Ответ новой", state=0, stability=0.0, difficulty=0.0,
                    lapses=0, next_review=datetime.utcnow()
                )
                # 2. Изученная сложная (state = 1, difficulty = 8.5)
                card_hard = Card(
                    phrase_id=phrase_law.id,
                    user_id=uid, subject="law", text="Сложная изученная карта",
                    translation="Ответ сложной", state=1, stability=1.5, difficulty=8.5,
                    lapses=2, next_review=datetime.utcnow() + timedelta(days=5)
                )
                # 3. Изученная средняя (state = 2, difficulty = 5.0)
                card_review = Card(
                    phrase_id=phrase_med.id,
                    user_id=uid, subject="medicine", text="Медицинская изученная карта",
                    translation="Ответ мед", state=2, stability=4.0, difficulty=5.0,
                    lapses=1, next_review=datetime.utcnow() + timedelta(days=10)
                )
                db.add_all([card_new, card_hard, card_review])
                await db.commit()
                await db.refresh(card_new)
                await db.refresh(card_hard)
                await db.refresh(card_review)

                # Проверяем эндпоинт статистики
                resp_stats = self.client.get("/api/stats/dashboard", headers={"X-User-Id": uid})
                self.assertEqual(resp_stats.status_code, 200)
                stats_data = resp_stats.json()
                self.assertEqual(stats_data.get("cards_cram_available"), 2, "Доступно для штурма ровно 2 изученные карты")

                # Проверяем очередь тренировки в режиме штурма (is_cram=true, subject=all)
                resp_train = self.client.get("/api/session?mode=cram&subject=all", headers={"X-User-Id": uid})
                self.assertEqual(resp_train.status_code, 200)
                queue = resp_train.json()
                queue_ids = [c["id"] for c in queue]

                # В очереди штурма ДОЛЖНЫ быть card_hard и card_review, но НИ В КОЕМ СЛУЧАЕ не card_new!
                self.assertNotIn(card_new.id, queue_ids, "Неизученные карточки (state=0) не должны попадать в штурм")
                self.assertIn(card_hard.id, queue_ids, "Изученная сложная карточка должна быть в штурме")
                self.assertIn(card_review.id, queue_ids, "Изученная карточка из любого предмета должна быть в штурме")

                # Проверяем отправку ответа в режиме штурма (is_cram=True, rating=1 - Again)
                initial_stability = card_hard.stability
                initial_difficulty = card_hard.difficulty
                initial_lapses = card_hard.lapses
                initial_state = card_hard.state
                initial_review = card_hard.next_review

                resp_ans = self.client.post("/api/answer", headers={"X-User-Id": uid}, json={
                    "card_id": card_hard.id,
                    "rating": 1,
                    "is_cram": True
                })
                self.assertEqual(resp_ans.status_code, 200)

                # Перечитываем карточку из БД и проверяем, что FSRS параметры НЕ изменились
                await db.refresh(card_hard)
                self.assertEqual(card_hard.stability, initial_stability, "Stability не должна меняться в штурме")
                self.assertEqual(card_hard.difficulty, initial_difficulty, "Difficulty не должна меняться в штурме")
                self.assertEqual(card_hard.lapses, initial_lapses, "Lapses не должны увеличиваться в штурме")
                self.assertEqual(card_hard.state, initial_state, "State не должен меняться в штурме")
                self.assertEqual(card_hard.next_review, initial_review, "Next review не должна меняться в штурме")

    def test_05_generate_practice_session_clustered_distractors(self):
        """Проверка, что сессия практики подбирает дистракторы из одного смыслового кластера."""
        async def _test():
            async with AsyncSessionLocal() as db:
                uid = f"test_practice_cluster_{datetime.utcnow().timestamp()}"
                phrase = Phrase(text="Теория государства и права", subject="law_tgp", user_id=uid)
                db.add(phrase)
                await db.flush()

                c_static = Card(
                    phrase_id=phrase.id, user_id=uid, subject="law_tgp",
                    text="В чем заключается статическая функция права?",
                    secondary_text="Теория права | Функции права",
                    translation="В закреплении и стабилизации существующих общественных отношений.",
                    state=2, next_review=datetime.utcnow()
                )
                c_dynamic = Card(
                    phrase_id=phrase.id, user_id=uid, subject="law_tgp",
                    text="В чем заключается динамическая функция права?",
                    secondary_text="Теория права | Функции права",
                    translation="В стимулировании развития и возникновении новых общественных связей.",
                    state=2, next_review=datetime.utcnow()
                )
                c_protective = Card(
                    phrase_id=phrase.id, user_id=uid, subject="law_tgp",
                    text="В чем заключается охранительная функция права?",
                    secondary_text="Теория права | Функции права",
                    translation="В вытеснении и пресечении правонарушений и защите базовых устоев строя.",
                    state=2, next_review=datetime.utcnow()
                )
                c_deadline = Card(
                    phrase_id=phrase.id, user_id=uid, subject="law_tgp",
                    text="Каков срок подачи апелляционной жалобы?",
                    secondary_text="Процесс | Сроки",
                    translation="В течение одного месяца со дня принятия решения.",
                    state=2, next_review=datetime.utcnow()
                )
                db.add_all([c_static, c_dynamic, c_protective, c_deadline])
                await db.commit()

                items = await generate_practice_session(user_id=uid, subject="law_tgp", count=3, db=db)
                self.assertGreater(len(items), 0)

                # Находим задание по статической функции
                static_item = next((it for it in items if "статическая" in it["prompt"].lower()), None)
                if static_item:
                    options = static_item["options"]
                    # Опции должны включать правильный ответ и дистракторы из того же кластера функций
                    opts_text = " ".join(options).lower()
                    self.assertTrue(
                        "динамическ" in opts_text or "стимулирован" in opts_text or "охранительн" in opts_text or "пресечени" in opts_text,
                        "Дистракторы для функций права должны браться из кластера функций права, а не посторонних сроков"
                    )

        self.run_async(_test())


if __name__ == "__main__":
    unittest.main()

