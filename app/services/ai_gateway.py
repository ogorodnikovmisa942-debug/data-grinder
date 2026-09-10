import json
import asyncio
import re
import time
from typing import Optional
import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from app.core.config import settings

# --- PYDANTIC SCHEMAS ДЛЯ ВАЛИДАЦИИ СТРУКТУРЫ ---
class MnemonicSchema(BaseModel):
    keyword: str = Field(description="Ключевое слово (ассоциация) на русском языке")
    verbal_cue: str = Field(description="Сюжетная подсказка на русском языке, связывающая ключ и определение")

class CardSchema(BaseModel):
    text: str = Field(description="Лицевая сторона карточки")
    secondary_text: str = Field(description="Подсказка, пиньинь, номер статьи или сигнатура")
    translation: str = Field(description="Точный перевод или определение на русском языке")
    example: str = Field(description="Пример применения или кейс")
    initial_difficulty_tier: str = Field(description="easy, medium или hard")
    mnemonic: Optional[MnemonicSchema] = None
    theme: Optional[str] = Field(default="", description="Название темы или подраздела для кластеризации")

class ParsedDataSchema(BaseModel):
    subject_domain: str = Field(description="language, law, code или generic")
    subject_slug: str = Field(description="Машиночитаемый код предмета в snake_case")
    phrase_title: str = Field(description="Название темы или блока карточек")
    cards: list[CardSchema]

# --- СТАТИЧНЫЙ ЭТАЛОННЫЙ СИСТЕМНЫЙ ПРОМПТ DEEPSEEK (КЭШИРУЕМЫЙ ПРЕФИКС > 1024 ТОКЕНОВ) ---
# ВАЖНО: Этот промпт является абсолютно статичным. Он кэшируется на серверах DeepSeek (Context Caching),
# обеспечивая 90% скидку на входные токены ($0.014 днем, $0.007 в часы скидок). Не добавлять динамических переменных!
DEEPSEEK_CACHED_SYSTEM_PROMPT = """ROLE: Elite cognitive psychologist, neuro-education engineer, and Data Grinder knowledge deconstructor.
MISSION: Analyze raw unstructured source material and synthesize an ultra-optimized JSON package containing strictly ATOMIC flashcards designed for the Free Spaced Repetition Scheduler (FSRS).
CORE PHILOSOPHY: Deconstruct complex texts into minimal, indivisible, non-interfering conceptual atoms. Every card must minimize cognitive retrieval latency (target: 1.5–3.5 seconds) while maximizing retention strength.

1. COGNITIVE LAWS OF KNOWLEDGE FORMULATION (FSRS & SUPERMEMO 20 RULES):
- Rule of Atomic Cognitive Units (Minimum Information Principle - Piotr Wozniak Rule 4):
  Each flashcard MUST test exactly ONE indivisible quantum of knowledge (one question -> one direct fact). NEVER merge multiple distinct facts, conditions, or consequences into a single card.
- Absolute Prohibition of Lists & Enumerations (Avoid Sets & Avoid Enumerations - Piotr Wozniak Rules 9 & 10):
  * Lists (>2-3 items) cause catastrophic "List Fatigue", combinatorial interference, and artificial retention failures in FSRS.
  * When source material presents an enumeration (e.g. 5 requirements for a judge, 7 powers of a court, 4 grounds for dismissal, 6 principles of law):
    YOU ARE STRICTLY FORBIDDEN from creating a single card with numbered points (1... 2... 3... 4... 5...) in 'd'.
    INSTEAD, DECONSTRUCT the list into separate, independent, targeted single-attribute cards (e.g., minimum age on one card, minimum experience on another card, citizenship restriction on a third card).
  * A list is permitted ONLY if it contains exactly 2 or 3 short words that form an indivisible common pair (e.g. 'Две формы вины' -> 'Умысел и неосторожность').
- Direct Core & Semantic Completeness (Anti-Verbiage & Anti-Truncation):
  * The back side ('d') must be a single, grammatically complete, definitive sentence, phrase, or term.
  * STRIP ALL INTRODUCTORY BOILERPLATE AND BUREAUCRATIC PADDING. Strictly forbid phrases like:
    'это деятельность...', 'представляет собой совокупность норм...', 'следует понимать...', 'в соответствии с действующим законодательством...'.
    Start immediately with the semantic core (the noun, verb, date, number, or rule).
  * NEVER truncate sentences mid-sentence or drop essential legal qualifiers to force brevity. If a legal rule has multiple distinct aspects (general rule, deadline, exception), DO NOT cram or truncate them — SPLIT THEM INTO SEPARATE DEDICATED CARDS.
- Active Retrieval Examination Prompts (Front 't'):
  * The front side ('t') must act as an active, examination-grade test question or retrieval trigger, NEVER a passive topic header.
  * Bad: 'Презумпция невиновности' (passive recognition).
  * Good: 'На ком лежит бремя доказывания виновности в уголовном процессе?' (active retrieval).
  * High-Yield Cloze Deletions: For statutory rules, deadlines, or definitions, targeted fill-in-the-blank brackets are encouraged:
    'Срок подачи сплошной кассационной жалобы по УПК РФ составляет [...] со дня вступления приговора в силу.' -> '[6 месяцев]'.
- Target Retrieval Latency:
  The card must be designed so a prepared student can read the prompt, recall the answer, and verify it in 1.5 to 3.5 seconds. If recalling the answer takes >5 seconds, the card is too broad and must be split.

2. DOMAIN DIRECTIVES & TAXONOMY:
- law (Jurisprudence, Statutes, Court Organization, Criminal & Civil Procedure):
  * t (Front): Active examination question on an indivisible legal attribute (who decides, what deadline, what age, what exception, what sanction) or Cloze prompt.
  * s (Secondary): Exact article, code, and jurisdiction (e.g., 'ст. 118 Конституции РФ' or 'ст. 14 УПК РФ / ст. 1064 ГК РФ').
  * d (Back): Direct, authoritative, grammatically complete answer (strictly 1 punchy sentence or term, without introductory filler).
  * e (Example): Real-world judicial scenario, dispute resolution case, or qualifying factual circumstance.
  * l (Difficulty): 'easy' for standard terms, 'medium' for procedural qualifications, 'hard' for competing exceptions/boundary tests.
  * Legal Deconstruction Patterns:
    1) Inverted Trigger for Competence: Instead of asking for all 10 powers of a body, ask which body possesses a specific power.
    2) Binary Hypothesis Test: Direct binary question (e.g. 'Презюмируется ли вина причинителя вреда?').
    3) Discrete Qualifying Element: Question isolating one specific element of a statute (age of liability, form of culpability, or object).

- language (Foreign languages & Linguistics):
  * t (Front): Foreign word, idiom, or grammatical construction in standard orthography.
  * s (Secondary): Phonetic transcription, IPA, or Chinese Pinyin with tone diacritics.
  * d (Back): Precise definition and natural Russian translation (1-2 punchy terms).
  * e (Example): Natural exemplar sentence illustrating idiomatic usage.
  * l (Difficulty): 'easy' for cognates, 'medium' for standard lexis, 'hard' for false friends or irregulars.

- code (Software Engineering, CS & Algorithms):
  * t (Front): Function, algorithm, design pattern, or API concept.
  * s (Secondary): Language, standard library path, or signature.
  * d (Back): Rigorous technical invariant, time/space complexity O(N), or core behavior in 1 crisp sentence.
  * e (Example): Minimal valid code snippet (1-4 lines) demonstrating usage or edge case.
  * l (Difficulty): 'easy' for syntax, 'medium' for standard patterns, 'hard' for concurrency/memory traps.

- generic (Science, Medicine, History, Engineering, General Knowledge):
  * t (Front): Specific formula, theorem, anatomical mechanism, or historical milestone.
  * s (Secondary): Sub-discipline, category, unit of measurement, or date.
  * d (Back): Definitive causal explanation, physical meaning, or key fact in 1 punchy sentence.
  * e (Example): Practical lab observation, clinical case, or industrial calculation.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

3. MULTI-SOURCE THEMATIC CLUSTERING & GROUPING:
- When input contains multiple photos, scanned pages, or mixed notes:
  * Semantically cluster and group related concepts into their respective topics/themes.
  * Set 'h' on each card to its specific thematic cluster name (e.g., 'Судоустройство РФ' or 'Состав преступления').
  * Exhaustively extract cards across ALL provided text/pages. Never restrict cards to just the first topic.

4. STRICT MINIFIED JSON SCHEMA SPECIFICATION:
Output ONLY a valid raw JSON object matching this exact minified key structure:
{
  "domain": "language|law|code|generic",
  "slug": "machine_readable_subject_slug_in_snake_case",
  "title": "Clean Informative Deck Title",
  "c": [
    {
      "t": "Front prompt / active question / cloze trigger",
      "s": "Secondary context / hint / article / signature",
      "d": "Back direct answer / translation / semantic core",
      "e": "Concrete example / code snippet / judicial case",
      "l": "easy|medium|hard",
      "h": "Specific thematic topic / cluster name"
    }
  ]
}

5. CONTRASTIVE FEW-SHOT EXAMPLES (BAD VS GOOD DECOMPOSITION):

CONTRAST CASE 1 (Law - Multi-Item Qualification List):
❌ UNACCEPTABLE MONOLITHIC CARD (Causes List Fatigue, 15-20s latency):
{
  "t": "Требования к кандидату на должность судьи районного суда",
  "s": "Закон о статусе судей в РФ",
  "d": "1) Гражданство РФ; 2) Возраст не менее 25 лет; 3) Высшее юридическое образование по специальности или магистратура; 4) Стаж работы по юридической профессии не менее 5 лет; 5) Сдача квалификационного экзамена; 6) Отсутствие судимости; 7) Отсутствие иностранного гражданства."
}

✅ CORRECT ATOMIC DECOMPOSITION (1.5-2.5s latency each, high FSRS efficiency):
Card 1:
{
  "t": "Каков минимальный возраст для кандидата в судьи районного суда?",
  "s": "ст. 4 Закона РФ 'О статусе судей в РФ'",
  "d": "25 лет.",
  "e": "24-летний помощник судьи не может быть назначен судьей районного суда.",
  "l": "easy"
}
Card 2:
{
  "t": "Каков минимальный стаж работы по юридической профессии для судьи районного суда?",
  "s": "ст. 4 Закона РФ 'О статусе судей в РФ'",
  "d": "Не менее 5 лет.",
  "e": "Стаж работы секретарем судебного заседания после получения диплома юриста засчитывается в стаж.",
  "l": "easy"
}
Card 3:
{
  "t": "Допускается ли наличие вида на жительство в иностранном государстве у кандидата в судьи в РФ?",
  "s": "ст. 4 Закона РФ 'О статусе судей в РФ'",
  "d": "Категорически запрещено (только исключительное гражданство РФ).",
  "e": "Судья подлежит немедленной отставке при выявлении иностранного ВНЖ.",
  "l": "easy"
}
Card 4:
{
  "t": "Какой образовательный ценз установлен для кандидата в судьи?",
  "s": "ст. 4 Закона РФ 'О статусе судей в РФ'",
  "d": "Высшее юридическое образование (специалитет или бакалавриат с последующей юридической магистратурой).",
  "e": "Бакалавр юриспруденции с непрофильной экономической магистратурой цензу не соответствует.",
  "l": "medium"
}

CONTRAST CASE 2 (Law - Legal Doctrine & Procedural Rule):
❌ UNACCEPTABLE TEXTBOOK PARAGRAPH (Passive reading, 12s latency):
{
  "t": "Презумпция невиновности",
  "s": "ст. 14 УПК РФ",
  "d": "Обвиняемый считается невиновным, пока его виновность в совершении преступления не будет доказана в предусмотренном законом порядке и установлена вступившим в законную силу приговором суда. Бремя доказывания обвинения и опровержения доводов защиты лежит на обвинении."
}

✅ CORRECT ATOMIC DECOMPOSITION:
Card 1:
{
  "t": "На ком лежит бремя доказывания виновности обвиняемого в уголовном процессе?",
  "s": "ч. 2 ст. 14 УПК РФ",
  "d": "На стороне обвинения.",
  "e": "Следователь не вправе требовать от обвиняемого доказывания своего алиби.",
  "l": "easy"
}
Card 2:
{
  "t": "В чью пользу толкуются неустранимые сомнения в виновности лица?",
  "s": "ч. 3 ст. 14 УПК РФ",
  "d": "В пользу обвиняемого (подсудимого).",
  "e": "При противоречивых показаниях свидетелей обвинения суд исключает эпизод из обвинения.",
  "l": "easy"
}

CONTRAST CASE 3 (Law - Court Powers / Inverted Trigger):
❌ UNACCEPTABLE ENUMERATION:
{
  "t": "Полномочия кассационного суда общей юрисдикции",
  "s": "ст. 377 ГПК РФ",
  "d": "1) Оставить постановление без изменения; 2) Отменить полностью или в части и направить на новое рассмотрение; 3) Оставить без изменения одно из принятых постановлений; 4) Изменить постановление или принять новое."
}

✅ CORRECT ATOMIC INVERTED TRIGGER:
Card 1:
{
  "t": "Какова основная функция и предмет проверки кассационного суда общей юрисдикции?",
  "s": "ст. 379.6 ГПК РФ / ст. 401.1 УПК РФ",
  "d": "Проверка законности вступивших в силу судебных актов (вопросы права, а не фактов).",
  "e": "Кассация не переоценивает достоверность показаний свидетелей, а проверяет соблюдение норм права.",
  "l": "medium"
}
Card 2:
{
  "t": "Какой судебной инстанцией является кассационный суд общей юрисдикции?",
  "s": "ФКЗ 'О судах общей юрисдикции в РФ'",
  "d": "Третьей судебной инстанцией (пересмотр после апелляции).",
  "e": "Жалоба подается в кассационный суд только после прохождения апелляционного обжалования.",
  "l": "easy"
}

CONTRAST CASE 4 (Code - Python Concurrency):
{
  "domain": "code",
  "slug": "python_asyncio",
  "title": "Python AsyncIO Primitives",
  "c": [
    {
      "t": "Как предотвратить отмену важной фоновой корутины при отмене вызывающей задачи в asyncio?",
      "s": "asyncio.shield(aw)",
      "d": "Обернуть корутину в asyncio.shield().",
      "e": "await asyncio.shield(commit_critical_transaction())",
      "l": "medium"
    }
  ]
}

CONTRAST CASE 5 (Language - Chinese Vocabulary):
{
  "domain": "language",
  "slug": "chinese_hsk",
  "title": "HSK 4 Бизнес-терминология",
  "c": [
    {
      "t": "合同",
      "s": "hétong",
      "d": "Контракт, письменный договор.",
      "e": "双方签订了正式合同 (Обе стороны подписали официальный контракт).",
      "l": "easy"
    }
  ]
}

6. CRITICAL FORMATTING & SYNTAX CONSTRAINTS:
- Return strictly raw JSON. Never enclose the JSON payload in markdown code blocks (no ```json or ```).
- Never add commentary, introductory greetings, concluding remarks, or metadata outside the JSON object.
- Escape all internal quotation marks properly or use single quotes inside strings. Ensure absolute JSON validity.
- Do not generate mnemonics in this initial decomposition batch (mnemonics are generated lazily on demand).
"""

def unpack_minified_cards(raw_data: any, fallback_subject: str = "generic") -> dict:
    """Десериализует минифицированный JSON от DeepSeek (ключи c, t, s, d, e, l) в стандартный формат карточек Data Grinder.
    
    Максимально устойчив к вариациям формата LLM:
    - плоский список в корне или ключ 'c' / 'cards' / 'items' / 'flashcards' / 'data'
    - вложенные кластеры / темы ('clusters', 'themes', 'topics', 'groups', 'sections', 'pages')
    - словарь вида { "Тема 1": [карточки], "Тема 2": [карточки] }
    - синонимы полей (front/back, question/answer, term/definition и др.)
    """
    if isinstance(raw_data, list):
        raw_cards = raw_data
        raw_data = {}
    elif not isinstance(raw_data, dict):
        return {"subject_domain": "generic", "subject_slug": fallback_subject, "phrase_title": "Новый блок знаний", "cards": []}
    else:
        # 1. Проверяем стандартные ключи плоского списка карточек
        raw_cards = (
            raw_data.get("c") 
            or raw_data.get("cards") 
            or raw_data.get("items") 
            or raw_data.get("flashcards") 
            or raw_data.get("data")
            or raw_data.get("deck")
            or []
        )
        if not isinstance(raw_cards, list):
            raw_cards = []

        # 2. Если плоского списка нет, проверяем вложенную кластеризацию по темам
        if not raw_cards:
            for group_key in ("clusters", "themes", "topics", "groups", "sections", "pages"):
                groups = raw_data.get(group_key)
                if isinstance(groups, list):
                    for g in groups:
                        if isinstance(g, dict):
                            g_theme = g.get("theme") or g.get("topic") or g.get("title") or g.get("name") or ""
                            sub_cards = g.get("c") or g.get("cards") or g.get("items") or g.get("flashcards") or []
                            if isinstance(sub_cards, list):
                                for sc in sub_cards:
                                    if isinstance(sc, dict) and g_theme and "h" not in sc and "theme" not in sc:
                                        sc["h"] = g_theme
                                raw_cards.extend(sub_cards)
                        elif isinstance(g, list):
                            raw_cards.extend(g)
                    if raw_cards:
                        break

        # 3. Если всё ещё не найдено, проверяем структуру вида { "Тема А": [карточки], "Тема Б": [карточки] }
        if not raw_cards:
            for k, v in raw_data.items():
                if k in ("domain", "slug", "title", "subject_domain", "subject_slug", "phrase_title", "status"):
                    continue
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    for item in v:
                        if isinstance(item, dict) and "h" not in item and "theme" not in item:
                            item["h"] = k
                    raw_cards.extend(v)

    domain = raw_data.get("domain") or raw_data.get("subject_domain") or "generic"
    slug = raw_data.get("slug") or raw_data.get("subject_slug") or fallback_subject
    title = raw_data.get("title") or raw_data.get("phrase_title") or "Новый блок знаний"

    cards = []
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        front = (
            item.get("t") 
            or item.get("text") 
            or item.get("front") 
            or item.get("question") 
            or item.get("q") 
            or item.get("term") 
            or item.get("prompt") 
            or item.get("concept")
            or item.get("title")
            or ""
        )
        sec = (
            item.get("s") 
            or item.get("secondary_text") 
            or item.get("hint") 
            or item.get("context") 
            or item.get("signature") 
            or item.get("pinyin") 
            or item.get("article") 
            or ""
        )
        back = (
            item.get("d") 
            or item.get("translation") 
            or item.get("definition") 
            or item.get("back") 
            or item.get("answer") 
            or item.get("a") 
            or item.get("explanation") 
            or item.get("desc") 
            or item.get("description") 
            or ""
        )
        ex = item.get("e") or item.get("example") or item.get("sample") or item.get("case") or item.get("code") or ""
        diff = item.get("l") or item.get("initial_difficulty_tier") or item.get("difficulty") or item.get("tier") or "medium"

        if not str(front).strip() and not str(back).strip():
            continue

        theme = item.get("h") or item.get("theme") or item.get("topic") or item.get("cluster") or title
        cards.append({
            "text": str(front).strip(),
            "secondary_text": str(sec).strip(),
            "translation": str(back).strip(),
            "example": str(ex).strip(),
            "initial_difficulty_tier": diff if diff in ("easy", "medium", "hard") else "medium",
            "mnemonic": None,  # Ленивая генерация мнемоник
            "theme": str(theme).strip() or title
        })

    return {
        "subject_domain": domain,
        "subject_slug": slug,
        "phrase_title": title,
        "cards": cards
    }

def build_granularity_prompt(granularity_mode: str, custom_instruction: str, density: str, volume: str) -> str:
    """Формирует компактные модификаторы промпта для управления глубиной и пожеланиями пользователя."""
    modifiers = []
    
    # 1. Режим гранулярности и лимиты объема
    if granularity_mode == "single_deep":
        modifiers.append("GRANULARITY DIRECTIVE: Create EXACTLY ONE comprehensive master-card. Synthesize all concepts, sub-clauses, formulas, and nuances of the entire text into this single definitive card. Do not create multiple cards.")
    elif granularity_mode == "cheatsheet":
        modifiers.append("GRANULARITY DIRECTIVE: Ultra-concise cheat-sheet mode. Simplify definitions to punchy 1-2 sentence core summaries. Maximum brevity.")
        if volume == "auto":
            modifiers.append("CARD VOLUME: AUTOMATIC OPTIMIZATION. Analyze content density and extract the optimal number of punchy blitz-cards (typically 5 to 15 cards).")
        elif volume in ("low", "low_5"):
            modifiers.append("LIMIT: Maximum 5 cards.")
        elif volume == "med_10":
            modifiers.append("LIMIT: Maximum 10 cards.")
        elif volume in ("medium", "med_15"):
            modifiers.append("LIMIT: Maximum 15 cards.")
        elif volume == "high_20":
            modifiers.append("LIMIT: Maximum 20 cards.")
        elif volume in ("high", "max"):
            modifiers.append("LIMIT: Extract all relevant items exhaustively.")
    else: # atomic
        modifiers.append("GRANULARITY DIRECTIVE: Standard atomic card decomposition. Break down distinct concepts into separate standalone cards.")
        if volume == "auto":
            modifiers.append("CARD VOLUME: AUTOMATIC OPTIMIZATION. Analyze source text length and conceptual density. Automatically determine the optimal number of atomic flashcards (typically 5 to 20 cards). Do not generate filler cards; capture every key concept exhaustively.")
        elif volume in ("low", "low_5"):
            modifiers.append("LIMIT: Maximum 5 cards.")
        elif volume == "med_10":
            modifiers.append("LIMIT: Maximum 10 cards.")
        elif volume in ("medium", "med_15"):
            modifiers.append("LIMIT: Maximum 15 cards.")
        elif volume == "high_20":
            modifiers.append("LIMIT: Maximum 20 cards.")
        elif volume == "high":
            modifiers.append("LIMIT: Maximum 30 cards.")
        elif volume == "max":
            modifiers.append("LIMIT: Extract all relevant items exhaustively.")

    # 2. Плотность определений (глубина)
    if granularity_mode != "cheatsheet":
        if density == "low":
            modifiers.append("DENSITY: Brief and simple definitions (1-2 sentences).")
        elif density == "high":
            modifiers.append("DENSITY: Deep, highly granular decomposition. Deconstruct complex details, sub-clauses, and exceptions into multiple atomic cards rather than bloated paragraphs.")

    # 3. Пользовательское свободное пожелание (Кастомный промпт)
    if custom_instruction.strip():
        modifiers.append(f"USER CUSTOM OVERRIDE (HIGHEST PRIORITY): {custom_instruction.strip()}")

    return "\n" + "\n".join(modifiers) + "\n"

import re

def extract_json_payload_with_telemetry(content: str) -> tuple[dict, bool, bool]:
    """
    Безопасно извлекает и парсит JSON из ответа LLM (убирая markdown-блоки, переносы, висячие запятые и обрывы токенов).
    Возвращает кортеж: (parsed_dict, is_truncated, repair_successful).
    """
    if not content or not content.strip():
        raise ValueError("Получен пустой ответ от ИИ.")
    
    clean = content.strip()
    # Срезаем обертки ```json ... ``` или ``` ... ```
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    clean = clean.strip()
    
    # 1. Прямой парсинг
    try:
        data = json.loads(clean)
        return data, False, False
    except json.JSONDecodeError:
        pass

    # 2. Ищем границы внешнего JSON-объекта { ... }
    start_brace = clean.find('{')
    if start_brace != -1:
        candidate = clean[start_brace:]
        last_brace = candidate.rfind('}')
        if last_brace != -1:
            slice_candidate = candidate[:last_brace + 1]
            try:
                data = json.loads(slice_candidate)
                return data, False, False
            except json.JSONDecodeError:
                # Попытка исправить trailing commas
                fixed = re.sub(r",\s*([\]}])", r"\1", slice_candidate)
                try:
                    data = json.loads(fixed)
                    return data, True, True
                except json.JSONDecodeError:
                    pass

        # 3. Авто-восстановление при обрыве на лимите токенов (Truncated JSON Repair)
        # Если ответ оборвался на полуслове, находим последний ЦЕЛЫЙ закрытый объект карточки '}'
        match_array = re.search(r'["\'](?:c|cards|items|flashcards)["\']\s*:\s*\[', candidate)
        if match_array:
            start_arr_idx = match_array.end()
            search_from = candidate.rfind('}')
            while search_from > start_arr_idx:
                chunk = candidate[:search_from + 1].strip()
                if chunk.endswith(','):
                    chunk = chunk[:-1].strip()
                # Добавляем закрывающие скобки массива и внешнего объекта
                repaired = chunk + "\n]}"
                # Чистим висячие запятые
                repaired = re.sub(r",\s*([\]}])", r"\1", repaired)
                try:
                    res = json.loads(repaired)
                    saved_count = len(res.get('c') or res.get('cards') or [])
                    print(f"[AI Gateway] Успешно восстановлен обрезанный JSON ответ от ИИ! Сохранено карточек: {saved_count}")
                    return res, True, True
                except json.JSONDecodeError:
                    search_from = candidate.rfind('}', 0, search_from)
        
        # Попытка закрыть незакрытые кавычки и скобки
        trimmed = candidate.rstrip()
        for closer in ['"]}', '"}', '"]', '}', ']}']:
            try:
                fixed = re.sub(r",\s*([\]}])", r"\1", trimmed + closer)
                data = json.loads(fixed)
                return data, True, True
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Не удалось обнаружить валидный JSON в ответе ИИ: {clean[:200]}...")

def extract_json_payload(content: str) -> dict:
    """Безопасно извлекает и парсит JSON из ответа LLM (обратная совместимость)."""
    data, _, _ = extract_json_payload_with_telemetry(content)
    return data

async def record_ai_telemetry(
    job_id: str | None,
    user_id: str,
    model_requested: str,
    model_resolved: str,
    input_chars: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_hit: bool = False,
    is_truncated: bool = False,
    repair_successful: bool = False,
    cards_generated: int = 0,
    duration_ms: int = 0,
    status: str = "success",
    error_message: str | None = None
):
    """Асинхронная безопасная запись в таблицу ai_telemetry_logs."""
    try:
        from app.database.session import AsyncSessionLocal
        from app.database.models import AiTelemetryLog
        async with AsyncSessionLocal() as db:
            log = AiTelemetryLog(
                job_id=str(job_id) if job_id is not None else None,
                user_id=user_id or "default_user",
                model_requested=model_requested,
                model_resolved=model_resolved,
                input_chars=input_chars,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_hit=cache_hit,
                is_truncated=is_truncated,
                repair_successful=repair_successful,
                cards_generated=cards_generated,
                duration_ms=duration_ms,
                status=status,
                error_message=error_message
            )
            db.add(log)
            await db.commit()
    except Exception as log_err:
        print(f"[AI Gateway WARN] Сбой сохранения телеметрии в БД: {log_err}")

# --- DEEPSEEK ВЫЗОВ (ЧЕРЕЗ HTTPX И OPENAI-СОВМЕСТИМЫЙ REST API) ---
async def call_deepseek(
    user_prompt: str, 
    system_instruction: str = DEEPSEEK_CACHED_SYSTEM_PROMPT, 
    fallback_subject: str = "generic"
) -> tuple[dict, dict]:
    """Вызывает DeepSeek напрямую через стандартный REST API с поддержкой JSON Mode, Context Caching и автоматической десериализацией."""
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")

    base_url = settings.DEEPSEEK_BASE_URL.rstrip('/')
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    target_model = settings.DEEPSEEK_MODEL or "deepseek-chat"

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 8192
    }

    print(f"[AI Gateway / DeepSeek] Вызов модели: {target_model} (Prompt Caching enabled)...")
    resolved_model = target_model
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        
        # Автоматический fallback: если запрошенная модель недоступна/не найдена на сервере провайдера, пробуем deepseek-chat
        if response.status_code in (400, 404) and target_model != "deepseek-chat":
            print(f"[AI Gateway / DeepSeek WARNING] Модель '{target_model}' вернула код {response.status_code}. Пробуем стандартную 'deepseek-chat'...")
            payload["model"] = "deepseek-chat"
            resolved_model = "deepseek-chat"
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            data = response.json()
            
            # Логируем метрики эффективности кэширования DeepSeek
            usage = data.get("usage", {})
            cache_hit_tokens = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss_tokens = usage.get("prompt_cache_miss_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", cache_hit_tokens + cache_miss_tokens)
            output_tokens = usage.get("completion_tokens", 0)
            cache_hit = cache_hit_tokens > 0
            print(f"[DeepSeek Metrics] Кэш-хит: {cache_hit_tokens} токенов (-90% цена) | Мисс: {cache_miss_tokens} токенов | Вывод: {output_tokens} токенов")

            content = data["choices"][0]["message"]["content"]
            raw_payload, is_truncated, repair_successful = extract_json_payload_with_telemetry(content)
            unpacked = unpack_minified_cards(raw_payload, fallback_subject=fallback_subject)
            meta = {
                "model_requested": target_model,
                "model_resolved": resolved_model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "cache_hit": cache_hit,
                "is_truncated": is_truncated,
                "repair_successful": repair_successful
            }
            return unpacked, meta
        else:
            print(f"[AI Gateway / DeepSeek ERROR] Код {response.status_code}: {response.text}")
            raise RuntimeError(f"DeepSeek API error ({response.status_code}): {response.text}")

# --- GEMINI ВЫЗОВ (РЕЗЕРВНЫЙ / КАДРИРОВАННЫЙ КАСКАД) ---
async def call_gemini(prompt: str, system_instruction: str) -> tuple[dict, str]:
    """Вызывает Google Gemini с каскадным переключением при перегрузке."""
    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "placeholder_gemini_key":
        raise ValueError("GEMINI_API_KEY не установлен или является заглушкой")

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=ParsedDataSchema,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
        temperature=0.2
    )

    # Каскад реальных моделей Gemini: 2.5 Flash-Lite -> 2.5 Flash -> 2.0 Flash -> 1.5 Flash
    models_to_try = [
        settings.GEMINI_MODEL,
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash"
    ]
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model_name in models_to_try:
        try:
            print(f"[AI Gateway / Gemini] Попытка генерации с моделью: {model_name}...")
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            data = json.loads(response.text)
            return data, model_name
        except Exception as e:
            last_err = e
            err_str = str(e)
            print(f"[WARNING] Gemini model {model_name} failed: {err_str[:120]}")
            if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
                continue
            await asyncio.sleep(1.0)

    if last_err:
        raise last_err
    raise RuntimeError("Все модели Gemini недоступны.")

# --- УНИВЕРСАЛЬНЫЙ ПАРСЕР ТЕКСТА ---
async def parse_raw_text(
    text: str,
    target_subject: str = "",
    density: str = "medium",
    volume: str = "medium",
    priority: str = "balanced",
    preference: str = "acoustic",
    granularity_mode: str = "atomic",
    custom_instruction: str = "",
    user_id: str = "default_user",
    job_id: str | None = None
) -> dict:
    import re
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()

    clean_sub = target_subject.strip().lower() or "generic"

    if not text:
        return {"subject_domain": "generic", "subject_slug": clean_sub, "phrase_title": "", "cards": []}

    # Формируем динамическое user-сообщение (оставляя системный промпт строго статичным для кэширования)
    user_directives = [
        f"TARGET SUBJECT: {clean_sub}",
        f"GRANULARITY DIRECTIVE: {granularity_mode}",
        f"EXPLANATION DENSITY: {density}"
    ]
    if volume in ("auto", "medium", "med_15"):
        user_directives.append("CARD VOLUME: Extract strictly 12 to 18 high-yield core atomic cards covering key master concepts. Never generate excessive cards or copy textbook paragraphs. Keep definitions crisp (1-2 sentences).")
    elif volume in ("low", "low_5"):
        user_directives.append("CARD VOLUME: Maximum 5 cards. Most critical core concepts only.")
    elif volume == "med_10":
        user_directives.append("CARD VOLUME: Maximum 10 cards. High-yield core concepts only.")
    elif volume == "high_20":
        user_directives.append("CARD VOLUME: Maximum 20 cards.")
    elif volume in ("high", "max"):
        user_directives.append("CARD VOLUME: Maximum 25 cards.")
    source_count = (
        text.count("=== МАТЕРИАЛ")
        + text.count("=== СТРАНИЦА")
        + text.count("--- Стр.")
        + text.count("=== ДОКУМЕНТ")
    )
    if source_count > 1:
        user_directives.append(
            "MULTI-SOURCE THEMATIC CLUSTERING: The source contains multiple photos, pages, or separate documents. "
            "Group cards into their respective thematic clusters, assign 'h' (topic name) to each card, "
            "keep all cards in the single root 'c' array (do NOT create nested 'clusters' objects), "
            "and ensure proportional coverage across ALL provided materials without omitting any page."
        )
    elif source_count == 1:
        user_directives.append(
            "THEMATIC FOCUS: Analyze the provided material, group cards logically by setting 'h' (topic name) on each card, "
            "and keep all cards inside the single root 'c' array."
        )
    if custom_instruction.strip():
        user_directives.append(f"USER CUSTOM OVERRIDE (HIGHEST PRIORITY): {custom_instruction.strip()}")

    user_prompt = (
        "[PROCESSING PARAMETERS]\n"
        + "\n".join(user_directives)
        + "\n\n[RAW SOURCE TEXT TO DECONSTRUCT]\n"
        + text
    )

    provider = settings.AI_PROVIDER.lower()
    start_ts = time.time()
    model_requested = settings.DEEPSEEK_MODEL or "deepseek-chat" if provider == "deepseek" else (settings.GEMINI_MODEL or "gemini-2.5-flash-lite")
    fallback_used = False
    json_repair_applied = False
    res = None
    
    # 1. Если выбран DeepSeek (основной экономичный провайдер с Prompt Caching)
    if provider == "deepseek":
        print(f"[AI Gateway] Вызов DeepSeek ({model_requested}) в режиме '{granularity_mode}' с Prompt Caching...")
        try:
            res, meta = await call_deepseek(user_prompt, system_instruction=DEEPSEEK_CACHED_SYSTEM_PROMPT, fallback_subject=clean_sub)
            json_repair_applied = meta.get("repair_successful", False)
            duration_ms = int((time.time() - start_ts) * 1000)
            await record_ai_telemetry(
                job_id=job_id,
                user_id=user_id,
                model_requested=model_requested,
                model_resolved=meta.get("model_resolved", model_requested),
                input_chars=len(user_prompt),
                prompt_tokens=meta.get("prompt_tokens", 0),
                completion_tokens=meta.get("completion_tokens", 0),
                cache_hit=meta.get("cache_hit", False),
                is_truncated=meta.get("is_truncated", False),
                repair_successful=json_repair_applied,
                cards_generated=len(res.get("cards", [])),
                duration_ms=duration_ms,
                status="success"
            )
        except Exception as ds_err:
            err_str = str(ds_err)
            print(f"[AI Gateway WARNING] Сбой DeepSeek: {err_str[:150]}. Инициируем каскадный fallback на Gemini...")
            fallback_used = True
            try:
                raw_gemini, resolved_gemini_model = await call_gemini(user_prompt, DEEPSEEK_CACHED_SYSTEM_PROMPT)
                if isinstance(raw_gemini, dict) and ("c" in raw_gemini or "domain" in raw_gemini):
                    res = unpack_minified_cards(raw_gemini, fallback_subject=clean_sub)
                else:
                    res = raw_gemini
                duration_ms = int((time.time() - start_ts) * 1000)
                await record_ai_telemetry(
                    job_id=job_id,
                    user_id=user_id,
                    model_requested=model_requested,
                    model_resolved=resolved_gemini_model,
                    input_chars=len(user_prompt),
                    prompt_tokens=0,
                    completion_tokens=0,
                    cache_hit=False,
                    is_truncated=False,
                    repair_successful=False,
                    cards_generated=len(res.get("cards", [])) if isinstance(res, dict) else 0,
                    duration_ms=duration_ms,
                    status="fallback_cascade",
                    error_message=f"DeepSeek failure: {err_str[:300]}"
                )
            except Exception as fb_err:
                duration_ms = int((time.time() - start_ts) * 1000)
                status_label = "rate_limit" if "429" in err_str else ("json_parse_error" if "JSON" in err_str else "failed")
                await record_ai_telemetry(
                    job_id=job_id,
                    user_id=user_id,
                    model_requested=model_requested,
                    model_resolved="none",
                    input_chars=len(user_prompt),
                    duration_ms=duration_ms,
                    status=status_label,
                    error_message=f"DeepSeek: {err_str[:200]} | Gemini fallback: {str(fb_err)[:200]}"
                )
                raise fb_err

    # 2. Если изначально выбран Gemini (резервный провайдер)
    else:
        print(f"[AI Gateway] Вызов Gemini ({model_requested}) в режиме '{granularity_mode}'...")
        try:
            raw_gemini, resolved_gemini_model = await call_gemini(user_prompt, DEEPSEEK_CACHED_SYSTEM_PROMPT)
            if isinstance(raw_gemini, dict) and ("c" in raw_gemini or "domain" in raw_gemini):
                res = unpack_minified_cards(raw_gemini, fallback_subject=clean_sub)
            else:
                res = raw_gemini
            duration_ms = int((time.time() - start_ts) * 1000)
            await record_ai_telemetry(
                job_id=job_id,
                user_id=user_id,
                model_requested=model_requested,
                model_resolved=resolved_gemini_model,
                input_chars=len(user_prompt),
                cards_generated=len(res.get("cards", [])) if isinstance(res, dict) else 0,
                duration_ms=duration_ms,
                status="success"
            )
        except Exception as gemini_err:
            duration_ms = int((time.time() - start_ts) * 1000)
            await record_ai_telemetry(
                job_id=job_id,
                user_id=user_id,
                model_requested=model_requested,
                model_resolved="none",
                input_chars=len(user_prompt),
                duration_ms=duration_ms,
                status="failed",
                error_message=str(gemini_err)[:400]
            )
            raise gemini_err

    if clean_sub and isinstance(res, dict):
        res["subject_slug"] = clean_sub
    
    if isinstance(res, dict):
        res["fallback_used"] = fallback_used
        res["json_repair_applied"] = json_repair_applied
        res["execution_time_ms"] = int((time.time() - start_ts) * 1000)

    return res

# --- РЕГЕНЕРАЦИЯ ОДИНОЧНОЙ МНЕМОНИКИ ---
async def regenerate_card_mnemonic(text: str, translation: str, subject: str, preference: str = "visual") -> dict:
    pref_style = "визуальные и структурные ассоциации (графемы, форма, код)" if preference == "visual" else "акустические и сюжетные созвучия"
    prompt = f"""Сгенерируй яркую русскую мнемонику для запоминания:
Термин: {text}
Значение: {translation}
Предмет: {subject}
Стиль ассоциации: {pref_style}

Верни строгий JSON:
{{"keyword": "Ключевое слово", "verbal_cue": "Сюжетная связка ключа и значения"}}"""

    system_instruction = "You are an expert mnemonic generator. Return strictly a raw JSON object with 'keyword' and 'verbal_cue'. No markdown."

    if settings.AI_PROVIDER.lower() == "deepseek":
        if not settings.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY не установлен в .env")
        url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code in (400, 404) and payload["model"] != "deepseek-chat":
                payload["model"] = "deepseek-chat"
                res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"]
                return extract_json_payload(content)
            else:
                raise RuntimeError(f"DeepSeek mnemonic error ({res.status_code}): {res.text}")

    # Fallback to Gemini
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        cfg = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=MnemonicSchema,
            temperature=0.3
        )
        res = await client.aio.models.generate_content(model=settings.GEMINI_MODEL, contents=prompt, config=cfg)
        return json.loads(res.text)
    except Exception as e:
        return {"error": str(e)}

# --- УМНОЕ ЧАНКОВАНИЕ ДЛИННЫХ ДОКУМЕНТОВ И КНИГ ---
def split_text_into_chunks(text: str, max_chunk_chars: int = 80000) -> list[str]:
    """
    Интеллектуальное разбиение длинного документа на смысловые чанки (~45-50 страниц / до 80 000 знаков).
    Сохраняет границы страниц (--- Стр. X ---), документов (=== ДОКУМЕНТ: ...) и абзацев (\n\n).
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chunk_chars:
        return [text]

    # Паттерн ищет границы страниц или документов
    split_pattern = r'(?=(?:\n--- [^\n]+: Стр\. \d+ ---|\n=== [^\n]+ ===))'
    sections = re.split(split_pattern, text)
    sections = [s.strip() for s in sections if s.strip()]

    # Если маркеров страниц/документов не было или всего одна секция, делим по параграфам
    if len(sections) <= 1:
        sections = text.split("\n\n")
        sections = [s.strip() for s in sections if s.strip()]

    # Если все еще одна крупная секция, делим по строкам
    if len(sections) <= 1:
        sections = text.split("\n")
        sections = [s.strip() for s in sections if s.strip()]

    chunks = []
    current_chunk = []
    current_len = 0

    for sec in sections:
        sec_len = len(sec)
        # Если отдельная секция сама по себе превышает max_chunk_chars, режем её принудительно
        if sec_len > max_chunk_chars:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            
            start = 0
            while start < sec_len:
                end = min(start + max_chunk_chars, sec_len)
                if end < sec_len:
                    last_period = sec.rfind(". ", start, end)
                    if last_period != -1 and last_period > start + (max_chunk_chars // 2):
                        end = last_period + 1
                piece = sec[start:end].strip()
                if piece:
                    chunks.append(piece)
                start = end
            continue

        if current_len + sec_len + 2 > max_chunk_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [sec]
            current_len = sec_len
        else:
            current_chunk.append(sec)
            current_len += sec_len + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks

