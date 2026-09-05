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
    mnemonic: MnemonicSchema

class ParsedDataSchema(BaseModel):
    subject_domain: str = Field(description="language, law, code или generic")
    subject_slug: str = Field(description="Машиночитаемый код предмета в snake_case")
    phrase_title: str = Field(description="Название темы или блока карточек")
    cards: list[CardSchema]

# --- ОПТИМИЗИРОВАННЫЙ СЖАТЫЙ СИСТЕМНЫЙ ПРОМПТ (В 3 РАЗА МЕНЬШЕ ТОКЕНОВ) ---
COMPACT_SYSTEM_PROMPT = """ROLE: Expert cognitive psychologist and Data Grinder knowledge deconstructor.
TASK: Analyze the user's raw text and output a strict, valid JSON object containing atomic flashcards for spaced repetition (FSRS).

DISCIPLINE RULES:
- language: text=Foreign word; secondary_text=Pronunciation/pinyin (with tone diacritics); translation=Precise Russian meaning; example=Sentence in Russian/source.
- law: text=Legal term/doctrine; secondary_text=Article/code reference (strict '_rb' suffix for Belarus, '_rf' for Russia); translation=Definitive legal meaning in Russian; example=Real application case.
- code: text=Function/concept/pattern; secondary_text=Signature/context; translation=Technical breakdown; example=Minimal code snippet.
- generic: text=Formula/core concept; secondary_text=Section/params; translation=Full explanation/proof; example=Application scenario.

MNEMONICS RULE: For every card, generate a memorable Russian association (keyword and verbal_cue). Keep it vivid and concise (1-2 sentences).
DIFFICULTY: Assign 'initial_difficulty_tier' strictly from: ["easy", "medium", "hard"].

CRITICAL JSON RULES:
- Return ONLY a single raw JSON object matching the schema below.
- Do NOT wrap in markdown formatting (no ```json). Do NOT add conversational prose.
- Never use unescaped double quotes inside text values; use single quotes instead.

SCHEMA STRUCTURE:
{
  "subject_domain": "language/law/code/generic",
  "subject_slug": "lowercase_snake_case_slug",
  "phrase_title": "Clear Topic Header",
  "cards": [
    {
      "text": "Front side term",
      "secondary_text": "Pinyin/article/hint",
      "translation": "Back side definition",
      "example": "Practical example/usage",
      "initial_difficulty_tier": "easy/medium/hard",
      "mnemonic": {"keyword": "Ключевое слово", "verbal_cue": "Сюжетная ассоциация"}
    }
  ]
}
"""

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

# --- DEEPSEEK ВЫЗОВ (ЧЕРЕЗ HTTPX И OPENAI-СОВМЕСТИМЫЙ REST API) ---
async def call_deepseek(prompt: str, system_instruction: str) -> dict:
    """Вызывает DeepSeek 4 поколения (v4-flash / v4-pro) через стандартный REST API с поддержкой JSON Mode."""
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")

    base_url = settings.DEEPSEEK_BASE_URL.rstrip('/')
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Каскад моделей 4 поколения: deepseek-v4-flash -> deepseek-v4-pro -> deepseek-chat
    models_to_try = [
        settings.DEEPSEEK_MODEL,
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-chat"
    ]
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model_name in models_to_try:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Analyze and structure the following text into JSON flashcards:\n\n{prompt}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 4096
        }

        try:
            print(f"[AI Gateway / DeepSeek] Попытка генерации с моделью: {model_name}...")
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    return json.loads(content)
                else:
                    err_msg = f"DeepSeek API error ({response.status_code}): {response.text}"
                    print(f"[WARNING] Модель {model_name} вернула ошибку: {err_msg[:140]}")
                    last_err = RuntimeError(err_msg)
                    if response.status_code in (400, 404, 429, 503):
                        continue
        except Exception as e:
            last_err = e
            print(f"[WARNING] Сбой при вызове {model_name}: {e}")
            await asyncio.sleep(0.5)

    if last_err:
        raise last_err
    raise RuntimeError("Все модели DeepSeek недоступны.")

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

    if not text:
        return {"subject_domain": "generic", "subject_slug": target_subject or "generic", "phrase_title": "", "cards": []}

    subject_instruction = ""
    if target_subject.strip():
        clean_sub = target_subject.strip().lower()
        subject_instruction = f"\nTARGET SUBJECT DIRECTIVE: All cards must strictly belong to subject '{clean_sub}'. Set 'subject_slug' to '{clean_sub}'.\n"

    system_instruction = COMPACT_SYSTEM_PROMPT + subject_instruction + build_granularity_prompt(
        granularity_mode, custom_instruction, density, volume
    )

    provider = settings.AI_PROVIDER.lower()
    
    # 1. Если выбран DeepSeek
    if provider == "deepseek":
        try:
            print(f"[AI Gateway] Вызов DeepSeek ({settings.DEEPSEEK_MODEL}) в режиме '{granularity_mode}'...")
            res = await call_deepseek(text, system_instruction)
        except Exception as ds_err:
            print(f"[WARNING] Сбой DeepSeek: {ds_err}. Попытка резервного вызова Gemini...")
            if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "placeholder_gemini_key":
                try:
                    res = await call_gemini(text, system_instruction)
                except Exception as gem_err:
                    raise RuntimeError(f"DeepSeek ({ds_err}) и Gemini ({gem_err}) не ответили.")
            else:
                raise ds_err

    # 2. Если выбран Gemini
    else:
        try:
            print(f"[AI Gateway] Вызов Gemini ({settings.GEMINI_MODEL}) в режиме '{granularity_mode}'...")
            res = await call_gemini(text, system_instruction)
        except Exception as gem_err:
            if settings.DEEPSEEK_API_KEY:
                print(f"[WARNING] Сбой Gemini: {gem_err}. Попытка резервного вызова DeepSeek...")
                res = await call_deepseek(text, system_instruction)
            else:
                raise gem_err

    if target_subject.strip() and isinstance(res, dict):
        res["subject_slug"] = target_subject.strip().lower()
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

    if settings.AI_PROVIDER.lower() == "deepseek" and settings.DEEPSEEK_API_KEY:
        models_to_try = [
            settings.DEEPSEEK_MODEL,
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat"
        ]
        models_to_try = list(dict.fromkeys(models_to_try))
        for model_name in models_to_try:
            try:
                url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
                headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    res = await client.post(url, headers=headers, json=payload)
                    if res.status_code == 200:
                        content = res.json()["choices"][0]["message"]["content"]
                        return json.loads(content)
                    print(f"[AI Gateway] DeepSeek ({model_name}) mnemonic error: {res.text[:120]}")
            except Exception as e:
                print(f"[AI Gateway] Ошибка регенерации мнемоники через {model_name}: {e}")

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
