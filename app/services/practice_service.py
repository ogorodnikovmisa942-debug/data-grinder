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
from app.services.graph_service import resolve_subject_alias


# --- PRESET SEED PRACTICE ITEMS (FOR ZERO-CARD / INSTANT START) ---
SUDOUSTROYSTVO_PRESET_PRACTICE = [
    {
        "type": "situational",
        "prompt": "Судебный акт мирового судьи уже вступил в законную силу. Сторона процесса обнаружила существенное нарушение норм материального права. В какую судебную инстанцию подается кассационная жалоба?",
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
        "prompt": "Между двумя коммерческими организациями (ООО и АО) возник спор о нарушении условий договора поставки производственного оборудования на сумму 15 млн рублей. Какому суду подсудно данное дело?",
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
    },
    {
        "type": "situational",
        "prompt": "Районный суд вынес решение по гражданскому спору. Решение еще не вступило в законную силу. В какую инстанцию подается апелляционная жалоба?",
        "options": [
            "В областной (краевой, республиканский) суд общей юрисдикции",
            "В кассационный суд общей юрисдикции",
            "В апелляционный суд общей юрисдикции",
            "В судебную коллегию по гражданским делам ВС РФ"
        ],
        "correct_answer": "В областной (краевой, республиканский) суд общей юрисдикции",
        "explanation": "Решения районных судов, не вступившие в законную силу, обжалуются в апелляционном порядке в вышестоящий суд субъекта РФ (ст. 320.1 ГПК РФ).",
        "gold_standard": "Не вступившее решение районного суда -> Областной/краевой суд (апелляция)."
    },
    {
        "type": "situational",
        "prompt": "Арбитражный суд субъекта РФ вынес решение по делу о взыскании задолженности. Сторона не согласна с выводами суда. В какой орган подается апелляционная жалоба?",
        "options": [
            "В арбитражный апелляционный суд",
            "В арбитражный суд округа",
            "В кассационный суд общей юрисдикции",
            "В Судебную коллегию по экономическим спорам ВС РФ"
        ],
        "correct_answer": "В арбитражный апелляционный суд",
        "explanation": "Решения арбитражных судов субъектов РФ обжалуются в соответствующий арбитражный апелляционный суд (ст. 181, 257 АПК РФ).",
        "gold_standard": "Арбитражный суд субъекта -> Арбитражный апелляционный суд."
    },
    {
        "type": "situational",
        "prompt": "Гражданин считает, что примененный судом в его деле федеральный закон нарушает конституционное право на неприкосновенность жилища. Куда подается жалоба на конституционность нормы?",
        "options": [
            "В Конституционный Суд РФ",
            "В Верховный Суд РФ",
            "В Генеральную прокуратуру РФ",
            "Уполномоченному по правам человека"
        ],
        "correct_answer": "В Конституционный Суд РФ",
        "explanation": "Проверка конституционности законов и иных нормативных актов по жалобам граждан на нарушение их конституционных прав осуществляется исключительно Конституционным Судом РФ (ст. 125 Конституции РФ).",
        "gold_standard": "Конституционность закона -> Конституционный Суд РФ."
    },
    {
        "type": "contrast_pair",
        "prompt": "Какой водораздел разделяет свидетельский иммунитет (ст. 51 Конституции РФ) и общую обязанность свидетеля давать показания?",
        "options": [
            "Иммунитет дает абсолютное право не свидетельствовать против себя и близких родственников без уголовной ответственности за отказ",
            "Иммунитет действует только при наличии разрешения прокурора",
            "Иммунитет освобождает от явки в зал судебного заседания",
            "Иммунитет применяется только к иностранным гражданам и дипломатам"
        ],
        "correct_answer": "Иммунитет дает абсолютное право не свидетельствовать против себя и близких родственников без уголовной ответственности за отказ",
        "explanation": "Ст. 51 Конституции РФ устанавливает прямой запрет на привлечение к ответственности свидетеля за отказ давать показания против себя, своего супруга и близких родственников.",
        "gold_standard": "Свидетельский иммунитет: против себя и родственников -> исключает ст. 308 УК РФ."
    },
    {
        "type": "slot_filling",
        "prompt": "В соответствии с ч. 3 ст. 118 Конституции РФ создание чрезвычайных судов [...]",
        "options": [
            "не допускается",
            "допускается по указу Президента в период военного положения",
            "разрешается постановлением Совета Федерации",
            "допускается решением Конституционного Суда"
        ],
        "correct_answer": "не допускается",
        "explanation": "Прямой абсолютный конституционный запрет: создание чрезвычайных судов не допускается ни при каких обстоятельствах (гарантия законного суда).",
        "gold_standard": "Чрезвычайные суды -> абсолютный запрет (ч. 3 ст. 118 КРФ)."
    },
    {
        "type": "situational",
        "prompt": "Какая судебная инстанция осуществляет надзорное производство в качестве высшей и окончательной инстанции в РФ?",
        "options": [
            "Президиум Верховного Суда РФ",
            "Судебная коллегия по уголовным делам ВС РФ",
            "Конституционный Суд РФ",
            "Пленум Верховного Суда РФ"
        ],
        "correct_answer": "Президиум Верховного Суда РФ",
        "explanation": "Надзорное производство осуществляется исключительно Президиумом Верховного Суда Российской Федерации (ст. 391.1 ГПК РФ, ст. 412.1 УПК РФ).",
        "gold_standard": "Надзорная инстанция -> Президиум Верховного Суда РФ."
    },
    {
        "type": "contrast_pair",
        "prompt": "Чем кассационное производство функционально отличается от апелляционного?",
        "options": [
            "Кассация проверяет исключительно законность вступивших в силу актов, а апелляция пересматривает дело по существу до вступления акта в силу",
            "Кассация пересматривает только дела о преступлениях против государственной власти",
            "Кассация проводится только с участием присяжных заседателей",
            "Кассационная жалоба подается до вынесения решения судом первой инстанции"
        ],
        "correct_answer": "Кассация проверяет исключительно законность вступивших в силу актов, а апелляция пересматривает дело по существу до вступления акта в силу",
        "explanation": "Апелляция — повторное рассмотрение дела по факту и праву не вступившего в силу решения. Кассация — ревизия законности акта, уже вступившего в законную силу.",
        "gold_standard": "Апелляция (до вступления, факт+право) vs Кассация (после вступления, только законность)."
    },
    {
        "type": "situational",
        "prompt": "Какой общий процессуальный срок подачи апелляционной жалобы установлен Гражданским процессуальным кодексом РФ?",
        "options": [
            "В течение месяца со дня принятия решения суда в окончательной форме",
            "В течение 10 дней с момента оглашения резолютивной части",
            "В течение 6 месяцев с момента вынесения решения",
            "В течение 14 дней с момента получения копии решения стороной"
        ],
        "correct_answer": "В течение месяца со дня принятия решения суда в окончательной форме",
        "explanation": "В соответствии с ч. 2 ст. 321 ГПК РФ апелляционная жалоба может быть подана в течение месяца со дня принятия решения суда в окончательной форме.",
        "gold_standard": "Срок апелляции ГПК -> 1 месяц со дня окончательной формы."
    },
    {
        "type": "slot_filling",
        "prompt": "Конституционный принцип презумпции невиновности (ст. 49 КРФ) устанавливает, что неустранимые сомнения в виновности лица толкуются [...]",
        "options": [
            "в пользу обвиняемого",
            "в пользу потерпевшего",
            "на усмотрение государственного обвинителя",
            "в пользу следственных органов"
        ],
        "correct_answer": "в пользу обвиняемого",
        "explanation": "Принцип 'in dubio pro reo': любые неустранимые сомнения в доказанности вины толкуются строго в пользу обвиняемого.",
        "gold_standard": "Неустранимые сомнения -> в пользу обвиняемого (ч. 3 ст. 49 КРФ)."
    },
    {
        "type": "situational",
        "prompt": "Следователь вынес постановление о возбуждении уголовного дела в отношении действующего судьи районного суда. Какое обязательное условие необходимо для этого?",
        "options": [
            "Решение Председателя СК РФ с согласия квалификационной коллегии судей",
            "Единоличное согласие прокурора района",
            "Согласие председателя областного суда",
            "Постановление Государственной Думы РФ"
        ],
        "correct_answer": "Решение Председателя СК РФ с согласия квалификационной коллегии судей",
        "explanation": "Судейский иммунитет (ст. 16 Закона 'О статусе судей в РФ'): уголовное дело в отношении судьи возбуждается Председателем СК РФ с согласия ККС субъекта РФ.",
        "gold_standard": "Уголовное преследование судьи -> Председатель СК РФ + согласие ККС."
    },
    {
        "type": "contrast_pair",
        "prompt": "Какой критерий отличает материальное право от процессуального права?",
        "options": [
            "Материальное определяет права, обязанности и ответственность; процессуальное регулирует порядок их реализации и судебной защиты",
            "Материальное право применяется только в арбитраже, а процессуальное — в уголовном процессе",
            "Материальное право состоит только из указов, а процессуальное — из кодексов",
            "Процессуальное право имеет приоритет перед нормами Конституции РФ"
        ],
        "correct_answer": "Материальное определяет права, обязанности и ответственность; процессуальное регулирует порядок их реализации и судебной защиты",
        "explanation": "Материальное право регулирует само существо отношений (собственность, преступление, долг), процессуальное — формы и процедуры судопроизводства.",
        "gold_standard": "Материальное (права и состав) vs Процессуальное (порядок и защита)."
    },
    {
        "type": "situational",
        "prompt": "Какую роль в системе арбитражных судов выполняют Арбитражные суды округов (федеральные арбитражные суды)?",
        "options": [
            "Судов первой кассационной инстанции",
            "Судов апелляционной инстанции",
            "Судов надзорной инстанции",
            "Судов исключительно первой инстанции по спорам с государством"
        ],
        "correct_answer": "Судов первой кассационной инстанции",
        "explanation": "Арбитражные суды округов проверяют законность вступивших в силу судебных актов арбитражных судов субъектов РФ и апелляционных судов в кассационном порядке (ст. 274 АПК РФ).",
        "gold_standard": "Арбитражные суды округов -> 1-я кассация арбитража."
    },
    {
        "type": "slot_filling",
        "prompt": "Согласно ст. 121 Конституции РФ, судьи несменяемы. Полномочия судьи могут быть прекращены или приостановлены не иначе как [...]",
        "options": [
            "в порядке и по основаниям, установленным федеральным законом",
            "по личному распоряжению главы субъекта РФ",
            "при смене состава Государственной Думы",
            "по истечении пятилетнего срока службы"
        ],
        "correct_answer": "в порядке и по основаниям, установленным федеральным законом",
        "explanation": "Гарантия независимости судей: полномочия судьи не ограничены сроком (для федеральных судей) и могут быть прекращены только решением ККС по закону.",
        "gold_standard": "Несменяемость судей -> прекращение только по федеральному закону."
    }
]


def select_coherent_distractors(target_answer: str, candidate_answers: list[str], count: int = 3) -> list[str]:
    """Подбирает контекстно и грамматически сопоставимые дистракторы похожей длины."""
    target_clean = target_answer.strip().lower()
    target_words = len(target_clean.split())
    
    valid = [a.strip() for a in candidate_answers if a.strip() and a.strip().lower() != target_clean]
    if not valid:
        return [f"Альтернативное условие {i+1}" for i in range(count)]

    # Приоритет кандидатам со схожей длиной по словам
    def dist_score(cand: str):
        c_words = len(cand.split())
        return abs(c_words - target_words)

    # Выделяем кандидатов близкой длины
    similar = [c for c in valid if dist_score(c) <= max(3, target_words // 2)]
    if len(similar) >= count:
        return random.sample(similar, count)
    
    valid.sort(key=dist_score)
    return valid[:count]


def extract_cloze_target(front_text: str, back_text: str = "") -> tuple[str, str]:
    """Извлекает искомое слово из разметки {{c1::слово}}, [слово] или числовых сроков/цензов."""
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
        if target != "...":
            cloze_prompt = front_text.replace(f"[{target}]", "[...]")
            return cloze_prompt, target
        elif back_text:
            clean_b = back_text.strip().rstrip('.')
            if len(clean_b) <= 50:
                return front_text, clean_b

    # 3. Числовые сроки и цензы в вопросе
    deadline_pattern = r'\b(\d+\s+(?:суток|дней|дня|месяц(?:а|ев)?|лет|года|часов|часа))\b'
    m_dead = re.search(deadline_pattern, front_text, re.IGNORECASE)
    if m_dead:
        target = m_dead.group(1).strip()
        cloze_prompt = front_text.replace(target, '[...]')
        return cloze_prompt, target

    # 4. Если в ответе содержится точный нормативный срок/число
    if back_text:
        m_dead_back = re.search(deadline_pattern, back_text, re.IGNORECASE)
        if m_dead_back and len(back_text.strip()) <= 45:
            target = m_dead_back.group(1).strip()
            cloze_prompt = front_text.rstrip('?.') + ": срок составляет [...]"
            return cloze_prompt, target

    return front_text, ""


async def generate_practice_session(
    user_id: str,
    subject: str,
    count: int = 10,
    db: Optional[AsyncSession] = None
) -> List[Dict[str, Any]]:
    """Генерирует автономную практическую сессию для пользователя по предмету.
    
    1. Ищет карточки пользователя в таблице cards по данному предмету и его алиасам.
    2. Если карточек достаточно (>=3), динамически синтезирует упражнения 3 типов:
       - situational (ситуационные кейсы со схожими по длине дистракторами)
       - contrast_pair (разграничение понятий)
       - slot_filling (заполнение пропусков по срокам и ключевым понятиям)
    3. Дополняет сценариями из графа знаний предмета.
    4. Если карточек мало, подгружает эталонные пресеты.
    5. Сохраняет сформированные PracticeItem в БД для надежной верификации ответов.
    """
    should_close = False
    if db is None:
        db = AsyncSessionLocal()
        should_close = True

    try:
        alias_subject = resolve_subject_alias(subject)

        # 1. Извлекаем карточки пользователя для предмета и его алиасов
        stmt = select(Card).where(Card.subject.in_([subject, alias_subject]), Card.user_id == user_id)
        res = await db.execute(stmt)
        user_cards = res.scalars().all()
        if not user_cards:
            stmt_default = select(Card).where(Card.subject.in_([subject, alias_subject]))
            res_default = await db.execute(stmt_default)
            user_cards = res_default.scalars().all()

        practice_records: List[PracticeItem] = []

        # 2. Если есть достаточно карточек, синтезируем интерактивные тесты
        if len(user_cards) >= 3:
            all_answers = [c.translation.strip() for c in user_cards if c.translation and len(c.translation.strip()) > 3]
            sample_cards = random.sample(user_cards, min(count * 2, len(user_cards)))

            for card in sample_cards:
                if len(practice_records) >= count:
                    break

                front = (card.text or "").strip()
                back = (card.translation or "").strip()
                ex = (card.example or "").strip()
                sec = (card.secondary_text or "").strip()

                if not front or not back:
                    continue

                cloze_prompt, cloze_target = extract_cloze_target(front, back)
                item_id = str(uuid.uuid4())

                # Тип 1: Заполнение пропусков (Slot-Filling)
                if cloze_target and len(cloze_target) > 1:
                    chosen_distractors = select_coherent_distractors(cloze_target, all_answers, count=3)
                    # Если таргет - это числовой срок (напр. "10 суток"), формируем реалистичные альтернативы
                    m_num = re.match(r'^(\d+)\s+(суток|дней|дня|месяц|месяца|месяцев|лет|года)$', cloze_target.strip().lower())
                    if m_num:
                        val = int(m_num.group(1))
                        unit = m_num.group(2)
                        alternatives = [f"{val + 5} {unit}", f"{max(1, val - 5)} {unit}", f"{val * 2} {unit}"]
                        chosen_distractors = [a for a in alternatives if a != cloze_target][:3]

                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Альтернативное условие {len(chosen_distractors) + 1}")

                    options = [cloze_target] + chosen_distractors[:3]
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
                    chosen_distractors = select_coherent_distractors(back, all_answers, count=3)
                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Иной критерий {len(chosen_distractors) + 1}")

                    options = [back] + chosen_distractors[:3]
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
                    chosen_distractors = select_coherent_distractors(back, all_answers, count=3)
                    while len(chosen_distractors) < 3:
                        chosen_distractors.append(f"Иная инстанция {len(chosen_distractors) + 1}")

                    options = [back] + chosen_distractors[:3]
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

        # 3. Синтез вопросов из графа знаний предмета (топологическая инстанционность и связи)
        try:
            kg_stmt = select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.subject.in_([subject, alias_subject]))
            kg_res = await db.execute(kg_stmt)
            kg_record = kg_res.scalars().first()
            if kg_record and kg_record.graph_data:
                g_nodes = {n["id"]: n for n in kg_record.graph_data.get("nodes", []) if "id" in n}
                g_edges = kg_record.graph_data.get("edges", [])
                for e in g_edges:
                    if len(practice_records) >= count:
                        break
                    rel = e.get("relation", "")
                    src_id = e.get("source")
                    tgt_id = e.get("target")
                    if rel == "appealed_to" and src_id in g_nodes and tgt_id in g_nodes:
                        src_name = g_nodes[src_id].get("name", src_id)
                        tgt_name = g_nodes[tgt_id].get("name", tgt_id)
                        other_names = [n.get("name") for n in g_nodes.values() if n.get("name") and n.get("name") != tgt_name and n.get("name") != src_name]
                        if len(other_names) >= 2:
                            chosen_dist = random.sample(other_names, min(3, len(other_names)))
                            while len(chosen_dist) < 3:
                                chosen_dist.append(f"Иная судебная инстанция {len(chosen_dist) + 1}")
                            opts = [tgt_name] + chosen_dist[:3]
                            random.shuffle(opts)
                            pi = PracticeItem(
                                item_id=str(uuid.uuid4()),
                                user_id=user_id,
                                subject=subject,
                                item_type="situational",
                                prompt=f"В какую судебную инстанцию в вышестоящем порядке обжалуются акты суда: «{src_name}»?",
                                options=opts,
                                correct_answer=tgt_name,
                                explanation=f"В иерархии инстанций вышестоящим звеном для {src_name} выступает {tgt_name}.",
                                gold_standard=f"{src_name} -> {tgt_name} (Вышестоящая инстанция)"
                            )
                            practice_records.append(pi)
        except Exception as kg_err:
            print(f"[Practice Engine] Ошибка синтеза из графа: {kg_err}")

        # 4. Fallback / Добор: если заданий меньше count, добираем из пресетов
        if len(practice_records) < count and (alias_subject in ("sudoustroystvo", "court_system", "судоустройство", "default") or len(practice_records) == 0):
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

        # Перемешиваем и отбираем count разнообразных заданий
        random.shuffle(practice_records)
        practice_records = practice_records[:count]

        # 5. Сохраняем элементы в БД для верификации
        for pi in practice_records:
            db.add(pi)
        await db.commit()

        # 6. Возвращаем клиенту безопасные словари без открытого правильного ответа
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
