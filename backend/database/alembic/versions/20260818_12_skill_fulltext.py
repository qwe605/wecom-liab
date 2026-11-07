"""为 MySQL Skill 标题和正文增加中文 ngram FULLTEXT 索引。"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_12"
down_revision = "20260818_11"
branch_labels = None
depends_on = None


def _indexes() -> set[str]:
    """返回当前 skills 表索引名，保证重复部署幂等。"""

    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("skills")}


def upgrade() -> None:
    """仅在 MySQL 创建 ngram FULLTEXT；其他测试数据库保持 ORM 回退。"""

    if op.get_bind().dialect.name != "mysql":
        return
    indexes = _indexes()
    if "ft_skills_title" not in indexes:
        op.execute("ALTER TABLE skills ADD FULLTEXT INDEX ft_skills_title (title) WITH PARSER ngram")
    if "ft_skills_content" not in indexes:
        op.execute("ALTER TABLE skills ADD FULLTEXT INDEX ft_skills_content (content) WITH PARSER ngram")


def downgrade() -> None:
    """移除本次建立的全文索引。"""

    if op.get_bind().dialect.name != "mysql":
        return
    indexes = _indexes()
    if "ft_skills_title" in indexes:
        op.drop_index("ft_skills_title", table_name="skills")
    if "ft_skills_content" in indexes:
        op.drop_index("ft_skills_content", table_name="skills")
