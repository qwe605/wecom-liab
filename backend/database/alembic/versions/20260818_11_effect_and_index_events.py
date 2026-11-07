"""增加回复效果、检索轨迹和轻量索引 outbox 表。"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_11"
down_revision = "20260818_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建只追加事件与可重建索引任务表。"""

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "ai_reply_events" not in tables:
        op.create_table(
            "ai_reply_events",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("ai_reply_id", sa.BigInteger(), nullable=False),
            sa.Column("operator_userid", sa.String(length=128), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["ai_reply_id"], ["ai_replies.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["operator_userid"], ["users.wecom_userid"], ondelete="SET NULL"),
        )
        op.create_index("idx_ai_reply_events_reply_time", "ai_reply_events", ["ai_reply_id", "created_at", "id"])
    if "retrieval_runs" not in tables:
        op.create_table(
            "retrieval_runs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("ai_reply_id", sa.BigInteger(), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column("query_text", sa.Text(), nullable=False),
            sa.Column("intent_snapshot", sa.JSON(), nullable=False),
            sa.Column("scope_json", sa.JSON(), nullable=False),
            sa.Column("strategy_version", sa.String(length=64), nullable=False),
            sa.Column("embedding_model", sa.String(length=128), nullable=False),
            sa.Column("index_version", sa.String(length=128), nullable=False),
            sa.Column("ranking_rule_version", sa.String(length=64), nullable=False),
            sa.Column("selected_refs", sa.JSON(), nullable=False),
            sa.Column("timings_json", sa.JSON(), nullable=False),
            sa.Column("fallback_reason", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["ai_reply_id"], ["ai_replies.id"], ondelete="RESTRICT"),
        )
    if "retrieval_candidates" not in tables:
        op.create_table(
            "retrieval_candidates",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("retrieval_run_id", sa.BigInteger(), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=False),
            sa.Column("source_ref", sa.String(length=255), nullable=False),
            sa.Column("stage", sa.String(length=32), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("score", sa.Numeric(12, 6), nullable=True),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column("detail_json", sa.JSON(), nullable=False),
            sa.ForeignKeyConstraint(["retrieval_run_id"], ["retrieval_runs.id"], ondelete="RESTRICT"),
        )
        op.create_index("idx_retrieval_candidates_run_stage", "retrieval_candidates", ["retrieval_run_id", "stage", "rank"])
    if "skill_index_jobs" not in tables:
        op.create_table(
            "skill_index_jobs",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("skill_id", sa.BigInteger(), nullable=False),
            sa.Column("operation", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        )
        op.create_index("idx_skill_index_jobs_status_id", "skill_index_jobs", ["status", "id"])


def downgrade() -> None:
    """按依赖反序移除效果与索引表。"""

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table, index in (
        ("skill_index_jobs", "idx_skill_index_jobs_status_id"),
        ("retrieval_candidates", "idx_retrieval_candidates_run_stage"),
        ("retrieval_runs", None),
        ("ai_reply_events", "idx_ai_reply_events_reply_time"),
    ):
        if table in tables:
            if index:
                op.drop_index(index, table_name=table)
            op.drop_table(table)
