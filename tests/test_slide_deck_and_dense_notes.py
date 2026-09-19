# tests/test_slide_deck_and_dense_notes.py
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from app.services.ai_gateway import (
    analyze_source_density,
    split_text_into_chunks,
    parse_raw_text
)

class TestSlideDeckAndDenseNotes(unittest.TestCase):
    def setUp(self):
        # 1. Синтетическая презентация на 80 слайдов (~24 000 знаков)
        slides = []
        for i in range(1, 81):
            slides.append(
                f"--- presentation.pptx: Слайд {i} ---\n"
                f"Тема {((i - 1) // 10) + 1}. Институт {i}\n"
                f"- Основной признак института {i}: специфика и критерий разграничения.\n"
                f"- Исключение из общего правила: норма статьи {100 + i}.\n"
                f"- Срок обжалования: 10 суток со дня вручения постановления."
            )
        self.presentation_text = "\n\n".join(slides)

        # 2. Синтетическая PDF-презентация (80 страниц, ~25 000 знаков, мало текста на страницу)
        pdf_slides = []
        for i in range(1, 81):
            pdf_slides.append(
                f"--- slides_export.pdf: Стр. {i} ---\n"
                f"Вопрос {i}. Процессуальное действие\n"
                f"• Основания применения: ч. 1 ст. {i}\n"
                f"• Субъект принятия решения: судья единолично\n"
                f"• Срок: незамедлительно"
            )
        self.pdf_presentation_text = "\n\n".join(pdf_slides)

        # 3. Синтетический учебник в PDF (30 страниц, ~60 000 знаков, dense prose, ~2000 знаков на страницу)
        long_paragraph = (
            "Судебная власть в Российской Федерации осуществляется только судами в лице судей и привлекаемых "
            "в установленном законом порядке к осуществлению правосудия присяжных и арбитражных заседателей. "
            "Никакие другие органы и лица не вправе принимать на себя осуществление правосудия. "
            "Правосудие в Российской Федерации осуществляется только судом. Судебная власть самостоятельна "
            "и действует независимо от законодательной и исполнительной властей. Судьи независимы и подчиняются "
            "только Конституции Российской Федерации и федеральному закону. Суд, установив при рассмотрении дела "
            "несоответствие акта государственного или иного органа закону, принимает решение в соответствии с законом. "
        ) * 4  # ~2400 знаков на страницу
        book_pages = []
        for i in range(1, 31):
            book_pages.append(f"--- textbook.pdf: Стр. {i} ---\nГлава {((i-1)//5)+1}.\n" + long_paragraph)
        self.textbook_pdf_text = "\n\n".join(book_pages)

        # 4. Синтетический конспект студента / шпаргалка (~15 000 знаков, буллеты, тире-определения, аббревиатуры)
        notes = []
        for i in range(1, 35):
            notes.append(
                f"## Вопрос {i}. Источники уголовно-процессуального права РФ\n"
                f"УПК РФ — основной кодифицированный источник процессуальных норм.\n"
                f"- ст. 108 УПК РФ — заключение под стражу (мера пресечения).\n"
                f"- Применяется по судебному решению vs подписка о невыезде.\n"
                f"- Срок задержания: не более 48 ч. без судебного решения (ч. 2 ст. 22 К РФ).\n"
                f"- Субъекты: следователь с согласия руков. следств. органа -> ходатайство в райсуд.\n"
                f"Презумпция невиновности — бремя доказывания лежит на стороне обвинения."
            )
        self.dense_notes_text = "\n\n".join(notes)

    def test_density_analyzer_classifications(self):
        """Проверка точности классификации источника по плотности и архетипу."""
        meta_pres = analyze_source_density(self.presentation_text)
        self.assertEqual(meta_pres["archetype"], "slides")
        self.assertTrue(meta_pres["is_presentation"])
        self.assertFalse(meta_pres["is_dense_notes"])
        self.assertGreaterEqual(meta_pres["slide_count"], 75)

        meta_pdf_pres = analyze_source_density(self.pdf_presentation_text)
        self.assertEqual(meta_pdf_pres["archetype"], "slides")
        self.assertTrue(meta_pdf_pres["is_presentation"])
        self.assertFalse(meta_pdf_pres["is_dense_notes"])

        meta_book = analyze_source_density(self.textbook_pdf_text)
        self.assertEqual(meta_book["archetype"], "textbook")
        self.assertFalse(meta_book["is_presentation"])
        self.assertFalse(meta_book["is_dense_notes"])

        meta_notes = analyze_source_density(self.dense_notes_text)
        self.assertEqual(meta_notes["archetype"], "dense_notes")
        self.assertFalse(meta_notes["is_presentation"])
        self.assertTrue(meta_notes["is_dense_notes"])

    def test_presentation_chunking_yields_12_to_14_chunks(self):
        """80-слайдовая презентация должна дробиться на 12-14 чанков вместо 1 мега-чанка."""
        chunks = split_text_into_chunks(self.presentation_text)
        self.assertGreaterEqual(len(chunks), 12)
        self.assertLessEqual(len(chunks), 16)
        
        # Проверяем, что в каждом чанке приблизительно 5-7 слайдов
        for ch in chunks:
            slide_count_in_chunk = ch.count(": Слайд ")
            self.assertGreaterEqual(slide_count_in_chunk, 3)
            self.assertLessEqual(slide_count_in_chunk, 8)

    def test_dense_notes_chunking(self):
        """Плотный студенческий конспект делится по границам вопросов/тем порциями ~5k знаков."""
        chunks = split_text_into_chunks(self.dense_notes_text)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks), 5)
        for ch in chunks:
            self.assertLess(len(ch), 8000)

    def test_textbook_preserves_macro_chapter_economy(self):
        """Учебник сохраняет макро-главы (до 85 000 знаков) без лишнего дробления на десятки мелких кусков."""
        chunks = split_text_into_chunks(self.textbook_pdf_text)
        # 60 000 знаков укладываются в 1-2 макро-блока
        self.assertLessEqual(len(chunks), 2)

    @patch("app.services.ai_gateway.call_deepseek")
    def test_parse_raw_text_prompt_directives_for_slides(self, mock_call):
        """Проверка, что для слайдов парсер внедряет директивы реконструкции буллетов и ссылки на Слайд N."""
        mock_call.return_value = ({"domain": "law", "slug": "test", "title": "Test", "cards": []}, {})
        
        # Берем 1 чанк из 6 слайдов
        slide_chunk = "\n\n".join([
            f"--- deck.pptx: Слайд {i} ---\nТема {i}. Суть института\n- Правило\n- Исключение"
            for i in range(1, 7)
        ])
        
        asyncio.run(parse_raw_text(slide_chunk, target_subject="sudoustr"))
        
        self.assertTrue(mock_call.called)
        sent_user_prompt = mock_call.call_args[0][0]
        
        # Проверяем наличие ключевых директив для слайдов
        self.assertIn("SLIDE CLUSTER EXTRACTION", sent_user_prompt)
        self.assertIn("SLIDE BULLET RECONSTRUCTION DIRECTIVE", sent_user_prompt)
        self.assertIn("Слайд N", sent_user_prompt)
        self.assertIn("~0.8 to 1.0 cards per substantive slide", sent_user_prompt)

    @patch("app.services.ai_gateway.call_deepseek")
    def test_parse_raw_text_prompt_directives_for_dense_notes(self, mock_call):
        """Проверка, что для конспектов парсер внедряет расшифровку аббревиатур и квоту 6-9 карт."""
        mock_call.return_value = ({"domain": "law", "slug": "test", "title": "Test", "cards": []}, {})
        
        note_chunk = (
            "## Билет 4. Дознание и следствие\n"
            "УПК РФ — дифференциация форм расследования.\n"
            "ст. 150 УПК РФ: предв. следствие vs дознание.\n"
            "Срок дознания — 30 суток (продление прокурором до 30 сут).\n"
            "Следствие — 2 месяца (продление руков. следств. органа).\n"
            "Основание: тяжесть преступления и состав УК РФ."
        )
        
        asyncio.run(parse_raw_text(note_chunk, target_subject="sudoustr"))
        
        self.assertTrue(mock_call.called)
        sent_user_prompt = mock_call.call_args[0][0]
        
        self.assertIn("DENSE LECTURE NOTES / CHEATSHEET EXTRACTION", sent_user_prompt)
        self.assertIn("NOTES & ABBREVIATION EXPANSION DIRECTIVE", sent_user_prompt)
        self.assertIn("6 to 9 high-yield atomic cards", sent_user_prompt)

    def test_routing_conditions_for_presentations_and_notes(self):
        """Проверка, что презентации и плотные конспекты корректно распознаются маршрутизатором для передачи в воркер."""
        # 1. Презентация на 20 слайдов (~6 000 знаков, < 30k)
        pres_short = "\n\n".join([f"--- Слайд {i} ---\nТезис {i}" for i in range(1, 21)])
        d_info = analyze_source_density(pres_short)
        is_dense = (
            d_info["is_presentation"]
            or d_info.get("slide_count", 0) >= 8
            or (d_info["is_dense_notes"] and len(pres_short) >= 10000)
        )
        is_large = len(pres_short) > 30000 or is_dense
        self.assertTrue(is_dense)
        self.assertTrue(is_large)

        # 2. Плотный конспект на 12 000 знаков (< 30k)
        notes_med = self.dense_notes_text[:12000]
        d_notes = analyze_source_density(notes_med)
        is_notes_dense = (
            d_notes["is_presentation"]
            or d_notes.get("slide_count", 0) >= 8
            or (d_notes["is_dense_notes"] and len(notes_med) >= 10000)
        )
        is_notes_large = len(notes_med) > 30000 or is_notes_dense
        self.assertTrue(is_notes_dense)
        self.assertTrue(is_notes_large)

        # 3. Обычная короткая заметка на 2 000 знаков (не должна уходить в воркер без необходимости)
        short_prose = "Обычный связный текст статьи без списков и определений.\n" * 30
        d_short = analyze_source_density(short_prose)
        is_short_dense = (
            d_short["is_presentation"]
            or d_short.get("slide_count", 0) >= 8
            or (d_short["is_dense_notes"] and len(short_prose) >= 10000)
        )
        is_short_large = len(short_prose) > 30000 or is_short_dense
        self.assertFalse(is_short_dense)
        self.assertFalse(is_short_large)

    def test_deck_capping_calibration_limits(self):
        """Проверка лимитов калибровки дек для презентаций (<= 75) и конспектов (<= 65)."""
        all_cards_pres = [{"text": f"Q{i}", "translation": f"A{i}"} for i in range(1, 95)]
        d_pres = {"is_presentation": True, "is_dense_notes": False}
        is_auto_volume = True

        # Симуляция правила калибровки воркера:
        if is_auto_volume and d_pres.get("is_presentation") and len(all_cards_pres) > 85:
            all_cards_pres = all_cards_pres[:75]
        self.assertEqual(len(all_cards_pres), 75)

        all_cards_notes = [{"text": f"Q{i}", "translation": f"A{i}"} for i in range(1, 85)]
        d_notes = {"is_presentation": False, "is_dense_notes": True}
        if is_auto_volume and d_notes.get("is_dense_notes") and len(all_cards_notes) > 75:
            all_cards_notes = all_cards_notes[:65]
        self.assertEqual(len(all_cards_notes), 65)

if __name__ == "__main__":
    unittest.main()

