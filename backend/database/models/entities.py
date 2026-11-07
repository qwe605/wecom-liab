"""客服宝 MySQL ORM 实体。

模型严格对应项目设计书中的十张核心表。企业微信会话内容存档是唯一消息来源，
消息以官方全局 ``archive_seq`` 幂等，系统只保存一条企业级同步状态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database.base import Base


def utcnow() -> datetime:
    """返回用于 MySQL DATETIME 的无时区 UTC 时间。"""

    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """登录用户和企业微信员工映射。"""

    __tablename__ = "users"

    wecom_userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="staff")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


class Customer(Base):
    """企业微信外部联系人。"""

    __tablename__ = "customers"

    external_userid: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="当前客户")


class Conversation(Base):
    """统一表示单聊和外部群聊。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    wecom_chat_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    conversation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))


class ConversationUser(Base):
    """会话与员工的多对多关联。"""

    __tablename__ = "conversation_users"

    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    wecom_userid: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.wecom_userid", ondelete="RESTRICT"),
        primary_key=True,
    )


class ConversationCustomer(Base):
    """会话与外部联系人的多对多关联。"""

    __tablename__ = "conversation_customers"

    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    external_userid: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("customers.external_userid", ondelete="RESTRICT"),
        primary_key=True,
    )


class Message(Base):
    """企业微信会话内容存档中的一条已解密消息。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_conversation_time", "conversation_id", "sent_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    archive_seq: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    sender_wecom_userid: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="SET NULL")
    )
    sender_external_userid: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("customers.external_userid", ondelete="SET NULL")
    )
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_text: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ArchiveSyncState(Base):
    """企业微信归档全局消息流的唯一同步边界。"""

    __tablename__ = "archive_sync_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    lower_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    upper_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reached_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AIReply(Base):
    """一次问题提取、AI 建议和客服采纳结果。"""

    __tablename__ = "ai_replies"
    __table_args__ = (Index("idx_ai_replies_conversation_time", "conversation_id", "generated_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    operator_userid: Mapped[str] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="RESTRICT"), nullable=False
    )
    question_summary: Mapped[str] = mapped_column(Text, nullable=False)
    context_message_ids: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=list
    )
    generated_reply: Mapped[str] = mapped_column(Text, nullable=False)
    final_reply: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="generated")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )


class ConversationSummary(Base):
    """每个单聊或群聊的一份滚动增量摘要。"""

    __tablename__ = "conversation_summaries"

    conversation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )


class Skill(Base):
    """统一保存知识候选、审核结果和正式发布内容。"""

    __tablename__ = "skills"
    __table_args__ = (
        Index("idx_skills_status_score", "status", "quality_score"),
        Index("idx_skills_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    source_conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="SET NULL")
    )
    ai_reply_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_replies.id", ondelete="SET NULL")
    )
    source_message_ids: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=list
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    taxonomy_path: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    submitted_by: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="SET NULL")
    )
    reviewer_userid: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="SET NULL")
    )
    review_opinion: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class SkillReviewEvent(Base):
    """不可变 Skill 审核事件，保存每次变更前后的完整证据。"""

    __tablename__ = "skill_review_events"
    __table_args__ = (
        Index("idx_skill_review_events_skill_time", "skill_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    skill_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_userid: Mapped[str] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    opinion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    governance_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )


class ReviewPolicySource(Base):
    """审核规则 Skill 与其原始审核事件之间的不可变来源关联。

    一条审核规则可以在后续人工归并时引用多个通过/驳回事件，同一个审核事件
    只自动生成一条初始规则草案。关联使用独立表而不是把事件 ID 塞进正文，
    这样查看规则时可以稳定加载当时的结构化原因和前后快照。
    """

    __tablename__ = "review_policy_sources"
    __table_args__ = (
        Index("idx_review_policy_sources_event", "review_event_id"),
    )

    policy_skill_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("skills.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    review_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("skill_review_events.id", ondelete="RESTRICT"),
        primary_key=True,
        unique=True,
    )


class AIReplyEvent(Base):
    """客服对 AI 回复执行的一次不可变操作事件。"""

    __tablename__ = "ai_reply_events"
    __table_args__ = (
        Index("idx_ai_reply_events_reply_time", "ai_reply_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ai_reply_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_replies.id", ondelete="RESTRICT"), nullable=False
    )
    operator_userid: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("users.wecom_userid", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RetrievalRun(Base):
    """一次 AI 回复检索的可复现运行摘要。"""

    __tablename__ = "retrieval_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ai_reply_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ai_replies.id", ondelete="RESTRICT"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    intent_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    index_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ranking_rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    selected_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    timings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fallback_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class RetrievalCandidate(Base):
    """检索运行中某个来源在一个阶段的排名记录。"""

    __tablename__ = "retrieval_candidates"
    __table_args__ = (
        Index("idx_retrieval_candidates_run_stage", "retrieval_run_id", "stage", "rank"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    retrieval_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("retrieval_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class SkillIndexJob(Base):
    """业务事务写入、轻量向量索引进程异步消费的 outbox。"""

    __tablename__ = "skill_index_jobs"
    __table_args__ = (Index("idx_skill_index_jobs_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    skill_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
