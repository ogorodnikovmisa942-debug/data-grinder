from app.database.session import AsyncSessionLocal

async def get_db():
    """
    Создает изолированную асинхронную сессию базы данных для каждого HTTP-запроса.
    Гарантированно закрывает соединение после отправки ответа пользователю.
    """
    async with AsyncSessionLocal() as db:
        yield db