#!/usr/bin/env python3
"""
Оценка колоды и уроков по учебнику по критериям из исследований (ничего не отправляет в ИИ, ничего не пишет в БД).

Использование:
    python scripts/deck_quality.py книга_или_глава.txt результат.json [--json отчёт.json]

Результат — JSON прогона build_learning_path (поля "map", "packs") или список карточек. Критерии и откуда они:

  A. Достоверность (content accuracy): доля карточек, чья цитата найдена в тексте точно; расхождения.
  B. Покрытие содержания (content validity, «таблица спецификаций»: Downing & Haladyna, Handbook of Test Development, 2006):
     - доля окон текста (≈5 тыс. знаков), на которые пришлась хотя бы одна карточка;
     - «ключевые предложения» (определения, числа и даты, перечни признаков, персоналии), покрытые карточками;
       знание делится на компоненты (Koedinger, Corbett & Perfetti 2012, KLI), покрытие считается по фактам, а не по числу карточек;
     - определяемые термины («X — это …»), числа и даты: есть ли они в карточках и в уроках.
  C. Качество карточек (Wozniak 1999, «Twenty rules of formulating knowledge»): минимум информации (короткий ответ),
     без перечислений и без «Да/Нет», без подсказки в вопросе, без повторов.
  D. Тестовые варианты (Haladyna, Downing & Rodriguez 2002, 31 правило): 3 дистрактора, однородные по длине,
     не совпадают с ответом, правдоподобны (слова встречаются в тексте), нет «всё перечисленное».
  E. Когнитивные уровни (Anderson & Krathwohl 2001, ревизия таксономии Блума): доля вопросов «помнить» (кто/что/сколько/как называется)
     и «понимать/анализировать» (почему, чем отличается, что произойдёт, при каком условии).
  F. Уроки (Mayer 2009, принципы сегментирования и когерентности; Sweller, теория когнитивной нагрузки; Biggs, согласованность):
     слов на экран, экранов на карточку, ответы карточек названы в уроке, в уроке нет чисел и имён, которых нет в тексте.

Пороги ниже — рабочие ориентиры, а не «научные нормы»: для покрытия единой нормы в литературе нет.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai_gateway import card_quality, coverage  # noqa: E402

DEF_RE = re.compile(r"([—–]\s*это\b|\bназывается\b|\bназывают\b|\bпонимается\b|\bпредставляет\s+собой\b|\bопределяется\s+как\b|\bозначает\b)", re.I)
LIST_RE = re.compile(r"(следующ\w+|признак\w*|включает|состоит\s+из|выделя\w+|различа\w+|\b\d\)\s)", re.I)
NAME_RE = re.compile(r"(?<![А-Яа-яЁё])[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?[А-ЯЁ][а-яё]{2,}")
NUM_RE = re.compile(r"\b\d{2,4}\b")
BIBLIO_RE = re.compile(r"(М\.:|СПб\.|Минск:|//|\bс\.\s?\d|\bИзд\.|\bУч\.\s?пособие|Режим доступа)")
SENT_RE = re.compile(r"(?<=[.!?…])(?<![А-ЯЁ]\.)\s+(?=[А-ЯЁA-Z«\"(])")        # инициалы «Ф. Бэкон» не рвут предложение
REMEMBER_RE = re.compile(r"^(кто|что|какой|какая|какое|какие|как называется|сколько|когда|где|какова|каков|какого)\b", re.I)
UNDERSTAND_RE = re.compile(r"(почему|чем\s+(объясняется|отличается|обусловлен)|отличает|отличие|различие|что\s+произойдёт|что\s+происходит|при\s+каком\s+условии|в\s+чём\s+(недостаток|суть|смысл|состоит|заключается)|зачем|для\s+чего|каким\s+образом|как\s+связан|к\s+чему\s+приводит|что\s+следует|причин|недостаток|достоинств|ценност|слабост|к\s+чему|при\s+каком|что\s+даёт|от\s+чего\s+зависит)", re.I)
TEXT_REF_RE = re.compile(r"(согласно\s+(тексту|учебнику|источнику|главе)|в\s+(тексте|учебнике|главе)|из\s+главы|отмечает\s+(автор|источник)|по\s+мнению\s+автора)", re.I)
ENUM_ANSWER_RE = re.compile(r"(,.*,)|(;)|(\b1\))")


def load_book(path: str) -> str:
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    return Path(path).read_text(encoding="utf-8")


def load_result(path: str):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "packs" in data:
        return data
    cards = data.get("cards") if isinstance(data, dict) else data
    return {"packs": {"_": {"cards": [c for c in cards if isinstance(c, dict)], "lesson": None}}, "map": {"nodes": []}}


def pct(a, b) -> float:
    return round(100 * a / b, 1) if b else 0.0


def key_sentences(text: str) -> list[dict]:
    """Предложения с «знанием, которое можно спросить»: определение, число/дата, перечень признаков, персоналия."""
    out, pos = [], 0
    for raw in SENT_RE.split(text):
        start = text.find(raw[:40], pos)
        start = pos if start < 0 else start
        pos = start + len(raw)
        s = re.sub(r"\s+", " ", coverage.join_wrapped(raw)).strip()
        if len(s) < 50 or len(s) > 700 or BIBLIO_RE.search(s) or s.count("....") or sum(c.isdigit() for c in s) > 0.15 * len(s):
            continue
        kinds = []
        if DEF_RE.search(s):
            kinds.append("определение")
        if NUM_RE.search(s):
            kinds.append("число/дата")
        if LIST_RE.search(s) and s.count(",") >= 2:
            kinds.append("перечень")
        if NAME_RE.search(s):
            kinds.append("персоналия")
        if kinds:
            out.append({"start": start, "end": pos, "text": s, "kinds": kinds})
    return out


def sentence_covered(sent: dict, cards: list[dict]) -> bool:
    """Карточка покрывает предложение, если её цитата (точно найденная) пересекается с ним, либо слова ответа лежат в предложении
    и хотя бы одно слово вопроса тоже."""
    stems = set(coverage.stems(sent["text"]))
    for c in cards:
        sp = c.get("src_span")
        if sp and sp[0] < sent["end"] and sp[1] > sent["start"]:
            return True
        ans = set(coverage.stems(c.get("translation", "")))
        q = set(coverage.stems(c.get("text", "")))
        if ans and sum(1 for w in ans if w in stems) / len(ans) >= 0.7 and (q & stems):
            return True
    return False


def defined_terms(text: str) -> list[str]:
    terms = []
    for m in re.finditer(r"([А-ЯЁа-яё][А-ЯЁа-яё\- ]{2,60}?)\s*(?:\([^)]{0,60}\))?\s+[—–]\s+это\b", coverage.join_wrapped(text)):
        t = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        t = " ".join(t.split()[-4:])
        if 4 <= len(t) <= 50:
            terms.append(t)
    return list(dict.fromkeys(terms))


def main() -> int:
    argv = sys.argv[1:]
    json_out = argv[argv.index("--json") + 1] if "--json" in argv and argv.index("--json") + 1 < len(argv) else None
    args = [a for a in argv if not a.startswith("--") and a != json_out]
    if len(args) != 2:
        print(__doc__)
        return 2
    text, res = load_book(args[0]), load_result(args[1])
    packs = res["packs"]
    cards = [c for p in packs.values() for c in (p.get("cards") or [])]
    lessons = {k: p["lesson"] for k, p in packs.items() if p.get("lesson")}
    report: dict = {"chars": len(text), "cards": len(cards), "lessons": len(lessons), "nodes": len(res["map"]["nodes"])}
    if not cards:
        print("Карточек нет.")
        return 1

    verifier = card_quality.CardVerifier(text)
    for c in cards:
        c.setdefault("evidence", "")
    summary, flagged = verifier.verify(cards)
    n = len(cards)

    # ---------------- A. Достоверность
    print(f"Текст: {len(text) // 1000} тыс. знаков; узлов {report['nodes']}, карточек {n} (одна на {len(text) // n} знаков), уроков {len(lessons)}")
    print("\nA. ДОСТОВЕРНОСТЬ (карточка подтверждена текстом)")
    print(f"   цитата найдена точно: {summary['grounded']} ({pct(summary['grounded'], n)}%), почти: {summary['near']}, "
          f"ответ рядом по словам: {summary['lexical']}, подозрительных: {summary['flagged']} ({pct(summary['flagged'], n)}%)")
    for c in flagged[:8]:
        print(f"     ? {c['text'][:70]} => {c['translation'][:40]} [{c.get('support_reason')}]")
    report["A_grounded_pct"] = pct(summary["grounded"], n)
    report["A_flagged_pct"] = pct(summary["flagged"], n)

    # ---------------- B. Покрытие
    index = verifier.index
    windows = len(index.spans)
    wc = verifier.card_windows(cards)
    covered_w = sum(1 for i in range(windows) if wc.get(i, 0) > 0)
    subst = [i for i in range(windows) if verifier.substantive(i)]
    covered_s = sum(1 for i in subst if wc.get(i, 0) > 0)
    print("\nB. ПОКРЫТИЕ СОДЕРЖАНИЯ (content validity)")
    print(f"   окон текста (~{coverage.WINDOW_CHARS // 1000} тыс. знаков) с карточкой: {covered_s} из {len(subst)} учебных ({pct(covered_s, len(subst))}%)")
    sections = coverage.extract_sections(text)
    if sections:
        rep = coverage.section_card_report(text, sections, cards)
        empty = [r for r in rep if r["cards"] == 0 and r["chars"] >= 3000]
        print(f"   разделов: {len(rep)}, без единой карточки: {len(empty)}")
        for r in rep:
            print(f"     §{r['chapter']}.{r['section']:<2} {r['chars'] // 1000:3} тыс. знаков  карточек {r['cards']:3}  на 10 тыс.: {r['per_10k']:4.1f}  {r['title'][:55]}")
    ks = key_sentences(text)
    by_kind = Counter(k for s in ks for k in s["kinds"])
    cov_ks = [s for s in ks if sentence_covered(s, cards)]
    print(f"   «ключевых предложений» (определение / число-дата / перечень / персоналия): {len(ks)}; покрыто карточками: {len(cov_ks)} ({pct(len(cov_ks), len(ks))}%)")
    for kind in ("определение", "число/дата", "перечень", "персоналия"):
        tot = [s for s in ks if kind in s["kinds"]]
        got = [s for s in tot if s in cov_ks]
        print(f"     {kind:11} {len(got):3} из {len(tot):3} ({pct(len(got), len(tot))}%)")
    report["B_windows_pct"] = pct(covered_s, len(subst))
    report["B_key_sentences_pct"] = pct(len(cov_ks), len(ks))

    cards_text = " ".join(f"{c['text']} {c['translation']} {c.get('example', '')}" for c in cards)
    lessons_text = " ".join(coverage.lesson_text(l) for l in lessons.values())
    cards_stems, lesson_stems = set(coverage.stems(cards_text)), set(coverage.stems(lessons_text))
    terms = defined_terms(text)
    in_cards = [t for t in terms if all(w in cards_stems for w in coverage.stems(t))]
    in_lessons = [t for t in terms if all(w in lesson_stems for w in coverage.stems(t))]
    in_any = [t for t in terms if t in in_cards or t in in_lessons]
    print(f"   определяемых терминов («X — это …»): {len(terms)}; в карточках {len(in_cards)}, в уроках {len(in_lessons)}, хотя бы где-то {len(in_any)} ({pct(len(in_any), len(terms))}%)")
    miss_terms = [t for t in terms if t not in in_any]
    if miss_terms:
        print("     нет ни в карточках, ни в уроках: " + "; ".join(miss_terms[:14]))
    src_nums = [m for m in dict.fromkeys(NUM_RE.findall(coverage.join_wrapped(text))) if not (m.startswith("0"))]
    src_years = [m for m in src_nums if len(m) == 4 and 1000 <= int(m) <= 2100]
    both_text = cards_text + " " + lessons_text
    got_years = [y for y in src_years if y in both_text]
    print(f"   годов в тексте: {len(src_years)}; встречаются в карточках/уроках: {len(got_years)} ({pct(len(got_years), len(src_years))}%)")
    report["B_terms_pct"] = pct(len(in_any), len(terms))

    # ---------------- C. Карточки (Wozniak)
    lens = [len(c["translation"].split()) for c in cards]
    long_ans = sum(1 for x in lens if x > 12)
    enum = sum(1 for c in cards if ENUM_ANSWER_RE.search(c["translation"]))
    yesno = sum(1 for c in cards if c["translation"].strip(" .!").lower() in ("да", "нет"))
    define_q = sum(1 for c in cards if re.match(r"^(что такое|дайте определение|определите)", c["text"].strip(), re.I))
    spoil = 0
    for c in cards:
        qs, ans = set(coverage.stems(c["text"])), set(coverage.stems(c["translation"]))
        if ans and len(ans & qs) / len(ans) >= 0.7:
            spoil += 1
    fronts = Counter(re.sub(r"\W+", " ", c["text"].lower()).strip() for c in cards)
    dup = sum(v - 1 for v in fronts.values() if v > 1)
    openers = Counter(" ".join(re.findall(r"\w+", c["text"].lower())[:2]) for c in cards)
    top, topn = openers.most_common(1)[0]
    print("\nC. КАРТОЧКИ (Wozniak: минимум информации, без перечислений и «Да/Нет»)")
    print(f"   длина ответа: средняя {sum(lens) / n:.1f} слов, длиннее 12 слов: {long_ans} ({pct(long_ans, n)}%)")
    textref = sum(1 for c in cards if TEXT_REF_RE.search(c["text"]))
    print(f"   ответ-перечисление: {enum} ({pct(enum, n)}%); Да/Нет: {yesno}; «Что такое…»: {define_q}; подсказка в вопросе: {spoil} ({pct(spoil, n)}%); "
          f"повторы вопросов: {dup}; вопрос ссылается на текст: {textref}")
    with_num = sum(1 for c in cards if NUM_RE.search(c["translation"]) or re.search(r"\d", c["translation"]))
    print(f"   самое частое начало вопроса: «{top}» — {pct(topn, n)}%; ответов с числом: {pct(with_num, n)}%")
    report.update({"C_long_pct": pct(long_ans, n), "C_enum_pct": pct(enum, n), "C_spoil_pct": pct(spoil, n)})

    # ---------------- D. Варианты ответа (Haladyna)
    with_x = [c for c in cards if c.get("distractors")]
    three = sum(1 for c in with_x if len(c["distractors"]) == 3)
    book_stems = set(coverage.stems(text))
    ratios, plaus, hom = [], 0, 0
    allx = 0
    for c in with_x:
        a_len = max(1, len(c["translation"]))
        for d in c["distractors"]:
            allx += 1
            ratios.append(len(d) / a_len)
            ds = coverage.stems(d)
            if ds and sum(1 for w in ds if w in book_stems) / len(ds) >= 0.6:
                plaus += 1
        if all(0.5 <= len(d) / a_len <= 2.0 for d in c["distractors"]):
            hom += 1
    banned = sum(1 for c in with_x for d in c["distractors"] if re.search(r"(все\s+(выше)?перечисленн|ни\s+один\s+из|нет\s+верного)", d, re.I))
    print("\nD. ВАРИАНТЫ ОТВЕТА (Haladyna, Downing & Rodriguez 2002)")
    print(f"   карточек с дистракторами: {len(with_x)} ({pct(len(with_x), n)}%), из них ровно 3: {three}")
    print(f"   однородны по длине с ответом (0.5–2×): {hom} ({pct(hom, len(with_x))}%); дистракторов со словами из текста книги (правдоподобие): {pct(plaus, allx)}%; «всё перечисленное»: {banned}")
    report["D_homogeneous_pct"] = pct(hom, len(with_x))

    # ---------------- E. Когнитивные уровни
    rem = sum(1 for c in cards if REMEMBER_RE.match(c["text"].strip()) and not UNDERSTAND_RE.search(c["text"]))
    und = sum(1 for c in cards if UNDERSTAND_RE.search(c["text"]))
    layers = Counter(c.get("layer", 1) for c in cards)
    print("\nE. КОГНИТИВНЫЕ УРОВНИ (таксономия Блума в ревизии Anderson & Krathwohl)")
    print(f"   «помнить» (кто/что/сколько/как называется): {rem} ({pct(rem, n)}%); «понимать/анализировать» (почему, чем отличается, что произойдёт…): {und} ({pct(und, n)}%)")
    print(f"   слои карточек: 0 — понятие {layers.get(0, 0)}, 1 — правило/условие {layers.get(1, 0)}, 2 — граница/различение {layers.get(2, 0)}")
    report["E_understand_pct"] = pct(und, n)

    # ---------------- F. Уроки
    words = [len(s["say"].split()) for l in lessons.values() for s in l["screens"]]
    screens = [len(l["screens"]) for l in lessons.values()]
    over = sum(1 for w in words if w > 40)
    cards_per_node = {k: len(p.get("cards") or []) for k, p in packs.items() if p.get("lesson")}
    ratio = [len(l["screens"]) / max(1, cards_per_node.get(k, 1)) for k, l in lessons.items()]
    al = coverage.lesson_alignment(packs)
    src_stems = book_stems
    fabricated = []
    book_nums = set(NUM_RE.findall(coverage.join_wrapped(text)))
    for k, l in lessons.items():
        for s in l["screens"]:
            for m in NUM_RE.findall(s["say"]):
                if m not in book_nums:
                    fabricated.append((k, m, s["say"][:80]))
            for nm in NAME_RE.findall(s["say"]):
                if not all(w in src_stems for w in coverage.stems(nm)):
                    fabricated.append((k, nm, s["say"][:80]))
    names_src = set(re.findall(r"[А-ЯЁ][а-яё]{3,}", text))
    capital = []
    for k, l in lessons.items():
        for s in l["screens"]:
            for w in re.findall(r"(?<![.!?]\s)(?<!^)\b[А-ЯЁ][а-яё]{3,}", s["say"]):
                if w not in names_src and w.lower() not in {x.lower() for x in names_src}:
                    capital.append((k, w))
    print("\nF. УРОКИ (Mayer: сегментирование и когерентность; Sweller; Biggs: согласованность)")
    if lessons:
        print(f"   слов на экран: среднее {sum(words) / len(words):.0f}, больше 40: {over} ({pct(over, len(words))}%); экранов на урок: {sum(screens) / len(screens):.1f}; экранов на карточку: {sum(ratio) / len(ratio):.2f}")
        print(f"   ответ карточки назван в уроке целиком: {al['full_share'] * 100:.0f}%, частично: {al['partial_share'] * 100:.0f}%, нет: {al['none_share'] * 100:.0f}%")
        print(f"   чисел/инициалов в уроках, которых нет в тексте книги: {len(fabricated)}")
        for k, v, s in fabricated[:8]:
            print(f"     ! [{k}] {v}: {s}")
        if capital:
            print(f"   слов с заглавной буквы вне текста книги (имена, названия): {len(capital)}: {', '.join(sorted({w for _, w in capital})[:15])}")
        report.update({"F_words_avg": round(sum(words) / len(words), 1), "F_aligned_pct": round(al["full_share"] * 100, 1),
                       "F_fabricated": len(fabricated)})
    else:
        print("   уроков нет")

    if json_out:
        Path(json_out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
