"""为 Skill 增加可选有效期。"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_09"
down_revision = "20260812_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加可空有效期；历史 Skill 默认长期有效。"""

    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("skills")}
    if "expires_at" not in columns:
        op.add_column("skills", sa.Column("expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """移除 Skill 有效期。"""

    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("skills")}
    if "expires_at" in columns:
        op.drop_column("skills", "expires_at")
