"""set_phase1_limit_10_and_lock_slicing

Revision ID: 20260924_phase1_limit_10
Revises: 20260924_purge_sudoust
Create Date: 2026-09-24 22:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260924_phase1_limit_10'
down_revision: Union[str, Sequence[str], None] = '20260924_purge_sudoust'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Установка дневного лимита 10 карточек для всех участников эксперимента Фазы 1."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE user_settings SET daily_limit = 10 "
            "WHERE is_experiment_participant = 1 AND experiment_phase = 1;"
        )
    )


def downgrade() -> None:
    pass
