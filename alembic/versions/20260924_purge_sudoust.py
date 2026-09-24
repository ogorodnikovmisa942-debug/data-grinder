"""purge_sudoustroystvo_and_isolate_decks

Revision ID: 20260924_purge_sudoust
Revises: 1926b71611fb
Create Date: 2026-09-24 19:05:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260924_purge_sudoust'
down_revision: Union[str, Sequence[str], None] = '1926b71611fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Очистка захардкоженных общих карточек default_user и нормализация названий колод пользователей."""
    bind = op.get_bind()

    # 1. Удаление тестовых карточек default_user и dev_user (изоляция)
    bind.execute(sa.text("DELETE FROM cards WHERE user_id IN ('default_user', 'dev_user');"))
    bind.execute(sa.text("DELETE FROM phrases WHERE user_id IN ('default_user', 'dev_user');"))
    bind.execute(sa.text("DELETE FROM topic_knowledge_graphs WHERE user_id IN ('default_user', 'dev_user');"))
    bind.execute(sa.text("DELETE FROM practice_items WHERE user_id IN ('default_user', 'dev_user');"))
    bind.execute(sa.text("DELETE FROM practice_session_logs WHERE user_id IN ('default_user', 'dev_user');"))

    # 2. Если у реальных пользователей были карточки под устаревшими алиасами 'sudoustroystvo', 'sudoustr', 'court_system'
    # нормализуем их в 'sudoust', чтобы пользовательская колода осталась в сохранности
    bind.execute(sa.text("UPDATE cards SET subject = 'sudoust' WHERE subject IN ('sudoustroystvo', 'sudoustr', 'court_system');"))
    bind.execute(sa.text("UPDATE phrases SET subject = 'sudoust' WHERE subject IN ('sudoustroystvo', 'sudoustr', 'court_system');"))
    bind.execute(sa.text("UPDATE topic_knowledge_graphs SET subject = 'sudoust' WHERE subject IN ('sudoustroystvo', 'sudoustr', 'court_system');"))
    bind.execute(sa.text("UPDATE practice_items SET subject = 'sudoust' WHERE subject IN ('sudoustroystvo', 'sudoustr', 'court_system');"))
    bind.execute(sa.text("UPDATE practice_session_logs SET subject = 'sudoust' WHERE subject IN ('sudoustroystvo', 'sudoustr', 'court_system');"))

    # 3. Нормализация названий тем в phrases: заменяем захардкоженные 'Судоустройство...' на название предмета
    bind.execute(sa.text("UPDATE phrases SET text = subject WHERE LOWER(text) LIKE '%судоустройств%';"))

    # 4. Удаление осиротевших записей истории повторений (ReviewLog)
    bind.execute(sa.text("DELETE FROM review_logs WHERE card_id NOT IN (SELECT id FROM cards);"))


def downgrade() -> None:
    pass
