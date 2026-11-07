"""增加账号首次登录修改初始密码状态。"""

import sqlalchemy as sa
from alembic import op


revision = "20260806_05"
down_revision = "20260805_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为现有用户表增加待改密标记，历史账号默认无需改密。"""

    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "must_change_password" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    """移除首次改密状态字段。"""

    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "must_change_password" in columns:
        op.drop_column("users", "must_change_password")
