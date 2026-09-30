"""Вопросы с открытым ответом: локальная проверка без ИИ, разбор списка, API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from main import app
from app.database.models import Card, Phrase
from app.database.session import AsyncSessionLocal
from app.services.open_answer import (
    derive_key_points, grade_answer, parse_questions, points_from_bullets, significant_stems, stem,
)

REF = ("Принцип разделения властей: государственная власть делится на законодательную, исполнительную и судебную. "
       "Ветви власти независимы и сдерживают друг друга.")


def test_full_answer_in_own_words_passes():
    r = grade_answer("Власть делится на законодательную, исполнительную и судебную ветви, они независимы и сдерживают друг друга",
                     None, REF)
    assert r["suggested_rating"] == 3 and r["percent"] >= 75 and r["graded"]


def test_typos_and_word_forms_are_tolerated():
    r = grade_answer("Государственная власть делиться на законодательную, исполнителную и судебную. Ветви независимые, сдерживают друг друга",
                     None, REF)
    assert r["suggested_rating"] == 3


def test_partial_and_wrong_answers_score_lower():
    partial = grade_answer("Есть три ветви: законодательная и исполнительная", None, REF)
    wrong = grade_answer("Это про федерализм и территориальное устройство", None, REF)
    assert 0 < partial["score"] < 0.5 and partial["suggested_rating"] == 1
    assert wrong["score"] == 0 and wrong["suggested_rating"] == 1


def test_empty_answer_is_again():
    r = grade_answer("   ", None, REF)
    assert r["suggested_rating"] == 1 and r["score"] == 0


def test_negation_is_not_counted_as_match():
    r = grade_answer("Власть не делится на законодательную, исполнительную и судебную", None, REF)
    assert r["suggested_rating"] == 1
    assert any(p["negated"] for p in r["points"])


def test_dates_and_numbers_are_exact():
    kp = [{"text": "Конституция принята", "variants": []}, {"text": "1994 год"}]
    assert grade_answer("Конституция принята в 1994 г.", kp)["percent"] == 100
    wrong = grade_answer("Конституция принята в 1995 г.", kp)
    assert wrong["percent"] < 60 and not wrong["points"][1]["matched"]


def test_explicit_key_points_with_variants_and_weights():
    kp = [{"text": "разделение властей", "variants": ["три ветви власти"], "weight": 2},
          {"text": "верховенство права"}]
    r = grade_answer("В государстве три ветви власти", kp)
    assert r["points"][0]["matched"] and not r["points"][1]["matched"]
    assert r["percent"] == 67   # вес 2 из 3


def test_short_words_are_not_fuzzy_matched():
    assert stem("вид") != stem("вина")
    kp = [{"text": "вина"}]
    assert not grade_answer("вид", kp)["points"][0]["matched"]


def test_answer_length_is_capped_and_no_points_means_ungraded():
    r = grade_answer("слово " * 5000, None, "")
    assert r["graded"] is False and r["total"] == 0
    assert significant_stems("и в на") == []


def test_derive_gives_heading_half_weight():
    pts = derive_key_points(REF)
    assert pts[0]["weight"] == 0.5 and len(pts) == 3


def test_bullets_become_key_points_with_variants():
    pts = points_from_bullets("- разделение властей / принцип разделения\n- независимость ветвей\nтекст")
    assert [p["text"] for p in pts] == ["разделение властей", "независимость ветвей"]
    assert pts[0]["variants"] == ["принцип разделения"]


def test_parse_questions_formats():
    items = parse_questions(
        "1. Что такое право?\nСистема норм\n\n"
        "Вопрос: Что такое закон?\nОтвет: Нормативный акт\nвысшей силы\n\n"
        "Q: Без ответа?\n\n"
        "Просто вопрос?"
    )
    assert [i["question"] for i in items] == ["Что такое право?", "Что такое закон?", "Без ответа?", "Просто вопрос?"]
    assert items[0]["answer"] == "Система норм"
    assert items[1]["answer"] == "Нормативный акт\nвысшей силы"
    assert items[2]["answer"] == "" and items[3]["answer"] == ""


# ---------- API ----------
UID = "open_q_user"
H = {"X-User-Id": UID}


async def _cleanup(uid=UID):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Card).where(Card.user_id == uid))
        await db.execute(delete(Phrase).where(Phrase.user_id == uid))
        await db.commit()


def test_import_check_flow_and_ownership():
    import asyncio
    asyncio.run(_cleanup(UID)); asyncio.run(_cleanup("open_q_other"))
    text = ("Что такое разделение властей?\n- деление власти на ветви / три ветви\n- независимость ветвей\n\n"
            "Сколько ветвей?\nТри\n\nВопрос без ответа?")
    with TestClient(app) as client:
        prev = client.post("/api/open/preview", json={"text": text}, headers=H).json()
        assert prev["count"] == 3 and prev["without_answer"] == 1 and prev["items"][0]["points"] == 2

        res = client.post("/api/open/import", json={"subject": "open_sub", "title": "Билеты", "text": text}, headers=H)
        assert res.status_code == 200 and res.json()["created"] == 3 and res.json()["without_answer"] == 1
        again = client.post("/api/open/import", json={"subject": "open_sub", "text": text}, headers=H).json()
        assert again["created"] == 0 and again["skipped_duplicates"] == 3

        sess = client.get("/api/session?subject=open_sub&mode=new", headers=H).json()
        assert {c["content_type"] for c in sess} == {"open"}
        first = next(c for c in sess if c["text"].startswith("Что такое разделение"))

        ok = client.post("/api/open/check", json={"card_id": first["id"], "answer": "власть делят на три ветви, они независимы"}, headers=H).json()
        assert ok["suggested_rating"] >= 3 and ok["reference"].startswith("•")
        none = client.post("/api/open/check", json={"card_id": first["id"], "answer": "не знаю"}, headers=H).json()
        assert none["suggested_rating"] == 1

        # чужая карточка недоступна
        assert client.post("/api/open/check", json={"card_id": first["id"], "answer": "x"},
                           headers={"X-User-Id": "open_q_other"}).status_code == 404
        assert client.post("/api/open/import", json={"subject": "s", "text": "   "}, headers=H).status_code == 400
    import asyncio as a2
    a2.run(_cleanup(UID))
