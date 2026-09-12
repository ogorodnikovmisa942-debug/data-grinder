import json
import asyncio
import re
import time
from typing import Optional
import httpx
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
DEEPSEEK_CACHED_SYSTEM_PROMPT = """ROLE: Elite cognitive psychologist, neuro-education engineer, and universal Data Grinder knowledge deconstructor.
MISSION: Analyze raw unstructured source material across ANY academic or professional discipline (law, medicine, STEM, software engineering, history, linguistics) and synthesize an ultra-optimized JSON package containing strictly ATOMIC flashcards designed for the Free Spaced Repetition Scheduler (FSRS).
CORE PHILOSOPHY: Deconstruct complex texts into minimal, indivisible, non-interfering conceptual atoms. Every card must minimize cognitive retrieval latency (target: 1.5–3.5 seconds) while maximizing retention strength and conceptual clarity.

1. UNIVERSAL COGNITIVE LAWS (DECISION TREES, CONTRAST PAIRS & KNOWLEDGE GRAPH):
- Minimum Information Principle: One card = One atomic fact, rule, pattern, or distinction. Never bundle multiple concepts into one card.
- Absolute Prohibition of Lists & Enumerations: Never generate questions that require reciting a multi-item list or enumeration.
- Narrow the Question, Never Mutilate the Answer: If a concept is multi-faceted, narrow the scope of the question so that the answer is inherently 1-2 words or 1 crisp sentence. Never truncate or amputate necessary qualifiers.
- Syntactic Completeness Guarantee: The back side ('d') must ALWAYS be a grammatically complete, self-contained phrase or sentence ending with terminal punctuation (period). Never stop mid-thought.
- Binary Qualification for High-Dimension Categorical Sets: When dealing with large categories, convert them into high-yield contrast pairs, jurisdictional decision forks, or functional criteria.
- Zero-Duplication & Deck Cannibalization Guard: Avoid redundant cards that test the same underlying statutory norm from trivially different angles.
- Rule 1: Mental Framework vs Cognitive Anchors (Separation of Tree and Leaf):
  * Understanding the structural system precedes flashcard memorization.
  * Flashcards MUST NOT test dry dictionary definitions or obvious taxonomic existence ("Бывают ли суды 1-й инстанции?").
  * Flashcards serve strictly as "anchors" (transition points, jurisdictional forks, boundary criteria, conflicting conditions).
- Rule 2: Decision Trees & Situational Forks (Cutting Junctions, Never Lazy Definitions):
  * ABSOLUTE BAN ON DEFINITION QUESTIONS: NEVER ask "Что такое X?", "Дайте определение Y", "Опишите институт Z". Such cards inflate decks by 60% with zero structural understanding.
  * Instead, construct a situational decision point (vignette): Given factual conditions A and B, which instance, authority, deadline, or legal/clinical remedy applies?
  * The front prompt ('t') presents the factual conflict/status.
  * The back ('d') delivers the exact jurisdictional, clinical, or algorithmic verdict with authority/article.
- Rule 3: The Contrast-Pair Differentiation Law (Comparison Prompts):
  * The human brain understands structure strictly through boundaries and differences.
  * When encountering two related, easily confused concepts, institutions, or diagnoses (e.g. народные vs присяжные заседатели; крайняя необходимость vs необходимая оборона; ИМпST vs расслоение аорты):
    Formulate a contrast card testing the single decisive dividing line (Gold Standard Discriminative Criterion).
- Rule 4: High-Yield Doctrinal Taxonomy (Functional Classification, Zero Fluff):
  * Foundational classifications (e.g. "На какие 2 типа делятся конституционные предписания по способу воздействия на субъектов? -> Императивные (категорические запреты/обязанности) и диспозитивные (допускающие выбор поведения)") are strictly preserved!
  * Formulate them strictly through their functional distinction, never through dictionary padding.
- Rule 5: Strict Negative Constraints & Blacklist:
  * 1. STRICT PROHIBITION OF BINARY YES/NO QUESTIONS: Under NO circumstances generate cards with answers "Да." or "Нет.". They produce noise and fail to construct mental connections.
  * 2. STRICT BAN ON TRIVIAL COMMON SENSE: Never ask "Что такое диалог?", "Что такое правосудие?", "Зачем юристу логика?".
  * 3. STRICT BAN ON INTRODUCTORY FLUFF: Never test introductory chapters, definitions of academic science ("что изучает синергетика"), historical lists of abolished 1920-1930s laws, or trivial sources of law ("Какой главный закон страны? — Конституция").
  * 4. STRICT BAN ON UNBOUNDED LISTS: Avoid enumerations >2 items.
- Universal Cognitive Anchors & Mnemonic Hints in 's' (Secondary Text):
  * Format 's': '[Context / Statutory Reference / Formula] | [Mnemonic Anchor / Acronym / Core Equation]'

2. DISCIPLINE DIRECTIVES & TAXONOMY:
- law (Jurisprudence, Statutes, Court Organization, Procedure, Doctrine):
  * t (Front): Active situational decision fork (conflict -> remedy/instance), contrast pair between confusing institutions, or foundational taxonomy. Zero 'Да/Нет', zero 'Что такое X'.
  * s (Secondary): Reference and anchor (e.g. 'ст. 109 Конституции | Монополия судейской мантии' or 'ГПК | Срок апелляции — 15 дней').
  * d (Back): Direct semantic core in 1 grammatically complete sentence or term (e.g. 'Обеспечение правопорядка и защита прав.' or 'Только судам.').
  * e (Example): Real-world judicial scenario, dispute resolution precedent, or qualifying factual circumstance.
  * l (Difficulty): 'easy' for standard terms, 'medium' for procedural qualifications, 'hard' for competing exceptions/boundary tests.

- medicine (Anatomy, Pharmacology, Pathology, Therapy, Surgery):
  * t (Front): Active clinical decision vignette (vital signs + conflict -> protocol), differential diagnostic contrast pair, or foundational pathophysiology cascade. Zero 'Да/Нет'.
  * s (Secondary): Discipline / System | Clinical anchor (e.g. 'Неврология | Менингеальный синдром' or 'Фармакология | Препарат 1-го выбора').
  * d (Back): Direct definitive drug, symptom triad, or mechanism (e.g. 'Эпинефрин (адреналин).' or 'Положителен.').
  * e (Example): Concrete clinical presentation or emergency scenario.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- code (Software Engineering, CS, Architecture, Algorithms):
  * t (Front): Technical decision scenario, pattern trade-off, complexity bound, or protocol invariant.
  * s (Secondary): Language / Signature | Architectural cue.
  * d (Back): Rigorous technical invariant, time/space complexity O(N), or core behavior in 1 crisp sentence.
  * e (Example): Minimal valid code snippet (1-4 lines) demonstrating usage or edge case.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- generic (Physics, Chemistry, Math, History, Humanities, Social Sciences):
  * t (Front): Causal mechanism, decision crossroads, physical law threshold, or milestone boundary. Zero 'Что такое X'.
  * s (Secondary): Sub-discipline / Unit | Conceptual equation.
  * d (Back): Direct causal explanation, physical meaning, or key fact in 1 punchy sentence.
  * e (Example): Practical calculation, industrial observation, or historical dispute.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- language (Foreign languages & Linguistics):
  * t (Front): Foreign word, idiom, or grammatical construction in standard orthography.
  * s (Secondary): Phonetic transcription, IPA, or Chinese Pinyin with tone diacritics.
  * d (Back): Precise natural translation (1-2 punchy terms).
  * e (Example): Natural exemplar sentence illustrating idiomatic usage.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

3. MULTI-SOURCE THEMATIC CLUSTERING & GROUPING:
- When input contains multiple photos, scanned pages, or mixed notes:
  * Semantically cluster and group related concepts into their respective topics/themes.
  * Set 'h' on each card to its specific thematic cluster name.
  * Exhaustively extract cards across ALL provided text/pages. Never restrict cards to just the first topic.

4. STRICT MINIFIED JSON SCHEMA SPECIFICATION:
Output ONLY a valid raw JSON object matching this exact minified key structure:
{
  "domain": "language|law|code|generic",
  "slug": "machine_readable_subject_slug_in_snake_case",
  "title": "Clean Informative Deck Title",
  "graph": {
    "nodes": [
      {
        "id": "slug_id",
        "name": "Concise Entity Name",
        "category": "authority|instance|condition|exception|legal_status",
        "summary": "1 factual sentence summary without fluff",
        "parent_id": null,
        "level": 0
      }
    ],
    "edges": [
      {
        "source": "source_node_id",
        "target": "target_node_id",
        "relation": "appealed_to|excludes_application|demarcated_from|subject_to_jurisdiction",
        "label": "Связка на русском"
      }
    ]
  },
  "c": [
    {
      "t": "Front situational case / contrast prompt / high-yield classification",
      "s": "Secondary context / statutory reference | Mnemonic anchor",
      "d": "Back direct answer / decisive criterion (grammatically complete)",
      "e": "Concrete practical consequence / precedent / case example",
      "l": "easy|medium|hard",
      "h": "Specific thematic topic / cluster name"
    }
  ]
}

5. CONTRASTIVE FEW-SHOT EXAMPLES (BAD VS GOOD COGNITIVE DECOMPOSITION):

CONTRAST CASE 1 (Law - Lazy Definition vs Situational Decision Tree):
❌ UNACCEPTABLE DEFINITION LAZINESS:
{
  "t": "Что такое кассация?",
  "s": "Судоустройство",
  "d": "Проверка не вступивших в законную силу судебных решений вышестоящей судебной инстанцией."
}

✅ CORRECT DECISION TREE (Situational Conflict + Jurisdiction + Specificity):
{
  "t": "Судебный акт уже вступил в законную силу, но обнаружена судебная ошибка в применении нормы права. Через какую инстанцию и по чьей инициативе возможен пересмотр?",
  "s": "ГПК / УПК | Статус: в силе -> Надзор",
  "d": "Надзорное производство (только по протесту уполномоченных должностных лиц: Председатель ВС, Генпрокурор и их заместители).",
  "e": "Жалоба стороны без протеста указанных должностных лиц не дает оснований для надзорного пересмотра.",
  "l": "hard"
}

CONTRAST CASE 2 (Law - Binary Trivia / List vs Contrast Pair):
❌ UNACCEPTABLE BINARY TRIVIA:
{
  "t": "Относится ли сравнительно-правовой метод к теоретическим методам в судоустройстве?",
  "s": "Методология",
  "d": "Да."
}

✅ CORRECT CONTRAST PAIR (Direct Boundary Differentiation):
{
  "t": "Чем принципиально отличается роль народных заседателей от присяжных заседателей в классическом процессе?",
  "s": "Судоустройство | Состав суда",
  "d": "Народные заседатели голосуют наравне с судьёй по всем вопросам (и вина, и мера наказания), а присяжные выносят только вердикт о виновности отдельно от профессионального судьи.",
  "e": "В коллегиях с народными заседателями судья не может единолично преодолеть их согласованное мнение.",
  "l": "medium"
}

CONTRAST CASE 3 (Law - Academic Padding vs High-Yield Taxonomy):
❌ UNACCEPTABLE THEORETICAL PADDING:
{
  "t": "Что понимается под нормами конституционного права?",
  "s": "Теория права",
  "d": "Общеобязательные правила поведения, закрепленные государством для регулирования основ общественного строя."
}

✅ CORRECT HIGH-YIELD CLASSIFICATION (Essential Division without Fluff):
{
  "t": "На какие 2 основных вида делятся конституционные предписания по характеру установленного правила поведения?",
  "s": "Конституционное право | Способ воздействия нормы",
  "d": "Императивные (категорические запреты и обязанности) и диспозитивные (допускающие выбор варианта поведения).",
  "e": "Статья 109 Конституции (правосудие только судом) носит строго императивный характер.",
  "l": "easy"
}

CONTRAST CASE 4 (Medicine - Symptom List vs Clinical Contrast Vignette):
❌ UNACCEPTABLE LIST RECITATION:
{
  "t": "Перечислите клинические проявления менингита",
  "s": "Инфекционные болезни",
  "d": "Лихорадка, головная боль, ригидность затылочных мышц, симптомы Кернига и Брудзинского."
}

✅ CORRECT CLINICAL CONTRAST PAIR:
{
  "t": "У пациента с острым нижним инфарктом миокарда (подъем ST в II, III, aVF) развилась артериальная гипотония (АД 80/50). Почему категорически противопоказан нитроглицерин?",
  "s": "Кардиология | Изолированный инфаркт ПЖ",
  "d": "При вовлечении правого желудочка нитраты вызывают фатальный коллапс преднагрузки; терапия выбора — инфузия физраствора, а не вазодилататоры.",
  "e": "Перед дачей нитратов при нижнем инфаркте обязательна регистрация правых грудных отведений V3R-V4R.",
  "l": "hard"
}

CONTRAST CASE 5 (Code - Python Concurrency):
{
  "domain": "code",
  "slug": "python_asyncio",
  "title": "Python AsyncIO Primitives",
  "c": [
    {
      "t": "Как предотвратить отмену критической фоновой корутины при отмене вызывающей задачи в asyncio?",
      "s": "asyncio.shield(aw) | Защита от родительской отмены",
      "d": "Обернуть корутину в asyncio.shield().",
      "e": "await asyncio.shield(commit_critical_transaction())",
      "l": "medium"
    }
  ]
}

CONTRAST CASE 6 (Language - Chinese Vocabulary):
{
  "domain": "language",
  "slug": "chinese_hsk",
  "title": "HSK 4 Бизнес-терминология",
  "c": [
    {
      "t": "合同",
      "s": "hétong | 合 (соединять) + 同 (одинаковый)",
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
        theme = item.get("h") or item.get("theme") or item.get("topic") or ""

        if not str(front).strip() and not str(back).strip():
            continue

        is_cloze = "{{c" in str(front)
        cards.append({
            "text": str(front).strip(),
            "secondary_text": str(sec).strip(),
            "translation": str(back).strip(),
            "example": str(ex).strip(),
            "initial_difficulty_tier": diff if diff in ("easy", "medium", "hard") else "medium",
            "mnemonic": None,  # Ленивая генерация мнемоник
            "theme": str(theme).strip() or title,
            "content_type": "cloze" if is_cloze else "text"
        })

    # 4. Извлекаем семантический граф знаний и ментальный каркас
    clean_nodes = []
    clean_edges = []
    tree_data = None
    if isinstance(raw_data, dict):
        raw_graph = raw_data.get("graph") or raw_data.get("knowledge_graph") or raw_data.get("g")
        if not isinstance(raw_graph, dict):
            if "nodes" in raw_data or "edges" in raw_data:
                raw_graph = {"nodes": raw_data.get("nodes", []), "edges": raw_data.get("edges", [])}
            else:
                raw_graph = {}

        input_nodes = raw_graph.get("nodes") or raw_graph.get("n") or []
        input_edges = raw_graph.get("edges") or raw_graph.get("e") or []
        if isinstance(input_nodes, list) and input_nodes:
            try:
                from app.services.graph_service import clean_graph_data, build_hierarchical_tree
                clean_nodes, clean_edges = clean_graph_data(input_nodes, input_edges if isinstance(input_edges, list) else [])
                if clean_nodes:
                    tree_data = build_hierarchical_tree(clean_nodes, clean_edges, root_title=title or slug or "Каркас дисциплины")
            except Exception as ge:
                print(f"[AI Gateway] Ошибка очистки графа из ответа LLM: {ge}")

    result = {
        "subject_domain": domain,
        "subject_slug": slug,
        "phrase_title": title,
        "cards": cards
    }
    if clean_nodes:
        result["knowledge_graph"] = {
            "nodes": clean_nodes,
            "edges": clean_edges,
            "tree_data": tree_data
        }
    return result

def build_granularity_prompt(granularity_mode: str, custom_instruction: str, density: str, volume: str) -> str:
    """Формирует компактные модификаторы промпта для управления глубиной и пожеланиями пользователя с защитным шлюзом."""
    modifiers = []
    
    # 1. Режим гранулярности и лимиты объема
    if granularity_mode in ("detailed", "deep"):
        modifiers.append("GRANULARITY DIRECTIVE: Deep, comprehensive decomposition. Exhaustively extract all fine-grained nuances, exceptions, and conditions into separate individual ATOMIC cards. Never merge multiple concepts into one card.")
    elif granularity_mode in ("blitz", "cheatsheet"):
        modifiers.append("GRANULARITY DIRECTIVE: High-yield Blitz mode. Extract strictly the most fundamental 5 to 10 core concepts into punchy, minimal atomic cards (1-2 sentences).")
        if volume == "auto":
            modifiers.append("CARD VOLUME: AUTOMATIC OPTIMIZATION. Extract 5 to 10 punchy blitz-cards.")
        elif volume in ("low", "low_5"):
            modifiers.append("LIMIT: Maximum 5 cards.")
        elif volume == "med_10":
            modifiers.append("LIMIT: Maximum 10 cards.")
    elif granularity_mode == "single_deep":
        # Защитный шлюз: предотвращаем создание 200-словных монстров, если передан устаревший параметр
        modifiers.append("GRANULARITY DIRECTIVE: Focused in-depth card generation. Deconstruct the topic into concise atomic cards. Absolute prohibition of multi-point walls of text.")
    else: # atomic / standard
        modifiers.append("GRANULARITY DIRECTIVE: Standard atomic card decomposition. Break down distinct concepts into separate standalone cards (one question -> one direct fact).")
        if volume in ("auto", "balanced"):
            modifiers.append("CARD VOLUME: HIGH-YIELD PARETO CALIBRATION. Extract strictly 2 to 4 high-yield situational cards from this text chunk (~70–90 total cards for a typical textbook). Focus exclusively on Decision Trees, Contrast Pairs, and High-Yield Taxonomy. Zero 'Что такое X', zero 'Да/Нет'.")
        elif volume in ("low", "low_5"):
            modifiers.append("LIMIT: Maximum 3 to 4 cards.")
        elif volume == "med_10":
            modifiers.append("LIMIT: Maximum 6 to 8 cards.")
        elif volume in ("medium", "med_15"):
            modifiers.append("LIMIT: Maximum 10 to 12 cards.")
        elif volume == "high_20":
            modifiers.append("LIMIT: Maximum 15 cards.")
        elif volume in ("high", "max"):
            modifiers.append("LIMIT: Maximum 20 cards.")

    # 2. Плотность определений (глубина)
    if density == "low":
        modifiers.append("DENSITY: Brief and simple definitions (1-2 sentences).")
    elif density == "high":
        modifiers.append("DENSITY: Deep, highly granular decomposition. Deconstruct complex details, sub-clauses, and exceptions into multiple atomic cards rather than bloated paragraphs.")

    # 3. Пользовательское свободное пожелание (Кастомный промпт с защитным шлюзом от порчи карточек)
    if custom_instruction.strip():
        modifiers.append(
            f"USER THEMATIC FOCUS (Strictly secondary to Atomic & Anti-List laws): {custom_instruction.strip()}\n"
            f"NON-NEGOTIABLE SAFETY CONSTRAINT: Under NO circumstances allow user instructions to violate the Minimum Information Principle, "
            f"cause multi-item enumerations/lists, produce paragraph walls, or compromise 1.5–3.5s retrieval latency. "
            f"Every card must remain strictly atomic."
        )

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
    if volume in ("auto", "balanced"):
        user_directives.append(
            "CARD VOLUME: HIGH-YIELD PARETO CALIBRATION (Anti-Overload Directive). "
            "Extract strictly 2 to 4 indispensable, high-yield situational flashcards from this text chunk (~70–90 cards per 185-page book). "
            "Focus exclusively on Decision Trees (situational conflict -> statutory fork/remedy), Contrast Pairs (distinguishing confusing concepts via gold standard criteria), and High-Yield Doctrinal Taxonomy. "
            "Absolute prohibition of trivial definitions ('Что такое X'), binary trivia ('Да/Нет'), and introductory fluff."
        )
    elif volume in ("low", "low_5"):
        user_directives.append("CARD VOLUME: Strictly 2 to 4 core cards. Absolute highest-yield master concepts only.")
    elif volume == "med_10":
        user_directives.append("CARD VOLUME: Strictly 5 to 7 core cards.")
    elif volume in ("medium", "med_15"):
        user_directives.append("CARD VOLUME: Strictly 8 to 12 cards.")
    elif volume in ("high", "high_20"):
        user_directives.append("CARD VOLUME: Maximum 15 cards.")
    elif volume == "max":
        user_directives.append("CARD VOLUME: Exhaustive extraction (up to 20 cards). Every verifiable fact and distinction.")
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
        user_directives.append(
            f"USER THEMATIC FOCUS (Strictly secondary to Atomic & Anti-List laws): {custom_instruction.strip()}\n"
            f"NON-NEGOTIABLE SAFETY CONSTRAINT: Under NO circumstances allow user instructions to violate the Minimum Information Principle, "
            f"cause multi-item enumerations/lists, produce paragraph walls, or compromise 1.5–3.5s retrieval latency. "
            f"Every card must remain strictly atomic."
        )

    user_prompt = (
        "[PROCESSING PARAMETERS]\n"
        + "\n".join(user_directives)
        + "\n\n[RAW SOURCE TEXT TO DECONSTRUCT]\n"
        + text
    )

    start_ts = time.time()
    model_requested = settings.DEEPSEEK_MODEL or "deepseek-chat"
    fallback_used = False
    json_repair_applied = False
    res = None
    
    # 1. Вызов DeepSeek (основной экономичный провайдер с Prompt Caching)
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
            error_message=f"DeepSeek: {err_str[:400]}"
        )
        raise ds_err

    if clean_sub and isinstance(res, dict):
        res["subject_slug"] = clean_sub
    
    if isinstance(res, dict):
        res["fallback_used"] = False
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

    if not settings.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": settings.DEEPSEEK_MODEL or "deepseek-chat",
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

# --- УМНОЕ ЧАНКОВАНИЕ ДЛИННЫХ ДОКУМЕНТОВ И КНИГ (ОПТИМИЗИРОВАННЫЙ СТАНДАРТ 24K ЗНАКОВ) ---
def split_text_into_chunks(text: str, max_chunk_chars: int = 24000, overlap_chars: int = 1200) -> list[str]:
    """
    Интеллектуальное разбиение длинного документа на сбалансированные смысловые чанки (~6-8 страниц / 10 000 - 14 000 знаков).
    Исключает эффект 'Lost in the middle', гарантирует 100% покрытие фактов и предотвращает обрезку лимита токенов LLM.
    Сохраняет границы страниц, документов, слайдов и параграфов, добавляя скользящее перекрытие (overlap).
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chunk_chars:
        return [text]

    # Паттерн ищет границы страниц, слайдов или документов
    split_pattern = r'(?=(?:\n--- [^\n]+: (?:Стр\.|Слайд) \d+ ---|\n=== [^\n]+ ===))'
    sections = re.split(split_pattern, text)
    sections = [s.strip() for s in sections if s.strip()]

    # Если маркеров страниц/документов не было или всего одна секция, делим по параграфам (\n\n) или заголовкам Markdown
    if len(sections) <= 1:
        sections = re.split(r'(?=(?:\n\n(?=[#A-ZА-Я0-9])|\n#{1,4} ))', text)
        sections = [s.strip() for s in sections if s.strip()]

    if len(sections) <= 1:
        sections = text.split("\n\n")
        sections = [s.strip() for s in sections if s.strip()]

    # Если все еще одна крупная секция, делим по строкам
    if len(sections) <= 1:
        sections = text.split("\n")
        sections = [s.strip() for s in sections if s.strip()]

    # Нормализуем секции: если отдельная секция превышает max_chunk_chars, режем её по предложениям
    normalized_sections = []
    for sec in sections:
        sec_len = len(sec)
        if sec_len <= max_chunk_chars:
            normalized_sections.append(sec)
        else:
            start = 0
            while start < sec_len:
                end = min(start + max_chunk_chars, sec_len)
                if end < sec_len:
                    last_period = sec.rfind(". ", start, end)
                    if last_period != -1 and last_period > start + (max_chunk_chars // 2):
                        end = last_period + 1
                    else:
                        last_newline = sec.rfind("\n", start, end)
                        if last_newline != -1 and last_newline > start + (max_chunk_chars // 2):
                            end = last_newline
                piece = sec[start:end].strip()
                if piece:
                    normalized_sections.append(piece)
                start = end

    # Собираем блоки до max_chunk_chars
    raw_chunks = []
    current_chunk = []
    current_len = 0

    for sec in normalized_sections:
        sec_len = len(sec)
        if current_len + sec_len + 2 > max_chunk_chars and current_chunk:
            raw_chunks.append("\n\n".join(current_chunk))
            current_chunk = [sec]
            current_len = sec_len
        else:
            current_chunk.append(sec)
            current_len += sec_len + 2

    if current_chunk:
        raw_chunks.append("\n\n".join(current_chunk))

    if len(raw_chunks) <= 1:
        return raw_chunks

    # Добавляем скользящий overlap к последующим чанкам для неразрывности контекста
    final_chunks = []
    for idx, ch in enumerate(raw_chunks):
        if idx == 0:
            final_chunks.append(ch)
        else:
            prev_chunk = raw_chunks[idx - 1]
            overlap_tail = prev_chunk[-overlap_chars:].strip() if len(prev_chunk) > overlap_chars else prev_chunk.strip()
            first_period = overlap_tail.find(". ")
            if first_period != -1 and first_period < len(overlap_tail) // 2:
                overlap_tail = overlap_tail[first_period + 2:].strip()
            
            if overlap_tail:
                stitched = f"[ПРЕДЫДУЩИЙ КОНТЕКСТ ДЛЯ НЕРАЗРЫВНОСТИ]:\n...{overlap_tail}\n\n[ОСНОВНОЙ ТЕКСТ РАЗДЕЛА]:\n{ch}"
                final_chunks.append(stitched)
            else:
                final_chunks.append(ch)

    return final_chunks


