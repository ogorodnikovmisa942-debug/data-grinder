"""
Document structure analysis, source density classification, adaptive chunking, and curriculum skeleton extraction.
"""
import re
import time
import sys
from app.core.config import settings
from .prompts import CURRICULUM_SKELETON_SYSTEM_PROMPT

def _get_call_deepseek():
    pkg_name = __name__.rsplit(".", 1)[0]
    client_mod = sys.modules.get(f"{pkg_name}.client") or sys.modules.get("app.services.ai_gateway.client")
    raw_fn = getattr(client_mod, "_raw_call_deepseek", None)
    if client_mod is not None and hasattr(client_mod, "call_deepseek"):
        if raw_fn is not None and client_mod.call_deepseek is not raw_fn:
            return client_mod.call_deepseek
    pkg = sys.modules.get(pkg_name)
    if pkg is not None and hasattr(pkg, "call_deepseek"):
        if raw_fn is not None and pkg.call_deepseek is not raw_fn:
            return pkg.call_deepseek
    gw = sys.modules.get("app.services.ai_gateway")
    if gw is not None and hasattr(gw, "call_deepseek"):
        if raw_fn is not None and gw.call_deepseek is not raw_fn:
            return gw.call_deepseek
    if client_mod is not None and hasattr(client_mod, "call_deepseek"):
        return client_mod.call_deepseek
    from .client import call_deepseek
    return call_deepseek

def _get_record_ai_telemetry():
    pkg_name = __name__.rsplit(".", 1)[0]
    client_mod = sys.modules.get(f"{pkg_name}.client") or sys.modules.get("app.services.ai_gateway.client")
    if client_mod is not None and hasattr(client_mod, "record_ai_telemetry"):
        return client_mod.record_ai_telemetry
    pkg = sys.modules.get(pkg_name) or sys.modules.get("app.services.ai_gateway")
    if pkg is not None and hasattr(pkg, "record_ai_telemetry"):
        return pkg.record_ai_telemetry
    from .client import record_ai_telemetry
    return record_ai_telemetry

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
    if len(text) > max_sample_chars:
        # Интеллектуальное извлечение сквозной структуры всей книги/курса
        toc_pattern = r'(?:^|\n)\s*(?:ОГЛАВЛЕНИЕ|СОДЕРЖАНИЕ|TABLE OF CONTENTS|ПЛАН КУРСА)[:\s]+([\s\S]{20,25000})'
        toc_match = re.search(toc_pattern, text, re.IGNORECASE)
        all_headers = re.findall(
            r'(?:^|\n)\s*((?:Глава|ГЛАВА|Раздел|РАЗДЕЛ|Тема|ТЕМА|Chapter|CHAPTER|§|Статья|СТАТЬЯ)\s+\d+[\.\s][^\n]{3,120})',
            text
        )
        if toc_match:
            sample_text = f"[ОГЛАВЛЕНИЕ КНИГИ/КУРСА]:\n{toc_match.group(0).strip()[:35000]}\n\n[БАЗОВЫЕ МАТЕРИАЛЫ КУРСА]:\n{text[:30000]}"
        elif all_headers and len(all_headers) >= 3:
            outline_text = "\n".join(f"- {h.strip()}" for h in all_headers[:60])
            sample_text = f"[СПИСОК ГЛАВ И РАЗДЕЛОВ ВСЕГО ДОКУМЕНТА]:\n{outline_text}\n\n[ОСНОВНЫЕ МАТЕРИАЛЫ КУРСА]:\n{text[:35000]}"
        else:
            sample_text = text[:max_sample_chars]
    else:
        sample_text = text

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
        raw_res, meta = await _get_call_deepseek()(
            user_prompt,
            system_instruction=CURRICULUM_SKELETON_SYSTEM_PROMPT,
            fallback_subject=clean_sub,
            force_chat_model=force_chat_model
        )

        duration_ms = int((time.time() - start_ts) * 1000)
        await _get_record_ai_telemetry()(
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



def analyze_source_density(text: str) -> dict:
    """
    Универсальный анализатор структуры, формата и информационной плотности текста.
    Классифицирует входящий материал по 4 когнитивным архетипам:
    1. 'slides' — презентации PPTX / PDF-слайды (маркеры '--- Слайд N ---', страницы с <800 знаков).
    2. 'dense_notes' — студенческие конспекты, шпаргалки, выжимки (короткие строки, списки, буллеты, тире-определения).
    3. 'textbook' — классические учебники, монографии, длинная связная проза (>45 000 знаков).
    4. 'short_article' — единичные статьи, небольшие заметки (<12 000 знаков).
    """
    clean_text = text.strip()
    total_chars = len(clean_text)
    if not clean_text:
        return {
            "archetype": "generic",
            "is_presentation": False,
            "is_dense_notes": False,
            "slide_count": 0,
            "target_chunk_chars": 25000,
            "min_cards_per_chunk": 6,
            "max_cards_per_chunk": 12
        }

    # 1. Поиск маркеров слайдов
    explicit_slide_matches = re.findall(r'(?:--- [^\n]+: Слайд \d+ ---|\b(?:Слайд|Slide)\s+\d+\b)', clean_text, re.IGNORECASE)
    page_matches = re.findall(r'--- [^\n]+: Стр\. \d+ ---', clean_text)
    explicit_slide_count = len(explicit_slide_matches)
    page_count = len(page_matches)

    # 2. Подсчет структурных элементов плотности конспектов и шпаргалок
    lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
    line_count = len(lines)
    avg_line_len = (total_chars / line_count) if line_count > 0 else 500

    # Буллеты: -, *, •, —, 1., 1.1, а)
    bullet_count = len(re.findall(r'^\s*(?:[-*•—–]|\d+[\.\)]|[а-яa-z][\.\)])\s+', clean_text, re.MULTILINE))
    bullet_ratio = (bullet_count / line_count) if line_count > 0 else 0.0

    chars_per_page = (total_chars / page_count) if page_count > 0 else 99999

    has_slide_markers = False
    effective_slides = 0
    if explicit_slide_count >= 3 or clean_text.count(": Слайд ") >= 2:
        has_slide_markers = True
        effective_slides = max(explicit_slide_count, clean_text.count(": Слайд "))
    elif page_count >= 4 and chars_per_page < 950:
        # PDF-презентация: мало текста на страницу (<950 знаков/стр)
        has_slide_markers = True
        effective_slides = page_count

    # Заголовки тем, вопросов, билетов, разделов:
    header_count = len(re.findall(r'^\s*#{1,4}\s+|\b(?:Тема|ТЕМА|Раздел|РАЗДЕЛ|Вопрос|ВОПРОС|Билет|БИЛЕТ|Лекция|ЛЕКЦИЯ|Блок|Глава|§)\s+\d+', clean_text, re.MULTILINE))

    # Плотные тире-определения и структурные пары (e.g., "Понятие — определение", "Термин: значение")
    definition_count = len(re.findall(r'(?:[А-Яа-яA-Za-z0-9\)]\s+[—–-]\s+[А-Яа-яA-Z0-9]|[А-Яа-яA-Za-z0-9\)]:\s+[А-Яа-яA-Z0-9])', clean_text))

    # Проверка на кодифицированные нормативно-правовые акты (НПА, кодексы)
    statute_matches = len(re.findall(r'\b(?:Статья|ст\.)\s+\d+[\.\s]', clean_text, re.IGNORECASE))
    is_statutory = not has_slide_markers and (statute_matches >= 15 and total_chars >= 25000)

    has_book_chapters = len(re.findall(r'\b(?:Глава|ГЛАВА|Раздел|РАЗДЕЛ|Chapter|CHAPTER)\s+\d+', clean_text)) >= 2
    has_tickets_or_notes = len(re.findall(r'\b(?:Билет|БИЛЕТ|Вопрос|ВОПРОС)\s+\d+|^\s*##\s+', clean_text, re.MULTILINE)) >= 2

    # Признаки конспекта/шпаргалки/тезисов/билетов (включая крупные сборники лекций и билетов):
    is_dense_notes = (
        not has_slide_markers
        and (
            # Компактный конспект до 45к знаков
            (total_chars < 45000 and not has_book_chapters and (
                has_tickets_or_notes
                or (avg_line_len < 160 and (bullet_ratio > 0.05 or definition_count >= 2))
                or (bullet_ratio > 0.10)
            ))
            # Или крупный сборник лекций/билетов/шпаргалок (более 45к знаков без структуры монографии/книги)
            or (total_chars >= 45000 and (
                has_tickets_or_notes
                or (bullet_ratio > 0.10 and not has_book_chapters)
                or (avg_line_len < 140 and bullet_ratio > 0.05 and not has_book_chapters)
            ))
        )
    )

    if has_slide_markers:
        effective_slides = max(effective_slides, clean_text.count(": Слайд "))
        return {
            "archetype": "slides",
            "is_presentation": True,
            "is_dense_notes": False,
            "slide_count": effective_slides,
            "target_chunk_chars": 6000,
            "slides_per_chunk": 6,
            "min_cards_per_chunk": 5,
            "max_cards_per_chunk": 8
        }
    elif total_chars >= 70000 and is_statutory:
        return {
            "archetype": "statutory_code",
            "is_presentation": False,
            "is_dense_notes": False,
            "slide_count": 0,
            "target_chunk_chars": 30000,
            "min_cards_per_chunk": 8,
            "max_cards_per_chunk": 14
        }
    elif is_dense_notes:
        return {
            "archetype": "dense_notes",
            "is_presentation": False,
            "is_dense_notes": True,
            "slide_count": 0,
            "target_chunk_chars": 6500,
            "min_cards_per_chunk": 8,
            "max_cards_per_chunk": 16
        }
    elif has_book_chapters or total_chars > 45000:
        return {
            "archetype": "textbook",
            "is_presentation": False,
            "is_dense_notes": False,
            "slide_count": 0,
            "target_chunk_chars": 40000,
            "min_cards_per_chunk": 8,
            "max_cards_per_chunk": 14
        }
    else:
        return {
            "archetype": "short_article",
            "is_presentation": False,
            "is_dense_notes": False,
            "slide_count": 0,
            "target_chunk_chars": 15000,
            "min_cards_per_chunk": 6,
            "max_cards_per_chunk": 10
        }



# --- УМНОЕ АДАПТИВНОЕ ЧАНКОВАНИЕ С УЧЕТОМ ТИПА ИСТОЧНИКА ---
def split_text_into_chunks(text: str, max_chunk_chars: int = 85000, overlap_chars: int = 2000) -> list[str]:
    """
    Интеллектуальное адаптивное разбиение материала на смысловые блоки с учетом типа источника:
    1. Для презентаций (slides) — группирует слайды в блоки по 5–7 слайдов (~4 000 – 6 000 знаков).
    2. Для конспектов и шпаргалок (dense_notes) — делит по заголовкам тем порциями по 4 500 – 6 500 знаков.
    3. Для учебников (textbook) — формирует крупные макро-главы (до 50 000 – 85 000 знаков) с перекрытием.
    """
    text = text.replace('\r\n', '\n').strip()
    if not text:
        return []

    density = analyze_source_density(text)
    archetype = density["archetype"]

    # 1. СПЕЦИАЛИЗИРОВАННЫЙ РЕЖИМ ДЛЯ ПРЕЗЕНТАЦИЙ (СЛАЙДЫ)
    if archetype == "slides":
        slide_pattern = r'(?=\n--- [^\n]+: (?:Слайд|Стр\.) \d+ ---)'
        slides = re.split(slide_pattern, text)
        slides = [s.strip() for s in slides if s.strip()]
        if len(slides) <= 1:
            slide_pattern_alt = r'(?=\n\s*(?:Слайд|Slide)\s+\d+)'
            slides = re.split(slide_pattern_alt, text)
            slides = [s.strip() for s in slides if s.strip()]

        if len(slides) > 1:
            slides_per_chunk = density.get("slides_per_chunk", 6)
            slide_chunks = []
            current_slide_group = []
            current_len = 0
            for sl in slides:
                sl_len = len(sl)
                # Если набрали 5-7 слайдов или превысили 7 500 знаков — закрываем блок
                if len(current_slide_group) >= slides_per_chunk or (current_len + sl_len > 7500 and current_slide_group):
                    slide_chunks.append("\n\n".join(current_slide_group))
                    current_slide_group = [sl]
                    current_len = sl_len
                else:
                    current_slide_group.append(sl)
                    current_len += sl_len + 2
            if current_slide_group:
                # Если в последней группе осталось 1-2 слайда и уже есть чанки, объединяем с предыдущим
                if len(current_slide_group) <= 2 and slide_chunks:
                    slide_chunks[-1] += "\n\n" + "\n\n".join(current_slide_group)
                else:
                    slide_chunks.append("\n\n".join(current_slide_group))
            return slide_chunks

    # 2. СПЕЦИАЛИЗИРОВАННЫЙ РЕЖИМ ДЛЯ КОНСПЕКТОВ И ШПАРГАЛОК (DENSE NOTES)
    if archetype == "dense_notes":
        target_max = density.get("target_chunk_chars", 6500)
        # Ищем естественные границы подтем, билетов, вопросов, разделов или заголовков Markdown
        note_split_pattern = r'(?=(?:\n\s*#{1,4}\s+|\n\s*(?:Тема|ТЕМА|Раздел|РАЗДЕЛ|Вопрос|ВОПРОС|Билет|БИЛЕТ|Лекция|ЛЕКЦИЯ|Блок|Глава|§)\s+\d+|\n\n(?=[А-ЯA-Z0-9\.\-]{3,}:?\n)))'
        sections = re.split(note_split_pattern, text)
        sections = [s.strip() for s in sections if s.strip()]
        if len(sections) <= 1:
            sections = [s.strip() for s in text.split("\n\n") if s.strip()]

        if len(sections) > 1:
            refined_sections = []
            for sec in sections:
                if len(sec) > target_max * 1.5:
                    sub_parts = [p.strip() for p in sec.split("\n\n") if p.strip()]
                    if len(sub_parts) > 1:
                        refined_sections.extend(sub_parts)
                    else:
                        refined_sections.append(sec)
                else:
                    refined_sections.append(sec)
            sections = refined_sections

            note_chunks = []
            cur_note = []
            cur_note_len = 0
            for sec in sections:
                s_len = len(sec)
                if cur_note_len + s_len > target_max and cur_note:
                    note_chunks.append("\n\n".join(cur_note))
                    cur_note = [sec]
                    cur_note_len = s_len
                else:
                    cur_note.append(sec)
                    cur_note_len += s_len + 2
            if cur_note:
                note_chunks.append("\n\n".join(cur_note))
            if len(note_chunks) > 1:
                return note_chunks

    # 3. СТАНДАРТНЫЙ РЕЖИМ ДЛЯ УЧЕБНИКОВ И ДЛИННОЙ ПРОЗЫ
    effective_max = min(max_chunk_chars, 45000)
    if len(text) <= effective_max:
        return [text]

    split_pattern = r'(?=(?:\n--- [^\n]+: (?:Стр\.|Слайд) \d+ ---|\n=== [^\n]+ ===|\n\s*(?:Глава|ГЛАВА|Раздел|РАЗДЕЛ|Chapter|CHAPTER|Тема|ТЕМА|§|Статья|СТАТЬЯ)\s+\d+))'
    sections = re.split(split_pattern, text)
    sections = [s.strip() for s in sections if s.strip()]

    if len(sections) <= 1:
        sections = re.split(r'(?=(?:\n\n(?=[#A-ZА-Я0-9])|\n#{1,4} ))', text)
        sections = [s.strip() for s in sections if s.strip()]

    if len(sections) <= 1:
        sections = text.split("\n\n")
        sections = [s.strip() for s in sections if s.strip()]

    if len(sections) <= 1:
        sections = text.split("\n")
        sections = [s.strip() for s in sections if s.strip()]

    normalized_sections = []
    for sec in sections:
        sec_len = len(sec)
        if sec_len <= effective_max:
            normalized_sections.append(sec)
        else:
            start = 0
            while start < sec_len:
                end = min(start + effective_max, sec_len)
                if end < sec_len:
                    last_period = sec.rfind(". ", start, end)
                    if last_period != -1 and last_period > start + (effective_max // 2):
                        end = last_period + 1
                    else:
                        last_newline = sec.rfind("\n", start, end)
                        if last_newline != -1 and last_newline > start + (effective_max // 2):
                            end = last_newline
                piece = sec[start:end].strip()
                if piece:
                    normalized_sections.append(piece)
                start = end

    raw_chunks = []
    current_chunk = []
    current_len = 0

    for sec in normalized_sections:
        sec_len = len(sec)
        if current_len + sec_len + 2 > effective_max and current_chunk:
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



__all__ = [
    "analyze_source_density",
    "split_text_into_chunks",
    "extract_curriculum_skeleton",
]
