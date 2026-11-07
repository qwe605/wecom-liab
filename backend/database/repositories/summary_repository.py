"""统一会话摘要与通用 Skill 候选的数据访问。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from backend.database.models import (
    Conversation,
    ConversationCustomer,
    ConversationSummary,
    ConversationUser,
    Customer,
    Message,
    Skill,
    User,
)
from backend.database.repositories.conversation_repository import ConversationRepository


DAILY_SKILL_CANDIDATE_LIMIT = 10


class SummaryRepository:
    """封装会话增量摘要、待处理会话和通用 Skill 候选持久化。"""

    def __init__(self, session: Session):
        """绑定请求级 Session，Repository 不调用模型或判断通用性。"""

        self.session = session
        self.conversations = ConversationRepository(session)

    def context(
        self,
        conversation_id: int,
        staff_userid: str,
        limit: int = 200,
    ) -> tuple[Conversation, ConversationSummary | None, list[Message]]:
        """读取客服有权访问的会话、现有摘要和增量消息。"""

        conversation = self.conversations.get_authorized(conversation_id, staff_userid)
        return self._context(conversation, limit)

    def context_for_system(
        self,
        conversation_id: int,
        limit: int = 200,
    ) -> tuple[Conversation, ConversationSummary | None, list[Message]]:
        """为后台定期任务读取会话摘要上下文，不伪造客服身份。"""

        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError("会话不存在")
        return self._context(conversation, limit)

    def _context(
        self,
        conversation: Conversation,
        limit: int,
    ) -> tuple[Conversation, ConversationSummary | None, list[Message]]:
        """按摘要边界读取指定会话的新增文本消息。"""

        summary = self.session.get(ConversationSummary, conversation.id)
        statement = select(Message).where(
            Message.conversation_id == conversation.id,
            Message.content_text.is_not(None),
        )
        if summary is not None:
            statement = statement.where(Message.id > summary.last_message_id)
        messages = list(
            self.session.scalars(
                statement.order_by(Message.sent_at.asc(), Message.id.asc()).limit(
                    min(max(int(limit), 1), 500)
                )
            )
        )
        return conversation, summary, messages

    def get(
        self,
        conversation_id: int,
        staff_userid: str,
    ) -> tuple[Conversation, ConversationSummary | None]:
        """读取当前客服有权访问的单聊或群聊摘要。"""

        conversation = self.conversations.get_authorized(conversation_id, staff_userid)
        return conversation, self.session.get(ConversationSummary, conversation.id)

    def get_for_admin(
        self, conversation_id: int
    ) -> tuple[Conversation, ConversationSummary | None, int]:
        """读取管理员纪要视图及尚未被摘要覆盖的文本消息数量。"""

        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError("会话不存在")
        summary = self.session.get(ConversationSummary, conversation_id)
        boundary = summary.last_message_id if summary else 0
        uncovered = int(
            self.session.scalar(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation_id,
                    Message.content_text.is_not(None),
                    Message.id > boundary,
                )
            )
            or 0
        )
        return conversation, summary, uncovered

    def save_manual_summary(
        self,
        conversation_id: int,
        expected_last_message_id: int,
        summary_text: str,
        analysis: dict[str, Any],
    ) -> ConversationSummary:
        """保存人工修订内容，但保持摘要消息边界不变。"""

        summary = self.session.get(ConversationSummary, conversation_id)
        if summary is None:
            raise LookupError("当前会话尚未生成纪要，请先刷新纪要")
        if summary.last_message_id != expected_last_message_id:
            raise ValueError("会话纪要已更新，请重新加载后再编辑")
        summary.summary_text = summary_text
        summary.analysis_json = analysis
        self.session.flush()
        return summary

    def create_manual_skill_candidate(
        self,
        conversation_id: int,
        title: str,
        content: str,
        taxonomy_path: list[dict[str, str]],
        source_message_ids: list[int],
        submitted_by: str,
    ) -> Skill:
        """校验证据归属后创建固定为 pending 的人工候选。"""

        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError("会话不存在")
        unique_ids = list(dict.fromkeys(int(item) for item in source_message_ids))
        actual_ids = set(
            self.session.scalars(
                select(Message.id).where(
                    Message.conversation_id == conversation_id,
                    Message.id.in_(unique_ids),
                )
            )
        )
        if len(actual_ids) != len(unique_ids):
            raise ValueError("证据消息不存在或不属于当前会话")
        candidate = Skill(
            source_conversation_id=conversation_id,
            source_message_ids=unique_ids,
            title=title[:255],
            content=content,
            taxonomy_path=taxonomy_path,
            quality_score=0,
            status="pending",
            submitted_by=submitted_by,
        )
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def stale_conversation_ids(
        self,
        limit: int,
        min_new_messages: int = 20,
    ) -> list[int]:
        """返回新增消息达到阈值的群聊会话。

        后台维护只处理群聊，并由新增消息数量而非固定时间决定是否调用模型。
        单聊和不足阈值的群聊仍可在生成回复前通过按需入口更新摘要。
        """

        rows = self.session.execute(
            select(Message.conversation_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .outerjoin(
                ConversationSummary,
                ConversationSummary.conversation_id == Message.conversation_id,
            )
            .where(
                Conversation.conversation_type == "group",
                Message.content_text.is_not(None),
                Message.id > func.coalesce(ConversationSummary.last_message_id, 0),
            )
            .group_by(Message.conversation_id)
            .having(func.count(Message.id) >= max(int(min_new_messages), 1))
            .order_by(func.min(Message.sent_at).asc(), Message.conversation_id.asc())
            .limit(min(max(int(limit), 1), 100))
        )
        return [int(row[0]) for row in rows]

    def sensitive_terms(self, conversation: Conversation) -> set[str]:
        """返回候选中不能出现的客户、员工和会话专属名称。"""

        terms = {str(conversation.name or "").strip()}
        terms.update(
            str(value or "").strip()
            for value in self.session.scalars(
                select(User.name)
                .join(
                    ConversationUser,
                    ConversationUser.wecom_userid == User.wecom_userid,
                )
                .where(ConversationUser.conversation_id == conversation.id)
            )
        )
        terms.update(
            str(value or "").strip()
            for value in self.session.scalars(
                select(Customer.name)
                .join(
                    ConversationCustomer,
                    ConversationCustomer.external_userid == Customer.external_userid,
                )
                .where(ConversationCustomer.conversation_id == conversation.id)
            )
        )
        return {item for item in terms if len(item) >= 2 and item != "当前客户"}

    def published_review_policies(self, limit: int = 100) -> list[dict[str, str]]:
        """返回当前已发布审核规则，供候选提炼阶段读取。

        规则数量和正文长度均设硬边界，避免审核规则增长后无界扩大模型输入；
        未发布、已驳回和已归档规则不会影响任何候选。
        """

        rows = list(
            self.session.scalars(
                select(Skill)
                .where(
                    Skill.status == "published",
                    cast(Skill.taxonomy_path, Text).like("%review_policy%"),
                )
                .order_by(Skill.id.asc())
                .limit(max(1, min(int(limit), 100)))
            )
        )
        return [
            {"id": str(row.id), "title": row.title, "content": row.content[:4000]}
            for row in rows
        ]

    def save_summary(
        self,
        conversation: Conversation,
        current: ConversationSummary | None,
        summary_text: str,
        analysis: dict[str, Any],
        last_message_id: int,
    ) -> ConversationSummary:
        """保存会话纪要和增量边界，不在此步骤创建 Skill。"""

        if current is None:
            current = ConversationSummary(
                conversation_id=conversation.id,
                summary_text=summary_text,
                analysis_json=analysis,
                last_message_id=last_message_id,
            )
            self.session.add(current)
        else:
            current.summary_text = summary_text
            current.analysis_json = analysis
            current.last_message_id = last_message_id
        self.session.flush()
        return current

    def save_skill_candidates(
        self,
        conversation: Conversation,
        candidates: list[dict[str, Any]],
        submitted_by: str | None,
    ) -> list[Skill]:
        """保存已由 Service 判定为通用内容的待审核 Skill 候选。"""

        # 候选产出是全局配额而非单会话配额，防止多个群聊并发摘要绕过限制。
        # 产品运营按中国标准时间统计“每天”，再转换为 MySQL 保存的 UTC 无时区值。
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        daily_start = local_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).replace(tzinfo=None)
        created_today = int(
            self.session.scalar(
                select(func.count(Skill.id)).where(Skill.created_at >= daily_start)
                .where(Skill.source_conversation_id.is_not(None))
            )
            or 0
        )
        remaining = max(0, DAILY_SKILL_CANDIDATE_LIMIT - created_today)
        saved: list[Skill] = []
        for item in candidates:
            title = str(item["title"]).strip()[:255]
            content = str(item["content"]).strip()
            existing = self.session.scalar(
                select(Skill)
                .where(
                    Skill.title == title,
                    Skill.status.in_(("pending", "published")),
                )
                .order_by(Skill.id.desc())
                .limit(1)
            )
            if existing is not None and existing.status == "published":
                continue
            # 同标题候选已由另一条会话或优质 AI 回复提交时不重复创建，也不覆盖
            # 原始溯源；只有当前会话之前生成的摘要候选允许增量更新。
            if existing is not None and (
                existing.source_conversation_id != conversation.id
                or existing.ai_reply_id is not None
            ):
                continue
            if existing is None:
                if remaining <= 0:
                    # 达到每日上限后不丢失会话摘要，只跳过本次新增候选。
                    continue
                existing = Skill(
                    source_conversation_id=conversation.id,
                    source_message_ids=list(item["source_message_ids"]),
                    title=title,
                    content=content,
                    taxonomy_path=list(item["taxonomy_path"]),
                    quality_score=float(item["quality_score"]),
                    status="pending",
                    submitted_by=submitted_by,
                )
                self.session.add(existing)
                remaining -= 1
            else:
                existing.content = content
                existing.quality_score = float(item["quality_score"])
                existing.source_message_ids = list(item["source_message_ids"])
                existing.taxonomy_path = list(item["taxonomy_path"])
            saved.append(existing)
        self.session.flush()
        return saved
