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
    
    # 1. Режим гранулярности
    if granularity_mode == "single_deep":
        modifiers.append("GRANULARITY DIRECTIVE: Create EXACTLY ONE comprehensive master-card. Synthesize all concepts, sub-clauses, and nuances of the entire text into this single definitive card. Do not create multiple cards.")
    elif granularity_mode == "cheatsheet":
        modifiers.append("GRANULARITY DIRECTIVE: Ultra-concise cheat-sheet mode. Simplify definitions to punchy 1-2 sentence core summaries. Maximum brevity.")
    else: # atomic
        modifiers.append("GRANULARITY DIRECTIVE: Standard atomic card decomposition. Break down distinct concepts into separate standalone cards.")
        if volume == "low":
            modifiers.append("LIMIT: Maximum 5 cards.")
        elif volume == "medium":
            modifiers.append("LIMIT: Maximum 15 cards.")
        elif volume == "high":
            modifiers.append("LIMIT: Maximum 30 cards.")
        elif volume == "max":
            modifiers.append("LIMIT: Extract all relevant items exhaustively.")

    # 2. Плотность определений
    if density == "low":
        modifiers.append("DENSITY: Brief and simple definitions.")
    elif density == "high":
        modifiers.append("DENSITY: Deep, exhaustive explanations with fine technical/legal details.")

    # 3. Пользовательское свободное пожелание (Кастомный промпт)
    if custom_instruction.strip():
        modifiers.append(f"USER CUSTOM OVERRIDE (HIGHEST PRIORITY): {custom_instruction.strip()}")

    return "\n" + "\n".join(modifiers) + "\n"

# --- DEEPSEEK ВЫЗОВ (ЧЕРЕЗ HTTPX И OPENAI-СОВМЕСТИМЫЙ REST API) ---
async def call_deepseek(prompt: str, system_instruction: str) -> dict:
    """Вызывает DeepSeek-V3 через стандартный REST API с поддержкой JSON Mode."""
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")

    base_url = settings.DEEPSEEK_BASE_URL.rstrip('/')
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Analyze and structure the following text into JSON flashcards:\n\n{prompt}"}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 4096
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"DeepSeek API error ({response.status_code}): {response.text}")
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

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
        temperature=0.2
    )

    models_to_try = [settings.GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model_name in models_to_try:
        try:
            print(f"[AI Gateway / Gemini] Attempting {model_name}...")
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )
            return json.loads(response.text)
        except Exception as e:
            last_err = e
            err_str = str(e)
            print(f"[WARNING] Gemini model {model_name} failed: {err_str[:100]}")
            if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str or "404" in err_str:
                continue
            await asyncio.sleep(1.0)

    if last_err:
        raise last_err
    raise RuntimeError("Все модели Gemini недоступны.")

# --- УНИВЕРСАЛЬНЫЙ ПАРСЕР ТЕКСТА ---
async def parse_raw_text(
    text: str,
    density: str = "medium",
    volume: str = "medium",
    priority: str = "balanced",
    preference: str = "acoustic",
    granularity_mode: str = "atomic",
    custom_instruction: str = ""
) -> dict:
    if not text.strip():
        return {"subject_domain": "generic", "subject_slug": "generic", "phrase_title": "", "cards": []}

    system_instruction = COMPACT_SYSTEM_PROMPT + build_granularity_prompt(
        granularity_mode, custom_instruction, density, volume
    )

    provider = settings.AI_PROVIDER.lower()
    
    # 1. Если выбран DeepSeek
    if provider == "deepseek":
        try:
            print(f"[AI Gateway] Вызов DeepSeek ({settings.DEEPSEEK_MODEL}) в режиме '{granularity_mode}'...")
            return await call_deepseek(text, system_instruction)
        except Exception as ds_err:
            print(f"[WARNING] Сбой DeepSeek: {ds_err}. Попытка резервного вызова Gemini...")
            if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "placeholder_gemini_key":
                try:
                    return await call_gemini(text, system_instruction)
                except Exception as gem_err:
                    raise RuntimeError(f"DeepSeek ({ds_err}) и Gemini ({gem_err}) не ответили.")
            raise ds_err

    # 2. Если выбран Gemini
    else:
        try:
            print(f"[AI Gateway] Вызов Gemini ({settings.GEMINI_MODEL}) в режиме '{granularity_mode}'...")
            return await call_gemini(text, system_instruction)
        except Exception as gem_err:
            if settings.DEEPSEEK_API_KEY:
                print(f"[WARNING] Сбой Gemini: {gem_err}. Попытка резервного вызова DeepSeek...")
                return await call_deepseek(text, system_instruction)
            raise gem_err

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
        try:
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
                if res.status_code == 200:
                    content = res.json()["choices"][0]["message"]["content"]
                    return json.loads(content)
                print(f"[AI Gateway] DeepSeek mnemonic error: {res.text}")
        except Exception as e:
            print(f"[AI Gateway] Ошибка регенерации мнемоники через DeepSeek: {e}")

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
