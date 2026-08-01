# app/services/queue_manager.py
import math
from datetime import datetime
from typing import List
from sqlalchemy.orm import Session
from app.database.models import Card, Phrase

def apply_interleaving(cards: List[Card], max_consecutive: int = 1) -> List[Card]:
    """
    Алгоритмический интерливинг по предметам (subjects).
    Предотвращает когнитивную интерференцию — не более max_consecutive карт одной темы подряд.
    """
    if not cards:
        return []
        
    result = []
    pool = list(cards)
    consecutive_counts = {}
    last_subject = None
    
    while pool:
        inserted = False
        for i, card in enumerate(pool):
            if card.subject == last_subject:
                if consecutive_counts.get(card.subject, 0) >= max_consecutive:
                    continue
            
            pool.pop(i)
            result.append(card)
            
            if card.subject == last_subject:
                consecutive_counts[card.subject] = consecutive_counts.get(card.subject, 0) + 1
            else:
                consecutive_counts = {card.subject: 1}
                last_subject = card.subject
                
            inserted = True
            break
            
        # Защитный фолбэк, если по правилам разбавить пулы больше невозможно
        if not inserted:
            card = pool.pop(0)
            result.append(card)
            last_subject = card.subject
            consecutive_counts = {}
            
    return result

def get_due_cards(db: Session, subject: str = "all", limit: int = 30):
    """
    Выгребает просроченные и новые карты сессии, соблюдая жесткий контракт полей для app.js
    """
    now = datetime.utcnow()
    
    # 1. Извлекаем карты для повторения (Review + Learning/Relearning)
    due_query = db.query(Card).filter(Card.state.in_([1, 2, 3]), Card.next_review <= now)
    if subject != "all":
        due_query = due_query.filter(Card.subject == subject)
    due_pool = due_query.order_by(Card.state.desc(), Card.next_review.asc()).all()
    
    # 2. Новые карты (добираются только если сессия не перегружена долгами)
    new_pool = []
    if len(due_pool) < limit:
        new_query = db.query(Card).filter(Card.state == 0)
        if subject != "all":
            new_query = new_query.filter(Card.subject == subject)
        new_pool = new_query.limit(limit - len(due_pool)).all()
        
    full_pool = due_pool + new_pool
    
    # Перемешиваем темы только в глобальном режиме ALL
    if subject == "all":
        full_pool = apply_interleaving(full_pool, max_consecutive=1)
        
    final_session_cards = full_pool[:limit]
    
    result = []
    for card in final_session_cards:
        phrase_text = ""
        if card.is_anchored and card.phrase_id:
            phrase = db.query(Phrase).filter(Phrase.id == card.phrase_id).first()
            if phrase:
                phrase_text = phrase.text

        # Жесткое соблюдение контракта полей под структуры app.js
        result.append({
            "id": card.id,
            "text": card.text,
            "secondary_text": card.secondary_text if card.secondary_text else "",
            "translation": card.translation,
            "state": card.state,
            "subject": card.subject,
            "is_anchored": card.is_anchored,
            "phrase_text": phrase_text,
            "mnemonic": card.mnemonic
        })
        
    return result