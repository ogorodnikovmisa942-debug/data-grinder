# tests/test_card_quality_and_blacklist.py
"""
Структурные проверки карточки, верные для любого предмета, и смысловые отпечатки для отсева дублей.
Запретов «по теме» (методология, даты, имена, области знаний) нет: что учить, решает книга.
"""

import unittest
from app.services.ai_gateway import is_structurally_invalid_card, semantic_normalize_front


class TestCardQualityAndBlacklist(unittest.TestCase):
    def test_01_filter_tautology(self):
        """Ответ повторяет слова вопроса — карточка ничего не проверяет."""
        bad_card = {
            "text": "Государственный орган создан специально для осуществления правоохранительной функции. К какой группе органов он относится?",
            "translation": "К правоохранительным органам.",
        }
        bad, reason = is_structurally_invalid_card(bad_card)
        self.assertTrue(bad, "Тавтология должна быть отфильтрована!")
        self.assertEqual(reason, "tautology")

    def test_02_filter_binary_yes_no(self):
        """Скрытые бинарные вопросы «Да/Нет» не принимаются."""
        bad_card = {
            "text": "Признаётся ли правовая доктрина официальным источником права?",
            "translation": "Нет, правовая доктрина не признаётся источником права, но служит фундаментом.",
        }
        bad, reason = is_structurally_invalid_card(bad_card)
        self.assertTrue(bad)
        self.assertEqual(reason, "binary_yes_no")

    def test_03_empty_or_too_short_cards_are_rejected(self):
        self.assertEqual(is_structurally_invalid_card({"text": "", "translation": "Ответ."}), (True, "empty_front_or_back"))
        self.assertEqual(is_structurally_invalid_card({"text": "Что?", "translation": "А"}), (True, "too_short"))

    def test_04_numbers_in_the_answer_are_information_not_echo(self):
        for q, a in (("Сколько функций имеет метод?", "Две функции."), ("На какой срок избирается совет?", "На 5 лет."),
                     ("Сколько видов законов выделял автор?", "Четыре вида законов.")):
            self.assertEqual(is_structurally_invalid_card({"text": q, "translation": a}), (False, ""), q)

    def test_05_cards_of_any_subject_are_accepted(self):
        """Методология, история, философия, право, физика, код: ничто из этого не отсеивается по теме."""
        for q, a in (
            ("Как называется наука о самоорганизации, совместном действии взаимосвязанных подсистем?", "Синергетика."),
            ("В каком году был принят Декрет о суде?", "В 1917 году."),
            ("Кто автор теории разделения властей?", "Шарль Монтескьё."),
            ("Чем отличается роль присяжных заседателей от роли народных заседателей?", "Присяжные выносят только вердикт о виновности."),
            ("Какая сила удерживает планеты на орбитах?", "Сила всемирного тяготения."),
            ("Какой метод списка возвращает последний элемент и удаляет его?", "Метод pop."),
        ):
            self.assertEqual(is_structurally_invalid_card({"text": q, "translation": a}), (False, ""), q)

    def test_08_semantic_deduplication_signature(self):
        """Инвариантный отпечаток для отсева дубликатов."""
        q1 = "Чем принципиально отличается роль народных заседателей от присяжных заседателей в судебном процессе?"
        q2 = "Чем отличается роль народных заседателей от присяжных заседателей в судебном процессе?"
        sig1 = semantic_normalize_front(q1)
        sig2 = semantic_normalize_front(q2)
        self.assertEqual(sig1, sig2, "Смысловые отпечатки перефразированных вопросов должны совпадать!")
        self.assertIn("народ", sig1)
        self.assertIn("прися", sig1)

    def test_09_semantic_deduplication_cross_phrasing(self):
        """Сопоставление отпечатков для синонимичных вводных конструкций."""
        q1 = "Чем принципиально отличается естественное право от позитивного права?"
        q2 = "В чем заключается ключевое различие между естественным и позитивным правом?"
        sig1 = semantic_normalize_front(q1)
        sig2 = semantic_normalize_front(q2)
        self.assertEqual(sig1, sig2, "Отпечатки вопросов с синонимичными формулировками должны совпадать!")
        self.assertIn("естес", sig1)
        self.assertIn("позит", sig1)

    def test_14_batch_deduplication(self):
        """Программное устранение точных и нечётких дубликатов."""
        from app.services.card_db_sync import deduplicate_cards_batch
        cards = [
            {"text": "Что проверяет суд кассационной инстанции?", "translation": "Законность судебных актов."},
            {"text": "1. Что проверяет суд кассационной инстанции?", "translation": "Законность судебных актов."},  # нумерация
            {"text": "Что проверяет суд кассационной инстанции", "translation": "Законность судебных актов."},   # без знака
            {"text": "Каковы полномочия кассации: что проверяет суд кассационной инстанции?", "translation": "Законность судебных актов."},
            {"text": "Какой орган назначает судей Конституционного Суда?", "translation": "Совет Федерации."}     # уникальная карточка
        ]
        deduped = deduplicate_cards_batch(cards)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["text"], "Что проверяет суд кассационной инстанции?")
        self.assertEqual(deduped[1]["text"], "Какой орган назначает судей Конституционного Суда?")


if __name__ == "__main__":
    unittest.main()
