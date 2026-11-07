"""为外部群聊保存企业微信返回的真实群名。"""

import sqlalchemy as sa
from alembic import op


revision = "20260805_04"
down_revision = "20260805_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加可空会话名称；单聊继续通过参与人名称展示。"""

    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("conversations")
    }
    if "name" not in columns:
        op.add_column("conversations", sa.Column("name", sa.String(255), nullable=True))


def downgrade() -> None:
    """移除会话名称字段。"""

    columns = {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("conversations")
    }
    if "name" in columns:
        op.drop_column("conversations", "name")
