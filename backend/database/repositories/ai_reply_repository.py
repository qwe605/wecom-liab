"""AI 回复的 SQLAlchemy ORM 仓储。

本模块只依赖客服宝核心业务表，不使用 PostgreSQL 专属 SQL 或额外生成任务表。
消息读取、权限校验和回复保存都在调用方提供的事务会话内完成。
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.database.models import (
    AIReply,
    AIReplyEvent,
    Conversation,
    ConversationCustomer,
    ConversationUser,
    Customer,
    ConversationSummary,
    Message,
    RetrievalCandidate,
    RetrievalRun,
    User,
)
from backend.database.session import get_session_factory
from backend.schemas.identifiers import parse_public_conversation_id

from backend.schemas.ai import (
    ChatMessage,
    ConversationContext,
    GeneratedReply,
    KnowledgeReference,
    ReplyRequest,
)


class AIReplyRepository(Protocol):
    """AI 回复服务依赖的最小仓储契约。"""

    def load_context(self, request: ReplyRequest, limit: int = 5) -> ConversationContext:
        """按同一客户原始消息数量加载有权限的意图上下文。"""

    def save_suggestion(
        self,
        request: ReplyRequest,
        context: ConversationContext,
        generated: GeneratedReply,
        references: list[KnowledgeReference],
        prompt_version: str,
        retrieval_trace: dict[str, Any],
    ) -> dict[str, Any]:
        """保存 AI 建议并返回前端元数据。"""

    def latest_suggestion(self, request: ReplyRequest) -> dict[str, Any] | None:
        """读取当前客服在当前会话的最新建议。"""

    def record_action(
        self,
        staff_userid: str,
        suggestion_id: int,
        action: str,
        edited_text: str = "",
    ) -> dict[str, Any]:
        """保存客服采纳、编辑或拒绝结果。"""


class SQLAlchemyAIReplyRepository:
    """基于 SQLAlchemy Session 工厂的 MySQL 仓储实现。"""

    def __init__(self, session_factory=None):
        """初始化 AI 回复仓储，并支持测试注入 Session 工厂。"""
        self.session_factory = session_factory or get_session_factory()

    def _resolve(
        self,
        session: Session,
        request: ReplyRequest,
    ) -> tuple[Conversation, User, str]:
        """解析会话、验证成员权限并返回展示名称。"""

        conversation_id = parse_public_conversation_id(request.public_conversation_id)
        row = session.execute(
            select(Conversation, User)
            .join(ConversationUser, ConversationUser.conversation_id == Conversation.id)
            .join(User, User.wecom_userid == ConversationUser.wecom_userid)
            .where(
                Conversation.id == conversation_id,
                User.wecom_userid == request.staff_userid,
                User.is_enabled.is_(True),
            )
        ).first()
        if row is None:
            raise PermissionError("当前客服无权访问该会话，或会话尚未导入数据库")
        conversation, user = row
        customer_name = (
            conversation.name or "当前群聊"
            if conversation.conversation_type == "group"
            else "当前客户"
        )
        customer = session.scalar(
            select(Customer)
            .join(
                ConversationCustomer,
                ConversationCustomer.external_userid == Customer.external_userid,
            )
            .where(ConversationCustomer.conversation_id == conversation.id)
            .limit(1)
        )
        if customer is not None:
            customer_name = customer.name
        return conversation, user, customer_name

    def load_context(self, request: ReplyRequest, limit: int = 5) -> ConversationContext:
        """加载同一问题发起人的渐进消息窗口和最新滚动摘要。

        Args:
            request: 当前客服和会话标识。
            limit: 目标外部联系人的原始消息数量，通常为 5、10 或 20。

        Returns:
            目标客户消息及其时间区间内员工回复组成的正序上下文。

        Raises:
            PermissionError: 当前客服无会话访问权限。
            ValueError: 会话中没有可识别的客户文本消息。
        """

        safe_limit = min(max(int(limit), 1), 20)
        with self.session_factory() as session:
            conversation, user, customer_name = self._resolve(session, request)
            latest_customer = session.scalar(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.sender_external_userid.is_not(None),
                    Message.content_text.is_not(None),
                )
                .order_by(Message.sent_at.desc(), Message.id.desc())
                .limit(1)
            )
            if latest_customer is None or not latest_customer.sender_external_userid:
                raise ValueError("当前上下文中没有客户消息，暂不生成回复")

            requester_id = latest_customer.sender_external_userid
            requester = session.get(Customer, requester_id)
            if requester is not None:
                customer_name = requester.name

            anchors = list(
                session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender_external_userid == requester_id,
                        Message.content_text.is_not(None),
                    )
                    .order_by(Message.sent_at.desc(), Message.id.desc())
                    .limit(safe_limit)
                )
            )
            boundary = anchors[-1]
            statement = select(Message).where(
                Message.conversation_id == conversation.id,
                Message.content_text.is_not(None),
                or_(
                    Message.sender_external_userid == requester_id,
                    Message.sender_wecom_userid.is_not(None),
                ),
                or_(
                    Message.sent_at > boundary.sent_at,
                    and_(
                        Message.sent_at == boundary.sent_at,
                        Message.id >= boundary.id,
                    ),
                ),
            )
            rows = list(
                session.scalars(
                    statement.order_by(Message.sent_at.desc(), Message.id.desc()).limit(200)
                )
            )
            rows.reverse()
            summary = session.get(ConversationSummary, conversation.id)

        messages = [
            ChatMessage(
                id=row.id,
                sender_kind=(
                    "internal_staff"
                    if str((row.content_json or {}).get("sender_type") or "") == "staff"
                    or row.sender_wecom_userid
                    else "external_contact"
                ),
                sender_name=str((row.content_json or {}).get("sender_name") or ""),
                content=row.content_text or "",
                created_at=row.sent_at,
                sender_id=(
                    row.sender_wecom_userid
                    or row.sender_external_userid
                    or str((row.content_json or {}).get("from") or "")
                ),
                message_type=row.message_type,
            )
            for row in rows
        ]
        trigger = next(
            (item.id for item in reversed(messages) if item.sender_kind == "external_contact"),
            None,
        )
        return ConversationContext(
            database_conversation_id=conversation.id,
            staff_id=0,
            staff_userid=user.wecom_userid,
            conversation_type=(
                "external_group" if conversation.conversation_type == "group" else "external_single"
            ),
            customer_name=customer_name,
            messages=messages,
            existing_summary=summary.summary_text if summary else "",
            trigger_message_id=trigger,
            requester_external_userid=requester_id,
            customer_message_count=len(anchors),
        )

    def save_suggestion(
        self,
        request: ReplyRequest,
        context: ConversationContext,
        generated: GeneratedReply,
        references: list[KnowledgeReference],
        prompt_version: str,
        retrieval_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存问题摘要和 AI 原始建议。

        回复、检索摘要和生成事件在同一事务内提交，确保效果统计可追溯。
        """

        allowed_ids = set(generated.used_knowledge_ids)
        used_refs = [item.public_dict() for item in references if item.reference_id in allowed_ids]
        with self.session_factory.begin() as session:
            reply = AIReply(
                conversation_id=context.database_conversation_id,
                operator_userid=request.staff_userid,
                question_summary=generated.summary,
                context_message_ids=[item.id for item in context.messages],
                generated_reply=generated.suggested_reply,
                status="generated",
            )
            session.add(reply)
            session.flush()
            trace = dict(retrieval_trace or {})
            candidates = [item for item in trace.get("candidates", []) if isinstance(item, dict)]
            retrieval_details = [
                item.get("retrieval", {})
                for item in candidates
                if isinstance(item.get("retrieval"), dict)
            ]
            first_details = next((item for item in retrieval_details if item), {})
            fallback_reason = str(trace.get("fallback_reason") or "")
            if not fallback_reason:
                fallback_reason = next(
                    (str(item.get("fallback_reason")) for item in retrieval_details if item.get("fallback_reason")),
                    "",
                )
            run = RetrievalRun(
                ai_reply_id=reply.id,
                trace_id=uuid4().hex,
                query_text=str(trace.get("query") or ""),
                intent_snapshot=dict(trace.get("intent") or {}),
                scope_json=dict(trace.get("scope") or {}),
                strategy_version=str(first_details.get("strategy_version") or "routed-knowledge-v1"),
                embedding_model=str(first_details.get("embedding_model") or ""),
                index_version=str(first_details.get("index_version") or ""),
                ranking_rule_version="deterministic-v1",
                selected_refs=list(generated.used_knowledge_ids),
                timings_json=dict(trace.get("timings") or {}),
                fallback_reason=fallback_reason,
            )
            session.add(run)
            session.flush()
            for item in candidates:
                details = item.get("retrieval") if isinstance(item.get("retrieval"), dict) else {}
                source_ref = str(item.get("source_ref") or "")
                stages = []
                if details.get("lexical_rank"):
                    stages.append(("lexical", int(details["lexical_rank"]), details.get("lexical_score")))
                if details.get("vector_rank"):
                    stages.append(("dense", int(details["vector_rank"]), details.get("vector_score")))
                if details.get("rrf_score") is not None:
                    stages.append((
                        "fused",
                        int(details.get("final_rank") or item.get("final_rank") or 1),
                        details.get("rrf_score"),
                    ))
                stages.append(("rule_ranked", int(item.get("final_rank") or 1), details.get("rrf_score")))
                if source_ref in allowed_ids:
                    stages.append(("selected", int(item.get("final_rank") or 1), details.get("rrf_score")))
                for stage, rank, score in stages:
                    session.add(RetrievalCandidate(
                        retrieval_run_id=run.id,
                        source_type=str(item.get("source_type") or "unknown"),
                        source_ref=source_ref,
                        stage=stage,
                        rank=rank,
                        score=float(score) if score is not None else None,
                        reason="模型采用" if stage == "selected" else "",
                        detail_json={"title": str(item.get("title") or "")},
                    ))
            session.add_all([
                AIReplyEvent(
                    ai_reply_id=reply.id,
                    operator_userid=request.staff_userid,
                    event_type="generated",
                    payload_json={
                        "model_name": generated.model_name,
                        "prompt_version": prompt_version,
                        "latency_ms": generated.latency_ms,
                        "retrieval_trace_id": run.trace_id,
                    },
                ),
                AIReplyEvent(
                    ai_reply_id=reply.id,
                    operator_userid=request.staff_userid,
                    event_type="displayed",
                    payload_json={"source": "generate_response"},
                ),
            ])
            result = {
                "suggestion_id": reply.id,
                "status": reply.status,
                "created_at": reply.generated_at.isoformat(),
                "knowledge_refs": used_refs,
                "retrieval_trace_id": run.trace_id,
            }
        return result

    def latest_suggestion(self, request: ReplyRequest) -> dict[str, Any] | None:
        """查询当前会话最新建议。"""

        with self.session_factory() as session:
            conversation, _, customer_name = self._resolve(session, request)
            reply = session.scalar(
                select(AIReply)
                .where(
                    AIReply.conversation_id == conversation.id,
                    AIReply.operator_userid == request.staff_userid,
                )
                .order_by(AIReply.generated_at.desc(), AIReply.id.desc())
                .limit(1)
            )
            if reply is None:
                return None
            return {
                "suggestion_id": reply.id,
                "customer_name": customer_name,
                "suggested_reply": reply.final_reply or reply.generated_reply,
                "status": reply.status,
                "knowledge_refs": [],
                "created_at": reply.generated_at.isoformat(),
            }

    def record_action(
        self,
        staff_userid: str,
        suggestion_id: int,
        action: str,
        edited_text: str = "",
    ) -> dict[str, Any]:
        """保存客服对建议的最终操作。

        `copy/use/send` 统一视为进入采纳流程；`discard` 视为拒绝；编辑和重新生成
        不改变状态，但会保存客服当前文本。
        """

        if action not in {
            "copy",
            "use",
            "send",
            "discard",
            "edit",
            "regenerate",
            "helpful",
            "unhelpful",
        }:
            raise ValueError("unsupported reply action")
        with self.session_factory.begin() as session:
            reply = session.scalar(
                select(AIReply).where(
                    AIReply.id == suggestion_id,
                    AIReply.operator_userid == staff_userid,
                )
            )
            if reply is None:
                raise PermissionError("回复建议不存在或不属于当前客服")
            final_text = edited_text.strip()
            if final_text:
                reply.final_reply = final_text
            if action in {"copy", "use", "send"}:
                reply.status = "adopted"
            elif action == "discard":
                reply.status = "rejected"
            normalized_generated = re.sub(r"\s+", "", reply.generated_reply or "")
            normalized_final = re.sub(r"\s+", "", final_text or reply.generated_reply or "")
            edit_ratio = 1.0 - SequenceMatcher(
                None, normalized_generated, normalized_final
            ).ratio()
            if action in {"copy", "use"}:
                event_type = "adopted_edited" if edit_ratio > 0.01 else "adopted_unchanged"
            else:
                event_type = {
                    "send": "sent",
                    "discard": "abandoned",
                    "regenerate": "regenerated",
                    "edit": "edited",
                    "helpful": "helpful",
                    "unhelpful": "reported_error",
                }[action]
            session.add(AIReplyEvent(
                ai_reply_id=reply.id,
                operator_userid=staff_userid,
                event_type=event_type,
                payload_json={
                    "source_action": action,
                    "edit_ratio": round(edit_ratio, 6),
                    "final_text": final_text,
                },
            ))
            session.flush()
            return {
                "suggestion_id": reply.id,
                "status": reply.status,
                "edited_text": reply.final_reply or "",
            }


def repository_from_env() -> SQLAlchemyAIReplyRepository:
    """构造当前 MySQL AI 回复仓储。"""

    return SQLAlchemyAIReplyRepository()
