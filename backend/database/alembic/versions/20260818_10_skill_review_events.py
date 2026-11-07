"""增加 Skill 乐观锁和不可变审核事件。"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_10"
down_revision = "20260814_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建审核事件表，并为历史 Skill 初始化版本号。"""

    inspector = sa.inspect(op.get_bind())
    skill_columns = {item["name"] for item in inspector.get_columns("skills")}
    if "version" not in skill_columns:
        op.add_column(
            "skills",
            sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        )

    if "skill_review_events" not in inspector.get_table_names():
        op.create_table(
            "skill_review_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("skill_id", sa.BigInteger(), nullable=False),
            sa.Column("reviewer_userid", sa.String(length=128), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("opinion", sa.Text(), nullable=False),
            sa.Column("before_snapshot", sa.JSON(), nullable=False),
            sa.Column("after_snapshot", sa.JSON(), nullable=False),
            sa.Column("governance_snapshot", sa.JSON(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["skill_id"], ["skills.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(
                ["reviewer_userid"], ["users.wecom_userid"], ondelete="RESTRICT"
            ),
            sa.UniqueConstraint("request_id", name="uq_skill_review_events_request_id"),
        )
        op.create_index(
            "idx_skill_review_events_skill_time",
            "skill_review_events",
            ["skill_id", "created_at", "id"],
        )


def downgrade() -> None:
    """移除审核事件表和 Skill 版本号。"""

    inspector = sa.inspect(op.get_bind())
    if "skill_review_events" in inspector.get_table_names():
        op.drop_index(
            "idx_skill_review_events_skill_time", table_name="skill_review_events"
        )
        op.drop_table("skill_review_events")
    skill_columns = {item["name"] for item in inspector.get_columns("skills")}
    if "version" in skill_columns:
        op.drop_column("skills", "version")
