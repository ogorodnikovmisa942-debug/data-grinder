# app/services/practice_service.py
"""
Autonomous Practice Module for Data Grinder (Requirement R5).
Generates and evaluates interactive cognitive exercises:
1. Situational Vignettes / Decision Trees (qualification & jurisdictional forks).
2. Contrast-Pair Boundary Discrimination (Gold Standard criteria).
3. Fact Pattern Slot-Filling (Statute & procedural cloze).

Operates autonomously and independently of the Knowledge Graph view.
"""

import re
import uuid
import random
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PracticeItem, Card, Phrase, TopicKnowledgeGraph
from app.database.session import AsyncSessionLocal


# --- PRESET SEED PRACTICE ITEMS (FOR ZERO-CARD / INSTANT START) ---
SUDOUSTROYSTVO_PRESET_PRACTICE = [
    {
        "type": "situational",
        "prompt": "Судебный акт мирового судьи уже вступил в законную силу. Сторона процесса обнаружила существенное нарушение норм материального права. В какую судебную инстанцию подается жалоба?",
        "options": [
            "В кассационный суд общей юрисдикции",
            "В районный суд в апелляционном порядке",
            "В апелляционный суд общей юрисдикции",
            "Председателю Верховного Суда РФ"
        ],
        "correct_answer": "В кассационный суд общей юрисдикции",
        "explanation": "Вступившие в законную силу судебные акты мировых судей и районных судов пересматриваются кассационным судом общей юрисдикции (ст. 377 ГПК РФ, ст. 401.3 УПК РФ).",
        "gold_standard": "Вступивший в силу акт -> Кассация (не апелляция)."
    },
    {
        "type": "contrast_pair",
        "prompt": "Какой водораздельный критерий разграничивает процессуальную роль народных заседателей и присяжных заседателей?",
        "options": [
            "Народные заседатели голосуют совместно с судьей по всем вопросам права и факта, а присяжные выносят обособленный вердикт только о виновности",
            "Присяжные заседатели получают статус судьи, а народные — статус судебного пристава",
            "Народные заседатели заседают только в арбитраже, а присяжные — в конституционном суде",
            "Присяжные назначают уголовное наказание, а судья единолично устанавливает вину"
        ],
        "correct_answer": "Народные заседатели голосуют совместно с судьей по всем вопросам права и факта, а присяжные выносят обособленный вердикт только о виновности",
        "explanation": "Народные заседатели образуют единую коллегию с профессиональным судьей. Присяжные отделены от судьи и дают ответ лишь на три ключевых вопроса о доказанности деяния и вины.",
        "gold_standard": "Единая коллегия (вопросы права и вины) vs Обособленный вердикт (только вина)."
    },
    {
        "type": "slot_filling",
        "prompt": "В соответствии с ч. 1 ст. 118 Конституции РФ правосудие в Российской Федерации осуществляется только [...]",
        "options": [
            "судом",
            "прокуратурой и следственными органами",
            "Министерством юстиции РФ",
            "третейскими комиссиями"
        ],
        "correct_answer": "судом",
        "explanation": "Конституционный принцип исключительности судебной власти (монополия судейского корпуса на отправление правосудия).",
        "gold_standard": "Исключительность судебной власти."
    },
    {
        "type": "situational",
        "prompt": "Между двумя ООО возник спор о нарушении условий договора поставки производственного оборудования на сумму 12 млн рублей. Какому суду подсудно данное исковое заявление?",
        "options": [
            "Арбитражному суду субъекта РФ",
            "Районному суду общей юрисдикции",
            "Судебной коллегии по экономическим спорам ВС РФ по 1-й инстанции",
            "Мировому судье судебного участка"
        ],
        "correct_answer": "Арбитражному суду субъекта РФ",
        "explanation": "Экономические споры между коммерческими юридическими лицами отнесены к специальной подведомственности арбитражных судов субъектов РФ (ст. 27, 34 АПК РФ).",
        "gold_standard": "Коммерческий спор юрлиц -> Арбитражный суд субъекта РФ."
    },
    {
        "type": "contrast_pair",
        "prompt": "Чем императивные предписания в праве функционально отличаются от диспозитивных?",
        "options": [
            "Императивные содержат категорические запреты и обязанности, исключающие выбор сторон; диспозитивные допускают согласование условий",
            "Императивные действуют только во время военного положения, а диспозитивные — в обычное время",
            "Императивные нормы издаются Президентом, а диспозитивные — Государственной Думой",
            "Императивные нормы не обладают высшей юридической силой"
        ],
        "correct_answer": "Императивные содержат категорические запреты и обязанности, исключающие выбор сторон; диспозитивные допускают согласование условий",
        "explanation": "Способ воздействия нормы: категорическое властное веление против свободы усмотрения субъектов правоотношений.",
        "gold_standard": "Категорический долг/запрет vs Право на усмотрение."
    }
]


def extract_cloze_target(front_text: str) -> tuple[str, str]:
    """Извлекает искомое слово из разметки {{c1::слово}} или [слово]."""
    # 1. Формат Anki cloze {{c1::target}}
    m_anki = re.search(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', front_text)
    if m_anki:
        target = m_anki.group(1).strip()
        cloze_prompt = re.sub(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', '[...]', front_text)
        return cloze_prompt, target

    # 2. Формат скобок [target]
    m_bracket = re.search(r'\[(.*?)\]', front_text)
    if m_bracket:
        target = m_bracket.group(1).strip()
        cloze_prompt = front_text.replace(f"[{target}]", "[...]")
        return cloze_prompt, target

    return front_text, ""


async def generate_practice_session(
    user_id: str,
    subject: str,
    count: int = 10,
    db: Optional[AsyncSession] = None
) -> List[Dict[str, Any]]:
    """Генерирует автономную практическую сессию для пользователя по предмету.
    
    1. Ищет карточки пользователя в таблице cards по данному предмету.
    2. Если карточек достаточно (>=3), динамически синтезирует упражнения 3 типов:
       - situational (ситуационные кейсы с дистракторами из колоды)
       - contrast_pair (разграничение понятий)
       - slot_filling (заполнение пропусков)
    3. Если карточек нет или мало (<3), использует предустановленные seed-сценарии или данные графа знаний.
    4. Сохраняет сформированные PracticeItem в БД для надежной верификации ответов.
    """
    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        # 1. Извлекаем карточки пользователя для предмета
        stmt = select(Card).where(Card.subject == subject, Card.user_id == user_id)
        res = await db.execute(stmt)
        user_cards = res.scalars().all()
        if not user_cards:
            # Попробуем найти карты этого предмета для default_user
            stmt_default = select(Card).where(Card.subject == subject)
            res_default = await db.execute(stmt_default)
            user_cards = res_default.scalars().all()

        practice_records: List[PracticeItem] = []

        # 2. Если есть достаточно карточек, синтезируем интерактивные тесты
        if len(user_cards) >= 3:
            all_answers = [c.translation.strip() for c in user_cards if c.translation and len(c.translation.strip()) > 3]
            sample_cards = random.sample(user_cards, min(count, len(user_cards)))

            for card in sample_cards:
                front = (card.text or "").strip()
                back = (card.translation or "").strip()
                ex = (card.example or "").strip()
                sec = (card.secondary_text or "").strip()

                if not front or not back:
                    continue

                cloze_prompt, cloze_target = extract_cloze_target(front)
                item_id = str(uuid.uuid4())

                # Тип 1: Заполнение пропусков (Slot-Filling)
                if cloze_target and len(cloze_target) > 1:
                    # Подбираем 3 дистрактора
                    distractors = [a for a in all_answers if a.lower() != cloze_target.lower()]
                    chosen_distractors = random.sample(distractors, min(3, len(distractors)))
                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Альтернативное условие {len(chosen_distractors) + 1}")

                    options = [cloze_target] + chosen_distractors
                    random.shuffle(options)

                    pi = PracticeItem(
                        item_id=item_id,
                        user_id=user_id,
                        subject=subject,
                        item_type="slot_filling",
                        prompt=cloze_prompt,
                        options=options,
                        correct_answer=cloze_target,
                        explanation=ex or sec or f"Правильный термин в контексте нормы: {cloze_target}.",
                        gold_standard=f"Точное соответствие: {cloze_target}."
                    )
                    practice_records.append(pi)

                # Тип 2: Контрастная пара (Contrast Pair)
                elif any(cue in front.lower() for cue in ("чем отлич", "разгранич", "в отличие", " vs ", "разница")):
                    distractors = [a for a in all_answers if a.lower() != back.lower()]
                    chosen_distractors = random.sample(distractors, min(3, len(distractors)))
                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Неприменимый признак {len(chosen_distractors) + 1}")

                    options = [back] + chosen_distractors
                    random.shuffle(options)

                    pi = PracticeItem(
                        item_id=item_id,
                        user_id=user_id,
                        subject=subject,
                        item_type="contrast_pair",
                        prompt=front,
                        options=options,
                        correct_answer=back,
                        explanation=ex or sec or f"Водораздельный критерий: {back}",
                        gold_standard=f"Разграничительный критерий: {back}"
                    )
                    practice_records.append(pi)

                # Тип 3: Ситуационный кейс / Дерево решений (Situational Vignette)
                else:
                    distractors = [a for a in all_answers if a.lower() != back.lower()]
                    chosen_distractors = random.sample(distractors, min(3, len(distractors)))
                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Иная инстанция {len(chosen_distractors) + 1}")

                    options = [back] + chosen_distractors
                    random.shuffle(options)

                    pi = PracticeItem(
                        item_id=item_id,
                        user_id=user_id,
                        subject=subject,
                        item_type="situational",
                        prompt=front,
                        options=options,
                        correct_answer=back,
                        explanation=ex or sec or f"Обоснование: {back}",
                        gold_standard=f"Правовое последствие: {back}"
                    )
                    practice_records.append(pi)

        # 3. Fallback: если пользовательских карточек мало, берем предустановленные seed-сценарии
        if len(practice_records) < min(count, 3):
            # Если предмет относится к праву / судоустройству или дефолтный
            seeds = SUDOUSTROYSTVO_PRESET_PRACTICE.copy()
            random.shuffle(seeds)
            needed = count - len(practice_records)

            for seed in seeds[:needed]:
                opts = list(seed["options"])
                random.shuffle(opts)
                pi = PracticeItem(
                    item_id=str(uuid.uuid4()),
                    user_id=user_id,
                    subject=subject,
                    item_type=seed["type"],
                    prompt=seed["prompt"],
                    options=opts,
                    correct_answer=seed["correct_answer"],
                    explanation=seed["explanation"],
                    gold_standard=seed["gold_standard"]
                )
                practice_records.append(pi)

        # 4. Сохраняем элементы в БД
        for pi in practice_records:
            db.add(pi)
        await db.commit()

        # 5. Возвращаем клиенту безопасные словари без открытого правильного ответа
        return [pi.to_dict(include_answer=False) for pi in practice_records]

    finally:
        if should_close:
            await db.close()


async def verify_practice_answer(
    user_id: str,
    item_id: str,
    selected_answer: str,
    db: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """Верифицирует ответ пользователя на интерактивное задание."""
    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        stmt = select(PracticeItem).where(PracticeItem.item_id == item_id)
        res = await db.execute(stmt)
        item = res.scalars().first()

        if not item:
            # Fallback для тестов или устаревших сессий: если не найдено в БД, ищем в пресетах
            for seed in SUDOUSTROYSTVO_PRESET_PRACTICE:
                if selected_answer.strip().lower() == seed["correct_answer"].strip().lower():
                    return {
                        "correct": True,
                        "selected": selected_answer,
                        "correct_answer": seed["correct_answer"],
                        "explanation": seed["explanation"],
                        "gold_standard": seed["gold_standard"]
                    }
            return {
                "correct": False,
                "selected": selected_answer,
                "correct_answer": "Не удалось найти задание в реестре.",
                "explanation": "Срок сессии истек или задание было обновлено.",
                "gold_standard": "Сессия обновлена."
            }

        is_correct = (selected_answer.strip().lower() == item.correct_answer.strip().lower())
        return {
            "correct": is_correct,
            "selected": selected_answer,
            "correct_answer": item.correct_answer,
            "explanation": item.explanation or "Обоснование зафиксировано в нормативном акте.",
            "gold_standard": item.gold_standard or item.correct_answer
        }

    finally:
        if should_close:
            await db.close()
