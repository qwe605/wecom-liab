"""新增消息来源企业级全局流状态。"""

import sqlalchemy as sa
from alembic import op

revision = "20260804_02"
down_revision = "20260804_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建全局流状态表；兼容初始迁移使用当前 ORM 建表的开发环境。"""

    bind = op.get_bind()
    if sa.inspect(bind).has_table("provider_stream_states"):
        return
    op.create_table(
        "provider_stream_states",
        sa.Column("provider", sa.String(length=32), primary_key=True),
        sa.Column("upper_cursor", sa.String(length=255), nullable=False),
        sa.Column(
            "reached_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """回滚时删除消息来源全局流状态表。"""

    bind = op.get_bind()
    if sa.inspect(bind).has_table("provider_stream_states"):
        op.drop_table("provider_stream_states")
