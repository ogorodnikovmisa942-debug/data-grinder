from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database.session import Base

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    user_id = Column(String, nullable=False, index=True)
    
    # Реляционная связь с карточками
    cards = relationship("Card", back_populates="category")


class Phrase(Base):
    __tablename__ = "phrases"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False)  # Название темы или контекстный якорь (e.g., "ГК РФ Ст. 401")
    
    # Индексируем предмет для мгновенной Drill-Down фильтрации в статистике
    subject = Column(String, nullable=False, index=True)  # (e.g., "law_civil", "chinese_hsk3")
    user_id = Column(String, nullable=False, index=True, default="default_user")

    # Связь с карточками
    cards = relationship("Card", back_populates="phrase", cascade="all, delete-orphan")


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        Index("idx_card_user_subject", "user_id", "subject"),
        Index("idx_card_user_next_review", "user_id", "next_review"),
    )

    id = Column(Integer, primary_key=True, index=True)
    phrase_id = Column(Integer, ForeignKey("phrases.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    
    # Денормализация: храним предмет прямо в карте, чтобы выгребать Due-очередь без JOIN
    subject = Column(String, nullable=False, index=True)
    
    # Абстрагированные текстовые поля под любую дисциплину
    text = Column(String, nullable=False)            # Лицо (Иероглиф / Юр. термин / Название функции)
    secondary_text = Column(String, nullable=True)   # Подсказка (Пиньинь / Номер статьи / Сигнатура кода)
    translation = Column(String, nullable=False)     # Значение (Перевод / Юр. определение / Тело функции)
    
    # Метрики FSRS памяти
    difficulty = Column(Float, default=5.5)
    stability = Column(Float, default=0.0)
    state = Column(Integer, default=0)               # 0 = New, 1 = Learning, 2 = Review, 3 = Relearning
    
    # Тайминги обзоров
    last_review = Column(DateTime, nullable=True)
    next_review = Column(DateTime, nullable=False, index=True) # Индекс для быстрой сортировки по времени
    lapses = Column(Integer, default=0)
    
    # Когнитивные фичи MVP 2.0
    is_anchored = Column(Boolean, default=False)
    mnemonic = Column(JSON, nullable=True)           # Ассоциации от Gemini
    has_seen_intro = Column(Boolean, default=False)
    intro_phase = Column(Integer, default=0)
    content_type = Column(String, default="text")
    example = Column(String, nullable=True)

    # Реляционные связи
    phrase = relationship("Phrase", back_populates="cards")
    category = relationship("Category", back_populates="cards")
    logs = relationship("ReviewLog", back_populates="card", cascade="all, delete-orphan")


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    rating = Column(Integer, nullable=False)         # 1 = Again, 2 = Hard, 3 = Good, 4 = Easy
    review_time = Column(DateTime, nullable=False)
    
    # Телеметрия FSRS для аналитики удержания знаний
    state = Column(Integer, nullable=True)           # Статус карты ДО ответа
    elapsed_days = Column(Integer, nullable=True)    # Сколько дней прошло по факту
    scheduled_days = Column(Integer, nullable=True)  # На сколько дней карточка откладывалась

    # Дополнительные метрики аналитики FSRS
    has_association = Column(Boolean, default=False)  # флаг применения кастомного веса (ассоциации)
    response_time = Column(Integer, nullable=True)    # время в миллисекундах от показа до ответа
    stability = Column(Float, nullable=True)          # стабильность FSRS после повторения
    difficulty = Column(Float, nullable=True)         # сложность FSRS после повторения
    timestamp = Column(DateTime, default=datetime.utcnow) # точное время повторения

    # Обратная связь
    card = relationship("Card", back_populates="logs")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    is_resting = Column(Boolean, default=False)
    rest_ends_at = Column(DateTime, nullable=True)
    notified = Column(Boolean, default=False) # Флаг, чтобы не спамить пушами по кругу

    # Отметки отправки уведомлений для исключения дубликатов в многопоточном режиме
    last_morning_sent = Column(String, nullable=True)
    last_evening_sent = Column(String, nullable=True)
    last_due_notified_at = Column(DateTime, nullable=True)
    last_due_count = Column(Integer, default=0)


class DailySession(Base):
    __tablename__ = "daily_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    date = Column(String, nullable=False)                # Маркер учебного дня (например, "День 1", "День 2")
    total_reviewed = Column(Integer, default=0)          # Сумма повторенных карт за сессию
    new_cards_learned = Column(Integer, default=0)       # Новых карт за день
    session_duration = Column(Integer, default=0)        # Длительность сессии в секундах
    true_retention = Column(Float, default=0.0)          # Процент вспоминаний (2,3,4 делить на общее)
    
    # Субъективные метрики опроса (шкала 1-5)
    mental_effort = Column(Integer, nullable=False)       # Нагрузка
    association_utility = Column(Integer, nullable=False) # Польза ассоциаций
    perceived_retention = Column(Integer, nullable=False) # Субъективная уверенность
    
    timestamp = Column(DateTime, default=datetime.utcnow)


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False)
    daily_limit = Column(Integer, default=10)
    target_retention = Column(Float, default=0.9)
    assoc_preference = Column(String, default="acoustic")
    subject_limits = Column(JSON, nullable=True) # например {"law_civil_rb": 15, "all": 10}
