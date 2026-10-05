"""Проверка карточек по книге (без ИИ): цитаты, расхождения в числах, выдержки для уроков, дыры в охвате, повторы."""
from app.services.ai_gateway import card_quality as cq


def _para(subject: str, fact: str, n: int = 14) -> str:
    """Абзац ~ нескольких тысяч знаков про одну тему, в середине — конкретный факт."""
    filler = f"Институт {subject} рассматривается в учебнике подробно, с примерами из практики и ссылками на нормативные акты. "
    return (filler * n) + fact + " " + (filler * n) + "\n"


def _book() -> str:
    return (
        _para("судебной власти", "Срок полномочий судьи Конституционного суда составляет шесть лет.") * 1
        + _para("подсудности", "Иск подаётся в районный суд по месту жительства ответчика.") * 1
        + _para("нотариата", "Нотариус удостоверяет сделки и выдаёт свидетельства о праве на наследство.") * 1
        + _para("прокуратуры", "Генеральный прокурор назначается Президентом с согласия Совета Республики.") * 1
    ) * 3


def _card(q, a, ev="", **kw):
    return {"text": q, "translation": a, "evidence": ev, "secondary_text": "", "example": "", **kw}


def test_quote_is_found_ignoring_case_punctuation_and_line_breaks():
    text = "Срок полно-\nмочий судьи Конституционного суда составляет шесть лет, и это важно."
    loc = cq.SourceLocator(text * 2)
    a, b = loc.find("срок ПОЛНОМОЧИЙ судьи конституционного суда — составляет шесть лет")
    assert text.startswith("Срок", a) or text[a:a + 4] == "Срок"
    assert loc.find("совсем другая фраза которой нет в книге вообще") is None
    assert loc.find("шесть лет") is None                     # слишком короткая цитата ничего не доказывает


def test_card_with_exact_quote_and_answer_nearby_is_grounded():
    v = cq.CardVerifier(_book())
    good = _card("Каков срок полномочий судьи КС?", "Шесть лет.", "Срок полномочий судьи Конституционного суда составляет шесть лет")
    summary, flagged = v.verify([good])
    assert good["support"] == "grounded" and good["src_span"] and not flagged and summary["grounded"] == 1


def test_wrong_number_with_real_quote_is_flagged():
    v = cq.CardVerifier(_book())
    bad = _card("Каков срок полномочий судьи КС?", "Пять лет, 5.", "Срок полномочий судьи Конституционного суда составляет шесть лет")
    summary, flagged = v.verify([bad])
    assert bad["support"] == "flagged" and flagged == [bad] and "цитат" in bad["support_reason"]


def test_invented_quote_is_judged_by_the_text_nearby():
    v = cq.CardVerifier(_book())
    invented = _card("Кто назначает Генерального прокурора?", "Президент.", "Генеральному прокурору присваивается ранг министра по указу")
    ok = _card("Кто назначает Генерального прокурора?", "Президент.", "")                      # цитаты нет, но ответ в книге есть
    ghost = _card("Какой орган утверждает бюджет санатория?", "Попечительский совет 7 лет.", "")
    v.verify([invented, ok, ghost])
    assert ok["support"] == "lexical"
    assert ghost["support"] == "flagged"


def test_book_without_words_cannot_be_checked_but_exact_quotes_still_work():
    v = cq.CardVerifier("字" * 30000)
    c = _card("Что?", "Это.", "")
    summary, flagged = v.verify([c])
    assert c["support"] == "unchecked" and not flagged


def test_passage_and_excerpt_surround_the_quote_and_respect_the_limit():
    text = _book()
    v = cq.CardVerifier(text)
    c1 = _card("Срок?", "Шесть лет.", "Срок полномочий судьи Конституционного суда составляет шесть лет")
    c2 = _card("Куда иск?", "В районный суд.", "Иск подаётся в районный суд по месту жительства ответчика")
    v.verify([c1, c2])
    p = v.passage(c1)
    assert "шесть лет" in p and len(p) <= cq.PASSAGE_CHARS + 100
    ex = v.excerpt([c1, c2], 1600)
    assert "шесть лет" in ex and "районный суд по месту жительства" in ex and len(ex) <= 1700
    assert v.excerpt([], 1600) == ""


def test_card_without_a_located_quote_still_gets_a_passage():
    v = cq.CardVerifier(_book())
    c = _card("Что удостоверяет нотариус?", "Сделки.", "")
    v.verify([c])
    assert "нотариус" in v.passage(c).lower()


def test_uncovered_stretches_of_the_book_are_found_and_toc_like_text_is_skipped():
    text = _book() + ("Оглавление ......... 12\nГлава 3 ...... 45\n" * 400)
    v = cq.CardVerifier(text)
    cards = [_card("Срок?", "Шесть лет.", "Срок полномочий судьи Конституционного суда составляет шесть лет")]
    v.verify(cards)
    spans = v.thin_spans(cards)
    assert spans and all(s["chars"] >= cq.MIN_THIN_SPAN_CHARS for s in spans)
    assert all(text[s["start"]:s["end"]].count("....") < 50 for s in spans)      # оглавление не предлагаем
    # охват есть везде, где есть карточки: участок с карточкой не считается дырой
    covered = v.index.spans[v.window_of(cards[0]["src_span"][0])]
    assert not any(s["start"] <= covered[0] < s["end"] for s in spans)


def test_suggest_node_picks_the_node_that_looks_like_the_passage():
    text = _book()
    v = cq.CardVerifier(text)
    nodes = [{"key": "court", "name": "Подсудность", "tier": 2, "summary": "Иск подаётся в районный суд"},
             {"key": "notary", "name": "Нотариат", "tier": 2, "summary": "Нотариус удостоверяет сделки"},
             {"key": "case", "name": "Кейс", "tier": 3, "summary": "x"}]
    assert v.suggest_node(nodes, "Нотариус удостоверяет сделки и выдаёт свидетельства") == "notary"
    assert v.suggest_node([], "что угодно") is None


def test_duplicates_across_nodes_are_removed_keeping_the_first():
    by_node = {
        "a": [_card("Кто назначает Генерального прокурора?", "Президент.")],
        "b": [_card("Кто назначает Генерального прокурора?", "Президент."),                 # точный повтор
              _card("Кто назначает Генерального прокурора республики?", "Президент."),     # почти повтор
              _card("Сколько лет длится срок судьи?", "Шесть лет.")],
    }
    removed = cq.dedupe_cards(by_node, ["a", "b"])
    assert removed == 2 and len(by_node["a"]) == 1 and [c["text"] for c in by_node["b"]] == ["Сколько лет длится срок судьи?"]


def test_words_broken_by_line_wraps_are_joined_for_matching_and_for_excerpts():
    from app.services.ai_gateway import coverage
    assert coverage.stems("обще-\nственные отношения") == coverage.stems("общественные отношения")
    assert coverage.join_wrapped("социально-экономический") == "социально-экономический"     # дефис не на конце строки — настоящий
    text = _para("прокуратуры", "Нотариус удостоверяет сде-\nлки и выдаёт свиде-\nтельства о праве на на-\nследство.") * 3
    v = cq.CardVerifier(text)
    card = _card("Что удостоверяет нотариус?", "Сделки и выдаёт свидетельства.", "Нотариус удостоверяет сделки и выдаёт свидетельства о праве")
    v.verify([card])
    assert card["support"] == "grounded"                                                   # ответ найден, хотя слова разорваны
    ex = v.excerpt([card], 900)
    assert "сделки" in ex and "сде-" not in ex and "свидетельства о праве на наследство" in ex
