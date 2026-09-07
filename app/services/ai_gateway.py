import json
import asyncio
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
DEEPSEEK_CACHED_SYSTEM_PROMPT = """ROLE: Expert cognitive psychologist, neuro-education engineer, and Data Grinder knowledge deconstructor.
MISSION: Analyze raw unstructured source material and synthesize an optimized JSON package containing atomic flashcards for the Free Spaced Repetition Scheduler (FSRS).
CORE PHILOSOPHY: Deconstruct complex texts into minimal, indivisible, non-interfering conceptual atoms. Every card must minimize cognitive load while maximizing retrieval strength.

1. ATOMICITY & COGNITIVE DESIGN RULES:
- Minimum Information Principle: One card = One atomic fact, rule, pattern, or distinction. Never bundle multiple concepts into one card.
- Eliminate Redundancy: Strip introductory filler, narrative padding, rhetorical questions, and pleasantries.
- Contrast & Non-Interference: Inverted pairs or easily confused terms must have clear distinct cues in the secondary text.
- Cognitive Anchor: The front side must act as a precise retrieval prompt, not a vague topic header.
- Definite Answer: The back side must provide a crisp, authoritative definition or explanation without unnecessary disclaimers.
- Real-World Grounding: The example field must contain a concise, concrete case, minimal code snippet, sentence in context, or legal precedent.

2. DISCIPLINE DIRECTIVES & TAXONOMY:
- language (Foreign languages & Linguistics):
  * t (Front): Foreign word, idiom, or grammatical construction in standard orthography.
  * s (Secondary): Phonetic transcription, IPA, or Chinese Pinyin with explicit tone diacritics.
  * d (Back): Precise definition and translation in Russian. Nuance and register notes if critical.
  * e (Example): Natural exemplar sentence illustrating idiomatic usage.
  * l (Difficulty): 'easy' for high-frequency cognates, 'medium' for regular lexis, 'hard' for false friends or irregulars.

- law (Jurisprudence, Statutes & Doctrine):
  * t (Front): Legal term, Latin maxim, constitutional principle, or statutory doctrine.
  * s (Secondary): Exact article and code identifier with jurisdiction code (e.g., 'ст. 303 ГК РФ' or 'ст. 100 УК РБ').
  * d (Back): Authoritative legal definition, disposition, qualifying signs, or legal consequences.
  * e (Example): Authentic judicial scenario, dispute resolution case, or qualifying factual circumstance.
  * l (Difficulty): 'easy' for standard terms, 'medium' for multi-element rules, 'hard' for competing doctrines/exceptions.

- code (Software Engineering, CS & Algorithms):
  * t (Front): Algorithm, design pattern, function name, API concept, or data structure.
  * s (Secondary): Language name, standard library path, or signature (e.g., 'Python 3.12 / asyncio.gather(*coros)').
  * d (Back): Rigorous technical breakdown, invariant, algorithmic time/space complexity O(N), or core mechanics.
  * e (Example): Minimal valid code snippet (1-4 lines) demonstrating usage or idiomatic edge-case trap.
  * l (Difficulty): 'easy' for syntax, 'medium' for standard patterns, 'hard' for concurrency/memory traps.

- generic (Science, Medicine, History, Engineering, General Knowledge):
  * t (Front): Core theorem, physiological mechanism, formula, diagnosis, or historical event.
  * s (Secondary): Sub-discipline, category, unit of measurement, or time period.
  * d (Back): Exhaustive causal explanation, physical meaning, proof idea, or clinical presentation.
  * e (Example): Practical lab observation, clinical case, historical trigger, or industrial calculation.
  * l (Difficulty): Strictly select from: 'easy', 'medium', 'hard'.

3. GRANULARITY MODES:
- atomic: Decompose concepts into standalone cards. Each card represents one testable memory unit.
- single_deep: Synthesize the entirety of the text into exactly ONE master reference card.
- cheatsheet: Ultra-compact blitz cards with punchy 1-2 sentence core summaries.

4. MULTI-SOURCE THEMATIC CLUSTERING & GROUPING:
- When input contains multiple photos, scanned pages, or mixed notes (e.g. photos of different topics taken in random order):
  * Semantically cluster and group related concepts into their respective topics/themes.
  * Set 'h' on each card to its specific thematic cluster or topic name (e.g., 'Договор купли-продажи' vs 'Состав преступления').
  * Exhaustively extract cards across ALL provided photos/pages. Never restrict cards to just the first photo or first topic.

5. STRICT MINIFIED JSON SCHEMA SPECIFICATION:
To conserve bandwidth, eliminate token waste, and maximize inference speed, output ONLY a valid raw JSON object matching this exact minified key structure:
{
  "domain": "language|law|code|generic",
  "slug": "machine_readable_subject_slug_in_snake_case",
  "title": "Clean Informative Deck Title",
  "c": [
    {
      "t": "Front prompt / question / term",
      "s": "Secondary context / hint / article / signature",
      "d": "Back definition / answer / translation",
      "e": "Concrete example / code snippet / judicial case",
      "l": "easy|medium|hard",
      "h": "Specific thematic topic / cluster name (especially if source has multiple mixed topics)"
    }
  ]
}

FEW-SHOT SYNTACTIC EXAMPLES:

Example 1 (Language - Chinese):
{
  "domain": "language",
  "slug": "chinese_hsk",
  "title": "HSK 4 Бизнес-лексика",
  "c": [
    {
      "t": "合同",
      "s": "hétong",
      "d": "Контракт, письменный договор",
      "e": "双方签订了正式合同 (Обе стороны подписали официальный контракт)",
      "l": "medium"
    }
  ]
}

Example 2 (Law - Criminal Procedure):
{
  "domain": "law",
  "slug": "criminal_procedure_rf",
  "title": "Меры пресечения в УПК РФ",
  "c": [
    {
      "t": "Презумпция невиновности",
      "s": "ст. 14 УПК РФ",
      "d": "Обвиняемый считается невиновным, пока его виновность не будет доказана в предусмотренном законом порядке и установлена вступившим в законную силу приговором суда. Бремя доказывания лежит на обвинении.",
      "e": "Неустранимые сомнения в виновности лица толкуются в пользу обвиняемого при оценке косвенных улик.",
      "l": "easy"
    }
  ]
}

Example 3 (Code - Python Concurrency):
{
  "domain": "code",
  "slug": "python_asyncio",
  "title": "Python AsyncIO Primitives",
  "c": [
    {
      "t": "asyncio.shield()",
      "s": "asyncio.tasks.shield(arg)",
      "d": "Предотвращает отмену переданной корутины или Future при отмене родительской задачи. Если родитель отменен, внутренняя задача продолжает выполняться в фоне.",
      "e": "res = await asyncio.shield(save_critical_transaction_to_db())",
      "l": "hard"
    }
  ]
}

5. CRITICAL FORMATTING & SYNTAX CONSTRAINTS:
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
            modifiers.append("DENSITY: Deep, exhaustive explanations with fine technical/legal details, sub-clauses, and exceptions.")

    # 3. Пользовательское свободное пожелание (Кастомный промпт)
    if custom_instruction.strip():
        modifiers.append(f"USER CUSTOM OVERRIDE (HIGHEST PRIORITY): {custom_instruction.strip()}")

    return "\n" + "\n".join(modifiers) + "\n"

import re

def extract_json_payload(content: str) -> dict:
    """Безопасно извлекает и парсит JSON из ответа LLM (убирая markdown-блоки, переносы, висячие запятые и обрывы токенов)."""
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
        return json.loads(clean)
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
                return json.loads(slice_candidate)
            except json.JSONDecodeError:
                # Попытка исправить trailing commas
                fixed = re.sub(r",\s*([\]}])", r"\1", slice_candidate)
                try:
                    return json.loads(fixed)
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
                    return res
                except json.JSONDecodeError:
                    search_from = candidate.rfind('}', 0, search_from)
        
        # Попытка закрыть незакрытые кавычки и скобки
        trimmed = candidate.rstrip()
        for closer in ['"]}', '"}', '"]', '}', ']}']:
            try:
                fixed = re.sub(r",\s*([\]}])", r"\1", trimmed + closer)
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Не удалось обнаружить валидный JSON в ответе ИИ: {clean[:200]}...")

# --- DEEPSEEK ВЫЗОВ (ЧЕРЕЗ HTTPX И OPENAI-СОВМЕСТИМЫЙ REST API) ---
async def call_deepseek(user_prompt: str, system_instruction: str = DEEPSEEK_CACHED_SYSTEM_PROMPT, fallback_subject: str = "generic") -> dict:
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
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        
        # Автоматический fallback: если запрошенная модель недоступна/не найдена на сервере провайдера, пробуем deepseek-chat
        if response.status_code in (400, 404) and target_model != "deepseek-chat":
            print(f"[AI Gateway / DeepSeek WARNING] Модель '{target_model}' вернула код {response.status_code}. Пробуем стандартную 'deepseek-chat'...")
            payload["model"] = "deepseek-chat"
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            data = response.json()
            
            # Логируем метрики эффективности кэширования DeepSeek
            usage = data.get("usage", {})
            cache_hit = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss = usage.get("prompt_cache_miss_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            print(f"[DeepSeek Metrics] Кэш-хит: {cache_hit} токенов (-90% цена) | Мисс: {cache_miss} токенов | Вывод: {output_tokens} токенов")

            content = data["choices"][0]["message"]["content"]
            raw_payload = extract_json_payload(content)
            return unpack_minified_cards(raw_payload, fallback_subject=fallback_subject)
        else:
            print(f"[AI Gateway / DeepSeek ERROR] Код {response.status_code}: {response.text}")
            raise RuntimeError(f"DeepSeek API error ({response.status_code}): {response.text}")

# --- GEMINI ВЫЗОВ (РЕЗЕРВНЫЙ / КАДРИРОВАННЫЙ КАСКАД) ---
async def call_gemini(prompt: str, system_instruction: str) -> dict:
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
            return json.loads(response.text)
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
    custom_instruction: str = ""
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
        user_directives.append("CARD VOLUME: Extract the 15 to 25 highest-value atomic cards. Do not exceed 25 cards per batch to prevent output truncation.")
    elif volume in ("low", "low_5"):
        user_directives.append("CARD VOLUME: Maximum 5 cards.")
    elif volume == "med_10":
        user_directives.append("CARD VOLUME: Maximum 10 cards.")
    elif volume == "high_20":
        user_directives.append("CARD VOLUME: Maximum 20 cards.")
    elif volume in ("high", "max"):
        user_directives.append("CARD VOLUME: Maximum 30 cards.")
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
    
    # 1. Если выбран DeepSeek (основной экономичный провайдер с Prompt Caching)
    if provider == "deepseek":
        print(f"[AI Gateway] Вызов DeepSeek ({settings.DEEPSEEK_MODEL}) в режиме '{granularity_mode}' с Prompt Caching...")
        res = await call_deepseek(user_prompt, system_instruction=DEEPSEEK_CACHED_SYSTEM_PROMPT, fallback_subject=clean_sub)

    # 2. Если выбран Gemini (резервный)
    else:
        print(f"[AI Gateway] Вызов Gemini ({settings.GEMINI_MODEL}) в режиме '{granularity_mode}'...")
        res = await call_gemini(user_prompt, DEEPSEEK_CACHED_SYSTEM_PROMPT)
        if isinstance(res, dict) and ("c" in res or "domain" in res):
            res = unpack_minified_cards(res, fallback_subject=clean_sub)

    if clean_sub and isinstance(res, dict):
        res["subject_slug"] = clean_sub
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
