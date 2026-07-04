"""chat_message created_at clock_timestamp

Revision ID: df385246e05d
Revises: d7395298fab5
Create Date: 2026-07-03 02:22:27.009782+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df385246e05d'
down_revision: Union[str, None] = 'd7395298fab5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # now() 是事务时间戳：同一轮的用户消息与 AI 回复同事务落库时 created_at 完全相同，
    # 历史排序先后不稳定；clock_timestamp() 为语句级时间戳，保证同事务内顺序确定
    op.alter_column(
        "chat_message",
        "created_at",
        server_default=sa.text("clock_timestamp()"),
    )


def downgrade() -> None:
    op.alter_column("chat_message", "created_at", server_default=sa.text("now()"))
