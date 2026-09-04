import json
import asyncio
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from app.core.config import settings

class MnemonicSchema(BaseModel):
    keyword: str = Field(description="Ключевое слово (ассоциация) на русском языке для запоминания термина/слова")
    verbal_cue: str = Field(description="Сюжетная подсказка на русском языке, связывающая ключевое слово и перевод/значение")

class CardSchema(BaseModel):
    text: str = Field(description="Текст лицевой стороны (слово, термин, концепт или название функции)")
    secondary_text: str = Field(description="Транскрипция, пиньинь, номер статьи или контекст (при отсутствии указать пустую строку)")
    translation: str = Field(description="Точный перевод или подробное определение на русском языке")
    example: str = Field(description="Конкретный пример, кейс или ситуация применения термина (при отсутствии указать пустую строку)")
    initial_difficulty_tier: str = Field(description="Сложность: easy, medium или hard")
    mnemonic: MnemonicSchema

class ParsedDataSchema(BaseModel):
    subject_domain: str = Field(description="Домен дисциплины: language, law, code или generic")
    subject_slug: str = Field(description="Машиночитаемый код предмета в lowercase snake_case")
    phrase_title: str = Field(description="Название темы или родительского блока карточек")
    cards: list[CardSchema]

# Прогрессивный системный промпт для извлечения абстрактных матриц знаний
UNIVERSAL_GRINDER_PROMPT = """
ROLE/CHARACTER:
You are an expert computational linguist, data architect, and cognitive learning psychologist inside the "Data Grinder" engine.

TASK/REQUEST:
Analyze the user's raw input text and completely break it down into a structured atomic dataset for space repetition (FSRS).
1. Detect the main topic or discipline and define the 'subject_domain' [language, law, code, generic].
2. Create a clean machine-readable 'subject_slug' (e.g., law_civil_rb, law_rb, python_pro, geometry, chinese_hsk3). 
   CRITICAL JURISDICTION RULE: Be extremely consistent with jurisdictions. If the text context belongs to the Republic of Belarus, strictly use the '_rb' suffix (e.g., 'law_civil_rb', 'law_rb'). If it belongs to the Russian Federation, use '_rf'. 
   DEFAULT ACTION: If the text contains post-Soviet legal terminology, articles, or codes without explicit mention of the country, STRICTLY DEFAULT to the Republic of Belarus context and use the '_rb' suffix. Never mix or invent arbitrary naming styles across uploads.
3. Extract or generate a contextual parent topic name ('phrase_title') that groups these cards.
4. Deconstruct the text into atomic, high-impact flashcards according to the domain rule:

   * DOMAIN "language":
     - text: Word/phrase in foreign language (e.g., "中国").
     - secondary_text: Pinyin or pronunciation guide (e.g., "Zhōngguó").
       PINYIN RULE: Keep as Latin characters with diacritic tone marks (e.g., "nǐ hǎo"). If the source specifies tones as numbers (e.g., "ni3 hao3"), strictly convert them into proper diacritic marks (e.g., "nǐ hǎo").
     - translation: Precise Russian definition.
       TRANSLATION RULE: Translate to precise, natural Russian. Avoid literal translation from English; adapt to real-world word usage. Translate any Chinese example sentences present in the input to Russian and append them to the translation.
     - example: Example sentence using the word in context (translated to Russian).

   * DOMAIN "law":
     - text: Legal term, doctrine, or core principle (e.g., "Форс-мажор" or definition name).
     - secondary_text: Reference to article, code, or clause (e.g., "ГК РБ Статья 401").
     - translation: Deep, complete, definitive legal definition in Russian.
     - example: Real-world legal case, situation, or article reference where the term applies.

   * DOMAIN "code":
     - text: Function name, method, design pattern, or algorithm (e.g., "asyncio.gather()").
     - secondary_text: Function signature, arguments, or execution context (e.g., "asyncio.gather(*aws)").
     - translation: Clear technical breakdown of its logic or code snippet implementation.
     - example: Minimal working code snippet demonstrating the concept.

   * DOMAIN "generic" (geometry, history, medicine):
     - text: Formula name, question, or key concept (e.g., "Площадь круга").
     - secondary_text: Scientific context, section, or core parameters (e.g., "Геометрия, S = ...").
     - translation: Core rule breakdown, proof, or complete answer.
     - example: Practical application or real-world scenario.

5. For EACH card, generate an unforgettable, high-impact mnemonic in Russian (keyword and verbal cue) using acoustic or visual associations.
   MNEMONIC RULE: 
   - All mnemonic components MUST be written strictly in Russian.
   - If the raw text or source material contains mnemonics/associations in English (e.g., wordplays), completely rewrite them from scratch in Russian.
   - The mnemonic must link the phonetic sound of the word (pinyin) or the radical logic of the character to the Russian translation.
   - Keep it short (1-2 sentences), vivid, and highly memorable.
6. Evaluate cognitive stiffness and assign an 'initial_difficulty_tier' strictly choosing from: ["easy", "medium", "hard"].

CONTEXT/ADJUSTMENTS:
- The output MUST strictly be a valid JSON object matching the exact schema below.
- Do NOT include any markdown formatting, markdown code blocks (like ```json), or conversational filler. Return raw JSON text only.
- CRITICAL JSON RULE: NEVER use unescaped double quotes (") inside any JSON string values (like 'translation', 'example', or 'verbal_cue'). If you need to write quotes inside a string, strictly use single quotes (') instead.

TYPE/JSON SCHEMA STRUCTURE:
{
  "subject_domain": "language/law/code/generic",
  "subject_slug": "lowercase_snake_case_slug",
  "phrase_title": "Clear Topic Header",
  "cards": [
    {
      "text": "Front side content",
      "secondary_text": "Hint, article, pinyin, or signature (can be empty string)",
      "translation": "Back side definition / complete explanation",
      "example": "Example sentence / code block / legal scenario / practical use",
      "initial_difficulty_tier": "easy/medium/hard",
      "mnemonic": {
        "keyword": "Ключевое слово",
        "verbal_cue": "Сюжетная подсказка связывающая ключ и определение"
      }
    }
  ]
}
"""

async def generate_content_with_retry(client, contents: str, config) -> str:
    # Каскадный список моделей для отказоустойчивости (Gemini 3.8 Flash -> 3.7 Flash -> 3.5 Flash)
    primary_model = settings.GEMINI_MODEL
    fallback_models = [primary_model, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"]
    models_to_try = list(dict.fromkeys(fallback_models))
    
    last_exception = None
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                print(f"[Gemini Parser] Generation attempt {attempt + 1} with model: {model_name}")
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config
                )
                return response.text
            except Exception as e:
                last_exception = e
                err_msg = str(e)
                print(f"[WARNING] Model {model_name} failed (attempt {attempt + 1}): {err_msg[:120]}")
                # Если 404 (модель устарела/удалена) или 429/исчерпан лимит - переходим к следующей модели каскада
                if "404" in err_msg or "NOT_FOUND" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    break
                if attempt == 0 and ("503" in err_msg or "UNAVAILABLE" in err_msg):
                    await asyncio.sleep(1.5)
    
    if last_exception:
        raise last_exception
    raise RuntimeError("Не удалось сгенерировать контент ни одной моделью из каскада.")

async def parse_raw_text(
    text: str, 
    density: str = "medium", 
    volume: str = "medium", 
    priority: str = "balanced",
    preference: str = "acoustic"
) -> dict:
    if not text.strip():
        return {"subject_domain": "generic", "subject_slug": "generic", "phrase_title": "", "cards": []}

    density_guidelines = {
        "low": "DENSITY REQUIREMENT: Simplify card contents. Keep 'translation' or explanation brief, under 2 sentences, avoiding nested details.",
        "medium": "DENSITY REQUIREMENT: Provide standard depth for explanations. Balanced and complete.",
        "high": "DENSITY REQUIREMENT: Provide deep, highly detailed explanations. Include granular details, corner cases, and thorough explanations in the 'translation'."
    }
    volume_guidelines = {
        "low": "VOLUME REQUIREMENT: Limit the output to a maximum of 5 cards.",
        "medium": "VOLUME REQUIREMENT: Limit the output to a maximum of 15 cards.",
        "high": "VOLUME REQUIREMENT: Limit the output to a maximum of 30 cards.",
        "max": "VOLUME REQUIREMENT: Extract and parse as many cards as possible from the provided text. Do not omit any relevant terms, concepts, or dictionary items. Translate and create a card for every single item in the text."
    }
    priority_guidelines = {
        "conceptual": "PRIORITY REQUIREMENT: Focus primarily on theoretical concepts, definitions, rules, and structures.",
        "practical": "PRIORITY REQUIREMENT: Focus primarily on practical applications, syntax, code examples, and actual usage scenarios.",
        "balanced": "PRIORITY REQUIREMENT: Balance theoretical concepts and practical application evenly."
    }

    custom_weights_prompt = f"""
    
    CRITICAL STRUCTURE & INTENSITY CONFIGURATION:
    1. {density_guidelines.get(density, density_guidelines['medium'])}
    2. {volume_guidelines.get(volume, volume_guidelines['medium'])}
    3. {priority_guidelines.get(priority, priority_guidelines['balanced'])}
    
    MNEMONIC PREFERENCE REQUIREMENT:
    Generate mnemonics focusing heavily on {'visual structural associations (e.g., breaking down characters, shapes, code blocks)' if preference == 'visual' else 'acoustic phonetic associations (e.g., sound-alike words, narrative hooks)'}.
    """
    
    system_instruction = UNIVERSAL_GRINDER_PROMPT + custom_weights_prompt

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=ParsedDataSchema,
        temperature=0.15
    )

    def chunk_text(t: str, max_lines: int = 25, max_chars: int = 3000) -> list[str]:
        lines = [line.strip() for line in t.splitlines() if line.strip()]
        if not lines:
            return []
        avg_line_len = sum(len(l) for l in lines) / len(lines)
        chunks = []
        if avg_line_len < 150:
            current_chunk = []
            for line in lines:
                current_chunk.append(line)
                if len(current_chunk) >= max_lines:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
            if current_chunk:
                chunks.append("\n".join(current_chunk))
        else:
            paragraphs = t.split("\n\n")
            current_chunk = []
            current_len = 0
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if current_len + len(para) > max_chars and current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = [para]
                    current_len = len(para)
                else:
                    current_chunk.append(para)
                    current_len += len(para) + 2
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
        return chunks

    if volume == "max":
        # Уменьшаем размер чанка до 15 строк для повышения стабильности парсинга
        chunks = chunk_text(text, max_lines=15)
        if not chunks:
            return {"subject_domain": "generic", "subject_slug": "generic", "phrase_title": "", "cards": []}
        
        sem = asyncio.Semaphore(3)
        
        async def call_gemini_chunk(chunk_content: str) -> dict:
            async with sem:
                for retry in range(2):
                    try:
                        response_text = await generate_content_with_retry(client, chunk_content, config)
                        return json.loads(response_text)
                    except json.JSONDecodeError as e:
                        print(f"[ERROR] Gemini returned invalid JSON structure in chunk: {e}")
                        if retry == 1:
                            return {"error": "Invalid JSON", "cards": []}
                    except Exception as e:
                        print(f"[ERROR] Gemini API execution failed in chunk: {e}")
                        if retry == 1:
                            return {"error": str(e), "cards": []}
                        await asyncio.sleep(1)
                return {"error": "Failed after retries", "cards": []}

        # Обрабатываем пачками по 2 чанка с паузой в 1.5 секунды, чтобы гарантированно не превысить лимиты (RPM) бесплатных ключей
        results = []
        batch_size = 2
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            tasks = [call_gemini_chunk(chunk) for chunk in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            if i + batch_size < len(chunks):
                await asyncio.sleep(1.5)

        aggregated_cards = []
        subject_domain = "generic"
        subject_slug = "generic"
        phrase_title = "Новый блок знаний"
        
        for idx, res in enumerate(results):
            if "cards" in res:
                aggregated_cards.extend(res["cards"])
            if idx == 0 or subject_slug == "generic":
                if res.get("subject_slug") and res.get("subject_slug") != "generic":
                    subject_slug = res.get("subject_slug")
                if res.get("subject_domain") and res.get("subject_domain") != "generic":
                    subject_domain = res.get("subject_domain")
                if res.get("phrase_title"):
                    phrase_title = res.get("phrase_title")

        return {
            "subject_domain": subject_domain,
            "subject_slug": subject_slug,
            "phrase_title": phrase_title,
            "cards": aggregated_cards
        }
    else:
        try:
            response_text = await generate_content_with_retry(client, text, config)
            parsed_data = json.loads(response_text)
            return parsed_data
        except json.JSONDecodeError as e:
            print(f"[ERROR] Gemini returned invalid JSON structure: {e}")
            return {"error": "Invalid JSON structural feedback", "cards": []}
        except Exception as e:
            print(f"[ERROR] Gemini API execution failed: {e}")
            return {"error": str(e), "cards": []}

async def regenerate_card_mnemonic(text: str, translation: str, subject: str, preference: str = "visual") -> dict:
    pref_instruction = "Use visual and structural associations (e.g., breaking down characters or words)." if preference == "visual" else "Use acoustic, phonetic, or narrative associations."
    
    prompt = f"""
ROLE: You are an expert computational linguist and cognitive learning psychologist.
TASK: Generate a single, highly memorable mnemonic in Russian for the following card.
Card Text: {text}
Translation: {translation}
Subject Context: {subject}
PREFERENCE: {pref_instruction}

RULES:
- Output MUST be a valid JSON object matching the schema.
- Do not use markdown blocks.
- ONLY output the JSON object.
"""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        system_instruction="You are a strict JSON API.",
        response_mime_type="application/json",
        response_schema=MnemonicSchema,
        temperature=0.4
    )
    
    try:
        response_text = await generate_content_with_retry(client, prompt, config)
        return json.loads(response_text)
    except Exception as e:
        print(f"[ERROR] Regenerate mnemonic failed: {e}")
        return {"error": str(e)}