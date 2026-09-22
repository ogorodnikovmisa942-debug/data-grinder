# app/services/fsrs_core.py
import math
import random
from datetime import datetime, timedelta

# Стандартные веса FSRS v4 для базовой настройки интервалов
W = [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.28, 2.61]

def calculate_target_interval(stability: float, target_retention: float) -> int:
    """
    Рассчитывает интервал в днях на основе стабильности (S) и целевого Retention (R).
    Формула FSRS: I = S * (ln(R) / ln(0.9))
    При R=0.9 множитель = 1.0. При R=0.8 интервалы длиннее (~2.1x), при R=0.95 — короче (~0.49x).
    """
    safe_r = max(0.70, min(0.98, target_retention))
    factor = math.log(safe_r) / math.log(0.9)
    raw_interval = stability * factor
    return max(1, round(raw_interval))

def apply_fuzz(interval_days: int) -> int:
    """
    Добавляет псевдослучайный джиттер (+/- 8%) к интервалу повторения,
    чтобы исключить наслоение 'лавин карточек' на один день.
    """
    if interval_days <= 2:
        return max(1, interval_days)
    fuzz = random.uniform(-0.08, 0.08)
    return max(1, round(interval_days * (1.0 + fuzz)))

def calculate_adaptive_retention_factor(review_logs: list) -> float:
    """
    Рассчитывает адаптивный множитель интервалов на основе фактического retention пользователя.
    Вход: список словарей или объектов с атрибутом/ключом rating (1=Again, 2=Hard, 3=Good, 4=Easy).
    - При len(review_logs) < 10: возврат 1.0 (cold-start защита).
    - Retention = successful_reviews / total_reviews (rating >= 2 — успех, rating == 1 — ошибка).
    - При retention < 0.80: 0.80 + (retention - 0.80) * 0.5, минимум 0.75.
    - При retention > 0.95: 1.0 + (retention - 0.95) * 2.0, максимум 1.25.
    - При 0.80 <= retention <= 0.95: 1.0.
    """
    if not review_logs or len(review_logs) < 10:
        return 1.0

    successful_reviews = 0
    total_reviews = len(review_logs)

    for item in review_logs:
        if isinstance(item, dict):
            rating = item.get("rating")
        elif hasattr(item, "rating"):
            rating = item.rating
        elif isinstance(item, (int, float)):
            rating = item
        else:
            rating = None

        if rating is not None and rating >= 2:
            successful_reviews += 1

    retention = successful_reviews / total_reviews

    if retention < 0.80:
        factor = 0.80 + (retention - 0.80) * 0.5
        return max(0.75, float(factor))
    elif retention > 0.95:
        factor = 1.0 + (retention - 0.95) * 2.0
        return min(1.25, float(factor))
    else:
        return 1.0

def calculate_intervals(
    card, 
    rating: int, 
    now: datetime, 
    response_time: int = 0,
    target_retention: float = 0.9,
    retention_factor: float = 1.0
):
    """
    Принимает объект карточки, оценку (1-4), текущее время, время отклика в мс,
    целевой retention и адаптивный retention_factor.
    Возвращает: stability, difficulty, state, next_review, elapsed_days
    """
    # Состояния: 0 = New, 1 = Learning, 2 = Review, 3 = Relearning
    # Оценки: 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    
    clamped_factor = max(0.70, min(1.30, retention_factor))

    elapsed_days = 0
    if card.last_review:
        elapsed_days = (now - card.last_review).total_seconds() / 86400.0

    # Коррекция сложности на основе когнитивной задержки (response_time)
    latency_penalty = 0.0
    if response_time > 15000 and rating in (3, 4):
        # Долгое мучительное вспоминание (>15с) повышает расчетную сложность
        latency_penalty = 0.3
    elif 0 < response_time < 2500 and rating == 3:
        # Мгновенное вспоминание (<2.5с) слегка снижает сложность
        latency_penalty = -0.2

    safe_retention = max(0.70, min(0.98, target_retention))

    # 1. Если карточка новая (First review)
    if card.state == 0:
        new_stability = W[rating - 1]
        new_difficulty = W[4] - W[5] * (rating - 3) + latency_penalty
        
        if rating == 1:
            new_state = 1  # Переводим в этап обучения (Learning)
            next_review = now + timedelta(minutes=5)
        elif rating == 4:
            new_state = 2  # Сразу в Review (интервал в днях с учетом retention)
            calculated_days = calculate_target_interval(new_stability, safe_retention)
            interval_days = apply_fuzz(max(1, round(calculated_days * clamped_factor)))
            next_review = now + timedelta(days=interval_days)
        else:
            new_state = 1
            next_review = now + timedelta(minutes=10 if rating == 2 else 30)
            
        return float(new_stability), max(1.0, min(10.0, float(new_difficulty))), new_state, next_review, 0

    # 2. Если карточка находится в процессе заучивания (Learning / Relearning)
    elif card.state in (1, 3):
        if rating == 1:
            next_review = now + timedelta(minutes=5)
            return float(card.stability), float(card.difficulty), card.state, next_review, elapsed_days
        else:
            new_stability = W[3] if rating == 4 else (W[2] if rating == 3 else W[1])
            new_difficulty = max(1.0, min(10.0, card.difficulty - W[6] * (rating - 3) + latency_penalty))
            new_state = 2
            calculated_days = calculate_target_interval(new_stability, safe_retention)
            interval_days = apply_fuzz(max(1, round(calculated_days * clamped_factor)))
            next_review = now + timedelta(days=interval_days)
            return float(new_stability), max(1.0, min(10.0, float(new_difficulty))), new_state, next_review, elapsed_days

    # 3. Основной цикл повторения (Review)
    elif card.state == 2:
        if card.stability > 0:
            retrievability = math.exp(math.log(safe_retention) * elapsed_days / card.stability)
        else:
            retrievability = 0.0

        new_difficulty = card.difficulty - W[6] * (rating - 3) + latency_penalty
        new_difficulty = W[7] * (W[4]) + (1 - W[7]) * new_difficulty
        new_difficulty = max(1.0, min(10.0, new_difficulty))

        if rating == 1:
            new_stability = W[11] * math.pow(card.difficulty, -W[12]) * (math.pow(card.stability + 1, W[13]) - 1) * math.exp(W[14] * (1 - retrievability))
            new_state = 3  # Состояние переобучения (Relearning)
            next_review = now + timedelta(minutes=5)
        else:
            hard_modifier = W[15] if rating == 2 else 1.0
            easy_modifier = W[16] if rating == 4 else 1.0
            
            new_stability = card.stability * (1 + math.exp(W[8]) * (11 - new_difficulty) * math.pow(card.stability, -W[9]) * math.exp(W[10] * (1 - retrievability)))
            new_stability *= hard_modifier * easy_modifier
            new_state = 2
            
            calculated_days = calculate_target_interval(new_stability, safe_retention)
            interval_days = apply_fuzz(max(1, round(calculated_days * clamped_factor)))
            next_review = now + timedelta(days=interval_days)

        return max(0.1, float(new_stability)), max(1.0, min(10.0, float(new_difficulty))), new_state, next_review, elapsed_days

    # Fallback для невалидного state — сброс карточки в New
    return 0.0, 5.5, 0, now + timedelta(minutes=5), 0