# tests/test_curriculum_and_plain_language.py
import unittest
import asyncio
from datetime import datetime
from app.services.ai_gateway import (
    unpack_minified_cards,
    is_blacklisted_card,
    CURRICULUM_SKELETON_SYSTEM_PROMPT,
    DEEPSEEK_CACHED_SYSTEM_PROMPT
)
from app.database.session import AsyncSessionLocal
from app.database.models import Card, Phrase, UserSession
from app.api.endpoints.train import get_session_cards

class TestCurriculumAndPlainLanguage(unittest.IsolatedAsyncioTestCase):

    def test_01_unpack_minified_cards_with_organ_and_topological_rank(self):
        """Проверка распаковки organ_slug, layer и автоматического топологического ранжирования."""
        payload = {
            "domain": "law",
            "slug": "sudoustroystvo",
            "title": "Судебная система",
            "c": [
                {
                    "t": "Какое место занимает районный суд в судебной системе?",
                    "s": "Судоустройство | Звенья судебной системы",
                    "d": "Основное низовое звено судов общей юрисдикции, действующее как суд первой инстанции.",
                    "e": "Любой гражданин подает стандартный иск о возмещении ущерба именно в районный суд.",
                    "l": "easy",
                    "h": "Районные суды",
                    "o": "district_court",
                    "y": 0
                },
                {
                    "t": "В каком составе районный суд рассматривает уголовные дела несовершеннолетних?",
                    "s": "УПК | Составы судов",
                    "d": "Коллегия в составе судьи и двух народных заседателей.",
                    "e": "Если 16-летний подросток обвиняется в краже, его дело слушают судья и два заседателя.",
                    "l": "medium",
                    "h": "Районные суды",
                    "o": "district_court",
                    "y": 1
                },
                {
                    "t": "Куда и в какой срок обжалуются не вступившие в силу решения районного суда?",
                    "s": "ГПК / УПК | Апелляционное производство",
                    "d": "В областной суд в апелляционном порядке в течение 10 суток со дня вынесения.",
                    "e": "Проигравшая сторона в 10-дневный срок направляет жалобу в областной суд.",
                    "l": "medium",
                    "h": "Районные суды",
                    "o": "district_court",
                    "y": 2
                },
                {
                    "t": "В качестве каких инстанций выступает областной суд?",
                    "s": "Судоустройство | Компетенция областного суда",
                    "d": "Суд первой инстанции (по особо тяжким делам), суд апелляционной инстанции и надзорная инстанция (Президиум).",
                    "e": "Областной суд проверяет решения районных судов, а сам судит за бандитизм и убийства при отягчающих.",
                    "l": "easy",
                    "h": "Областные суды",
                    "o": "regional_court",
                    "y": 0
                }
            ]
        }
        res = unpack_minified_cards(payload, fallback_subject="sudoustroystvo")
        cards = res["cards"]
        self.assertEqual(len(cards), 4)

        # Проверяем сохранение organ_slug и layer
        self.assertEqual(cards[0]["organ_slug"], "district_court")
        self.assertEqual(cards[0]["layer"], 0)
        self.assertEqual(cards[1]["organ_slug"], "district_court")
        self.assertEqual(cards[1]["layer"], 1)
        self.assertEqual(cards[3]["organ_slug"], "regional_court")
        self.assertEqual(cards[3]["layer"], 0)

        # Проверяем топологический ранг: от 1 до 4 строго по порядку органов и слоев
        ranks = [c["topological_rank"] for c in cards]
        self.assertEqual(ranks, [1, 2, 3, 4])

    def test_02_filter_meta_course_trivia(self):
        """Проверка отсева мета-вопросов об учебнике ('на какие три части делится курс')."""
        card_meta = {
            "text": "Какие три части условно выделяются в системе курса «Судоустройство»?",
            "translation": "Общая, специальная и особенная части.",
            "secondary_text": "Структура учебной дисциплины"
        }
        is_bl, reason = is_blacklisted_card(card_meta, subject_domain="law")
        self.assertTrue(is_bl)
        self.assertEqual(reason, "meta_course_trivia")

    def test_03_domain_aware_scope_governance(self):
        """
        Проверка контекстно-зависимой фильтрации:
        - В прикладном праве философия (Монтескье vs Локк) — это шум (отсекается).
        - В философии учение мыслителей — это ядро (сохраняется).
        - В философии биографическая шелуха ('в каком году родился') — отсекается.
        """
        # 1. Философия во вводной главе Права (должна отсечься)
        law_card_with_philosophy = {
            "text": "Чем принципиально отличается подход Ш. Монтескье к судебной власти от подхода Дж. Локка?",
            "translation": "Локк считал её элементом исполнительной власти, а Монтескье провозгласил самостоятельной ветвью.",
            "secondary_text": "История учений"
        }
        is_bl_law, reason_law = is_blacklisted_card(law_card_with_philosophy, subject_domain="law")
        self.assertTrue(is_bl_law)
        self.assertEqual(reason_law, "out_of_domain_intro_theory")

        # 2. Та же тема в курсе Философии / Истории учений (должна сохраниться!)
        is_bl_phil, _ = is_blacklisted_card(law_card_with_philosophy, subject_domain="philosophy")
        self.assertFalse(is_bl_phil)

        # 3. Субстантивная карточка по этике в курсе философии (должна сохраниться!)
        kant_card = {
            "text": "В чём состоит категорический императив И. Канта?",
            "translation": "Поступай только по такому правилу, которое ты можешь желать видеть всеобщим законом для всех людей.",
            "secondary_text": "Этика | Немецкая классическая философия"
        }
        is_bl_kant, _ = is_blacklisted_card(kant_card, subject_domain="philosophy")
        self.assertFalse(is_bl_kant)

        # 4. Пустая биографическая шелуха в философии (должна отсечься!)
        bio_card = {
            "text": "В каком году родился Иммануил Кант?",
            "translation": "В 1724 году.",
            "secondary_text": "Биография мыслителя"
        }
        is_bl_bio, reason_bio = is_blacklisted_card(bio_card, subject_domain="philosophy")
        self.assertTrue(is_bl_bio)
        self.assertEqual(reason_bio, "biographical_trivia")

    def test_04_system_prompts_contain_feynman_and_curriculum_directives(self):
        """Проверка наличия директив Фейнмана, топологических слоев и Прохода 1 в системных промптах."""
        self.assertIn("Rule 7: Plain Language & Intuitive Example Directive (Feynman Principle)", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("DISCIPLINE-AWARE SCOPE GOVERNANCE", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("organ_slug or module_slug", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("CHIEF EDUCATIONAL ARCHITECT", CURRICULUM_SKELETON_SYSTEM_PROMPT.upper())
        self.assertIn("quota", CURRICULUM_SKELETON_SYSTEM_PROMPT)

    async def test_05_study_session_topological_rank_ordering(self):
        """Интеграционный тест: проверка выдачи новых карточек строго по возрастанию topological_rank."""
        test_user = "test_topological_user_42"
        test_subject = "test_curriculum_law"
        now = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            phrase = Phrase(text="Тестовый каркас", subject=test_subject, user_id=test_user)
            db.add(phrase)
            await db.flush()

            # Создаем 3 карточки с перемешанными рангами (3, 1, 2)
            c3 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка третьего уровня (Обжалование)",
                translation="Определение областного суда.",
                topological_rank=3,
                organ_slug="district_court",
                layer=2,
                state=0,
                next_review=now
            )
            c1 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка первого уровня (Определение органа)",
                translation="Районный суд — основное звено.",
                topological_rank=1,
                organ_slug="district_court",
                layer=0,
                state=0,
                next_review=now
            )
            c2 = Card(
                phrase_id=phrase.id,
                user_id=test_user,
                subject=test_subject,
                text="Карточка второго уровня (Состав суда)",
                translation="Судья и 2 народных заседателя.",
                topological_rank=2,
                organ_slug="district_court",
                layer=1,
                state=0,
                next_review=now
            )
            db.add_all([c3, c1, c2])
            await db.commit()

        # Вызываем endpoint сессии обучения
        async with AsyncSessionLocal() as db:
            session_cards = await get_session_cards(
                subject=test_subject,
                mode="new",
                current_user=test_user,
                db=db
            )

        # Очищаем тестовые данные
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(delete(Card).where(Card.user_id == test_user))
            await db.execute(delete(Phrase).where(Phrase.user_id == test_user))
            await db.commit()

        # Проверяем, что карты вернулись строго 1 -> 2 -> 3
        self.assertEqual(len(session_cards), 3)
        self.assertEqual(session_cards[0]["topological_rank"], 1)
        self.assertEqual(session_cards[0]["text"], "Карточка первого уровня (Определение органа)")
        self.assertEqual(session_cards[1]["topological_rank"], 2)
        self.assertEqual(session_cards[1]["text"], "Карточка второго уровня (Состав суда)")
        self.assertEqual(session_cards[2]["topological_rank"], 3)
        self.assertEqual(session_cards[2]["text"], "Карточка третьего уровня (Обжалование)")

if __name__ == "__main__":
    unittest.main()
