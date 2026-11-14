"""Текст из загруженного файла (без ИИ): PDF, DOCX, PPTX, EPUB, FB2, ODT, HTML, TXT/MD — и чистка текста перед нарезкой."""
import io
import posixpath
import re
import zipfile
from collections import Counter
from html.parser import HTMLParser

MAX_EXTRACT_BYTES = 8 * 1024 * 1024
# Форматы, которые разбирает только этот модуль (PDF/DOCX/PPTX/TXT нарезка книги читает сама, с номерами страниц и слайдов)
BOOK_FORMATS = (".epub", ".fb2", ".odt", ".html", ".htm", ".xhtml")
SUPPORTED = (".pdf", ".docx", ".pptx", ".txt", ".md") + BOOK_FORMATS
SCAN_PDF_MESSAGE = ("В PDF нет текстового слоя — похоже, это скан. Нужен PDF с текстом, "
                    "или распознайте фото страниц кнопкой «Фото / Скан (OCR)».")


class ExtractError(ValueError):
    pass


class _TextParser(HTMLParser):
    """HTML / XHTML / FB2 / ODT → текст: блочные теги дают перевод строки, служебные пропускаются."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "title", "subtitle", "section", "v",
             "stanza", "blockquote", "pre", "dt", "dd", "td", "th", "text:p", "text:h", "text:list-item", "table:table-row"}
    SKIP = {"script", "style", "head", "binary", "description", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def markup_to_text(markup: str) -> str:
    p = _TextParser()
    p.feed(markup)
    p.close()
    return "\n".join(line.strip() for line in "".join(p.parts).split("\n"))


def _decode_xml(raw: bytes) -> str:
    """FB2 часто в windows-1251: кодировку берём из объявления <?xml ... encoding="..."?>."""
    m = re.search(rb'encoding=["\']([\w-]+)', raw[:200])
    try:
        return raw.decode(m.group(1).decode() if m else "utf-8", errors="ignore")
    except LookupError:
        return raw.decode("utf-8", errors="ignore")


def _epub_text(contents: bytes) -> str:
    """Главы EPUB в порядке чтения (spine из OPF); без OPF — все xhtml по имени."""
    with zipfile.ZipFile(io.BytesIO(contents)) as z:
        names = set(z.namelist())
        order: list[str] = []
        m = re.search(r'full-path="([^"]+)"', z.read("META-INF/container.xml").decode("utf-8", "ignore")) \
            if "META-INF/container.xml" in names else None
        if m and m.group(1) in names:
            opf_path = m.group(1)
            opf = z.read(opf_path).decode("utf-8", "ignore")
            items = {i: h for i, h in re.findall(r'<item\b[^>]*?\bid="([^"]+)"[^>]*?\bhref="([^"]+)"', opf)}
            items.update({i: h for h, i in re.findall(r'<item\b[^>]*?\bhref="([^"]+)"[^>]*?\bid="([^"]+)"', opf)})
            base = posixpath.dirname(opf_path)
            for idref in re.findall(r'<itemref\b[^>]*?\bidref="([^"]+)"', opf):
                href = items.get(idref)
                if href:
                    order.append(posixpath.normpath(posixpath.join(base, href.split("#")[0])))
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        return "\n\n".join(markup_to_text(z.read(n).decode("utf-8", "ignore")) for n in order if n in names)


def _odt_text(contents: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(contents)) as z:
        return markup_to_text(z.read("content.xml").decode("utf-8", "ignore"))


def extract_book_format(name: str, contents: bytes) -> str:
    """EPUB, FB2, ODT, HTML: текст в порядке чтения. Битый файл — ExtractError с понятным сообщением."""
    try:
        if name.endswith(".epub"):
            return _epub_text(contents)
        if name.endswith(".odt"):
            return _odt_text(contents)
        if name.endswith(".fb2"):
            return markup_to_text(_decode_xml(contents))
        return markup_to_text(contents.decode("utf-8-sig", errors="ignore"))
    except (zipfile.BadZipFile, KeyError) as e:
        raise ExtractError("Файл повреждён или это не тот формат, что указан в расширении.") from e


_PAGE_NO = re.compile(r"[\s\-–—]*\d{1,4}[\s\-–—]*")
_HEADING = re.compile(r"(глава|раздел|часть|тема|лекция|параграф|chapter|section|part|§)", re.I)


def strip_page_furniture(pages: list[str]) -> list[str]:
    """Колонтитулы и номера страниц PDF: голый номер у края страницы и строка, которая открывает или закрывает
    не меньше 5% страниц (цифры не в счёт: «12 Общая теория права» = «13 Общая теория права»). Заголовки глав не трогаем."""
    def key(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip().lower())

    def trim(lines: list[str], junk) -> list[str]:
        while lines and junk(lines[0]):
            lines = lines[1:]
        while lines and junk(lines[-1]):
            lines = lines[:-1]
        return lines

    def page_no(line: str) -> bool:
        return not line.strip() or bool(_PAGE_NO.fullmatch(line))

    # Колонтитул ищем под номером страницы: «12 / Общая теория права / текст»
    split = [trim(p.strip().split("\n"), page_no) for p in pages]
    edges = Counter(k for lines in split if lines for k in {key(lines[0]), key(lines[-1])})
    min_repeat = max(4, round(0.05 * len(pages)))
    furniture = {k for k, n in edges.items() if n >= min_repeat and len(k) <= 90 and not _HEADING.match(k.lstrip("# "))}
    return ["\n".join(trim(lines, lambda line: page_no(line) or key(line) in furniture)) for lines in split]


_WRAP_HYPHEN = re.compile(r"(?<=[^\W\d_])-[ \t]*\n[ \t]*(?=[^\W\d_])")
WRAPPED_LINE_MIN = 45      # строка короче — не перенос, а пункт списка, строка словаря или стих


def clean_source_text(text: str) -> str:
    """Склейка вёрстки PDF перед нарезкой: переносы слов, строки абзаца, неразрывные и мягкие пробелы.
    Меньше токенов на чтение книги (≈5%), и цитаты карточек находятся в тексте целиком. Списки и короткие строки не склеиваются."""
    text = (text or "").replace("\r\n", "\n").replace("\xa0", " ").replace("­", "")
    text = _WRAP_HYPHEN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        prev = out[-1] if out else ""
        if (len(prev) >= WRAPPED_LINE_MIN and prev[-1] not in ".:;!?»\"" and line[:1].islower()
                and not prev.startswith(("--- ", "=== "))):            # метки страниц и документов остаются отдельными строками
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def extract_text(filename: str, contents: bytes) -> str:
    name = (filename or "").lower()
    if len(contents) > MAX_EXTRACT_BYTES:
        raise ExtractError("Файл слишком большой (максимум 8 МБ).")
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        pages = [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(contents)).pages]
        text = "\n".join(p for p in strip_page_furniture(pages) if p)
        if not text.strip() and pages:
            raise ExtractError(SCAN_PDF_MESSAGE)
    elif name.endswith(".docx"):
        import docx
        doc = docx.Document(io.BytesIO(contents))
        lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        text = "\n".join(lines)
    elif name.endswith(".pptx"):
        from pptx import Presentation
        lines = []
        for slide in Presentation(io.BytesIO(contents)).slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    lines.append(shape.text.strip())
        text = "\n".join(lines)
    elif name.endswith((".txt", ".md")):
        text = contents.decode("utf-8-sig", errors="ignore")
    elif name.endswith(BOOK_FORMATS):
        text = extract_book_format(name, contents)
    else:
        raise ExtractError("Этот формат не поддерживается. Подойдут PDF, Word (DOCX), EPUB, FB2, ODT, HTML, PowerPoint (PPTX) и текст.")
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ExtractError("В файле не нашёлся текст.")
    return text
