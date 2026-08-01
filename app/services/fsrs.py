from datetime import datetime, timedelta, timezone
import math
from typing import Dict, Any

class FSRSScheduler:
    """
    Детерминированный когнитивный движок FSRS (Free Spaced Repetition Scheduler).
    Управляет интервалами повторения на основе чистого Python-кода с учетом
    модификаций спецификации MVP 2.0.
    """
    
    def __init__(self):
        # Базовые константы алгоритма FSRS
        self.DEFAULT_DIFFICULTY = 5.5
        self.MIN_DIFFICULTY = 1.0
        self.MAX_DIFFICULTY = 10.0
        self.MAX_INTERVAL_DAYS = 90  # Защита от парадокса выжившего (Catch-up Mode)

    def calculate_review(
        self, 
        card: Any, 
        rating: int, 
        initial_difficulty_tier: str = "medium", 
        has_mnemonic: bool = False
    ) -> Dict[str, Any]:
        """
        Главная функция расчета шага памяти. 
        Принимает объект карточки из БД, оценку пользователя (1-4) и метаданные ИИ.
        Возвращает словарь с обновленными полями для записи в БД.
        """
        now = datetime.now(timezone.utc)
        
        # Инициализируем базовые переменные из текущего состояния карты
        current_state = card.state
        difficulty = card.difficulty if card.difficulty else self.DEFAULT_DIFFICULTY
        stability = card.stability if card.stability else 0.0
        lapses = card.lapses if card.lapses else 0
        is_anchored = card.is_anchored

        # Вычисляем прошедшее время (elapsed_days)
        if card.last_review:
            # Приводим к UTC для безопасного вычитания dates
            last_review_utc = card.last_review.replace(tzinfo=timezone.utc)
            elapsed_days = (now - last_review_utc).total_seconds() / 86400.0
        else:
            elapsed_days = 0.0

        # --- МОДИФИКАЦИЯ 1: Решение проблемы «Холодного старта» ---
        is_first_review = (current_state == 0)  # Статус New
        if is_first_review:
            if initial_difficulty_tier == "easy":
                difficulty = 3.0
            elif initial_difficulty_tier == "hard":
                difficulty = 7.5
            else:
                difficulty = self.DEFAULT_DIFFICULTY

            # Стартовая стабильность в зависимости от первой оценки пользователя
            if rating == 1:    # Again
                stability = 0.1
                current_state = 1  # Learning
            elif rating == 2:  # Hard
                stability = 1.0
                current_state = 2  # Review
            elif rating == 3:  # Good
                stability = 3.0
                current_state = 2  # Review
            else:              # Easy
                stability = 8.0
                current_state = 2  # Review

            # --- МОДИФИКАЦИЯ 2: Коэффициент мнемоник для первого шага ---
            if rating > 1 and has_mnemonic:
                stability *= 1.5

        # --- РАБОТА СО ЗРЕЛЫМИ КАРТОЧКАМИ (Повторные ревью) ---
        else:
            # Рассчитываем текущую извлекаемость (Retrievability) по экспоненте забывания
            if stability > 0:
                retrievability = math.exp(math.log(0.9) * elapsed_days / stability)
            else:
                retrievability = 0.0

            # Обновление сложности на основе оценки (1 = Again, 2 = Hard, 3 = Good, 4 = Easy)
            # Смещаем сложность: за ошибки (Again) увеличиваем, за Easy снижаем
            difficulty_delta = 0.5 * (3 - rating)
            difficulty = max(self.MIN_DIFFICULTY, min(self.MAX_DIFFICULTY, difficulty - difficulty_delta))

            if rating == 1:  # Ошибка (Again)
                lapses += 1
                
                # --- МОДИФИКАЦИЯ 3: Контекстный якорь при сбое зрелой памяти ---
                if stability >= 14.0:
                    current_state = 3  # Перевод в состояние Relearning
                    is_anchored = True  # Включаем контекстную подсказку
                else:
                    current_state = 1  # Learning

                # Падение стабильности после ошибки
                stability = max(0.1, stability * 0.25)
            
            else:  # Успешное припоминание (Hard, Good, Easy)
                current_state = 2  # Переводим / удерживаем в Review
                
                # Стандартный адаптивный множитель FSRS
                # Стабильность растет быстрее, если извлекаемость была низкой (сложный момент)
                hard_modifier = 1.0 if rating == 2 else (2.2 if rating == 3 else 4.0)
                factor = hard_modifier * (1.0 + 0.1 * (self.MAX_DIFFICULTY - difficulty)) * math.exp(0.1 * (1.0 - retrievability))
                stability = stability * max(1.1, factor)

        # --- МОДИФИКАЦИЯ 4: Защита от парадокса выжившего (Max Interval Ceiling) ---
        interval = max(1, round(stability))
        if interval > self.MAX_INTERVAL_DAYS:
            interval = self.MAX_INTERVAL_DAYS

        # Рассчитываем точное время следующего показа
        next_review = now + timedelta(days=interval)

        return {
            "difficulty": round(difficulty, 2),
            "stability": round(stability, 2),
            "state": current_state,
            "last_review": now.replace(tzinfo=None),  # Убираем таймзону для сохранения в SQLite/Postgres
            "next_review": next_review.replace(tzinfo=None),
            "lapses": lapses,
            "is_anchored": is_anchored,
            "elapsed_days": round(elapsed_days, 2)  # Возвращаем для записи в лог транзакции
        }

# Инициализируем синглтон для импорта в эндпоинты
fsrs_scheduler = FSRSScheduler()