"""
Режим «словарь»: материал из независимых статей «Термин — определение». Без вызовов ИИ, расход нулевой.

Зачем отдельный режим. Конвейер учебника (карта → карточки → уроки) сравнивает новый материал с курсом кусками по ~5 тыс. знаков.
В словаре кусок — это 25–30 несвязанных статей: одной совпавшей карточки хватало, чтобы весь кусок считался «уже изученным»
(опыт на 739 статьях: 92% окон признаны покрытыми при реальных 16% повторов). Здесь каждая статья обрабатывается сама по себе:

  1. статьи разбираются программой (тире, двоеточие или «ТЕРМИН. определение», переносы строк, колонтитулы);
  2. статья, чей термин курс уже спрашивает (ответ карточки — этот термин), пропускается;
  3. остальные ранжируются: сначала термины, которые встречаются в карточках курса, потом статьи, подходящие к темам курса, потом прочие;
     если их больше, чем позволяет размер колоды (source_profile.deck_goal), берутся лучшие, а остаток равномерно по алфавиту;
  4. статья, подходящая к теме курса, кладётся в эту тему (как «дополнение» с коротким разбором терминов); остальные собираются
     в алфавитные группы по GROUP_SIZE терминов;
  5. карточка: определение (с замаскированным термином) → «Какое понятие определяется так…?», ответ — термин,
     неверные варианты — термины соседних по смыслу статей.
Результат имеет ту же форму, что у build_learning_path, и сохраняется тем же кодом (knowledge_path.save_learning_path).
"""
import math
import random
import re
from collections import Counter, defaultdict

from . import coverage
from . import source_profile

GROUP_SIZE = 12                 # терминов в группе нового материала (одна тема графа)
GROUPS_PER_ROOT = 1             # больше одной группы — над ними общая основа «Словарь терминов»
ATTACH_MIN = 0.2                # насколько статья должна быть похожа на тему курса, чтобы лечь в неё
MIN_ENTRIES = 5                 # меньше статей — это не словарь (явный выбор пользователя тоже получит понятную ошибку)
AUTO_MIN_ENTRIES = 80           # без указания пользователя режим включается сам только для очевидного словаря
AUTO_MIN_PER_10K = 15           # статей на 10 тыс. знаков
AUTO_MIN_SHARE = 0.6            # доля текста, занятая статьями
DEF_MIN_CHARS = 25
DEF_MAX_CHARS = 320
TERM_MAX_WORDS = 8
TERM_MAX_CHARS = 80
BRIEF_CHARS = 180
SUPPLEMENT_MIN = source_profile.SUPPLEMENT_MIN_CARDS
DISTRACTORS = 3

# Слова, после которых двоеточие — не словарная статья
_NOT_TERMS = {"например", "примечание", "замечание", "пример", "вывод", "итак", "см", "внимание", "важно", "определение", "правило",
              "ответ", "вопрос", "задание", "упражнение", "литература", "источник", "источники", "комментарий", "таким образом"}

_MARKER = re.compile(r"^\s*(?:-{2,}.*-{2,}|={2,}.*={2,})\s*$")        # «--- файл: Стр. 5 ---», «=== ДОКУМЕНТ ===»
_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_SEPARATOR = re.compile(r"\s*[—–‒―]\s*|\s-\s|:\s")                      # тире (с пробелами или без), дефис с пробелами, двоеточие
_ALLCAPS = re.compile(r"^([A-ZА-ЯЁ][A-ZА-ЯЁ\- ]{2,60})[.,]\s+(\S.*)$")  # «ПРАВОСУДИЕ. Деятельность суда…»
_WORD = re.compile(r"[^\W_]+")
_SENTENCE_STARTERS = {"мы", "я", "он", "она", "они", "оно", "вы", "ты", "это", "эти", "тот", "та", "что", "как", "если", "когда", "так", "но", "и", "а",
                     "в", "на", "по", "при", "для", "из", "от", "к", "с", "у", "о", "об", "не", "ни", "то", "же", "вот", "также", "однако", "таким"}
_VERB_ENDINGS = ("ли", "ется", "ются", "ится", "ются", "ают", "яют", "ует", "уют", "ила", "ало", "али")
_SENTENCE_END = re.compile(r"[.!?…]['\"»)]*\s")


class GlossaryError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Разбор статей
# ---------------------------------------------------------------------------

def _fold(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _words(s: str) -> list[str]:
    return _WORD.findall(_fold(s))


def term_key(term: str) -> frozenset[str]:
    """Термин как множество 6-буквенных основ: «судебной власти» и «Судебная власть.» дают одно и то же."""
    return frozenset(w[:6] for w in _words(term))


def _clean_term(t: str) -> str | None:
    t = t.strip(" \t\"«»*_•·")
    t = t.rstrip(".")
    core = re.sub(r"\([^)]*\)", "", t).strip()                    # скобки («лат. appellatio») в проверках не участвуют
    if not (2 <= len(t) <= TERM_MAX_CHARS) or not core:
        return None
    first_letter = next((ch for ch in core if ch.isalpha()), "")
    if not first_letter or not first_letter.isupper():
        return None
    if len(core.split()) > TERM_MAX_WORDS or re.search(r"[.!?;]\s|,\s*$", core) or sum(ch.isalpha() for ch in core) < 2:
        return None
    words = _words(core)
    # Обрывок предложения, а не термин: начинается с местоимения или союза либо содержит глагол («мы рассмотрели», «называется»)
    if words[0] in _SENTENCE_STARTERS or any(len(w) >= 5 and w.endswith(_VERB_ENDINGS) for w in words):
        return None
    return t


def _split_entry_start(line: str) -> tuple[str, str] | None:
    """Строка начинает статью? Возвращает (термин, начало определения) или None."""
    s = line.strip()
    m = _ALLCAPS.match(s)
    if m and not any(ch.islower() for ch in m.group(1)):
        t = _clean_term(m.group(1).capitalize())
        if t:
            return t, m.group(2).strip()
    sep = _SEPARATOR.search(s[: TERM_MAX_CHARS + 6])
    if not sep:
        return None
    term, body = _clean_term(s[: sep.start()]), s[sep.end():].strip()
    if not term or not body:
        return None
    if sep.group(0).strip() == ":" and (len(term.split()) > 5 or _fold(term) in _NOT_TERMS):
        return None
    return term, body


def _shorten(body: str, limit: int = DEF_MAX_CHARS) -> str:
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(cut + " ")]
    good = [e for e in ends if e >= 100]
    if good:
        return cut[: good[-1]].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"


def parse_entries(text: str) -> list[dict]:
    """[{term, answer, definition, raw_chars}] в порядке текста. Повторы одного термина сливаются (остаётся более полное определение)."""
    text = coverage.join_wrapped((text or "").replace("\xa0", " ").replace("\u00ad", ""))     # неразрывные пробелы и мягкие переносы из PDF
    entries: list[dict] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur:
            definition = _shorten(" ".join(cur["parts"]))
            if len(definition) >= DEF_MIN_CHARS:
                answer = re.sub(r"\s*\([^)]*\)\s*$", "", cur["term"]).strip() or cur["term"]
                entries.append({"term": cur["term"], "answer": answer, "definition": definition, "raw_chars": cur["chars"]})
        cur = None

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if _MARKER.match(stripped) or _PAGE_NUMBER.match(stripped) or (len(stripped) <= 2 and stripped.isalpha()):
            continue                                   # колонтитул, номер страницы, заголовок буквы
        start = _split_entry_start(stripped)
        if start:
            flush()
            cur = {"term": re.sub(r"\s+", " ", start[0]), "parts": [start[1]], "chars": len(stripped)}
        elif cur:
            cur["parts"].append(stripped)
            cur["chars"] += len(stripped)
    flush()

    best: dict[frozenset, dict] = {}
    for e in entries:
        k = term_key(e["term"])
        if k and (k not in best or len(e["definition"]) > len(best[k]["definition"])):
            best[k] = e
    kept = {id(e) for e in best.values()}
    return [e for e in entries if id(e) in kept]


def looks_like_glossary(text: str) -> bool:
    """Очевидный словарь: много статей на единицу текста и статьи занимают большую часть. Для явного выбора пользователя не нужна."""
    if len(text or "") < 3000:
        return False
    entries = parse_entries(text)
    n = len(entries)
    if n < AUTO_MIN_ENTRIES:
        return False
    per_10k = n / (len(text) / 10_000)
    share = sum(e["raw_chars"] for e in entries) / max(1, len(text))
    return per_10k >= AUTO_MIN_PER_10K and share >= AUTO_MIN_SHARE


# ---------------------------------------------------------------------------
# Сопоставление с курсом
# ---------------------------------------------------------------------------

def _idf(docs: list[set[str]]) -> dict[str, float]:
    df = Counter(w for d in docs for w in d)
    n = max(1, len(docs))
    return {w: math.log(n / (1 + c)) + 1.0 for w, c in df.items()}


def _vec(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = Counter(tokens)
    v = {w: (1 + math.log(c)) * idf.get(w, 1.0) for w, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {w: x / norm for w, x in v.items()}


def _course_index(course: dict) -> dict:
    nodes = [n for n in (course.get("nodes") or []) if n.get("tier", 0) < 3]
    cards = course.get("cards") or {}
    docs = []
    for n in nodes:
        qa = " ".join(f"{c['q']} {c['a']}" for c in (cards.get(n["key"]) or [])[:12])
        docs.append(coverage.stems(f"{n['name']} {n['name']} {n['name']} {n.get('summary') or ''} {qa}"))
    idf = _idf([set(d) for d in docs])
    # какие термины курс уже спрашивает (ответ карточки) и в каких карточках слова встречаются
    answers: set[frozenset] = set()
    token_cards: dict[str, set[int]] = defaultdict(set)
    i = 0
    for qs in cards.values():
        for c in qs:
            ans = term_key(c["a"].rstrip("."))
            if 0 < len(ans) <= 6:
                answers.add(ans)
            for w in {w[:6] for w in _words(f"{c['q']} {c['a']}")}:
                token_cards[w].add(i)
            i += 1
    return {"nodes": nodes, "vecs": [_vec(d, idf) for d in docs], "idf": idf, "answers": answers, "token_cards": token_cards}


def _mentions(key: frozenset[str], token_cards: dict[str, set[int]]) -> int:
    sets = [token_cards.get(t) for t in key]
    if not key or any(s is None for s in sets):
        return 0
    return len(set.intersection(*sets))


def _best_node(entry: dict, index: dict) -> str | None:
    if not index["nodes"]:
        return None
    q = _vec(coverage.stems(f"{entry['term']} {entry['term']} {entry['definition']}"), index["idf"])
    best_i, best_s = -1, 0.0
    for i, v in enumerate(index["vecs"]):
        s = sum(x * v.get(w, 0.0) for w, x in q.items())
        if s > best_s:
            best_i, best_s = i, s
    return index["nodes"][best_i]["key"] if best_s >= ATTACH_MIN else None


# ---------------------------------------------------------------------------
# Карточки
# ---------------------------------------------------------------------------

def mask_term(definition: str, term: str) -> str:
    """Определение без самого термина (его формы заменены на «…»): иначе вопрос выдаёт ответ. Однокоренное слово маскируется, если
    начинается с тех же 5 букв и отличается по длине не больше чем на 2–5 знаков (окончания, «-ский», «-ция»)."""
    rules = []
    for w in _words(re.sub(r"\([^)]*\)", "", term)):
        if len(w) >= 3:
            rules.append((w[:5] if len(w) >= 5 else w, len(w) + (5 if len(w) >= 8 else 3 if len(w) >= 5 else 2)))

    def repl(m: re.Match) -> str:
        w = _fold(m.group(0))
        for stem, max_len in rules:
            if w.startswith(stem) and len(w) <= max_len:
                return "…"
        return m.group(0)
    return re.sub(r"[^\W\d_]+", repl, definition)


def _usable_question(masked: str) -> bool:
    words = _WORD.findall(masked)
    if len(masked) < 20 or len(words) < 4:
        return False
    return masked.count("…") <= max(1, len(words) * 4 // 10)


def _nearest(pool: list[dict]) -> dict[int, list[int]]:
    """Для каждой статьи — до DISTRACTORS самых близких по определению других статей (инвертированный индекс по основам)."""
    toks = [set(coverage.stems(f"{e['definition']} {e['term']}")) for e in pool]
    idf = _idf(toks)
    postings: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(toks):
        for w in t:
            postings[w].append(i)
    out: dict[int, list[int]] = {}
    for i, e in enumerate(pool):
        scores: Counter = Counter()
        for w in toks[i]:
            plist = postings[w]
            if len(plist) > 60:
                continue
            for j in plist:
                if j != i:
                    scores[j] += idf[w]
        own = term_key(e["answer"])
        picks = []
        for j, _ in scores.most_common(12):
            other = pool[j]
            if term_key(other["answer"]) & own or _fold(other["answer"]) == _fold(e["answer"]):
                continue
            picks.append(j)
            if len(picks) == DISTRACTORS:
                break
        out[i] = picks
    return out


def _clean_distractors(answer: str, options: list[str]) -> list[str] | None:
    seen = {_fold(answer)}
    out = []
    for o in options:
        f = _fold(o)
        if not f or f in seen or (len(f) >= 4 and (f in _fold(answer) or _fold(answer) in f)):
            continue
        seen.add(f)
        out.append(o)
    return out[:DISTRACTORS] if len(out) >= 2 else None


def _dot(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def make_card(entry: dict, distractors: list[str], context: str) -> dict | None:
    masked = mask_term(entry["definition"], entry["answer"]).strip(" .;:—–")
    if not _usable_question(masked):
        return None
    answer = _dot(entry["answer"])
    return {
        "text": f"Какое понятие определяется так: «{masked}»?",
        "secondary_text": context,
        "translation": answer,
        "example": "",
        "initial_difficulty_tier": "medium",
        "layer": 0,
        "answer_type": "term",
        "distractors": _clean_distractors(answer, [_dot(d) for d in distractors]),
        "evidence": entry["definition"][:240],
        "support": "grounded",
    }


def _brief(definition: str) -> str:
    return _shorten(definition, BRIEF_CHARS)


def make_lesson(title: str, entries: list[dict]) -> dict:
    """Короткий разбор терминов группы: по три статьи на экран. Пишет программа, ИИ не нужен."""
    screens = [{"say": f"Разберём термины: {title}. Их {len(entries)}.", "emo": "talk", "focus": []}]
    for i in range(0, len(entries), 3):
        screens.append({"say": "\n".join(f"{e['term']} — {_brief(e['definition'])}" for e in entries[i:i + 3]), "emo": "talk", "focus": []})
    screens.append({"say": "Теперь карточки: по определению назови термин.", "emo": "happy", "focus": []})
    return {"screens": screens, "check": []}


# ---------------------------------------------------------------------------
# Сборка результата
# ---------------------------------------------------------------------------

def spread_pick(items: list, n: int) -> list:
    """n элементов, равномерно рассыпанных по списку (иначе при нехватке места достались бы только начальные буквы)."""
    if n >= len(items):
        return list(items)
    if n <= 0:
        return []
    if n == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def select_entries(candidates: list[dict], goal: int) -> list[dict]:
    """Какие статьи брать, если их больше цели: сначала термины, которые встречаются в карточках курса, потом статьи по темам курса,
    потом прочие; внутри каждой группы — равномерно по алфавиту."""
    if len(candidates) <= goal:
        return candidates
    tiers = [[e for e in candidates if e["mentions"] > 0],
             [e for e in candidates if e["mentions"] == 0 and e["node"]],
             [e for e in candidates if e["mentions"] == 0 and not e["node"]]]
    chosen: list[dict] = []
    for tier in tiers:
        tier.sort(key=lambda e: (_fold(e["term"]), e["term"]))
        room = goal - len(chosen)
        if room <= 0:
            break
        chosen += spread_pick(tier, room)
    return chosen


def _range_name(entries: list[dict]) -> str:
    a, b = entries[0]["answer"], entries[-1]["answer"]
    return f"{a} — {b}" if len(entries) > 1 and _fold(a) != _fold(b) else a


def build_glossary_path(text: str, subject: str, course: dict | None = None, depth: str | None = None,
                        goal_override: int | None = None) -> dict:
    """Результат в форме build_learning_path (карта, пакеты узлов, слияние с курсом) без единого вызова ИИ."""
    entries = parse_entries(text)
    if len(entries) < MIN_ENTRIES:
        raise GlossaryError(f"Похоже, это не словарь: нашлось только {len(entries)} статей вида «Термин — определение». "
                            "Выберите другой тип материала или проверьте, что текст прочитался.")
    course = course or {}
    index = _course_index(course)
    existing_total = sum(len(v) for v in (course.get("cards") or {}).values())

    skipped = 0
    candidates: list[dict] = []
    for e in entries:
        key = term_key(e["answer"])
        if key in index["answers"]:
            skipped += 1                                        # курс уже спрашивает этот термин
            continue
        e = {**e, "mentions": _mentions(key, index["token_cards"]), "node": _best_node(e, index)}
        candidates.append(e)

    depth = source_profile.normalize_depth(depth)
    policy = (source_profile.deck_goal(len(candidates), depth, existing_total, 1.0) if goal_override is None
              else {"goal": goal_override, "planned": len(candidates), "mult": 1.0, "source_cap": None, "room": None, "limited_by": "explicit"})
    chosen = select_entries(candidates, max(1, policy["goal"])) if candidates else []

    # карточки: неверные варианты — термины ближайших по смыслу статей всего словаря
    pool = entries
    pos = {id(e): i for i, e in enumerate(pool)}
    near = _nearest(pool)
    base_by_term = {term_key(p["answer"]): p for p in pool}
    rng = random.Random(len(pool))
    attached: dict[str, list[dict]] = defaultdict(list)
    loose: list[dict] = []
    card_of: dict[int, dict] = {}
    for e in chosen:
        src = base_by_term.get(term_key(e["answer"]), e)
        opts = [pool[j]["answer"] for j in near.get(pos.get(id(src), -1), [])]
        for _ in range(30):                                                  # у статьи мало близких: добираем случайными, не повторяя ответ
            if len(opts) >= DISTRACTORS:
                break
            cand = rng.choice(pool)["answer"]
            if cand not in opts and term_key(cand) != term_key(e["answer"]):
                opts.append(cand)
        card = make_card(e, opts, "")
        if not card:
            continue
        card_of[id(e)] = card
        (attached[e["node"]] if e["node"] else loose).append(e)

    nodes: list[dict] = []
    packs: dict[str, dict] = {}
    merge: dict[str, str] = {}
    by_key = {n["key"]: n for n in index["nodes"]}

    def add(key, name, tier, parent=None, prereqs=(), summary=""):
        nodes.append({"key": key, "name": name, "tier": tier, "parent": parent, "prereqs": list(prereqs), "order": len(nodes) + 1,
                      "summary": summary, "src": "Словарь", "kind": "core"})

    for ek, group in sorted(attached.items(), key=lambda kv: by_key[kv[0]].get("name", "")):
        group.sort(key=lambda e: (_fold(e["term"]), e["term"]))
        ex = by_key[ek]
        nk = f"gl__{ek}"
        add(nk, ex["name"], ex.get("tier", 0), summary="Термины словаря по теме")
        merge[nk] = ek
        cards = []
        for e in group:
            c = card_of[id(e)]
            c.update({"secondary_text": f"Словарь | {ex['name']}", "theme": ex["name"], "node_key": nk})
            cards.append(c)
        lesson = make_lesson(ex["name"], group) if len(group) >= SUPPLEMENT_MIN else None
        packs[nk] = {"lesson": lesson, "cards": cards}

    loose.sort(key=lambda e: (_fold(e["term"]), e["term"]))
    groups = [loose[i:i + GROUP_SIZE] for i in range(0, len(loose), GROUP_SIZE)]
    root_key = None
    if len(groups) > GROUPS_PER_ROOT:
        root_key = "gl_root"
        add(root_key, "Словарь терминов", 0, summary=f"{len(loose)} терминов в алфавитном порядке")
        packs[root_key] = {"lesson": {"screens": [
            {"say": f"Это словарь: {len(loose)} терминов, которые ещё не встречались в курсе.", "emo": "talk", "focus": []},
            {"say": f"Они разбиты на группы по {GROUP_SIZE} в алфавитном порядке. В каждой: короткий разбор и карточки.", "emo": "talk", "focus": []},
            {"say": "Карточка показывает определение, а ты называешь термин.", "emo": "happy", "focus": []}], "check": []}, "cards": []}
    for gi, group in enumerate(groups, 1):
        name = _range_name(group)
        key = f"gl_g{gi}"
        if root_key:
            add(key, name, 1, prereqs=[root_key], summary="Группа терминов словаря")
        else:
            add(key, name, 0, summary="Термины словаря")
        cards = []
        for e in group:
            c = card_of[id(e)]
            c.update({"secondary_text": f"Словарь | {name}", "theme": name, "node_key": key})
            cards.append(c)
        packs[key] = {"lesson": make_lesson(name, group), "cards": cards}

    packs = {k: v for k, v in packs.items() if v["cards"] or k == root_key}
    nodes = [n for n in nodes if n["key"] in packs]
    for i, n in enumerate(nodes, 1):
        n["order"] = i
    final_cards = [c for p in packs.values() for c in p["cards"]]
    if not final_cards:
        raise GlossaryError("В словаре не нашлось статей, которых нет в курсе. Новых карточек нет.")

    return {
        "map": {"title": "Словарь терминов", "domain": "glossary", "source_type": "glossary", "nodes": nodes, "edges": []},
        "packs": packs,
        "intro": None,
        "merge": merge,
        "supplements": sorted(k for k in merge if packs[k]["lesson"]),
        "facts": {},
        "source_type": "glossary",
        "quotas": {},
        "gap_report": None,
        "stats": {
            "source": {"kind": "glossary", "entries": len(entries), "skipped_already_asked": skipped, "candidates": len(candidates),
                       "chosen": len(chosen), "attached_to_course_topics": sum(len(g) for g in attached.values()),
                       "in_new_groups": len(loose), "unusable_for_cards": len(chosen) - len(final_cards),
                       "with_mentions_in_course": sum(1 for e in candidates if e["mentions"] > 0)},
            "deck_policy": {**policy, "depth": depth, "existing_cards": existing_total, "new_share": 1.0},
            "density": {"cards": len(final_cards)},
        },
        "missing_nodes": [],
        "calls": [],
        "cost_usd": 0.0,
    }


__all__ = ["GlossaryError", "parse_entries", "looks_like_glossary", "build_glossary_path", "mask_term", "term_key", "select_entries"]
