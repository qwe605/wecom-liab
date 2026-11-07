"""为已发布 Skill 增加经过人工确认的关系边。"""

import sqlalchemy as sa
from alembic import op


revision = "20260913_15"
down_revision = "20260913_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建关系表，关系只能连接两个不同 Skill。"""

    if "skill_relations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "skill_relations",
        sa.Column("source_skill_id", sa.BigInteger(), nullable=False),
        sa.Column("target_skill_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.CheckConstraint("source_skill_id <> target_skill_id", name="ck_skill_relations_not_self"),
        sa.ForeignKeyConstraint(["source_skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("source_skill_id", "target_skill_id", "relation_type", name="pk_skill_relations"),
    )
    op.create_index("idx_skill_relations_target", "skill_relations", ["target_skill_id", "relation_type"])


def downgrade() -> None:
    """删除关系表，不删除 Skill 页面。"""

    if "skill_relations" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("idx_skill_relations_target", table_name="skill_relations")
    op.drop_table("skill_relations")
