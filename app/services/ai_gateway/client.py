"""
DeepSeek and LLM API client, raw text parsing, telemetry recording, and failover handling.
"""
import httpx
import time
import sys
import re
from typing import Optional
from app.core.config import settings
from .prompts import DEEPSEEK_CACHED_SYSTEM_PROMPT
from .json_repair import (
    extract_json_payload_with_telemetry,
    extract_json_payload,
    unpack_minified_cards,
)
from .chunker import analyze_source_density

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



def is_failover_error(err: Exception) -> bool:
    """Определяет, относится ли ошибка провайдера к сбоям баланса, авторизации, лимитов или сети."""
    err_str = str(err).lower()
    return (
        any(s in err_str for s in ("402", "insufficient", "balance", "401", "unauthorized", "api_key не установлен", "not set", "429", "quota", "timeout", "connect"))
        or isinstance(err, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError))
    )



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
        "temperature": 0.1,
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



_raw_call_deepseek = call_deepseek

def _get_call_deepseek():
    if call_deepseek is not _raw_call_deepseek:
        return call_deepseek
    pkg_name = __name__.rsplit(".", 1)[0]
    pkg = sys.modules.get(pkg_name)
    if pkg is not None and hasattr(pkg, "call_deepseek") and pkg.call_deepseek is not _raw_call_deepseek:
        return pkg.call_deepseek
    gw = sys.modules.get("app.services.ai_gateway")
    if gw is not None and hasattr(gw, "call_deepseek") and gw.call_deepseek is not _raw_call_deepseek:
        return gw.call_deepseek
    return call_deepseek

async def call_gemini(
    user_prompt: str,
    system_instruction: str = "",
    fallback_subject: str = "generic",
    **kwargs
) -> tuple[dict, dict]:
    """Stub/Adapter for Google Gemini API for multi-provider fallback."""
    raise NotImplementedError("Gemini provider is not configured. Use call_deepseek.")

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
    # Анализируем плотность переданного блока текста
    density_meta = analyze_source_density(text)
    is_slides = density_meta["is_presentation"] or (text.count(": Слайд ") >= 1)
    is_notes = density_meta["is_dense_notes"]

    if is_slides:
        slide_matches = re.findall(r'(?:Слайд|Slide)\s+(\d+)', text)
        slide_in_chunk = max(text.count(": Слайд "), len(slide_matches))
        min_c = max(4, int(slide_in_chunk * 0.8)) if slide_in_chunk >= 2 else 5
        max_c = min(12, max(min_c + 2, int(slide_in_chunk * 1.2))) if slide_in_chunk >= 2 else 8
        user_directives.append(
            f"CARD VOLUME: SLIDE CLUSTER EXTRACTION ({slide_in_chunk} slides in this section). "
            f"Extract strictly {min_c} to {max_c} high-yield atomic cards (~0.8 to 1.0 cards per substantive slide). "
            f"Capture every key distinction, formula, rule, classification, or procedural step presented on these slides. "
            f"Do NOT artificially compress the section to 1-2 cards. "
            f"In 's', ALWAYS include the slide number and scope (format: '[Domain / Subject Code] | Слайд N')."
        )
        user_directives.append(
            "SLIDE BULLET RECONSTRUCTION DIRECTIVE: Slide text is frequently condensed into telegraphic bullet points, tables, and short fragments. "
            "Reconstruct full, grammatically complete, self-contained questions and answers from the slide context. "
            "Never leave dangling bullet fragments or telegraphic ellipses."
        )
    elif is_notes:
        is_micro_snippet = len(text) < 1200
        if volume in ("low", "low_5"):
            note_cards_directive = "Extract strictly 4 to 6 high-yield atomic cards (focus exclusively on the highest-priority rules and core terms)."
        elif volume in ("high", "high_20"):
            note_cards_directive = "Extract 14 to 18 high-yield atomic cards (deep extraction covering all classifications, criteria, and operational rules)."
        elif volume == "max":
            note_cards_directive = "Extract 18 to 24 high-yield atomic cards (exhaustive extraction of every verifiable rule, distinction, scheme branch, and formula)."
        elif is_micro_snippet:
            note_cards_directive = "Extract 6 to 9 high-yield atomic cards from this section (approximately 1 card per key definition, distinction, or rule)."
        else:
            note_cards_directive = "Extract 10 to 14 high-yield atomic cards (proportionate to the concentrated conceptual density of this section)."

        user_directives.append(
            f"CARD VOLUME: DENSE LECTURE NOTES / CHEATSHEET EXTRACTION. "
            f"The input represents concentrated student lecture notes or a cheatsheet with high conceptual density and zero narrative filler. "
            f"{note_cards_directive} "
            f"Do NOT artificially compress this dense section to 1-2 cards. "
            f"In 's', preserve the section header, ticket number, or topic name from the notes."
        )
        user_directives.append(
            "SCHEME & TAXONOMY ATOMIZATION DIRECTIVE: Dense student notes and cheatsheets frequently present information via ASCII schemes, indented classification trees, comparative tables, and flowcharts. "
            "Under the Minimum Information Principle, NEVER summarize a multi-branch diagram into a single card with a list of items! "
            "Instead, DECOMPOSE THE SCHEME INTO ATOMIC DECISION FORKS: create dedicated cards for (1) the core criterion distinguishing each branch, (2) contrast pairs between adjacent branches, (3) situational cases applying a specific branch, and (4) boundary exceptions. "
            "Ensure 100% conceptual coverage of every branch with answers ('d') strictly 1-12 words."
        )
        user_directives.append(
            "NOTES & ABBREVIATION EXPANSION DIRECTIVE: Student notes and cheatsheets frequently use domain abbreviations "
            "(e.g. 'ст.', 'ч.', 'РФ', 'ГК', 'УПК', 'vs', 'т.е.', 'юр. лицо', 'дисп. норма', 'AO', 'OOP', 'PR'). "
            "Expand standard abbreviations into precise, professional terminology in 't' and 'd' without losing atomic conciseness (1-12 words in 'd')."
        )
    elif volume in ("auto", "balanced"):
        user_directives.append(
            "CARD VOLUME: HIGH-YIELD BALANCED EXTRACTION. "
            "Extract 10 to 14 master conceptual cards from this section (proportionate to its substantive weight, targeting ~140–180 cards for an entire multi-chapter course). "
            "Do NOT exceed 15 cards. "
            "Ensure diverse, natural phrasing across 4 universal cognitive archetypes: "
            "1) Situational Cases / Problem Vignettes (concrete factual conflict/scenario -> domain qualification, protocol, or solution), "
            "2) Contrast Pairs (distinguishing confusing concepts via gold standard criteria), "
            "3) Doctrinal Principles & Causal Mechanisms (substantive tenets, arguments of thinkers, and operational mechanisms), "
            "4) Normative Conditions & Exceptions (exact threshold, qualification, or consequence, 'если-то'). "
            "Strictly avoid robotic boilerplate question openers (do NOT repeat 'Какое понятие обозначает...' or 'Чем принципиально отличается...'). "
            "Absolute prohibition of internal administrative minutiae (quorums, routine paperwork deadlines, office intervals), naive dictionary definitions ('Что такое X'), and binary 'Да/Нет'. "
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
    user_directives.append(
        "ANTI-TAUTOLOGY & ZERO-ECHO DIRECTIVE: Under NO circumstances generate tautological pseudo-questions where the answer 'd' merely echoes or repeats words from the question 't' "
        "(e.g. NEVER ask 'Что в системе социального регулирования определяет характер взаимодействия? -> Их взаимодействие...' or 'Какой уровень правосознания выступает целью? -> Уровень правосознания...'). "
        "The answer must state the decisive substantive rule, criterion, classification, or domain qualification."
    )
    user_directives.append(
        "HARD ATOMICITY & RETRIEVAL LATENCY CONSTRAINT (NON-NEGOTIABLE ACROSS ALL DISCIPLINES):\n"
        "• Field length limits (strictly count words before outputting):\n"
        "  - 'd' (Back): strictly 1 to 12 words (max 1 short, punchy sentence) for law and generic subjects; strictly up to 15 words for code and medicine; strictly 1 to 5 words for foreign language translations.\n"
        "  - 't' (Front): strictly up to 30 words. If the question requires >30 words to avoid giveaways, rephrase it as a direct situational vignette rather than an essay.\n"
        "  - 'e' (Example): strictly 1 vivid sentence, maximum 15 words. Zero multi-sentence essays.\n"
        "• ABSOLUTE BAN ON COMPOUND ANSWERS: Under NO circumstances join two distinct rules or contrast sides in 'd' using conjunctions ('а ... в то время как ...', 'however ... whereas ...', 'но при этом ...'). One card tests ONE rule.\n"
        "• ABSOLUTE BAN ON RECITING MULTI-ITEM LISTS: If the source contains 3 or more elements, duties, categories, or requirements, NEVER ask to list or enumerate them. Formulate a card testing ONLY the single decisive hallmark, or split across separate atomic cards.\n"
        "• RETRIEVAL LATENCY SELF-CHECK: Every card must be mentally retrievable and verifiable in 1.5–3.5 seconds. If recalling 'd' requires pausing to remember a 3-part enumeration or paragraph, it is a latency-killer — narrow or split the question immediately."
    )
    user_directives.append(
        "OPERATIVE VALUE FILTER (Practical Utility Gate):\n"
        "• Apply the universal practitioner utility test before generating each card:\n"
        "  Will a student or practitioner (lawyer, engineer, clinician, linguist) be able to make a concrete decision, avoid an error, or solve a problem by knowing this exact fact?\n"
        "• DISCARD empty academic scholasticism and abstract philosophical filler that carries zero operative or exam utility "
        "(e.g. 'объективно-субъективный характер компетенции', 'нормы как регулятор сами по себе', 'теоретико-методологическая сущность института'). "
        "Extract ONLY concrete criteria, mechanisms, thresholds, boundaries, and actionable rules."
    )
    user_directives.append(
        "ZERO-DUPLICATE SELF-SCANNING DIRECTIVE:\n"
        "• Before finalizing the card array, perform an active deduplication scan across all generated cards in 'c':\n"
        "  - Verify that no two cards test the same underlying statutory article, formula, or distinction from trivially varied angles.\n"
        "  - Absolutely never generate duplicate cards where 't' or 'd' are functionally identical or paraphrase each other.\n"
        "  - If two cards target the same concept, keep only the sharper, more situational one and discard the other."
    )

    # Domain-gated factual guardrails (activated only for legal and judicial subjects)
    law_domain_markers = ("law", "право", "судо", "юриспруд", "процесс", "кодекс", "норма", "арбитраж", "криминалист", "legal", "court")
    combined_domain_context = f"{clean_sub} {text[:400]}".lower()
    if any(marker in combined_domain_context for marker in law_domain_markers):
        user_directives.append(
            "CONSTITUTIONAL ACCURACY CHECKPOINT (Law Discipline Guard):\n"
            "Foundational constitutional and procedural principles are IMMUTABLE. Never generate cards with factual inversions:\n"
            "1. Состязательность (ст. 123 Конституции РФ): Суд НЕ возбуждает уголовные дела по собственной инициативе (это функция стороны обвинения / следствия / прокурора).\n"
            "2. Презумпция невиновности (ст. 49 Конституции РФ) — фундаментальный конституционный принцип судопроизводства.\n"
            "3. Правосудие осуществляется ТОЛЬКО судом (ст. 118 Конституции РФ).\n"
            "4. Независимость судей (ст. 120 Конституции РФ): Никакие органы или должностные лица не вправе давать судьям указания по существу рассматриваемых дел.\n"
            "If the source text contains an erroneous or obsolete doctrinal claim contradicting these, do NOT replicate the factual error in flashcards."
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
            "PARETO PRIORITY: Select high-yield conceptual nodes across the material, skipping clerical minutiae, quorums, paperwork intervals, or routine administrative procedures."
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
        res, meta = await _get_call_deepseek()(
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



__all__ = [
    "record_ai_telemetry",
    "is_failover_error",
    "call_deepseek",
    "call_gemini",
    "parse_raw_text",
    "regenerate_card_mnemonic",
]
