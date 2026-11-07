"""建立客服宝 MySQL 十张核心业务表。"""

from alembic import op

from backend.database.base import Base
from backend.database import models  # noqa: F401

revision = "20260804_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按 ORM 元数据创建尚不存在的核心表。"""

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """仅用于全新测试环境回滚初始结构。"""

    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
