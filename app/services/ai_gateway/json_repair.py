"""
Извлечение и починка JSON из ответов LLM (markdown-обёртки, висячие запятые, обрыв на лимите).
"""
import json
import re


def _escape_inner_quotes(text: str) -> str:
    """Модель копирует цитаты из книги и забывает экранировать кавычки внутри строк («"что?", то»). Кавычка — конец строки, только если
    дальше идёт «, "ключ»», «:», «}» или «]»; остальные внутри строки экранируем."""
    out: list[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
        elif ch == "\\" and i + 1 < n:
            out.append(ch + text[i + 1])
            i += 1
        elif ch == '"':
            tail = text[i + 1:i + 40].lstrip()
            if tail[:1] in (":", "}", "]", "") or re.match(r',\s*(["\[{\d-]|true|false|null)', tail):
                out.append(ch)
                in_str = False
            else:
                out.append('\\"')
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_MISSING_KEY_QUOTE = re.compile(r'([{,]\s*)([A-Za-z_]\w*)"(\s*:)')


def _fix_missing_key_quote(text: str) -> str:
    """Опечатка модели: у ключа нет открывающей кавычки («,emo":"talk» вместо «,"emo":"talk»; замер 2026-10-07 в ответе LESSON)."""
    return _MISSING_KEY_QUOTE.sub(r'\1"\2"\3', text)


def _fix_brackets(text: str) -> str:
    """Скобки в ответе расставлены с ошибкой (замер 2026-10-07: массив ключевых мыслей закрыт «}» вместо «]»). Идём по тексту вне строк
    со стеком ожидаемых закрывающих скобок: недостающие закрывающие вставляем, лишние выбрасываем, в конце закрываем всё открытое."""
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    i, n = 0, len(text)
    pair = {"{": "}", "[": "]"}
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch in pair:
            stack.append(pair[ch])
            out.append(ch)
        elif ch in "}]":
            if ch in stack:
                while stack and stack[-1] != ch:
                    out.append(stack.pop())                # недостающая закрывающая скобка вложенного массива/объекта
                stack.pop()
                out.append(ch)
            # закрывающая скобка, которой ничего не соответствует, — лишняя: пропускаем
        else:
            out.append(ch)
        i += 1
    return "".join(out) + "".join(reversed(stack))


def _loads(text: str):
    """strict=False: допускает переносы строк и табуляции внутри строк (модель копирует цитату вместе с переносом строки из PDF)."""
    return json.loads(text, strict=False)

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
        data = _loads(clean)
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
                data = _loads(slice_candidate)
                return data, False, False
            except json.JSONDecodeError:
                # Попытка исправить trailing commas
                fixed = re.sub(r",\s*([\]}])", r"\1", slice_candidate)
                try:
                    data = _loads(fixed)
                    return data, True, True
                except json.JSONDecodeError:
                    pass
                # Неэкранированные кавычки внутри строк (цитаты из книги) и неверно расставленные скобки
                for fixer in (_escape_inner_quotes, _fix_brackets, _fix_missing_key_quote,
                              lambda t: _fix_brackets(_escape_inner_quotes(_fix_missing_key_quote(t)))):
                    try:
                        data = _loads(re.sub(r",\s*([\]}])", r"\1", fixer(slice_candidate)))
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
