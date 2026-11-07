"""增加审核规则 Skill 与审核事件的来源关联。"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_13"
down_revision = "20260818_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建规则来源关联表；历史事件由幂等编译接口补生成草案。"""

    inspector = sa.inspect(op.get_bind())
    if "review_policy_sources" in inspector.get_table_names():
        return
    op.create_table(
        "review_policy_sources",
        sa.Column("policy_skill_id", sa.BigInteger(), nullable=False),
        sa.Column("review_event_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["policy_skill_id"], ["skills.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["review_event_id"], ["skill_review_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "policy_skill_id", "review_event_id",
            name="pk_review_policy_sources",
        ),
        sa.UniqueConstraint(
            "review_event_id", name="uq_review_policy_sources_event"
        ),
    )
    op.create_index(
        "idx_review_policy_sources_event",
        "review_policy_sources",
        ["review_event_id"],
    )


def downgrade() -> None:
    """移除审核规则来源关联，不删除任何 Skill 或审核事件。"""

    inspector = sa.inspect(op.get_bind())
    if "review_policy_sources" not in inspector.get_table_names():
        return
    op.drop_index(
        "idx_review_policy_sources_event", table_name="review_policy_sources"
    )
    op.drop_table("review_policy_sources")
