"""为 AI 回复和 Skill 候选补充可核验的原始消息证据。"""

import sqlalchemy as sa
from alembic import op


revision = "20260811_07"
down_revision = "20260806_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加消息 ID 数组，并为升级前数据写入空数组标记无精确证据。"""

    inspector = sa.inspect(op.get_bind())
    ai_reply_columns = {item["name"] for item in inspector.get_columns("ai_replies")}
    skill_columns = {item["name"] for item in inspector.get_columns("skills")}
    if "context_message_ids" not in ai_reply_columns:
        op.add_column("ai_replies", sa.Column("context_message_ids", sa.JSON(), nullable=True))
        op.execute("UPDATE ai_replies SET context_message_ids = JSON_ARRAY() WHERE context_message_ids IS NULL")
        op.alter_column("ai_replies", "context_message_ids", existing_type=sa.JSON(), nullable=False)
    if "source_message_ids" not in skill_columns:
        op.add_column("skills", sa.Column("source_message_ids", sa.JSON(), nullable=True))
        op.execute("UPDATE skills SET source_message_ids = JSON_ARRAY() WHERE source_message_ids IS NULL")
        op.alter_column("skills", "source_message_ids", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    """移除 Skill 原始消息证据字段。"""

    inspector = sa.inspect(op.get_bind())
    if "source_message_ids" in {item["name"] for item in inspector.get_columns("skills")}:
        op.drop_column("skills", "source_message_ids")
    if "context_message_ids" in {
        item["name"] for item in inspector.get_columns("ai_replies")
    }:
        op.drop_column("ai_replies", "context_message_ids")
