import asyncio
from datetime import datetime
from sqlalchemy import delete
from app.database.session import AsyncSessionLocal, engine
from app.database.models import Base, Phrase, Card, ReviewLog, DailySession

async def seed_database(user_id: str = "dev_user"):
    # Создаем таблицы асинхронно перед посевом
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as db:
        try:
            # Очищаем старые тестовые данные
            await db.execute(delete(ReviewLog))
            await db.execute(delete(DailySession))
            await db.execute(delete(Card))
            await db.execute(delete(Phrase))
            
            print("[Seed] База данных очищена от старых тестовых данных.")

            # ==========================================
            # ЕДИНСТВЕННЫЙ ДЕМО-НАБОР: КИТАЙСКИЙ ЯЗЫК (chinese_hsk3)
            # ==========================================
            p1 = Phrase(text="Урок 1: Базовые понятия", subject="chinese_hsk3", user_id=user_id)
            db.add(p1)
            await db.flush()  # Получаем p1.id

            cards_chinese = [
                Card(
                    phrase_id=p1.id,
                    user_id=user_id,
                    subject="chinese_hsk3",
                    text="中国",
                    secondary_text="Zhōngguó",
                    translation="Китай (Срединное государство)",
                    example="中国是一个历史悠久的国家。(Китай — страна с древней историей)",
                    difficulty=5.0,
                    stability=1.5,
                    state=0,
                    mnemonic={"keyword": "ДЖОНГ-ГУО", "verbal_cue": "В центре (ДЖОНГ) мира стоит великое государство (ГУО)."},
                    next_review=datetime.utcnow()
                ),
                Card(
                    phrase_id=p1.id,
                    user_id=user_id,
                    subject="chinese_hsk3",
                    text="大学",
                    secondary_text="dàxué",
                    translation="Университет",
                    example="他在北京大学学习。(Он учится в Пекинском университете)",
                    difficulty=4.5,
                    stability=1.5,
                    state=0,
                    mnemonic={"keyword": "ДА-СЮЭ", "verbal_cue": "БОЛЬШОЕ (да) УЧЕНИЕ (сюэ) получают в университете."},
                    next_review=datetime.utcnow()
                ),
                Card(
                    phrase_id=p1.id,
                    user_id=user_id,
                    subject="chinese_hsk3",
                    text="准备",
                    secondary_text="zhǔnbèi",
                    translation="Готовить, подготавливать",
                    example="你准备好了吗？(Ты готов?)",
                    difficulty=6.0,
                    stability=1.2,
                    state=0,
                    mnemonic={"keyword": "ДЖУН-БЭЙ", "verbal_cue": "ДЖУНГЛИ зовут, БЕЙ в барабан — готовься к походу!"},
                    next_review=datetime.utcnow()
                ),
            ]
            db.add_all(cards_chinese)
            await db.commit()
            print(f"[Seed] Единственный чистый демонстрационный набор успешно загружен для пользователя '{user_id}'!")
            
        except Exception as e:
            await db.rollback()
            print(f"[Seed] Критическая ошибка при заполнении базы: {e}")

if __name__ == "__main__":
    asyncio.run(seed_database("dev_user"))