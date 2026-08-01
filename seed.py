import asyncio
from datetime import datetime
from sqlalchemy import delete
from app.database.session import AsyncSessionLocal, engine
from app.database.models import Base, Phrase, Card

async def seed_database():
    # Создаем таблицы асинхронно перед посевом
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    async with AsyncSessionLocal() as db:
        try:
            # Очищаем старые данные асинхронно
            await db.execute(delete(Card))
            await db.execute(delete(Phrase))
            
            print("[Seed] База данных очищена.")

            # ==========================================
            # 1. ДОМЕН: КИТАЙСКИЙ ЯЗЫК (chinese_hsk3)
            # ==========================================
            p1 = Phrase(text="Урок 1: Базовые понятия", subject="chinese_hsk3")
            db.add(p1)
            await db.flush()  # Получаем p1.id

            cards_chinese = [
                Card(phrase_id=p1.id, subject="chinese_hsk3", text="中国", secondary_text="Zhōngguó", translation="Китай", next_review=datetime.utcnow()),
                Card(phrase_id=p1.id, subject="chinese_hsk3", text="大学", secondary_text="dàxué", translation="Университет", next_review=datetime.utcnow()),
                Card(phrase_id=p1.id, subject="chinese_hsk3", text="准备", secondary_text="zhǔnbèi", translation="Готовить, подготавливать", next_review=datetime.utcnow()),
            ]
            db.add_all(cards_chinese)

            # ==========================================
            # 2. ДОМЕН: ГРАЖДАНСКОЕ ПРАВО (law_civil)
            # ==========================================
            p2 = Phrase(text="ГК РФ Глава 25. Ответственность", subject="law_civil")
            db.add(p2)
            await db.flush()

            cards_law = [
                Card(
                    phrase_id=p2.id, 
                    subject="law_civil", 
                    text="Форс-мажор", 
                    secondary_text="ГК РФ Статья 401 ч.3", 
                    translation="Чрезвычайные и непредотвратимые при данных условиях обстоятельства (непреодолимая сила), освобождающие от ответственности.",
                    next_review=datetime.utcnow()
                ),
                Card(
                    phrase_id=p2.id, 
                    subject="law_civil", 
                    text="Реальный ущерб", 
                    secondary_text="ГК РФ Статья 15 ч.2", 
                    translation="Расходы, которые лицо произвело или должно будет произвести для восстановления нарушенного права, а также утрата или повреждение его имущества.",
                    next_review=datetime.utcnow()
                ),
            ]
            db.add_all(cards_law)

            # ==========================================
            # 3. ДОМЕН: ПРОГРАММИРОВАНИЕ (python_pro)
            # ==========================================
            p3 = Phrase(text="Продвинутый Python: Асинхронность", subject="python_pro")
            db.add(p3)
            await db.flush()

            cards_python = [
                Card(
                    phrase_id=p3.id, 
                    subject="python_pro", 
                    text="asyncio.gather()", 
                    secondary_text="asyncio.gather(*aws, return_exceptions=False)", 
                    translation="Аппаратно запускает несколько асинхронных задач (Awaitable) одновременно и ожидает их выполнения, возвращая список результатов.",
                    next_review=datetime.utcnow()
                ),
                Card(
                    phrase_id=p3.id, 
                    subject="python_pro", 
                    text="GIL (Global Interpreter Lock)", 
                    secondary_text="Потоки в CPython", 
                    translation="Способ синхронизации, не позволяющий нескольким процессорным потокам исполнять байт-код Python одновременно. Ограничивает параллелизм в CPU-bound задачах.",
                    next_review=datetime.utcnow()
                ),
            ]
            db.add_all(cards_python)

            await db.commit()
            print("[Seed] Тестовая матрица знаний успешно загружена в базу!")
            
        except Exception as e:
            await db.rollback()
            print(f"[Seed] Критическая ошибка при заполнении базы: {e}")

if __name__ == "__main__":
    asyncio.run(seed_database())