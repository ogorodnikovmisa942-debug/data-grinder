"""
Извлечение и починка JSON из ответов LLM (markdown-обёртки, висячие запятые, обрыв на лимите).
"""
import json
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


__all__ = [
    "extract_json_payload_with_telemetry",
    "extract_json_payload",
]
