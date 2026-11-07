"""切换为企业微信官方归档唯一消息源和全局游标。"""

import sqlalchemy as sa
from alembic import op


revision = "20260805_03"
down_revision = "20260804_02"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    """返回现有表字段名，兼容不同阶段的测试数据库。"""

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def _drop_indexes_using(table_name: str, column_names: set[str]) -> None:
    """删除依赖待移除字段的普通索引或唯一索引。"""

    inspector = sa.inspect(op.get_bind())
    dropped: set[str] = set()
    for index in inspector.get_indexes(table_name):
        if set(index.get("column_names") or []) & column_names:
            op.drop_index(index["name"], table_name=table_name)
            dropped.add(index["name"])
    for constraint in inspector.get_unique_constraints(table_name):
        if set(constraint.get("column_names") or []) & column_names:
            name = constraint.get("name")
            if name and name not in dropped:
                op.drop_constraint(name, table_name, type_="unique")


def upgrade() -> None:
    """清除旧来源数据并建立官方归档全局序号结构。"""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 探马历史消息没有官方 archive_seq，不能与新链路安全混用。先清空依赖消息
    # 的滚动摘要，再清空消息；会话、人员、AI 回复和已审核知识继续保留。
    if inspector.has_table("group_summaries"):
        op.execute(sa.text("DELETE FROM group_summaries"))
    if inspector.has_table("messages"):
        op.execute(sa.text("DELETE FROM messages"))
        columns = _columns("messages")
        if "archive_seq" not in columns:
            op.add_column("messages", sa.Column("archive_seq", sa.BigInteger(), nullable=True))
        _drop_indexes_using("messages", {"provider", "source_key"})
        columns = _columns("messages")
        for column in ("provider", "source_key"):
            if column in columns:
                op.drop_column("messages", column)
        op.alter_column("messages", "archive_seq", existing_type=sa.BigInteger(), nullable=False)
        message_inspector = sa.inspect(bind)
        archive_unique_exists = any(
            bool(index.get("unique"))
            and index.get("column_names") == ["archive_seq"]
            for index in message_inspector.get_indexes("messages")
        ) or any(
            constraint.get("column_names") == ["archive_seq"]
            for constraint in message_inspector.get_unique_constraints("messages")
        )
        if not archive_unique_exists:
            op.create_unique_constraint("uq_messages_archive_seq", "messages", ["archive_seq"])

    if inspector.has_table("users"):
        columns = _columns("users")
        if "tanma_staff_id" in columns:
            _drop_indexes_using("users", {"tanma_staff_id"})
            op.drop_column("users", "tanma_staff_id")
        op.alter_column("users", "username", existing_type=sa.String(100), nullable=True)
        op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)
        op.alter_column(
            "users",
            "is_enabled",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )

    for table_name in ("sync_states", "provider_stream_states"):
        if inspector.has_table(table_name):
            op.drop_table(table_name)

    if not sa.inspect(bind).has_table("archive_sync_state"):
        op.create_table(
            "archive_sync_state",
            sa.Column("id", sa.SmallInteger(), primary_key=True),
            sa.Column("lower_seq", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("upper_seq", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("reached_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    """仅恢复旧字段和旧游标表；已清理的探马消息无法恢复。"""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("archive_sync_state"):
        op.drop_table("archive_sync_state")
    if not inspector.has_table("provider_stream_states"):
        op.create_table(
            "provider_stream_states",
            sa.Column("provider", sa.String(32), primary_key=True),
            sa.Column("upper_cursor", sa.String(255), nullable=False),
            sa.Column("reached_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if inspector.has_table("messages"):
        for index in sa.inspect(bind).get_indexes("messages"):
            if bool(index.get("unique")) and index.get("column_names") == ["archive_seq"]:
                op.drop_index(index["name"], table_name="messages")
                break
        op.add_column("messages", sa.Column("provider", sa.String(32), nullable=True))
        op.add_column("messages", sa.Column("source_key", sa.String(255), nullable=True))
        op.drop_column("messages", "archive_seq")
