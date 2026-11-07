"""将仅群聊语义的摘要表升级为统一会话摘要表。"""

import sqlalchemy as sa
from alembic import op


revision = "20260806_06"
down_revision = "20260806_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """保留现有摘要数据并重命名为统一会话摘要表。"""

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("group_summaries") and not inspector.has_table(
        "conversation_summaries"
    ):
        op.rename_table("group_summaries", "conversation_summaries")


def downgrade() -> None:
    """把统一会话摘要表名称恢复为旧群聊摘要表。"""

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("conversation_summaries") and not inspector.has_table(
        "group_summaries"
    ):
        op.rename_table("conversation_summaries", "group_summaries")
