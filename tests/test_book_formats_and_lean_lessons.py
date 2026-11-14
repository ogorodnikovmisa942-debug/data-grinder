"""Нарезка любых учебников: книжные форматы, чистка вёрстки PDF и краткий формат ответа уроков (экономия вывода)."""
import io
import zipfile

import pytest

from app.services import text_extract as te
from app.services.ai_gateway.path_builder import normalize_lessons


def _epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<package><manifest><item id="c2" href="ch2.xhtml"/><item id="c1" href="ch1.xhtml"/>'
                                        '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/ch1.xhtml", "<html><head><title>мусор</title><style>p{}</style></head><body><h1>Глава 1</h1><p>Сила &mdash; мера.</p></body></html>")
        z.writestr("OEBPS/ch2.xhtml", "<html><body><h1>Глава 2</h1><p>Масса тела.</p></body></html>")
    return buf.getvalue()


def test_epub_chapters_follow_spine_order_and_skip_head():
    text = te.extract_text("book.epub", _epub())
    assert text.index("Глава 1") < text.index("Сила — мера.") < text.index("Глава 2")
    assert "мусор" not in text and "p{}" not in text


def test_fb2_in_windows_1251_and_without_binary():
    fb2 = ('<?xml version="1.0" encoding="windows-1251"?><FictionBook><description><title-info>автор</title-info></description>'
           '<body><section><title><p>Тема</p></title><p>Право — система норм.</p></section></body><binary>QUJD</binary></FictionBook>')
    text = te.extract_text("book.fb2", fb2.encode("cp1251"))
    assert "Право — система норм." in text and "Тема" in text
    assert "QUJD" not in text and "автор" not in text


def test_broken_epub_gives_clear_error():
    with pytest.raises(te.ExtractError, match="повреждён"):
        te.extract_text("book.epub", b"not a zip")


def test_page_furniture_removed_but_chapter_headings_kept():
    words = ["норма", "закон", "право", "суд", "иск", "сделка", "договор", "лицо"]
    pages = [f"{n}\nОбщая теория права\nСтраница о том, что такое {words[n % 8]}{'!' * n}" for n in range(1, 21)]
    for n in (5, 9, 12, 15):
        pages[n] = f"{n + 1}\nГлава {n}\nТекст главы {'?' * n}"
    out = te.strip_page_furniture(pages)
    assert out[0] == "Страница о том, что такое закон!"      # номер и колонтитул срезаны, текст цел
    assert out[5].startswith("Глава 5")                      # заголовок главы на краю страницы остался


def test_clean_source_text_joins_wraps_but_keeps_lists_and_markers():
    raw = ("--- book.pdf: Стр. 1 длинное имя файла учебника по праву ---\nпродолжение со страницы\n"
           "Государство обеспечивает исполнение правовых норм посред-\nством принуждения и убеждения граждан\n"
           "в обществе.\napple — яблоко\nbanana — банан")
    text = te.clean_source_text(raw)
    assert "посредством принуждения и убеждения граждан в обществе." in text
    assert text.startswith("--- book.pdf: Стр. 1 длинное имя файла учебника по праву ---\nпродолжение")
    assert "apple — яблоко\nbanana — банан" in text


def test_lesson_screens_as_pairs_and_focus_from_named_nodes():
    path_map = {"nodes": [{"key": "sila", "name": "Сила", "tier": 0, "parent": None, "prereqs": []},
                          {"key": "massa", "name": "Масса тела", "tier": 2, "parent": None, "prereqs": ["sila"]}],
                "edges": []}
    raw = {"nodes": [{"key": "massa", "lesson": {"screens": [["Представь тележку.", "surprised"],
                                                            ["Помнишь, что такое сила? Тут она встречает массу тела.", "talk"],
                                                            ["Запомни главное.", "happy"]], "check": []}}]}
    lesson = normalize_lessons(raw, path_map, ["massa"])["massa"]
    assert [s["emo"] for s in lesson["screens"]] == ["surprised", "talk", "happy"]
    assert lesson["screens"][0]["focus"] == []
    assert set(lesson["screens"][1]["focus"]) == {"sila", "massa"}
