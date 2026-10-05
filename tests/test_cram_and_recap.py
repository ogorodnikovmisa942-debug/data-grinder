"""Штурм: случайная выборка по выбранному предмету без повторов за день; закрепление выученного в конце занятия."""
import unittest
from datetime import timedelta

from sqlalchemy import delete

from app.api.endpoints.train import get_session_cards, CRAM_ROUND_SIZE
from app.database.models import Card, Phrase, ReviewLog, KnowledgeNode, NodeProgress, utc_now
from app.database.session import AsyncSessionLocal
from app.services.knowledge_path import next_path_step

USER = "test_cram_recap_user"


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ReviewLog).where(ReviewLog.user_id == USER))
        await db.execute(delete(Card).where(Card.user_id == USER))
        await db.execute(delete(Phrase).where(Phrase.user_id == USER))
        await db.execute(delete(NodeProgress).where(NodeProgress.user_id == USER))
        await db.execute(delete(KnowledgeNode).where(KnowledgeNode.user_id == USER))
        await db.commit()


async def _make_cards(subject, n, state=2, prefix="К"):
    async with AsyncSessionLocal() as db:
        phrase = Phrase(text=f"Тема {subject}", subject=subject, user_id=USER)
        db.add(phrase)
        await db.flush()
        now = utc_now()
        db.add_all([Card(
            phrase_id=phrase.id, user_id=USER, subject=subject, text=f"{prefix} {subject} {i}?", translation=f"Ответ {i}.",
            state=state, stability=5.0, difficulty=5.5, last_review=now - timedelta(days=3),
            next_review=now + timedelta(days=2), topological_rank=i,
        ) for i in range(n)])
        await db.commit()


class TestCram(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _cleanup()

    async def asyncTearDown(self):
        await _cleanup()

    async def test_cram_uses_only_the_chosen_subject_and_mixes_all_subjects_for_all(self):
        await _make_cards("test_cram_a", 30)
        await _make_cards("test_cram_b", 30)
        async with AsyncSessionLocal() as db:
            only_a = await get_session_cards(subject="test_cram_a", mode="cram", current_user=USER, db=db)
            self.assertEqual(len(only_a), CRAM_ROUND_SIZE)
            self.assertEqual({c["subject"] for c in only_a}, {"test_cram_a"})
            mixed = await get_session_cards(subject="all", mode="cram", current_user=USER, db=db)
            self.assertEqual({c["subject"] for c in mixed}, {"test_cram_a", "test_cram_b"})
            # новых карточек в штурме нет
            self.assertTrue(all(c["state"] != 0 for c in mixed))

    async def test_cram_rounds_do_not_repeat_the_same_cards_until_all_are_seen(self):
        await _make_cards("test_cram_c", 45)
        seen = set()
        async with AsyncSessionLocal() as db:
            for round_no in range(3):
                cards = await get_session_cards(subject="test_cram_c", mode="cram", current_user=USER, db=db)
                ids = {c["id"] for c in cards}
                if round_no < 2:
                    self.assertEqual(len(ids), CRAM_ROUND_SIZE)
                    self.assertFalse(ids & seen, "раунд повторил карточки, уже пройденные в штурме сегодня")
                else:
                    self.assertEqual(len(ids), CRAM_ROUND_SIZE)       # осталось 5 непройденных + добор уже виденными
                    self.assertTrue(set(list(ids)) >= (ids - seen))
                for cid in ids:                                       # раунд пройден: ответы штурма пишутся в журнал
                    db.add(ReviewLog(card_id=cid, user_id=USER, rating=3, review_time=utc_now(), state=2, is_cram=True))
                await db.commit()
                seen |= ids
        self.assertEqual(len(seen), 45)                               # за три раунда прошли все 45 карточек

    async def test_cram_round_is_random_not_the_same_ten(self):
        await _make_cards("test_cram_d", 60)
        async with AsyncSessionLocal() as db:
            first = {c["id"] for c in await get_session_cards(subject="test_cram_d", mode="cram", current_user=USER, db=db)}
            second = {c["id"] for c in await get_session_cards(subject="test_cram_d", mode="cram", current_user=USER, db=db)}
        self.assertNotEqual(first, second)


class TestRecapStep(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _cleanup()

    async def asyncTearDown(self):
        await _cleanup()

    async def test_recently_learned_cards_get_a_recap_step_at_the_end_of_a_run(self):
        subject = "test_recap_subject"
        await _make_cards(subject, 4, state=1, prefix="Недавно")           # в заучивании, повтор ещё не подошёл
        async with AsyncSessionLocal() as db:
            step = await next_path_step(db, USER, subject, [])
            self.assertEqual(step["type"], "recap")
            self.assertEqual(step["count"], 4)
            # шаг выдаётся один раз за запуск; в режиме «Новая тема» его нет
            step = await next_path_step(db, USER, subject, ["recap"])
            self.assertEqual(step["type"], "done")
            step = await next_path_step(db, USER, subject, [], scope="topic")
            self.assertEqual(step["type"], "done")
