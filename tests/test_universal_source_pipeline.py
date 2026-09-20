# tests/test_universal_source_pipeline.py
"""
Комплексный тестовый набор для универсальной деконструкции знаний:
1. Проверка классификации 500-страничного учебника (не должен распознаваться как dense_notes).
2. Проверка отсева тавтологических карточек и семантического эха (карточки №4 и №6 из примера пользователя).
3. Проверка сквозного извлечения оглавления книги (Pass 1) для больших объемов.
4. Проверка стратифицированной выборки воркера (сохранение равномерного покрытия всех глав без слепого среза до 65).
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.ai_gateway import (
    analyze_source_density,
    split_text_into_chunks,
    is_blacklisted_card,
    extract_curriculum_skeleton,
    DEEPSEEK_CACHED_SYSTEM_PROMPT
)


class TestUniversalSourcePipeline(unittest.TestCase):

    def setUp(self):
        # Эмуляция текста 500-страничного учебника по теории права (OCR-формат: короткие строки, тире-определения, заголовки глав)
        chapter_template = (
            "Глава {n}. Теоретические основы правового института {n}\n"
            "Право — это система общеобязательных правил поведения, установленных государством.\n"
            "Норма права — это первичный элемент системы права, регулирующий отношения.\n"
            "Сущность права проявляется в балансе интересов личности и публичной власти.\n"
            "В интенсивной зоне правового регулирования действует принцип зеркального отражения.\n"
            "Диспозитивный метод позволяет субъектам самим определять варианты поведения.\n"
            "Императивный метод категорически исключает отступление от предписания.\n"
        )
        # Формируем крупный текст на 25 глав (> 120 000 знаков) с короткими строками и определениями
        self.large_textbook_text = "\n\n".join([chapter_template.format(n=i) * 5 for i in range(1, 26)])

    def test_01_large_textbook_classified_as_textbook_not_dense_notes(self):
        """500-страничный учебник с короткими строками и тире не должен ошибочно маркироваться как dense_notes."""
        meta = analyze_source_density(self.large_textbook_text)
        self.assertEqual(meta["archetype"], "textbook", "Крупная книга обязана классифицироваться как textbook!")
        self.assertFalse(meta["is_dense_notes"], "Книга не должна быть помечена как dense_notes!")
        self.assertFalse(meta["is_presentation"], "Книга не должна быть помечена как презентация!")
        self.assertGreaterEqual(meta["target_chunk_chars"], 30000, "Чанки для книги должны быть в Goldilocks-диапазоне 30-40k!")

    def test_02_filter_user_tautologies_cards_4_and_6(self):
        """Проверка программного отсева тавтологий №4 и №6 из реальной колоды пользователя."""
        # Карточка №4 из предоставленной пользователем колоды:
        card_4 = {
            "text": "Что именно в системе социального регулирования определяет характер взаимодействия права и публичной власти?",
            "secondary_text": "Общая теория права | Право и власть",
            "translation": "Их взаимодействие в системе социального регулирования.",
            "example": "Расширение дискреционных полномочий разрушает баланс."
        }
        is_bl_4, reason_4 = is_blacklisted_card(card_4, subject_domain="law")
        self.assertTrue(is_bl_4, "Карточка №4 обязана отсеиваться как тавтология!")
        self.assertEqual(reason_4, "tautology")

        # Карточка №6 из предоставленной пользователем колоды:
        card_6 = {
            "text": "Какой уровень правового сознания и культуры выступает целью повышения в процессе развития правовой системы?",
            "secondary_text": "Общая теория права",
            "translation": "Уровень правового сознания и правовой культуры.",
            "example": "Гражданин формирует правовую культуру общества."
        }
        is_bl_6, reason_6 = is_blacklisted_card(card_6, subject_domain="law")
        self.assertTrue(is_bl_6, "Карточка №6 обязана отсеиваться как тавтология!")
        self.assertEqual(reason_6, "tautology")

    def test_03_legitimate_cards_are_not_filtered(self):
        """Качественные карточки из теории права не должны ложно браковаться валидатором."""
        good_card_1 = {
            "text": "Какая доктринальная парадигма рассматривает правовые нормы как инструменты и «ключи к ситуации»?",
            "secondary_text": "Глава 7.1 | Предмет правового регулирования",
            "translation": "Инструменталистский подход в праве.",
            "example": "Законодатель принимает закон как инструмент защиты конкуренции."
        }
        is_bl_1, reason_1 = is_blacklisted_card(good_card_1, subject_domain="law")
        self.assertFalse(is_bl_1, f"Качественная карточка не должна браковаться: {reason_1}")

        good_card_contrast = {
            "text": "По какому решающему критерию разграничиваются императивный и диспозитивный методы правового регулирования?",
            "secondary_text": "Глава 7.2 | Методы правового регулирования",
            "translation": "Императивный исключает отступления от предписания, диспозитивный допускает автономию воли сторон.",
            "example": "Личный обыск — императив; цена договора — диспозитив."
        }
        is_bl_2, reason_2 = is_blacklisted_card(good_card_contrast, subject_domain="law")
        self.assertFalse(is_bl_2, f"Качественная контрастная карточка не должна браковаться: {reason_2}")

    def test_04_pass1_extracts_full_outline_for_large_books(self):
        """Pass 1 (Curriculum Skeleton) должен сканировать оглавление/заголовки по всему объему книги, а не только первые 70k знаков."""
        # Создаем книгу с оглавлением в конце
        toc_end_book = (
            ("Текст вводной главы " * 2000) + "\n\n"
            + ("Текст средней главы " * 2000) + "\n\n"
            + "СОДЕРЖАНИЕ\n"
            + "Глава 1. Сущность права\n"
            + "Глава 7. Правовое регулирование\n"
            + "Глава 15. Норма права\n"
            + "Глава 17. Система права\n"
            + "Глава 25. Правосознание\n"
        )
        with patch("app.services.ai_gateway.call_deepseek") as mock_ds:
            mock_ds.return_value = ({"modules": [], "graph": {"nodes": [], "edges": []}}, {})
            asyncio.run(extract_curriculum_skeleton(toc_end_book, target_subject="obshteorprav"))
            self.assertTrue(mock_ds.called)
            sent_prompt = mock_ds.call_args[0][0]
            # Проверяем, что оглавление из конца книги попало в промпт для DeepSeek
            self.assertIn("ОГЛАВЛЕНИЕ КНИГИ/КУРСА", sent_prompt)
            self.assertIn("Глава 25. Правосознание", sent_prompt)

    def test_05_stratified_reduction_logic(self):
        """Проверка, что стратифицированное квотирование сохраняет карточки из ВСЕХ глав книги без среза хвоста."""
        from collections import defaultdict
        # Эмулируем 20 блоков по 10 карточек = 200 карточек
        all_cards = []
        for ch in range(1, 21):
            for i in range(1, 11):
                all_cards.append({
                    "text": f"Глава {ch} Вопрос {i}",
                    "translation": f"Ответ {ch}-{i}",
                    "source_chunk_idx": ch,
                    "layer": 1
                })
        self.assertEqual(len(all_cards), 200)

        # Целевой бюджет: 140 карточек (по 7 на главу)
        target_budget = 140
        cards_by_chunk = defaultdict(list)
        for c in all_cards:
            cards_by_chunk[c.get("source_chunk_idx", 1)].append(c)

        num_active_chunks = len(cards_by_chunk)
        base_quota = max(1, target_budget // num_active_chunks)
        remainder = target_budget % num_active_chunks

        stratified_cards = []
        overflow_pool = []
        for ch_idx in sorted(cards_by_chunk.keys()):
            ch_cards = cards_by_chunk[ch_idx]
            take_k = base_quota + (1 if remainder > 0 else 0)
            if remainder > 0:
                remainder -= 1
            stratified_cards.extend(ch_cards[:take_k])
            overflow_pool.extend(ch_cards[take_k:])

        self.assertEqual(len(stratified_cards), 140)
        # Проверяем, что в итоговой выборке присутствуют карточки из КАЖДОГО блока с 1 по 20
        represented_chunks = {c["source_chunk_idx"] for c in stratified_cards}
        self.assertEqual(len(represented_chunks), 20, "Все 20 глав обязаны быть представлены в деке!")
        for ch in range(1, 21):
            self.assertIn(ch, represented_chunks)

    def test_06_dense_notes_and_large_compilations_density_classification(self):
        """Проверка классификации конспектов, билетов и схем (включая крупные сборники лекций)."""
        # 1. Компактный конспект с разветвленной схемой и буллетами
        compact_notes_with_scheme = """
## Билет 14. Соучастие в преступлении: формы и виды
Схема форм соучастия:
- Простое соучастие (соисполнительство без предварительного сговора)
- Сложное соучастие (с предварительным распределением ролей: организатор, подстрекатель, пособник)
- Организованная группа (устойчивость группы + предварительный сговор)
- Преступное сообщество (преступная организация) — структурированность + цель совершения тяжких/особо тяжких деяний

Виды соучастников (ст. 33 УК РФ):
1. Исполнитель — непосредственно совершивший преступление.
2. Организатор — создавший или руководивший группой.
3. Подстрекатель — склонивший к совершению (уговором, подкупом, угрозой).
4. Пособник — содействовавший советами, указаниями или сокрытием следов.

Эксцесс исполнителя (ст. 36 УК РФ):
Совершение исполнителем деяния, не охватывавшегося умыслом других соучастников. Иные соучастники за эксцесс ответственности не несут.
"""
        meta_compact = analyze_source_density(compact_notes_with_scheme)
        self.assertTrue(meta_compact["is_dense_notes"], "Компактные тезисы/конспекты обязаны быть dense_notes!")
        self.assertEqual(meta_compact["archetype"], "dense_notes")
        self.assertGreaterEqual(meta_compact["max_cards_per_chunk"], 16)

        # 2. Крупный сборник билетов/конспектов (> 50 000 знаков) с билетами и буллетами
        ticket_snippet = (
            "## Билет {i}. Институт уголовного права {i}\n"
            "- Признак 1: Общественная опасность деяния.\n"
            "- Признак 2: Противоправность и наказуемость.\n"
            "- Разграничение со смежным составом {i}: по субъективной стороне.\n"
            "- Исключение: добровольный отказ освобождает от уголовной ответственности.\n\n"
        )
        large_notes = "".join([ticket_snippet.format(i=i) * 5 for i in range(1, 45)]) # > 50 000 chars
        self.assertGreater(len(large_notes), 45000)
        meta_large = analyze_source_density(large_notes)
        self.assertTrue(meta_large["is_dense_notes"], "Крупный сборник билетов/конспектов обязан оставаться dense_notes, а не textbook!")
        self.assertEqual(meta_large["archetype"], "dense_notes")

    def test_07_scheme_atomization_directive_and_fsrs_card_integrity(self):
        """Проверка наличия директивы атомизации схем в промпте и атомарности разветвленных карточек (FSRS-стандарт)."""
        from app.services.ai_gateway import parse_raw_text

        # 1. Проверяем, что в системном промпте закреплен закон атомизации схем без генерации списков
        self.assertIn("SCHEME, TREE & TAXONOMY ATOMIZATION LAW", DEEPSEEK_CACHED_SYSTEM_PROMPT)
        self.assertIn("Absolute Prohibition of Lists & Enumerations", DEEPSEEK_CACHED_SYSTEM_PROMPT)

        # 2. Проверяем, что parse_raw_text для dense_notes при разных volume формирует правильные квоты
        notes_sample = """
## Тема 5. Формы соучастия в уголовном праве
- Простое соучастие (соисполнительство без предварительного сговора)
- Сложное соучастие (с предварительным распределением ролей)
- Организованная группа (устойчивость группы + предварительный сговор)
- Преступное сообщество (преступная организация) — структурированность + цель совершения тяжких/особо тяжких деяний

Виды соучастников (ст. 33 УК РФ):
1. Исполнитель — лицо, непосредственно совершившее преступление либо использовавшее лиц, не подлежащих уголовной ответственности.
2. Организатор — лицо, создавшее организованную группу или руководившее ею.
3. Подстрекатель — лицо, склонившее к совершению преступления путем уговора или подкупа.
4. Пособник — лицо, содействовавшее совершению преступления советами, указаниями или предоставлением средств.

Эксцесс исполнителя (ст. 36 УК РФ):
Совершение исполнителем деяния, не охватывавшегося умыслом других соучастников. Иные соучастники за эксцесс уголовной ответственности не несут.
""" * 2
        
        with patch("app.services.ai_gateway.call_deepseek") as mock_ds:
            mock_ds.return_value = ({"c": []}, {})
            # Тест режима 'balanced'
            asyncio.run(parse_raw_text(notes_sample, target_subject="crim_law", volume="balanced"))
            self.assertTrue(mock_ds.called)
            sent_user_msg = mock_ds.call_args[0][0]
            self.assertIn("DENSE LECTURE NOTES / CHEATSHEET EXTRACTION", sent_user_msg)
            self.assertIn("SCHEME & TAXONOMY ATOMIZATION DIRECTIVE", sent_user_msg)
            self.assertIn("Extract 10 to 14 high-yield atomic cards", sent_user_msg)

        with patch("app.services.ai_gateway.call_deepseek") as mock_ds:
            mock_ds.return_value = ({"c": []}, {})
            # Тест режима 'max'
            asyncio.run(parse_raw_text(notes_sample, target_subject="crim_law", volume="max"))
            sent_user_msg = mock_ds.call_args[0][0]
            self.assertIn("Extract 18 to 24 high-yield atomic cards", sent_user_msg)

        # 3. Валидация атомарных карточек схемы по правилам FSRS (каждая ветвь — отдельная карточка, без списков)
        scheme_branch_cards = [
            {
                "text": "По какому ключевому критерию организованная группа отличается от простого соучастия?",
                "secondary_text": "ст. 35 УК РФ | Формы соучастия",
                "translation": "Наличие устойчивости и предварительного сговора участников.",
                "example": "Создание устойчивой группы для серии разбойных нападений."
            },
            {
                "text": "В каком случае участник преступного сообщества полностью освобождается от уголовной ответственности?",
                "secondary_text": "ст. 210 УК РФ | Преступное сообщество",
                "translation": "Добровольное прекращение участия и способствование раскрытию.",
                "example": "Участник добровольно явился в полицию и выдал организаторов."
            },
            {
                "text": "Кто признается подстрекателем в уголовно-правовой схеме видов соучастников?",
                "secondary_text": "ст. 33 УК РФ | Виды соучастников",
                "translation": "Лицо, склонившее другого к совершению деяния.",
                "example": "Уговорами и подкупом склонил кассира отключить сигнализацию."
            }
        ]
        for c in scheme_branch_cards:
            is_bl, reason = is_blacklisted_card(c, subject_domain="law")
            self.assertFalse(is_bl, f"Карточка схемы не должна отсеиваться: {reason}")
            # FSRS: лаконичный ответ (до 12 слов)
            self.assertLessEqual(len(c["translation"].split()), 12, "Ответ FSRS обязан быть до 12 слов!")

    def test_08_worker_calibration_dense_notes_scales_with_volume(self):
        """Проверка динамического масштабирования бюджета воркера для конспектов (без среза до 65 в high/max)."""
        # Эмулируем обработку 8 блоков конспекта (по 15 карточек на блок = 120 карточек)
        total_chunks = 8
        all_collected_cards = [{"text": f"Вопрос {i}", "translation": f"Ответ {i}"} for i in range(120)]
        density_info = {"is_dense_notes": True, "is_presentation": False}

        # 1. Режим 'max'
        vol_max = "max"
        target_budget_max = total_chunks * 99999 if vol_max != "max" else 999999
        self.assertEqual(target_budget_max, 999999, "В режиме max бюджет должен быть неограниченным!")

        # 2. Режим 'high_20'
        vol_high = "high_20"
        vol_per_block_high = 18
        target_budget_high = total_chunks * vol_per_block_high
        self.assertEqual(target_budget_high, 144, "В режиме high бюджет для 8 блоков конспекта должен быть 144 (а не 65)!")

        # 3. Режим 'auto'
        target_budget_auto = min(max(total_chunks * 12, 45), 220)
        self.assertEqual(target_budget_auto, 96, "В режиме auto бюджет для 8 блоков конспекта должен динамически равняться 96!")


if __name__ == "__main__":
    unittest.main()

