"""为 Skill 候选增加创建时间，支持每日候选产出上限。"""

import sqlalchemy as sa
from alembic import op


revision = "20261004_14"
down_revision = "20260913_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为历史 Skill 补齐 UTC 创建时间，并建立每日统计索引。"""

    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("skills")}
    if "created_at" not in columns:
        op.add_column(
            "skills",
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        # 历史记录不应占用上线当天的新增配额；新候选由 ORM 写入当前 UTC 时间。
        op.execute(sa.text("UPDATE skills SET created_at = '1970-01-01 00:00:00' WHERE created_at IS NULL"))
        op.alter_column("skills", "created_at", existing_type=sa.DateTime(), nullable=False)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("skills")}
    if "idx_skills_created_at" not in indexes:
        op.create_index("idx_skills_created_at", "skills", ["created_at"])


def downgrade() -> None:
    """回滚创建时间字段及统计索引。"""

    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("skills")}
    if "idx_skills_created_at" in indexes:
        op.drop_index("idx_skills_created_at", table_name="skills")
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("skills")}
    if "created_at" in columns:
        op.drop_column("skills", "created_at")
