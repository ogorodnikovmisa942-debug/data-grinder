# tests/test_card_quality_and_blacklist.py
"""
Unit tests for Card Quality Gate and Strict Blacklist Validation (R3, R4).
Verifies:
1. Rejection of tautologies (answers repeating the question words).
2. Rejection of binary Yes/No style questions.
3. Rejection of academic methodology fluff (synergetics, empirical vs theoretical methods).
4. Rejection of obsolete 1918-1930s historical decrees in modern law courses.
5. Rejection of clerical office trivia.
6. Acceptance of high-yield situational decision trees, contrast pairs, and procedural rules.
"""

import unittest
from app.services.ai_gateway import is_blacklisted_card, semantic_normalize_front


class TestCardQualityAndBlacklist(unittest.TestCase):
    def test_01_filter_tautology(self):
        """Проверка отсева тривиальных тавтологий."""
        bad_card = {
            "text": "Государственный орган создан специально для осуществления правоохранительной функции. К какой группе органов он относится?",
            "secondary_text": "Теория права | Правоохранительная функция",
            "translation": "К правоохранительным органам.",
            "example": ""
        }
        is_bl, reason = is_blacklisted_card(bad_card, subject_domain="law")
        self.assertTrue(is_bl, "Тавтология должна быть отфильтрована!")
        self.assertEqual(reason, "tautology")

    def test_02_filter_binary_yes_no(self):
        """Проверка отсева скрытых бинарных вопросов 'Да/Нет'."""
        bad_card = {
            "text": "В Республике Беларусь правовая доктрина официально признается источником права?",
            "secondary_text": "Судоустройство | Источники права",
            "translation": "Нет, правовая доктрина не признается источником права, но служит фундаментом.",
            "example": ""
        }
        is_bl, reason = is_blacklisted_card(bad_card, subject_domain="law")
        self.assertTrue(is_bl, "Вопросы с ответом 'Нет' должны отсеиваться!")
        self.assertEqual(reason, "binary_yes_no")

    def test_03_filter_academic_methodology_fluff(self):
        """Проверка отсева методологической воды учебников (синергетика, методы науки)."""
        card_synergetics = {
            "text": "Какой метод позволяет выявить процессы самоорганизации деятельности суда, влияние случайных факторов и возникновение порядка через флуктуации?",
            "secondary_text": "Судоустройство | Синергетика",
            "translation": "Синергетический метод.",
            "example": ""
        }
        is_bl, reason = is_blacklisted_card(card_synergetics, subject_domain="law")
        self.assertTrue(is_bl, "Синергетика в праве должна быть отфильтрована!")
        self.assertEqual(reason, "academic_methodology_fluff")

        card_methods = {
            "text": "Чем отличаются теоретические методы исследования судоустройства от эмпирических?",
            "secondary_text": "Судоустройство | Методология",
            "translation": "Теоретические методы основаны на логических построениях, а эмпирические — на фактах.",
            "example": ""
        }
        is_bl2, reason2 = is_blacklisted_card(card_methods, subject_domain="law")
        self.assertTrue(is_bl2, "Методология науки должна быть отфильтрована!")
        self.assertEqual(reason2, "academic_methodology_fluff")

    def test_04_filter_obsolete_historical_trivia(self):
        """Проверка отсева архивных декретов 1918-1930 гг. в прикладных юридических дисциплинах."""
        bad_card = {
            "text": "В 1918 г. ВЧК получила право применять внесудебные репрессии. Какое постановление СНК стало основанием для расстрела на месте?",
            "secondary_text": "Постановление СНК от 21 февраля 1918 г.",
            "translation": "Постановление СНК от 21 февраля 1918 г. «Социалистическое отечество в опасности!».",
            "example": ""
        }
        is_bl, reason = is_blacklisted_card(bad_card, subject_domain="law")
        self.assertTrue(is_bl, "Декреты 1918 года должны отсеиваться из курса судоустройства!")
        self.assertEqual(reason, "obsolete_historical_trivia")

    def test_05_filter_clerical_office_trivia(self):
        """Проверка отсева канцелярского делопроизводства."""
        bad_card = {
            "text": "Чем архивная справка отличается от архивной выписки?",
            "secondary_text": "Инструкция по делопроизводству | Виды архивных документов",
            "translation": "Справка подтверждает наличие сведений, а выписка дословно воспроизводит часть документа.",
            "example": ""
        }
        is_bl, reason = is_blacklisted_card(bad_card, subject_domain="law")
        self.assertTrue(is_bl, "Канцелярское делопроизводство должно отсеиваться!")
        self.assertEqual(reason, "clerical_office_trivia")

    def test_06_accept_high_yield_contrast_pair(self):
        """Проверка сохранения глубоких контраст-пар с золотым стандартом."""
        good_card = {
            "text": "Чем принципиально отличается роль народных заседателей от присяжных заседателей в судебном процессе?",
            "secondary_text": "Судоустройство | Состав суда",
            "translation": "Народные заседатели голосуют наравне с судьёй по всем вопросам (и вина, и мера наказания), а присяжные выносят только вердикт о виновности отдельно от профессионального судьи.",
            "example": "В коллегиях с народными заседателями судья не может единолично преодолеть их согласованное мнение."
        }
        is_bl, reason = is_blacklisted_card(good_card, subject_domain="law")
        self.assertFalse(is_bl, f"Качественная контраст-пара не должна отсеиваться: {reason}")

    def test_07_accept_situational_decision_tree(self):
        """Проверка сохранения ситуационных кейсов (развилок)."""
        good_card = {
            "text": "При рассмотрении дела суд применяет норму отраслевого закона, которая противоречит Конституции. Какое решение должен принять суд?",
            "secondary_text": "Конституция РБ | Высшая юридическая сила и прямое действие",
            "translation": "Суд обязан применить Конституцию, так как она обладает высшей юридической силой и прямым действием, а противоречащий акт не подлежит применению.",
            "example": "В случае коллизии между законом и Конституцией суд общей юрисдикции не вправе применять закон, противоречащий Конституции."
        }
        is_bl, reason = is_blacklisted_card(good_card, subject_domain="law")
        self.assertFalse(is_bl, f"Качественный ситуационный кейс не должен отсеиваться: {reason}")

    def test_08_semantic_deduplication_signature(self):
        """Проверка формирования инвариантного отпечатка для отсева дубликатов."""
        q1 = "Чем принципиально отличается роль народных заседателей от присяжных заседателей в судебном процессе?"
        q2 = "Чем отличается роль народных заседателей от присяжных заседателей в судебном процессе?"
        sig1 = semantic_normalize_front(q1)
        sig2 = semantic_normalize_front(q2)
        self.assertEqual(sig1, sig2, "Смысловые отпечатки перефразированных вопросов должны совпадать!")
        self.assertIn("народ", sig1)
        self.assertIn("прися", sig1)


if __name__ == "__main__":
    unittest.main()
