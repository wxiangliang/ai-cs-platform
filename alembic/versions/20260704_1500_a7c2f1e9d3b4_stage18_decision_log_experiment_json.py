"""stage18 add chat_decision_log.experiment_json for A/B experiments

Revision ID: a7c2f1e9d3b4
Revises: 3a1e7630141b
Create Date: 2026-07-04 15:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c2f1e9d3b4'
down_revision: Union[str, None] = '3a1e7630141b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A/B 实验变体分配（Stage 18）：{assignments:[{exp_id,variant}], overrides:{...}}，
    # 可空——无实验命中为空。供事后按变体切分 quality_daily 对比
    op.add_column(
        "chat_decision_log",
        sa.Column(
            "experiment_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="A/B 实验变体分配",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_decision_log", "experiment_json")
