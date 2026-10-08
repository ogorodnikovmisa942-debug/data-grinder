# app/api/endpoints/anki.py
"""Экспорт карточек предмета в Anki (.apkg): скачать файлом или получить в чате с ботом."""
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.config import settings
from app.core.limiter import limiter
from app.database.models import Card, KnowledgeNode
from app.database.session import get_db
from app.services.anki_export import build_apkg
from app.services.graph_service import resolve_subject_alias
from app.services.knowledge_path import normalize_subject

router = APIRouter()


async def _deck(db: AsyncSession, user_id: str, subject: str) -> tuple[str, list[dict], bytes]:
    subject = normalize_subject(resolve_subject_alias(subject.strip().lower()) or subject)
    rows = (await db.execute(
        select(Card, KnowledgeNode.name).outerjoin(KnowledgeNode, KnowledgeNode.id == Card.node_id)
        .where(Card.user_id == user_id, Card.subject == subject).order_by(Card.topological_rank, Card.id)
    )).all()
    if not rows:
        raise HTTPException(status_code=404, detail="В этом предмете нет карточек.")
    cards = [{"id": c.id, "text": c.text, "secondary_text": c.secondary_text, "translation": c.translation, "example": c.example,
              "tag": node_name or ""} for c, node_name in rows]
    name = f"Data Grinder: {subject}"
    return name, cards, build_apkg(name, cards)


@router.get("/export/anki")
@limiter.limit("20/hour")
async def export_anki(request: Request, subject: str, current_user: str = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    """Колода предмета одним файлом .apkg: открывается в Anki на компьютере и телефоне."""
    name, cards, data = await _deck(db, current_user, subject)
    filename = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_") or "deck"
    return Response(content=data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"deck.apkg\"; filename*=UTF-8''{quote(filename)}.apkg",
        "X-Cards": str(len(cards)),
    })


@router.post("/export/anki/send")
@limiter.limit("10/hour")
async def send_anki_to_chat(request: Request, subject: str, current_user: str = Depends(get_current_user_id),
                            db: AsyncSession = Depends(get_db)):
    """Колода в чат с ботом: в мини-приложении Telegram скачивание файлов нередко заблокировано, а документ в чате открывается везде."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or token == "placeholder_bot_token" or not str(current_user).isdigit():
        raise HTTPException(status_code=409, detail="Отправка в Telegram здесь недоступна: скачайте файл.")
    name, cards, data = await _deck(db, current_user, subject)
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.types import BufferedInputFile
    session = AiohttpSession(timeout=60)
    try:
        bot = Bot(token=token, session=session)
        await bot.send_document(chat_id=int(current_user), document=BufferedInputFile(data, filename="data_grinder.apkg"),
                                caption=f"Колода для Anki: {len(cards)} карточек. Откройте файл в Anki.")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Не получилось отправить файл. Скачайте его напрямую.")
    finally:
        await session.close()
    return {"sent": True, "cards": len(cards)}
