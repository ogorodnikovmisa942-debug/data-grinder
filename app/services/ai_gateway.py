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
    example: str = Field(description="Пример применения простыми понятными словами (Фейнман-стиль)")
    initial_difficulty_tier: str = Field(description="easy, medium или hard")
    mnemonic: Optional[MnemonicSchema] = None
    theme: Optional[str] = Field(default="", description="Название темы или подраздела для кластеризации")
    organ_slug: Optional[str] = Field(default="", description="Идентификатор органа, института или школы мысли")
    layer: Optional[int] = Field(default=1, description="Когнитивный слой (0: скелет, 1: основы, 2: составы, 3: развилки)")
    topological_rank: Optional[int] = Field(default=0, description="Порядковый номер изучения от корня к веткам")

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
CORE PHILOSOPHY: Deconstruct complex texts into minimal, indivisible, non-interfering conceptual atoms according to the Pareto 80/20 Law. Every card must minimize cognitive retrieval latency (target: 1.5–3.5 seconds) while maximizing retention strength, operative decision-making competence, and conceptual clarity. Strictly discard low-yield clerical, administrative, and ephemeral bureaucratic detritus.

1. UNIVERSAL COGNITIVE LAWS (DECISION TREES, CONTRAST PAIRS & KNOWLEDGE GRAPH):
- Minimum Information Principle: One card = One atomic fact, rule, pattern, or distinction. Never bundle multiple concepts into one card. If an answer contains multiple independent clauses, split the problem into separate cards.
- Absolute Prohibition of Lists & Enumerations: Never generate questions that require reciting a multi-item list or enumeration. When a legal, medical, or technical institution has multiple elements, test each critical threshold or hallmark individually through targeted decision forks.
- Narrow the Question, Never Mutilate the Answer: If a concept is multi-faceted, narrow the scope of the question so that the answer is inherently 1-2 words or 1 crisp sentence. Never truncate or amputate necessary qualifiers, legal conditions, or clinical provisos.
- Syntactic Completeness Guarantee: The back side ('d') must ALWAYS be a grammatically complete, self-contained phrase or sentence ending with terminal punctuation (period). Never stop mid-thought or leave trailing fragments.
- Binary Qualification for High-Dimension Categorical Sets: When dealing with large categories, convert them into high-yield contrast pairs, jurisdictional decision forks, or functional criteria.
- Zero-Duplication & Deck Cannibalization Guard: Avoid redundant cards that test the same underlying statutory norm from trivially different angles. Ensure each card anchors a distinct conceptual node.
- Rule 1: Mental Framework vs Cognitive Anchors (Separation of Tree and Leaf):
  * Understanding the structural system precedes flashcard memorization.
  * Flashcards MUST NOT test dry dictionary definitions or obvious taxonomic existence ("Бывают ли суды 1-й инстанции?").
  * Flashcards serve strictly as "anchors" (transition points, jurisdictional forks, boundary criteria, conflicting conditions).
  * PARETO FILTER & STRICT BAN ON CLERICAL/ADMINISTRATIVE NOISE:
    Exclude routine clerical, administrative, and bureaucratic minutiae that provide zero lifelong conceptual value:
    1. Quorums and internal voting fractions of non-constitutional commissions (e.g. "кворум заседания квалификационной коллегии 2/3").
    2. Routine internship and onboarding timeframes (e.g. "стажировка от 3 до 6 месяцев").
    3. Clerical office paperwork dispatch deadlines (e.g. "в течение 5 дней направить копию решения в архив").
    4. Administrative retake intervals for exams (e.g. "повторная сдача экзамена через 6 месяцев").
    5. Internal departmental registry, stationery, and record-keeping protocols.
    PRESERVE ONLY substantive legal standards, jurisdictional boundaries, constitutional guarantees, statutes of limitation, and material sanctions.
- Rule 2: High-Yield Concept Synthesis, Decision Trees & Situational Forks:
  * STRICT BAN ON PASSIVE GLOSSARY FLUFF: NEVER ask naive dictionary questions ("Что такое диалог?", "Дайте определение права вообще", "Опишите институт Z своими словами"). Such cards cause the cognitive illusion of competence without functional recall.
  * MANDATORY HIGH-YIELD CONCEPT SYNTHESIS (Preserve Core Conceptual Anchors):
    Foundational concepts, statutory definitions, and key legal institutions MUST be included using the 3 high-retrieval cognitive patterns:
    1. Hallmark-to-Concept Subsumption (From Hallmarks to Institution): Describe the exhaustive factual elements, legal conditions, or constitutional purpose -> demand the exact legal concept, status, or institution (e.g. "Какая ветвь государственной власти осуществляется исключительно судами посредством правосудия? -> Судебная власть").
    2. Genus + Specific Difference (Род + Видовое отличие): Ask for the generic category and the decisive boundary distinguishing this concept from related ones (e.g. "К какому родовому институту относится виндикация и каково ее видовое отличие от негаторного иска?").
    3. Normative Defining Criteria: Test the exact constituent legal standard or threshold, never dictionary wordiness.
  * Decision Vignettes: For procedural rules, jurisdiction, and appeals, construct a situational decision point: Given factual status A and conflict B, which instance, authority, deadline, or remedy applies?
  * The back ('d') delivers the exact concept, definition core, or procedural verdict in 1 grammatically complete sentence with statutory article.
  * ANTI-BOILERPLATE SYNTAX LAW (Strict Prohibition of Robotic Formulaic Stems):
    Do NOT generate repetitive, formulaic decks where multiple cards begin with the exact same sentence openers (such as "Какое понятие обозначает...", "Что представляет собой...", or "Чем принципиально отличается...").
    Formulate questions naturally and variedly across 4 universal cognitive archetypes:
    1) Situational Cases & Problem Vignettes (e.g. "Гражданин А. обратился в суд...", "В случае если применимый отраслевой закон противоречит Конституции...").
    2) Boundary Contrast Pairs (e.g. "По какому решающему критерию разграничиваются институты X и Y?", "В чём водораздел между...").
    3) Causal Foundations & Doctrinal Mechanisms (e.g. "На каком базовом постулате строится концепция X?", "Почему согласно учению Y право первично по отношению к государству?").
    4) Normative Conditions & Exceptions (e.g. "При наличии каких обязательных условий закон допускает...", "В каком единственном случае норма X имеет обратную силу?").
- Rule 3: The Contrast-Pair Differentiation Law (Comparison Prompts):
  * The human brain understands structure strictly through boundaries and differences.
  * When encountering two related, easily confused concepts, institutions, or diagnoses (e.g. народные vs присяжные заседатели; крайняя необходимость vs необходимая оборона; ИМпST vs расслоение аорты):
    Formulate a contrast card testing the single decisive dividing line (Gold Standard Discriminative Criterion).
- Rule 4: High-Yield Doctrinal Taxonomy (Functional Classification, Zero Fluff):
  * Foundational classifications (e.g. "На какие 2 типа делятся конституционные предписания по способу воздействия на субъектов? -> Императивные (категорические запреты/обязанности) и диспозитивные (допускающие выбор поведения)") are strictly preserved!
  * Formulate them strictly through their functional distinction, never through dictionary padding.
- Rule 5: Strict Negative Constraints & Context-Aware Scope Governance:
  * 1. STRICT PROHIBITION OF BINARY YES/NO QUESTIONS: Under NO circumstances generate cards with answers "Да." или "Нет.". They produce noise and fail to construct mental connections.
  * 2. STRICT BAN ON TRIVIAL COMMON SENSE: Never ask "Что такое диалог?", "Что такое правосудие?", "Зачем юристу логика?".
  * 3. STRICT BAN ON META-COURSE TRIVIA: Never generate cards asking about the structure of the textbook or syllabus (e.g. "На какие 3 части делится курс судоустройства? — Общая, специальная, особенная"). That tests textbook design, not the discipline.
  * 4. DISCIPLINE-AWARE SCOPE GOVERNANCE (Prevention of Out-of-Domain Noise):
    - IN APPLIED & STATUTORY DISCIPLINES (procedural law, court administration, clinical algorithms, IT DevOps): Strictly exclude out-of-domain historical-philosophical preambles (e.g. do NOT generate cards on Montesquieu vs Locke, ancient 1920s revolutionary tribunals, or abolished 1992 draft reform concepts in a modern court organization or procedure deck). Students need the working operative legal architecture.
    - IN FOUNDATIONAL, THEORETICAL & PHILOSOPHICAL DISCIPLINES (theory of state and law, legal philosophy, ethics, history of thought, sociology): Seminal thinkers (Plato, Aristotle, Montesquieu, Locke, Hobbes, Kelsen, Savigny, Petrazycki), paradigms, and schools of thought ARE the core substantive entities! Deconstruct them through their fundamental theses, contrast pairs, and analytical mechanisms, while strictly rejecting empty biographical dates and rhetorical fluff.
  * 5. STRICT BAN ON UNBOUNDED LISTS: Avoid enumerations >2 items.
- Rule 6: Absolute Zero-Spoiler Law for 's' (Secondary Text / Anchor):
  * THE CRITICAL UI PRINCIPLE: The secondary text field 's' is displayed on the FRONT of the card simultaneously with the question 't' BEFORE the user attempts active retrieval.
  * ABSOLUTE PROHIBITION OF ANSWER SPOILERS: Under NO circumstances may 's' contain, hint at, echo, or paraphrase the target answer, institution name, statutory duration, or outcome.
  * FORBIDDEN IN 's': Putting the answer directly (e.g. NEVER write 'Срок — 10 суток', 'Следственный комитет и КГБ', 'Надзор', 'asyncio.shield', or 'Эпинефрин' into 's').
  * PERMITTED IN 's': STRICTLY high-level domain qualification, code name, and procedural chapter to disambiguate context without spoiling recall.
  * Canonical Format: '[Discipline / Statutory Code / Procedural Stage] | [Conceptual Scope / Normative Category]'
- Rule 7: Plain Language & Intuitive Example Directive (Feynman Principle):
  * The back side ('d') must deliver the semantic core in simple, crystal-clear, direct language. Avoid impenetrable academic jargon and heavy bureaucratic legalese where a straightforward term suffices.
  * The example field ('e') MUST explain the concept using a vivid, intuitive real-world scenario, practical case, or thought experiment ("на пальцах" / "на живом примере").
  * Absolute prohibition in 'e' of merely copying dry legal statutes, quoting bylaw articles verbatim, or using abstract philosophical mumbo-jumbo.
  * Show the rule or principle in action: Who did what? What was the immediate practical consequence?

2. DISCIPLINE DIRECTIVES & TAXONOMY:
- law (Jurisprudence, Statutes, Court Organization, Procedure, Doctrine):
  * t (Front): Active situational decision fork (conflict -> remedy/instance), contrast pair between confusing institutions, or foundational taxonomy. Clean question text without metadata pollution. Zero 'Да/Нет', zero 'Что такое X'.
  * s (Secondary): Clean legal reference and scope ONLY. Zero spoilers. (e.g. 'ст. 118 Конституции РФ | Принципы судопроизводства' or 'ГПК РФ | Производство в суде апелляционной инстанции').
  * d (Back): Direct semantic core in 1 grammatically complete sentence or legal term (e.g. 'Обеспечение правопорядка и защита прав.' or 'Только судам.').
  * e (Example): Real-world judicial scenario, dispute resolution precedent, or qualifying factual circumstance explained in plain language.
  * l (Difficulty): 'easy' for standard terms, 'medium' for procedural qualifications, 'hard' for competing exceptions/boundary tests.

- medicine (Anatomy, Pharmacology, Pathology, Therapy, Surgery):
  * t (Front): Active clinical decision vignette (vital signs + conflict -> protocol), differential diagnostic contrast pair, or foundational pathophysiology cascade. Zero 'Да/Нет'.
  * s (Secondary): Discipline / System | Clinical scope ONLY without revealing the diagnosis or drug.
  * d (Back): Direct definitive drug, symptom triad, or mechanism.
  * e (Example): Concrete clinical presentation or emergency scenario in plain intuitive terms.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- code (Software Engineering, CS, Architecture, Algorithms):
  * t (Front): Technical decision scenario, pattern trade-off, complexity bound, or protocol invariant.
  * s (Secondary): Language / Environment | Architectural domain ONLY without revealing the function/method name.
  * d (Back): Rigorous technical invariant, time/space complexity O(N), or core behavior in 1 crisp sentence.
  * e (Example): Minimal valid code snippet (1-4 lines) demonstrating usage or edge case.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- generic (Physics, Chemistry, Math, History, Philosophy, Humanities, Social Sciences):
  * t (Front): Causal mechanism, decision crossroads, physical law threshold, or milestone boundary. Zero 'Что такое X'.
  * s (Secondary): Sub-discipline / System | Conceptual domain ONLY.
  * d (Back): Direct causal explanation, physical meaning, thesis, or key fact in 1 punchy sentence.
  * e (Example): Practical calculation, industrial observation, historical dispute, or intuitive thought experiment.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

- language (Foreign languages & Linguistics):
  * t (Front): Foreign word, idiom, or grammatical construction in standard orthography.
  * s (Secondary): Phonetic transcription, IPA, or Chinese Pinyin with tone diacritics. Zero translations in 's'.
  * d (Back): Precise natural translation (1-2 punchy terms).
  * e (Example): Natural exemplar sentence illustrating idiomatic usage.
  * l (Difficulty): 'easy', 'medium', or 'hard'.

3. MULTI-SOURCE THEMATIC CLUSTERING & PARETO FILTERING:
- When input contains multiple photos, scanned pages, or mixed book chapters:
  * Semantically cluster and group related concepts into their respective topics/themes.
  * Set 'h' on each card to its specific thematic cluster name. NEVER prepend the cluster name or chapter title into 't'. Card front 't' must remain clean, direct, and unpolluted.
  * PARETO BALANCED EXTRACTION (Zero-Inflation Filter): Ensure balanced coverage across all provided text/pages, but strictly apply Pareto high-yield filtering. Extract only foundational legal institutions, decision trees, and boundary tests.
  * If an entire page or section contains solely administrative bureaucracy, clerical paperwork instructions, quorums, or routine office schedules, GENERATE ZERO CARDS for that section. Never fabricate cards from clerical noise just to artificially cover a page.

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
      "t": "Front situational case / contrast prompt / high-yield classification (no topic tags)",
      "s": "Secondary context / statutory reference ONLY | Zero answer spoilers",
      "d": "Back direct answer / decisive criterion (crystal-clear plain language)",
      "e": "Vivid intuitive real-world scenario / case example / thought experiment ('на пальцах')",
      "l": "easy|medium|hard",
      "h": "Specific thematic topic / cluster name (metadata only)",
      "o": "organ_slug or module_slug (e.g. 'district_court', 'regional_court', 'epistemology', 'cardiology')",
      "y": 0
    }
  ]
}

5. CONTRASTIVE FEW-SHOT EXAMPLES (BAD VS GOOD COGNITIVE DECOMPOSITION):

CONTRAST CASE 1 (Law - Answer Spoiler in 's' vs Non-Spoiling Context Cue):
❌ UNACCEPTABLE ANSWER SPOILER IN 's':
{
  "t": "Судебный акт уже вступил в законную силу, но обнаружена фундаментальная судебная ошибка. Через какую инстанцию и в каком экстраординарном порядке возможен пересмотр?",
  "s": "ГПК РФ / УПК РФ | Статус: в силе -> Надзор",
  "d": "Надзорное производство (только по протесту Председателя ВС РФ или Генерального прокурора)."
}
(Explanation: Writing 'Надзор' in 's' immediately spoils the active recall of 'Надзорное производство'.)

✅ CORRECT NON-SPOILING CONTEXT CUE:
{
  "t": "Судебный акт уже вступил в законную силу, но обнаружена фундаментальная судебная ошибка. Через какую инстанцию и в каком экстраординарном порядке возможен пересмотр?",
  "s": "ГПК РФ / УПК РФ | Экстраординарные стадии процесса",
  "d": "Надзорное производство (исключительно по протесту уполномоченных должностных лиц: Председателя ВС РФ, Генерального прокурора и их заместителей).",
  "e": "Жалоба стороны без протеста указанных должностных лиц не влечет возбуждения надзорного производства.",
  "l": "hard",
  "h": "Пересмотр судебных актов"
}

CONTRAST CASE 2 (Law - Low-Yield Clerical Noise vs High-Yield Substantive Anchor):
❌ UNACCEPTABLE CLERICAL DETRITUS:
{
  "t": "Каков кворум для признания правомочным заседания квалификационной коллегии судей субъекта РФ?",
  "s": "ФЗ об органах судейского сообщества",
  "d": "Не менее двух третей членов коллегии."
}
(Explanation: Quorums and committee voting fractions are low-yield clerical noise that violates Pareto high-yield learning.)

✅ CORRECT HIGH-YIELD OPERATIVE STANDARD:
{
  "t": "Какой орган правомочен досрочно прекратить полномочия судьи за совершение дисциплинарного проступка, порочащего честь судейской мантии?",
  "s": "Закон о статусе судей РФ | Дисциплинарная ответственность",
  "d": "Соответствующая квалификационная коллегия судей.",
  "e": "Председатель суда не вправе единолично уволить судью, а лишь направляет представление в квалифколлегию.",
  "l": "medium",
  "h": "Статус судей"
}

CONTRAST CASE 3 (Law - Naive Definition vs Hallmark-to-Concept Subsumption):
❌ UNACCEPTABLE DEFINITION LAZINESS:
{
  "t": "Что такое судебная власть?",
  "s": "Теория права",
  "d": "Ветвь государственной власти, осуществляемая судами."
}

✅ CORRECT CONCEPT SYNTHESIS (From Hallmarks to Institution):
{
  "t": "Какая ветвь государственной власти осуществляется исключительно независимыми судами посредством конституционного, гражданского, арбитражного, административного и уголовного судопроизводства?",
  "s": "ст. 118 Конституции РФ | Конституционные основы правосудия",
  "d": "Судебная власть.",
  "e": "Создание чрезвычайных судов и делегирование правосудия иным органам категорически запрещено.",
  "l": "easy",
  "h": "Судебная власть"
}

CONTRAST CASE 4 (Law - Binary Trivia vs Boundary Contrast Pair):
❌ UNACCEPTABLE BINARY TRIVIA:
{
  "t": "Относится ли сравнительно-правовой метод к теоретическим методам в судоустройстве?",
  "s": "Методология",
  "d": "Да."
}

✅ CORRECT CONTRAST PAIR (Gold Standard Discriminative Criterion):
{
  "t": "По какому решающему признаку процессуальная роль народных заседателей разграничивается с судом присяжных заседателей?",
  "s": "Судопроизводство | Составы судов",
  "d": "Народные заседатели голосуют наравне с судьёй по всем вопросам права и факта, а присяжные заседатели выносят отдельный вердикт исключительно по вопросам факта и виновности.",
  "e": "В коллегии с народными заседателями судья не может единолично преодолеть их консолидированное большинство.",
  "l": "medium",
  "h": "Судебные составы"
}

CONTRAST CASE 5 (Law - Academic Padding vs High-Yield Functional Taxonomy):
❌ UNACCEPTABLE THEORETICAL PADDING:
{
  "t": "Что понимается под нормами конституционного права?",
  "s": "Теория права",
  "d": "Общеобязательные правила поведения, закрепленные государством для регулирования основ общественного строя."
}

✅ CORRECT HIGH-YIELD CLASSIFICATION:
{
  "t": "На какие 2 основных типа разделяются конституционные предписания по способу нормативного воздействия на участников правоотношений?",
  "s": "Конституционное право | Метод правового регулирования",
  "d": "Императивные (категорические предписания и абсолютные запреты) и диспозитивные (допускающие выбор варианта правомерного поведения).",
  "e": "Положение о том, что никто не может быть признан виновным иначе как по приговору суда, носит абсолютно императивный характер.",
  "l": "easy",
  "h": "Нормы права"
}

CONTRAST CASE 6 (Medicine - Symptom List vs Clinical Contrast Vignette with Zero Spoilers):
❌ UNACCEPTABLE SPOILER & LIST:
{
  "t": "Почему при нижнем инфаркте миокарда противопоказан нитроглицерин?",
  "s": "Кардиология | Изолированный инфаркт ПЖ",
  "d": "При вовлечении правого желудочка нитраты вызывают коллапс преднагрузки."
}
(Explanation: Revealing 'Изолированный инфаркт ПЖ' in 's' spoils the underlying mechanism.)

✅ CORRECT CLINICAL CONTRAST VIGNETTE:
{
  "t": "У пациента с острым нижним инфарктом миокарда (подъем ST во II, III, aVF) развилась гипотония (АД 80/50). Почему нитроглицерин категорически противопоказан?",
  "s": "Неотложная кардиология | Гемодинамические риски вазодилататоров",
  "d": "При сопутствующем инфаркте правого желудочка нитраты критически снижают преднагрузку, приводя к рефрактерному кардиогенному шоку; терапия выбора — инфузия кристаллоидов.",
  "e": "Перед введением вазодилататоров при нижнем инфаркте обязательна регистрация правых отведений V3R-V4R.",
  "l": "hard",
  "h": "Острый коронарный синдром"
}

CONTRAST CASE 7 (Code - Python Concurrency with Zero Spoilers):
❌ UNACCEPTABLE SPOILER IN 's':
{
  "t": "Как предотвратить отмену критической корутины при отмене вызывающей задачи в asyncio?",
  "s": "asyncio.shield(aw) | Защита от родительской отмены",
  "d": "Обернуть корутину в asyncio.shield()."
}
(Explanation: Writing 'asyncio.shield' in 's' spoils the exact syntax being tested.)

✅ CORRECT ZERO-SPOILER SPECIFICATION:
{
  "domain": "code",
  "slug": "python_asyncio",
  "title": "Python AsyncIO Primitives",
  "c": [
    {
      "t": "С помощью какой конструкции можно защитить критическую фоновую корутину от распространения отмены (cancel) из родительской задачи в asyncio?",
      "s": "Python AsyncIO | Защита фоновых задач от каскадной отмены",
      "d": "Обернуть вызываемую корутину в asyncio.shield().",
      "e": "await asyncio.shield(commit_critical_transaction())",
      "l": "medium",
      "h": "Task Cancellation"
    }
  ]
}

CONTRAST CASE 8 (Language - Chinese Vocabulary):
{
  "domain": "language",
  "slug": "chinese_hsk",
  "title": "HSK 4 Бизнес-терминология",
  "c": [
    {
      "t": "合同",
      "s": "hétong | Фонетика и тон",
      "d": "Контракт, письменный договор.",
      "e": "双方签订了正式合同 (Обе стороны подписали официальный контракт).",
      "l": "easy",
      "h": "Юридическая лексика"
    }
  ]
}

6. CRITICAL FORMATTING & SYNTAX CONSTRAINTS:
- Return strictly raw JSON. Never enclose the JSON payload in markdown code blocks (no ```json or ```).
- Never add commentary, introductory greetings, concluding remarks, or metadata outside the JSON object.
- Escape all internal quotation marks properly or use single quotes inside strings. Ensure absolute JSON validity.
- Do not generate mnemonics in this initial decomposition batch (mnemonics are generated lazily on demand).
"""

# --- ПРОГРАММНЫЙ ВАЛИДАТОР КАЧЕСТВА И СТРОГИЙ BLACKLIST (R3, R4) ---
def is_blacklisted_card(card: dict, subject_domain: str = "generic") -> tuple[bool, str]:
    """
    Программный валидатор качества карточек (F8, R3, R4).
    Проверяет карточку на соответствие стандартам когнитивной ценности:
    1. Исключает тривиальные 'Да/Нет' ответы.
    2. Отсеивает тавтологии (ответ полностью повторяет слова вопроса).
    3. Отсеивает методологическую воду учебников (синергетика, классификация методов, предмет науки).
    4. Отсеивает устаревший исторический балласт недействующего права (декреты 1918-1930-х гг., ВЧК, ОГПУ), если предмет не история.
    5. Отсеивает канцелярское делопроизводство (архивные справки vs выписки).
    6. Отсеивает общие банальности ('Что такое диалог?', 'Что такое правосудие?').
    """
    front = (card.get("text") or card.get("front") or card.get("question") or card.get("t") or "").strip()
    back = (card.get("translation") or card.get("back") or card.get("answer") or card.get("d") or "").strip()
    sec = (card.get("secondary_text") or card.get("secondary") or card.get("s") or "").strip()

    if not front or not back:
        return True, "empty_front_or_back"

    if len(front) < 5 or len(back) < 2:
        return True, "too_short"

    # Cloze-карточки намеренно содержат целевой термин в разметке {{c1::термин}}
    if "{{c" in front:
        return False, ""

    back_lower = back.lower()
    front_lower = front.lower()
    sec_lower = sec.lower()

    # 1. Бинарные Да/Нет
    if re.match(r'^(да|нет)[\.,\s!]', back_lower) or back_lower in ("да", "нет", "да.", "нет."):
        return True, "binary_yes_no"

    # 2. Тавтологии (когда короткий ответ целиком состоит из слов, уже упомянутых в вопросе)
    stop_words = {
        "орган", "органы", "органов", "органам", "органами", "дело", "дела", "государство", "государства",
        "является", "относятся", "относится", "входит", "входят", "группе", "какой", "какому", "какая",
        "это", "для", "при", "том", "что"
    }
    back_words = [w for w in re.findall(r'[a-zA-Zа-яА-Я0-9]{4,}', back_lower) if w not in stop_words]
    if back_words and len(back_words) <= 3:
        back_stems = {w[:6] for w in back_words}
        front_stems = {w[:6] for w in re.findall(r'[a-zA-Zа-яА-Я0-9]{4,}', front_lower)}
        if back_stems.issubset(front_stems):
            return True, "tautology"

    # 3. Академическая методология и вода вводных глав
    methodology_patterns = [
        r'\bсинергетическ',
        r'\bдиалектическ',
        r'\bметодологи',
        r'методы?\s+исследовани',
        r'теоретические\s+методы.*эмпирическ',
        r'эмпирические\s+методы.*теоретическ',
        r'анкетировани.*интервьюировани',
        r'метод\s+экспертных\s+оценок',
        r'предмет\s+курса',
        r'учебная\s+дисциплина\s*\|\s*предмет',
    ]
    for p in methodology_patterns:
        if re.search(p, front_lower) or re.search(p, back_lower) or re.search(p, sec_lower):
            return True, "academic_methodology_fluff"

    # 4. Устаревшие исторические справки недействующего советского законодательства (1918–1989)
    is_history_subject = any(h in subject_domain.lower() for h in ("history", "история"))
    if not is_history_subject:
        history_patterns = [
            r'\b191[7-9]\b', r'\b192[0-9]\b', r'\b193[0-9]\b', r'\b196[1-3]\b',
            r'\bвчк\b', r'\bогпу\b', r'\bнквд\b', r'ревтрибунал', r'военный\s+трибунал\s+западного\s+фронта',
            r'декрет\s+о\s+суде', r'положение\s+о\s+судоустройстве\s+бсср', r'сельский\s+\(местечковый\)\s+суд',
            r'социалистическое\s+отечество\s+в\s+опасности', r'«тройки»\s+нквд', r'особые\s+совещания'
        ]
        for p in history_patterns:
            if re.search(p, front_lower) or re.search(p, sec_lower):
                return True, "obsolete_historical_trivia"

    # 5. Канцелярское делопроизводство
    clerical_patterns = [
        r'архивная\s+справка.*архивной\s+выписк',
        r'архивная\s+выписка.*архивной\s+справк',
        r'инструкция\s+по\s+делопроизводству\s*\|\s*виды\s+архивных'
    ]
    for p in clerical_patterns:
        if re.search(p, front_lower) or re.search(p, sec_lower):
            return True, "clerical_office_trivia"

    # 6. Банальности и пустые бытовые определения
    banality_patterns = [
        r'^что\s+такое\s+правосудие\??$',
        r'^что\s+такое\s+диалог\??$',
        r'^зачем\s+юристу\s+логика\??$',
        r'какой\s+главный\s+закон\s+страны\??'
    ]
    for p in banality_patterns:
        if re.search(p, front_lower):
            return True, "trivial_banality"

    # 6.1. Мета-вопросы о структуре учебника или программы курса
    meta_course_patterns = [
        r'какие\s+(?:три|3|две|2|четыре|4)\s+части.*(?:курса|дисциплин)',
        r'части\s+курса.*судоустройств',
        r'структур[аеы]\s+учебной\s+дисциплины',
        r'система\s+курса\s+«?судоустройство»?',
        r'на\s+какие\s+(?:три|3)\s+части\s+условно\s+выделяются'
    ]
    for p in meta_course_patterns:
        if re.search(p, front_lower) or re.search(p, sec_lower):
            return True, "meta_course_trivia"

    # 6.2. Контекстная фильтрация (Domain-Aware Scope Governance):
    # В прикладных предметах (право, медицина, IT) отсекаем внепредметные философские экскурсы вводных глав
    is_humanities_subject = any(h in subject_domain.lower() for h in ("philosophy", "философ", "history", "истори", "sociology", "социолог", "political", "политол"))
    if not is_humanities_subject:
        out_of_domain_patterns = [
            r'монтескь[её].*локк',
            r'локк.*монтескь[её]',
            r'концепци[яи]\s+судебно-правовой\s+реформы\s+1992',
            r'джон\s+локк',
            r'шарль\s+монтескь',
            r'монтескь[её]'
        ]
        for p in out_of_domain_patterns:
            if re.search(p, front_lower) or re.search(p, sec_lower):
                return True, "out_of_domain_intro_theory"
    else:
        # В философии и истории отсекаем пустую биографическую шелуху
        bio_trivia = [
            r'в\s+каком\s+году\s+родился',
            r'где\s+родился',
            r'в\s+каком\s+городе\s+(?:жил|умер)',
            r'годы\s+жизни\s+философа'
        ]
        for p in bio_trivia:
            if re.search(p, front_lower):
                return True, "biographical_trivia"

    # 7. Канцелярский балласт: кворумы комиссий, стажировки, рутинные сроки направления бумаг канцелярией
    combined_card_text = f"{front_lower} {back_lower} {sec_lower}"
    clerical_noise_patterns = [
        r'кворум.*(?:заседан|коллеги|комисси)',
        r'правомочн.*заседани.*квалификационн',
        r'стажировк.*(?:продолжительност|срок|месяц|мес|год|претендент|адвокат)',
        r'стажировк.*(?:3|6|от\s+трех|до\s+шести|до\s+одного)',
        r'повторн.*сдач.*экзамен.*(?:срок|месяц|мес|ранее)',
        r'в\s+течение\s+(?:трех|пяти|3|5)\s+(?:рабочих\s+)?дней\s+.*(?:прием|заявлен|направляет\s+копию|регистрац)',
        r'делопроизводств.*(?:канцеляр|архивн|журнал\s+учета)',
    ]
    for p in clerical_noise_patterns:
        if re.search(p, combined_card_text):
            return True, "clerical_bureaucratic_trivia"

    return False, ""


def semantic_normalize_front(text: str) -> str:
    """Формирует инвариантный смысловой отпечаток вопроса для семантической дедупликации."""
    t = re.sub(r'\{\{c\d+::(.*?)(?:::.*?)?\}\}', r'\1', text)
    t = re.sub(r'\[(.*?)\]', r'\1', t)
    t = t.lower()
    words = re.findall(r'[a-zA-Zа-яА-Я0-9]{3,}', t)
    stop_words = {
        "чем", "как", "какой", "какая", "какие", "каком", "каков", "что", "где", "куда",
        "кто", "когда", "почему", "зачем", "отличается", "отличие", "принципиально",
        "судебном", "процессе", "процесс", "суде", "деле", "случае", "согласно", "соответствии",
        "рамках", "сферы", "точки", "зрения", "какова", "заключается", "ключевое", "ключевой",
        "различие", "различия", "разграничение", "основное", "основной", "между", "суть",
        "понятие", "обозначает", "представляет", "собой", "называют", "называется", "определяется"
    }
    def _stem(w: str) -> str:
        for ending in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ях", "ах", "ом", "ем", "ой", "ей", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "а", "я", "о", "е", "у", "ю", "ы", "и"):
            if w.endswith(ending) and len(w) - len(ending) >= 3:
                return w[:-len(ending)][:5]
        return w[:5]

    stems = sorted({_stem(w) for w in words if w not in stop_words})
    # Для коротких или шаблонных вопросов (<3 значащих лемм) не задействуем нечеткую дедупликацию
    if len(stems) < 3:
        return ""
    return " ".join(stems)


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

        # Санитайзер спойлеров в secondary_text ('s'):
        # Если в sec после '|' содержится текст, пересекающийся с ответом back (утечка ключевых слов),
        # отсекаем спойлерную часть, оставляя только нейтральную норму / контекст.
        clean_sec = str(sec).strip()
        clean_back = str(back).strip()
        clean_front = str(front).strip()
        if "|" in clean_sec and clean_back:
            parts = [p.strip() for p in clean_sec.split("|")]
            safe_parts = [parts[0]]
            
            def extract_stems(text_val: str) -> set[str]:
                stop_stems = {"суд", "дел", "прав", "закон", "орган", "норм", "стат", "кодекс", "област", "виды", "вид", "form", "part", "case", "rule", "type"}
                w_list = re.findall(r'[a-zA-Zа-яА-Я0-9]{4,}', text_val.lower())
                stems = set()
                for w in w_list:
                    s = re.sub(r'(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ых|их|ым|им|ом|ем|ами|ями|ях|ах|ов|ев|ей|ам|ям|а|я|у|ю|е|о|ы|и|ь|ing|ed|es|s)$', '', w)
                    if len(s) >= 3 and s not in stop_stems:
                        stems.add(s)
                return stems

            back_stems = extract_stems(clean_back)
            is_concept_def = bool(re.search(r'\b(?:какое понятие|какой термин|назовите понятие|назовите термин|что обозначает|what concept|what term|which term)\b', clean_front.lower()))

            for part in parts[1:]:
                part_lower = part.lower()
                part_stems = extract_stems(part)
                overlap_stems = part_stems.intersection(back_stems)
                is_spoiler = False

                if part_stems and len(overlap_stems) > 0:
                    is_spoiler = True
                elif is_concept_def and any(s in clean_back.lower() for s in part_stems if len(s) >= 4):
                    is_spoiler = True
                elif re.search(r'\b(?:срок|дней|суток|месяц|кгб|комитет|надзор|отмена|запрещен|противопоказан)\b', part_lower) and any(w in clean_back.lower() for w in part_lower.split()):
                    is_spoiler = True

                if not is_spoiler:
                    safe_parts.append(part)
            clean_sec = " | ".join(safe_parts)

        organ = (
            item.get("o")
            or item.get("organ_slug")
            or item.get("organ")
            or item.get("module")
            or item.get("subsystem")
            or ""
        )
        try:
            layer_val = item.get("y") if item.get("y") is not None else (item.get("layer") if item.get("layer") is not None else 1)
            layer = int(layer_val)
        except (ValueError, TypeError):
            layer = 1
        layer = max(0, min(3, layer))

        c_obj = {
            "text": str(front).strip(),
            "secondary_text": clean_sec,
            "translation": clean_back,
            "example": str(ex).strip(),
            "initial_difficulty_tier": diff if diff in ("easy", "medium", "hard") else "medium",
            "mnemonic": None,  # Ленивая генерация мнемоник
            "theme": str(theme).strip() or title,
            "organ_slug": str(organ).strip().lower() or None,
            "layer": layer,
            "topological_rank": 0,
            "content_type": "cloze" if "{{c" in str(front) else "text"
        }

        # Фильтр качества (Blacklist Fluff Purge)
        is_bl, bl_reason = is_blacklisted_card(c_obj, subject_domain=domain)
        if is_bl:
            continue

        cards.append(c_obj)

    # Топологическое ранжирование (Curriculum-First / "Graph in engine, playlist in UI"):
    # Упорядочиваем карточки строго от фундамента к частностям:
    # 1. По порядку появления органов/модулей (organ_slug)
    # 2. По когнитивному слою (layer: 0 -> 1 -> 2 -> 3)
    organ_order = {}
    for c in cards:
        o = c.get("organ_slug") or "general"
        if o not in organ_order:
            organ_order[o] = len(organ_order)

    cards.sort(key=lambda c: (organ_order.get(c.get("organ_slug") or "general", 999), int(c.get("layer", 1))))
    for rank_idx, c in enumerate(cards, 1):
        c["topological_rank"] = rank_idx

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
                from app.services.graph_service import clean_graph_data, build_hierarchical_tree, ensure_connected_spiderweb
                clean_nodes, clean_edges = clean_graph_data(input_nodes, input_edges if isinstance(input_edges, list) else [])
                if clean_nodes:
                    clean_nodes, clean_edges = ensure_connected_spiderweb(clean_nodes, clean_edges, fallback_title=title or slug or "Каркас дисциплины")
                    tree_data = build_hierarchical_tree(clean_nodes, clean_edges, root_title=title or slug or "Каркас дисциплины")
            except Exception as ge:
                print(f"[AI Gateway] Ошибка очистки графа из ответа LLM: {ge}")

    result = {
        "subject_domain": domain,
        "subject_slug": slug,
        "phrase_title": title,
        "cards": cards
    }
    if "modules" in raw_data and isinstance(raw_data["modules"], list):
        result["modules"] = raw_data["modules"]
    if clean_nodes:
        result["knowledge_graph"] = {
            "nodes": clean_nodes,
            "edges": clean_edges,
            "tree_data": tree_data
        }
        result["graph"] = result["knowledge_graph"]
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
            modifiers.append("CARD VOLUME: STRICT HIGH-YIELD PARETO LIMIT. Extract strictly 2 to 4 high-yield situational cards from this text chunk (~60–80 total cards for a typical textbook). Focus exclusively on Decision Trees, Contrast Pairs, and High-Yield Taxonomy. Zero 'Что такое X', zero 'Да/Нет'. If this chunk contains solely clerical paperwork, bylaw procedures, or committee quorums, return 0 cards.")
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
    fallback_subject: str = "generic",
    force_chat_model: bool = False
) -> tuple[dict, dict]:
    """Вызывает DeepSeek напрямую через стандартный REST API с поддержкой JSON Mode, Context Caching и автоматической десериализацией."""
    api_key = (settings.DEEPSEEK_API_KEY or "").strip().strip('"\'')
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")

    base_url = settings.DEEPSEEK_BASE_URL.rstrip('/')
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    target_model = "deepseek-chat" if force_chat_model else (settings.DEEPSEEK_MODEL or "deepseek-flash")

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 8192 if target_model == "deepseek-chat" else 32768,
        "thinking": {"type": "disabled"}
    }
    if target_model == "deepseek-reasoner":
        payload.pop("thinking", None)

    print(f"[AI Gateway / DeepSeek] Вызов модели: {target_model} (Prompt Caching enabled)...")
    resolved_model = target_model
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        
        # Автоматический fallback: если запрошенная модель недоступна/не найдена на сервере провайдера, пробуем deepseek-chat
        if response.status_code in (400, 404) and target_model != "deepseek-chat":
            print(f"[AI Gateway / DeepSeek WARNING] Модель '{target_model}' вернула код {response.status_code}. Пробуем стандартную 'deepseek-chat'...")
            payload["model"] = "deepseek-chat"
            payload["max_tokens"] = 8192
            payload.pop("thinking", None)
            resolved_model = "deepseek-chat"
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            data = response.json()
            
            # Логируем метрики эффективности кэширования DeepSeek
            usage = data.get("usage") or {}
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("Ответ от DeepSeek API не содержит choices.")

            cache_hit_tokens = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss_tokens = usage.get("prompt_cache_miss_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", cache_hit_tokens + cache_miss_tokens)
            output_tokens = usage.get("completion_tokens", 0)
            cache_hit = cache_hit_tokens > 0
            print(f"[DeepSeek Metrics] Кэш-хит: {cache_hit_tokens} токенов (~$0.003-0.006/1M) | Мисс: {cache_miss_tokens} токенов | Вывод: {output_tokens} токенов | Модель: {resolved_model}")

            content = choices[0]["message"]["content"]
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

# --- ПРОХОД 1 (TWO-PASS ARCHITECTURE): ИЗВЛЕЧЕНИЕ АРХИТЕКТУРНОГО ДЕРЕВА ОРГАНОВ/МОДУЛЕЙ ---
CURRICULUM_SKELETON_SYSTEM_PROMPT = """ROLE: Chief Educational Architect and Knowledge Graph Ontologist.
MISSION: Analyze the uploaded syllabus, table of contents, or course material across ANY academic or professional discipline (law, medicine, STEM, humanities, philosophy).
Synthesize a strictly hierarchical, clean, cycle-free curriculum outline and knowledge graph structure.
CORE GOAL: Break down the discipline into 5 to 8 major organs, branches, or structural modules in strict didactic order (from foundational macro-structures to specialized components).

STRICT MINIFIED JSON SCHEMA:
{
  "subject_slug": "snake_case_slug",
  "phrase_title": "Informative Main Course Title",
  "modules": [
    {
      "slug": "unique_slug",
      "name": "Clear Module / Organ Name",
      "summary": "1 sentence defining this module's place and purpose",
      "quota": 10,
      "layer": 0
    }
  ],
  "graph": {
    "nodes": [
      {
        "id": "slug_id",
        "name": "Entity Name",
        "category": "authority|instance|condition|exception|legal_status",
        "summary": "1 sentence definition",
        "parent_id": null,
        "level": 0
      }
    ],
    "edges": [
      {
        "source": "source_id",
        "target": "target_id",
        "relation": "appealed_to|excludes_application|demarcated_from|subject_to_jurisdiction",
        "label": "Связка на русском"
      }
    ]
  }
}
Return STRICTLY raw JSON without markdown or commentary.
"""

# --- ФУНКЦИЯ ОПРЕДЕЛЕНИЯ КРИТИЧЕСКИХ ОШИБОК ---
def is_failover_error(err: Exception) -> bool:
    """Определяет, относится ли ошибка провайдера к сбоям баланса, авторизации, лимитов или сети."""
    err_str = str(err).lower()
    return (
        any(s in err_str for s in ("402", "insufficient", "balance", "401", "unauthorized", "api_key не установлен", "not set", "429", "quota", "timeout", "connect"))
        or isinstance(err, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))
    )

async def extract_curriculum_skeleton(
    text: str,
    target_subject: str = "",
    target_card_count: int = 70,
    user_id: str = "default_user",
    job_id: str | None = None,
    force_chat_model: bool = False
) -> dict:
    """Проход 1: Извлекает иерархический скелет органов/модулей и распределяет квоты на целевой пул карточек через DeepSeek."""
    clean_sub = target_subject.strip().lower() or "generic"
    max_sample_chars = 70000
    sample_text = text[:max_sample_chars] if len(text) > max_sample_chars else text
    user_prompt = (
        f"[TARGET SUBJECT]: {clean_sub}\n"
        f"[TARGET TOTAL CARDS]: {target_card_count}\n"
        f"Extract strictly 5 to 8 pedagogical modules/organs in logical didactic progression (from root foundation to branches).\n"
        f"Distribute the {target_card_count} card quota across these modules so that the sum of 'quota' equals {target_card_count}.\n\n"
        f"[COURSE MATERIAL SAMPLE / OUTLINE]:\n{sample_text}"
    )
    start_ts = time.time()
    model_requested = "deepseek-chat" if force_chat_model else (settings.DEEPSEEK_MODEL or "deepseek-flash")
    try:
        raw_res, meta = await call_deepseek(
            user_prompt,
            system_instruction=CURRICULUM_SKELETON_SYSTEM_PROMPT,
            fallback_subject=clean_sub,
            force_chat_model=force_chat_model
        )

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
            repair_successful=meta.get("repair_successful", False),
            cards_generated=0,
            duration_ms=duration_ms,
            status="success"
        )
        return raw_res
    except Exception as e:
        print(f"[AI Gateway WARN] Сбой Прохода 1 (Curriculum Skeleton): {e}")
        return {
            "subject_slug": clean_sub,
            "phrase_title": clean_sub,
            "modules": [],
            "graph": {"nodes": [], "edges": []}
        }

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
    job_id: str | None = None,
    skip_graph: bool = False,
    force_chat_model: bool = False
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
    if skip_graph:
        user_directives.append(
            "INTRA-CHUNK GRAPH DIRECTIVE: Master course ontology and graph are already synthesized in Pass 1. "
            "Return strictly empty graph: \"graph\": {\"nodes\": [], \"edges\": []}. "
            "Do NOT waste tokens generating redundant graph nodes or edges. Direct 100% capacity exclusively to high-yield cards in 'c'."
        )
    if volume in ("auto", "balanced"):
        user_directives.append(
            "CARD VOLUME: HIGH-YIELD BALANCED EXTRACTION. "
            "Extract 10 to 14 master conceptual cards from this section (proportionate to its substantive weight, targeting ~140–180 cards for an entire multi-chapter course). "
            "Do NOT exceed 15 cards. "
            "Ensure diverse, natural phrasing across 4 universal cognitive archetypes: "
            "1) Situational Cases / Problem Vignettes (concrete factual conflict/scenario -> statutory qualification or solution), "
            "2) Contrast Pairs (distinguishing confusing concepts via gold standard criteria), "
            "3) Doctrinal Principles & Causal Mechanisms (substantive tenets, arguments of thinkers, and operational mechanisms), "
            "4) Normative Conditions & Exceptions (exact threshold, qualification, or consequence, 'если-то'). "
            "Strictly avoid robotic boilerplate question openers (do NOT repeat 'Какое понятие обозначает...' or 'Чем принципиально отличается...'). "
            "Absolute prohibition of clerical trivia (quorums, paperwork deadlines, routine office intervals), naive dictionary definitions ('Что такое X'), and binary 'Да/Нет'. "
            "If this chunk contains solely clerical paperwork or administrative procedures, return 0 cards."
        )
    elif volume in ("low", "low_5"):
        user_directives.append("CARD VOLUME: Strictly 4 to 6 core cards. Absolute highest-yield master concepts only.")
    elif volume == "med_10":
        user_directives.append("CARD VOLUME: Strictly 8 to 10 core cards.")
    elif volume in ("medium", "med_15"):
        user_directives.append("CARD VOLUME: Strictly 10 to 14 cards.")
    elif volume in ("high", "high_20"):
        user_directives.append("CARD VOLUME: Maximum 16 to 18 cards.")
    elif volume == "max":
        user_directives.append("CARD VOLUME: Exhaustive extraction (up to 20 cards). Every verifiable fact and distinction.")

    user_directives.append(
        "EXAMPLE CONCISENESS DIRECTIVE: The 'e' field must be strictly 1 punchy, vivid sentence (maximum 15 words) "
        "providing a concrete real-world case, thought experiment, or practical scenario. Never write verbose multi-sentence essays in 'e'."
    )
    user_directives.append(
        "SYNTACTIC VARIETY DIRECTIVE: Strictly avoid monotonous boilerplate phrasing. "
        "Do NOT start multiple cards with identical formulaic stems. Formulate questions naturally, variedly, and professionally as an expert university examiner or senior practitioner."
    )
    source_count = (
        text.count("=== МАТЕРИАЛ")
        + text.count("=== СТРАНИЦА")
        + text.count("--- Стр.")
        + text.count("=== ДОКУМЕНТ")
    )
    if source_count > 1:
        user_directives.append(
            "MULTI-SOURCE THEMATIC CLUSTERING & PARETO FILTER: The source contains multiple photos, pages, or separate documents. "
            "Group cards into their respective thematic clusters, assign 'h' (topic name) to each card, "
            "keep all cards in the single root 'c' array (do NOT create nested 'clusters' objects). "
            "PARETO PRIORITY: Select high-yield conceptual nodes across the material, skipping clerical minutiae, quorums, or paperwork intervals."
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
    model_requested = "deepseek-chat" if force_chat_model else (settings.DEEPSEEK_MODEL or "deepseek-chat")
    fallback_used = False
    json_repair_applied = False
    res = None
    meta = {}
    
    print(f"[AI Gateway] Вызов DeepSeek ({model_requested}) в режиме '{granularity_mode}' с Prompt Caching...")
    try:
        res, meta = await call_deepseek(
            user_prompt,
            system_instruction=DEEPSEEK_CACHED_SYSTEM_PROMPT,
            fallback_subject=clean_sub,
            force_chat_model=force_chat_model
        )

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
            cards_generated=len(res.get("cards", [])) if isinstance(res, dict) else 0,
            duration_ms=duration_ms,
            status="success"
        )
    except Exception as llm_err:
        err_str = str(llm_err)
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
        raise llm_err

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

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    target_model = settings.DEEPSEEK_MODEL or "deepseek-flash"
    fallback_model = "deepseek-chat"

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code in (400, 404) and payload["model"] != fallback_model:
            payload["model"] = fallback_model
            res = await client.post(url, headers=headers, json=payload)
        if res.status_code == 200:
            choices = res.json().get("choices") or []
            if not choices:
                raise ValueError("Ответ от модели не содержит choices.")
            content = choices[0]["message"]["content"]
            return extract_json_payload(content)
        else:
            raise RuntimeError(f"DeepSeek mnemonic error ({res.status_code}): {res.text}")

# --- УМНОЕ ЧАНКОВАНИЕ ДЛИННЫХ ДОКУМЕНТОВ И КНИГ (МАКРО-ГЛАВЫ ДЛЯ 64K КОНТЕКСТА) ---
def split_text_into_chunks(text: str, max_chunk_chars: int = 85000, overlap_chars: int = 2000) -> list[str]:
    """
    Интеллектуальное разбиение длинного документа на смысловые разделы/главы (по умолчанию ~10-15 страниц / до 35 000 знаков).
    Сохраняет естественные границы глав, разделов, страниц, документов, слайдов и параграфов, добавляя скользящее перекрытие (overlap).
    Обеспечивает гарантированное внимание LLM к каждому разделу книги без овер-сжатия материала.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chunk_chars:
        return [text]

    # Паттерн ищет границы глав, разделов, тем, страниц, слайдов или документов
    split_pattern = r'(?=(?:\n--- [^\n]+: (?:Стр\.|Слайд) \d+ ---|\n=== [^\n]+ ===|\n\s*(?:Глава|ГЛАВА|Раздел|РАЗДЕЛ|Chapter|CHAPTER|Тема|ТЕМА|§)\s+\d+))'
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


