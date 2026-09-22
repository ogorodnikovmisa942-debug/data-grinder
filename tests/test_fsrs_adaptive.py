# tests/test_fsrs_adaptive.py
import unittest
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.fsrs_core import (
    calculate_adaptive_retention_factor,
    calculate_intervals,
)


class TestFSRSAdaptiveRetention(unittest.TestCase):
    """
    Тестирование адаптивной калибровки retention для алгоритма FSRS (Phase 10).
    """

    def test_cold_start_under_10_reviews(self):
        """Менее 10 обзоров возвращают фактор 1.0 (защита холодного старта)."""
        # Пустой список
        self.assertEqual(calculate_adaptive_retention_factor([]), 1.0)

        # 9 обзоров со 100% ошибками (Again)
        low_sample_fail = [{"rating": 1}] * 9
        self.assertEqual(calculate_adaptive_retention_factor(low_sample_fail), 1.0)

        # 9 обзоров со 100% успехом (Good/Easy)
        low_sample_success = [{"rating": 3}] * 9
        self.assertEqual(calculate_adaptive_retention_factor(low_sample_success), 1.0)

        # 5 обзоров с объектами
        objects_sample = [SimpleNamespace(rating=1)] * 5
        self.assertEqual(calculate_adaptive_retention_factor(objects_sample), 1.0)

    def test_optimal_retention_zone(self):
        """Retention в диапазоне 80%-95% возвращает фактор 1.0."""
        # Ровно 80% (16 из 20 успешных)
        reviews_80 = [{"rating": 3}] * 16 + [{"rating": 1}] * 4
        self.assertEqual(calculate_adaptive_retention_factor(reviews_80), 1.0)

        # 90% (18 из 20 успешных)
        reviews_90 = [{"rating": 3}] * 18 + [{"rating": 1}] * 2
        self.assertEqual(calculate_adaptive_retention_factor(reviews_90), 1.0)

        # Ровно 95% (19 из 20 успешных)
        reviews_95 = [{"rating": 3}] * 19 + [{"rating": 1}] * 1
        self.assertEqual(calculate_adaptive_retention_factor(reviews_95), 1.0)

    def test_low_retention_compresses_factor(self):
        """Retention < 80% уменьшает фактор (< 1.0, >= 0.75)."""
        # 50% lapses (10 из 20 успешных, 10 ошибок)
        reviews_50 = [{"rating": 3}] * 10 + [{"rating": 1}] * 10
        factor_50 = calculate_adaptive_retention_factor(reviews_50)
        self.assertLess(factor_50, 1.0)
        self.assertGreaterEqual(factor_50, 0.75)
        # 0.80 + (0.50 - 0.80) * 0.5 = 0.65 -> clamped to 0.75
        self.assertAlmostEqual(factor_50, 0.75)

        # 75% retention (15 из 20 успешных): 0.80 + (0.75 - 0.80) * 0.5 = 0.775
        reviews_75 = [{"rating": 4}] * 15 + [{"rating": 1}] * 5
        factor_75 = calculate_adaptive_retention_factor(reviews_75)
        self.assertLess(factor_75, 1.0)
        self.assertGreaterEqual(factor_75, 0.75)
        self.assertAlmostEqual(factor_75, 0.775)

        # 70% retention: 0.80 + (0.70 - 0.80) * 0.5 = 0.75
        reviews_70 = [{"rating": 2}] * 14 + [{"rating": 1}] * 6
        factor_70 = calculate_adaptive_retention_factor(reviews_70)
        self.assertAlmostEqual(factor_70, 0.75)

    def test_high_retention_expands_factor(self):
        """Retention > 95% увеличивает фактор (> 1.0, <= 1.25)."""
        # 100% retention (20 из 20 успешных)
        reviews_100 = [{"rating": 3}] * 20
        factor_100 = calculate_adaptive_retention_factor(reviews_100)
        self.assertGreater(factor_100, 1.0)
        self.assertLessEqual(factor_100, 1.25)
        # 1.0 + (1.0 - 0.95) * 2.0 = 1.10
        self.assertAlmostEqual(factor_100, 1.10)

        # 98% retention (49 из 50 успешных): 1.0 + (0.98 - 0.95) * 2.0 = 1.06
        reviews_98 = [{"rating": 4}] * 49 + [{"rating": 1}] * 1
        factor_98 = calculate_adaptive_retention_factor(reviews_98)
        self.assertGreater(factor_98, 1.0)
        self.assertLessEqual(factor_98, 1.25)
        self.assertAlmostEqual(factor_98, 1.06)

    def test_calculate_intervals_with_retention_factor_compressed(self):
        """calculate_intervals с retention_factor=0.8 дает более короткий интервал, чем с 1.0."""
        random.seed(42)
        now = datetime(2026, 9, 21, 12, 0, 0)
        card = SimpleNamespace(
            state=2,
            stability=60.0,
            difficulty=5.0,
            last_review=now - timedelta(days=10)
        )

        _, _, _, next_review_08, _ = calculate_intervals(
            card=card, rating=3, now=now, retention_factor=0.8
        )
        _, _, _, next_review_10, _ = calculate_intervals(
            card=card, rating=3, now=now, retention_factor=1.0
        )

        interval_08 = (next_review_08 - now).days
        interval_10 = (next_review_10 - now).days

        self.assertLess(interval_08, interval_10)

    def test_calculate_intervals_with_retention_factor_expanded(self):
        """calculate_intervals с retention_factor=1.2 дает более длинный интервал, чем с 1.0."""
        random.seed(42)
        now = datetime(2026, 9, 21, 12, 0, 0)
        card = SimpleNamespace(
            state=2,
            stability=60.0,
            difficulty=5.0,
            last_review=now - timedelta(days=10)
        )

        _, _, _, next_review_10, _ = calculate_intervals(
            card=card, rating=3, now=now, retention_factor=1.0
        )
        _, _, _, next_review_12, _ = calculate_intervals(
            card=card, rating=3, now=now, retention_factor=1.2
        )

        interval_10 = (next_review_10 - now).days
        interval_12 = (next_review_12 - now).days

        self.assertGreater(interval_12, interval_10)

    def test_minimum_interval_for_review_card_is_at_least_1_day(self):
        """Минимальный интервал для карточки повторения (state=2) всегда не менее 1 дня."""
        now = datetime(2026, 9, 21, 12, 0, 0)
        # Карточка с очень низкой стабильностью
        card = SimpleNamespace(
            state=2,
            stability=0.1,
            difficulty=9.0,
            last_review=now - timedelta(hours=1)
        )

        # Даже при агрессивном сжатии retention_factor
        _, _, state, next_review, _ = calculate_intervals(
            card=card, rating=2, now=now, retention_factor=0.70
        )

        self.assertEqual(state, 2)
        interval_days = (next_review - now).days
        self.assertGreaterEqual(interval_days, 1)

    def test_learning_steps_not_compressed(self):
        """Интервалы этапов обучения (state in (0, 1)) остаются внутридневными в минутах."""
        now = datetime(2026, 9, 21, 12, 0, 0)
        # Новая карточка (state=0), ответ Good (rating=3) -> должна быть запланирована через 30 минут
        new_card = SimpleNamespace(
            state=0,
            stability=0.0,
            difficulty=5.5,
            last_review=None
        )
        _, _, state, next_review, _ = calculate_intervals(
            card=new_card, rating=3, now=now, retention_factor=0.70
        )
        self.assertEqual(state, 1)
        self.assertEqual(next_review, now + timedelta(minutes=30))
