# app/services/fsrs_core.py
import math
from datetime import datetime, timedelta

# Стандартные веса FSRS v4 для базовой настройки интервалов
W = [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.28, 2.61]

def calculate_intervals(card, rating: int, now: datetime):
    """
    Принимает объект карточки, оценку (1-4) и текущее время.
    Возвращает измененные параметры: stability, difficulty, state, next_review, elapsed_days
    """
    # Состояния: 0 = New, 1 = Learning, 2 = Review, 3 = Relearning
    # Оценки: 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    
    elapsed_days = 0
    if card.last_review:
        elapsed_days = (now - card.last_review).total_seconds() / 86400.0

    # 1. Если карточка новая (First review)
    if card.state == 0:
        new_stability = W[rating - 1]
        new_difficulty = W[4] - W[5] * (rating - 3)
        
        if rating == 1:
            new_state = 1  # Переводим в этап обучения (Learning)
            next_review = now + timedelta(minutes=5)
        elif rating == 4:
            new_state = 2  # Сразу в Review (интервал в днях)
            next_review = now + timedelta(days=max(1, round(new_stability)))
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
            new_stability = W[2] if rating == 3 else W[1]
            new_difficulty = max(1.0, min(10.0, card.difficulty - W[6] * (rating - 3)))
            new_state = 2
            next_review = now + timedelta(days=max(1, round(new_stability)))
            return float(new_stability), max(1.0, min(10.0, float(new_difficulty))), new_state, next_review, elapsed_days

    # 3. Основной цикл повторения (Review)
    elif card.state == 2:
        if card.stability > 0:
            retrievability = math.exp(math.log(0.9) * elapsed_days / card.stability)
        else:
            retrievability = 0.0

        new_difficulty = card.difficulty - W[6] * (rating - 3)
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
            
            interval_days = max(1, round(new_stability))
            next_review = now + timedelta(days=interval_days)

        return max(0.1, float(new_stability)), max(1.0, min(10.0, float(new_difficulty))), new_state, next_review, elapsed_days