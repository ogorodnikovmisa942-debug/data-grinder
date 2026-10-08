"""Экспорт карточек предмета в Anki (.apkg). Работает без ИИ; формат собирает библиотека genanki."""
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile

import genanki

MODEL_ID = 1_607_392_319          # фиксированный: повторный импорт обновляет карточки, а не плодит новый тип записей
CSS = """.card { font-family: -apple-system, Arial, sans-serif; font-size: 18px; text-align: left; color: #1c1b1b; background: #fff; }
.ctx { font-size: 12px; color: #777; margin-bottom: 10px; }
.extra { font-size: 14px; color: #555; margin-top: 12px; }"""

MODEL = genanki.Model(
    MODEL_ID, "Data Grinder",
    fields=[{"name": "Front"}, {"name": "Context"}, {"name": "Back"}, {"name": "Extra"}],
    templates=[{
        "name": "Карточка",
        "qfmt": '{{#Context}}<div class="ctx">{{Context}}</div>{{/Context}}{{Front}}',
        "afmt": '{{FrontSide}}<hr id="answer">{{Back}}{{#Extra}}<div class="extra">{{Extra}}</div>{{/Extra}}',
    }],
    css=CSS,
)


def _html(text: str | None) -> str:
    """Текст карточки в безопасный HTML: экранирование и переводы строк."""
    return html.escape((text or "").strip()).replace("\n", "<br>")


def deck_id_for(name: str) -> int:
    return int(hashlib.sha1(name.encode("utf-8")).hexdigest()[:8], 16) % (2 ** 31 - 1) + 1


def build_apkg(deck_name: str, cards: list[dict]) -> bytes:
    """cards: [{"id", "text", "secondary_text", "translation", "example", "tag"}]. Возвращает содержимое .apkg."""
    deck = genanki.Deck(deck_id_for(deck_name), deck_name)
    for c in cards:
        tags = [t for t in [(c.get("tag") or "").strip().replace(" ", "_")] if t]
        deck.add_note(genanki.Note(
            model=MODEL, guid=genanki.guid_for("data-grinder", c["id"]), tags=tags,
            fields=[_html(c.get("text")), _html(c.get("secondary_text")), _html(c.get("translation")), _html(c.get("example"))],
        ))
    fd, path = tempfile.mkstemp(suffix=".apkg")
    os.close(fd)
    try:
        genanki.Package(deck).write_to_file(path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------------------------------------------------
# Импорт колоды Anki (.apkg): готовые карточки без ИИ
# ---------------------------------------------------------------------------------------------------------------------
MAX_APKG_CARDS = 20_000
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>|</div>|</p>", re.IGNORECASE)
_SOUND = re.compile(r"\[sound:[^\]]*\]")


class ApkgError(ValueError):
    pass


# Роли полей по именам (английские и русские названия из популярных типов записей); не нашли — берём по порядку
_FRONT_NAMES = ("front", "question", "вопрос", "лицо", "термин", "word", "expression")
_BACK_NAMES = ("back", "answer", "ответ", "definition", "определение", "meaning", "translation", "перевод")
_CONTEXT_NAMES = ("context", "контекст", "hint", "подсказка")
_EXTRA_NAMES = ("extra", "example", "пример", "notes", "заметки")


def _field_index(names: list[str], wanted: tuple[str, ...], default: int | None) -> int | None:
    low = [n.strip().lower() for n in names]
    for w in wanted:
        if w in low:
            return low.index(w)
    return default if (default is not None and default < len(names)) else None


def _plain(field: str) -> str:
    """Поле заметки Anki в обычный текст: переводы строк из тегов, без разметки и ссылок на звук."""
    text = _SOUND.sub("", _BR.sub("\n", field or ""))
    return re.sub(r"\n{3,}", "\n\n", html.unescape(_TAG.sub("", text))).strip()


def read_apkg(contents: bytes) -> list[dict]:
    """Заметки из .apkg: первое поле — вопрос, второе — ответ, третье (если есть) — пример. Остальное и медиа игнорируются."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile:
        raise ApkgError("Файл повреждён или это не колода Anki.")
    names = zf.namelist()
    name = next((n for n in ("collection.anki21", "collection.anki2") if n in names), None)
    if not name:
        raise ApkgError("Колода в новом формате Anki. Экспортируйте её с галкой «Поддержка старых версий Anki» (Support older Anki versions).")
    fd, path = tempfile.mkstemp(suffix=".anki2")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(zf.read(name))
        con = sqlite3.connect(path)
        try:
            rows = con.execute("select mid, flds from notes order by id").fetchall()
            models = json.loads(con.execute("select models from col").fetchone()[0] or "{}")
        finally:
            con.close()
    except sqlite3.DatabaseError:
        raise ApkgError("Не удалось прочитать колоду Anki.")
    finally:
        os.unlink(path)
    cards: list[dict] = []
    roles: dict = {}
    for mid, flds in rows[:MAX_APKG_CARDS]:
        parts = (flds or "").split("\x1f")
        if mid not in roles:
            names = [f.get("name", "") for f in (models.get(str(mid)) or {}).get("flds", [])] or [f"f{i}" for i in range(len(parts))]
            roles[mid] = (_field_index(names, _FRONT_NAMES, 0), _field_index(names, _BACK_NAMES, 1),
                          _field_index(names, _CONTEXT_NAMES, None), _field_index(names, _EXTRA_NAMES, None))
        fi, bi, ci, ei = roles[mid]
        get = lambda i: _plain(parts[i]) if (i is not None and i < len(parts)) else ""
        front, back = get(fi), get(bi)
        if front and back and fi != bi:
            cards.append({"text": front, "secondary_text": get(ci), "translation": back, "example": get(ei),
                          "initial_difficulty_tier": "medium", "mnemonic": None})
    if not cards:
        raise ApkgError("В колоде нет заметок с вопросом и ответом.")
    return cards
