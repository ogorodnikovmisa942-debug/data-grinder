from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, JSON, Index, Text, UniqueConstraint, func
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
    is_outlier = Column(Boolean, default=False, nullable=False) # фильтрация невалидных задержек / мисскликов
    is_cram = Column(Boolean, default=False, nullable=False) # флаг сессии режима штурма

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

    # Участие в научном эксперименте (Фаза 1: изоляция, Фаза 2: свободный режим)
    is_experiment_participant = Column(Boolean, default=False, nullable=False)
    experiment_phase = Column(Integer, default=1, nullable=False)
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=True)


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
    is_experiment_participant = Column(Boolean, default=False, nullable=False)
    experiment_phase = Column(Integer, default=1, nullable=False)


class GenerationJob(Base):
    """Очередь отложенных задач генерации карточек (Ночной Грайнд со скидкой 50%)."""
    __tablename__ = "generation_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    telegram_id = Column(String, nullable=True)
    subject = Column(String, nullable=False)
    theme = Column(String, nullable=False, default="Новый блок знаний")
    raw_text = Column(String, nullable=False)
    granularity_mode = Column(String, default="atomic")
    density = Column(String, default="medium")
    volume = Column(String, default="medium")
    custom_instruction = Column(String, default="")
    status = Column(String, default="pending", index=True)  # pending | processing | ready_for_review | completed | failed | cancelled
    error_message = Column(String, nullable=True)
    cards_count = Column(Integer, default=0)
    result_cards_json = Column(String, nullable=True)  # JSON массив готовых карточек для модерации в Песочнице
    is_deferred = Column(Boolean, default=False)  # True = ждать ночного окна скидок, False = обработка сейчас в фоне
    
    # Инженерная телеметрия конвейера декомпозиции
    char_count = Column(Integer, nullable=True)
    fallback_used = Column(Boolean, default=False, nullable=False)
    json_repair_applied = Column(Boolean, default=False, nullable=False)
    execution_time_ms = Column(Integer, nullable=True)
    error_trace = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class AiTelemetryLog(Base):
    """Детальный аудит каждого обращения к LLM-шлюзу платформы."""
    __tablename__ = "ai_telemetry_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    job_id = Column(String(64), nullable=True, index=True)
    user_id = Column(String(64), nullable=False, default="default_user", index=True)
    model_requested = Column(String(64), nullable=False)  # deepseek-chat, gemini-2.5-flash-lite, etc.
    model_resolved = Column(String(64), nullable=False)   # модель, вернувшая финальный ответ
    input_chars = Column(Integer, default=0, nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    cache_hit = Column(Boolean, default=False, nullable=False)  # попадание в Context Cache
    is_truncated = Column(Boolean, default=False, nullable=False)
    repair_successful = Column(Boolean, default=False, nullable=False)
    cards_generated = Column(Integer, default=0, nullable=False)
    duration_ms = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="success", nullable=False)  # success, json_parse_error, rate_limit, fallback_cascade, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class InviteCode(Base):
    """Инвайт-коды для контролируемого допуска участников к эксперименту."""
    __tablename__ = "invite_codes"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    code = Column(String(32), unique=True, index=True, nullable=False)
    created_by = Column(String(64), nullable=True)       # telegram_id / admin
    used_by_user_id = Column(String(64), nullable=True)  # telegram_id участника
    used_by_username = Column(String(64), nullable=True) # @username участника
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    used_at = Column(DateTime, nullable=True)


class TopicKnowledgeGraph(Base):
    """
    Семантический граф знаний и иерархическое дерево ментального каркаса предмета (R1, R2).
    Обеспечивает мгновенную O(1) загрузку структуры книги/темы без повторного обращения к LLM.
    """
    __tablename__ = "topic_knowledge_graphs"
    __table_args__ = (
        UniqueConstraint("user_id", "subject", name="uq_topic_knowledge_graphs_user_subject"),
        Index("ix_topic_knowledge_graphs_user_subject", "user_id", "subject"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    subject = Column(String, nullable=False, index=True)
    graph_data = Column(JSON, nullable=False)
    tree_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "subject": self.subject,
            "graph_data": self.graph_data,
            "tree_data": self.tree_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PracticeItem(Base):
    """
    Интерактивные практические задания (ситуационные кейсы, разграничение контрастных пар, заполнение пропусков) (R5).
    Функционирует автономно без жесткой блокировки графом знаний.
    """
    __tablename__ = "practice_items"
    __table_args__ = (
        Index("ix_practice_items_user_subject", "user_id", "subject"),
    )

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(String, nullable=False, index=True, default="default_user")
    subject = Column(String, nullable=False, index=True)
    item_type = Column(String, nullable=False, default="situational")  # situational | contrast_pair | slot_filling
    prompt = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)  # list of strings
    correct_answer = Column(Text, nullable=False)
    explanation = Column(Text, nullable=True)
    gold_standard = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    def to_dict(self, include_answer: bool = False) -> dict:
        data = {
            "id": self.item_id,
            "type": self.item_type,
            "prompt": self.prompt,
            "options": self.options,
            "subject": self.subject,
        }
        if include_answer:
            data["correct_answer"] = self.correct_answer
            data["explanation"] = self.explanation
            data["gold_standard"] = self.gold_standard
        return data

