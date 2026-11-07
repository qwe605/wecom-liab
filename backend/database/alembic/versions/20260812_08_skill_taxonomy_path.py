"""为现有 Skill 增加最小五级知识分类路径。"""

import sqlalchemy as sa
from alembic import op


revision = "20260812_08"
down_revision = "20260811_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加分类路径，并以空数组兼容尚未人工分类的历史 Skill。"""

    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("skills")}
    if "taxonomy_path" not in columns:
        op.add_column("skills", sa.Column("taxonomy_path", sa.JSON(), nullable=True))
        op.execute(
            "UPDATE skills SET taxonomy_path = JSON_ARRAY() "
            "WHERE taxonomy_path IS NULL"
        )
        op.alter_column(
            "skills", "taxonomy_path", existing_type=sa.JSON(), nullable=False
        )


def downgrade() -> None:
    """回滚时只移除本次新增的分类路径字段。"""

    inspector = sa.inspect(op.get_bind())
    if "taxonomy_path" in {item["name"] for item in inspector.get_columns("skills")}:
        op.drop_column("skills", "taxonomy_path")
