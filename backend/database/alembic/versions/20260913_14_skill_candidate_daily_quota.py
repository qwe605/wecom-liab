"""为业务 Skill 候选每日配额预留持久化表。"""

import sqlalchemy as sa
from alembic import op


revision = "20260913_14"
down_revision = "20260818_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建每日候选配额表；历史候选不回填使用量。"""

    if "skill_candidate_daily_quotas" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "skill_candidate_daily_quotas",
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("used_count", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("quota_date", name="pk_skill_candidate_daily_quotas"),
    )


def downgrade() -> None:
    """删除配额表，不删除任何 Skill 候选。"""

    if "skill_candidate_daily_quotas" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("skill_candidate_daily_quotas")
