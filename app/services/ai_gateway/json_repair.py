"""
LLM JSON extraction, truncation auto-repair, and minified cards deserialization routines.
"""
import json
import re
from typing import Any
from .blacklist import is_blacklisted_card

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



__all__ = [
    "extract_json_payload_with_telemetry",
    "extract_json_payload",
    "unpack_minified_cards",
]
