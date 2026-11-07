"""企业微信归档消息和全局同步边界的 ORM 访问。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.database.models import (
    ArchiveSyncState,
    Conversation,
    Customer,
    Message,
    User,
)
from backend.database.repositories.conversation_repository import ConversationRepository


class MessageRepository:
    """封装归档消息幂等写入、查询和全局游标访问。"""

    def __init__(self, session: Session):
        """绑定请求级 Session，保证整页消息和游标处于同一事务。"""

        self.session = session

    def list_recent(self, conversation_id: int, limit: int = 100) -> list[Message]:
        """按时间正序返回指定会话最近消息。

        Args:
            conversation_id: 数据库会话主键。
            limit: 最大返回条数，限制在 1 到 1000。

        Returns:
            由旧到新排列的消息实体。
        """

        safe_limit = min(max(int(limit), 1), 1000)
        rows = list(
            self.session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sent_at.desc(), Message.id.desc())
                .limit(safe_limit)
            )
        )
        rows.reverse()
        return rows

    def list_for_admin(
        self,
        conversation_id: int,
        start_at: datetime | None,
        end_at: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Message], int]:
        """分页查询指定会话的消息明细。

        Args:
            conversation_id: 会话数据库主键。
            start_at: 可选消息开始时间。
            end_at: 可选消息结束时间。
            page: 从 1 开始的页码。
            page_size: 每页消息数量。

        Returns:
            ``(当前页消息实体, 筛选结果总数)``。
        """

        conditions = [Message.conversation_id == conversation_id]
        if start_at is not None:
            conditions.append(Message.sent_at >= start_at)
        if end_at is not None:
            conditions.append(Message.sent_at <= end_at)
        total = int(
            self.session.scalar(
                select(func.count(Message.id)).where(*conditions)
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                select(Message)
                .where(*conditions)
                .order_by(Message.sent_at, Message.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def admin_public_list(self, messages: list[Message]) -> list[dict[str, Any]]:
        """把消息实体转换为管理后台使用的稳定结构。

        Args:
            messages: 当前页消息实体。

        Returns:
            含真实姓名、正文和 JSON 的管理端消息字典。
        """

        names = self._sender_names(messages)
        result: list[dict[str, Any]] = []
        for message in messages:
            sender_id = message.sender_wecom_userid or message.sender_external_userid or ""
            sender_type = "staff" if message.sender_wecom_userid else "customer"
            result.append(
                {
                    "id": message.id,
                    "conversation_id": message.conversation_id,
                    "archive_seq": message.archive_seq,
                    "sent_at": message.sent_at,
                    "sender_type": sender_type,
                    "sender_id": sender_id,
                    "sender_name": names.get(sender_id) or sender_id,
                    "message_type": message.message_type,
                    "content": message.content_text or "",
                    "content_json": message.content_json or {},
                }
            )
        return result

    def iter_admin_export(
        self,
        conversation_type: str | None,
        keyword: str,
        start_at: datetime | None,
        end_at: datetime | None,
        conversation_id: int | None = None,
        batch_size: int = 500,
    ):
        """按稳定键集分页返回聊天记录导出批次。

        Args:
            conversation_type: 可选会话类型。
            keyword: 会话或参与人检索词。
            start_at: 可选消息开始时间。
            end_at: 可选消息结束时间。
            conversation_id: 可选的单个会话数据库主键。
            batch_size: 单次数据库读取行数。

        Yields:
            ``(消息实体, 企业微信会话 ID, 会话类型)`` 元组列表。
        """

        last_sent_at: datetime | None = None
        last_id = 0
        while True:
            conditions = ConversationRepository.admin_filter_conditions(
                conversation_type,
                keyword,
            )
            if conversation_id is not None:
                conditions.append(Conversation.id == conversation_id)
            if start_at is not None:
                conditions.append(Message.sent_at >= start_at)
            if end_at is not None:
                conditions.append(Message.sent_at <= end_at)
            if last_sent_at is not None:
                conditions.append(
                    or_(
                        Message.sent_at > last_sent_at,
                        and_(Message.sent_at == last_sent_at, Message.id > last_id),
                    )
                )
            rows = self.session.execute(
                select(
                    Message,
                    Conversation.wecom_chat_id,
                    Conversation.conversation_type,
                )
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(*conditions)
                .order_by(Message.sent_at, Message.id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            yield rows
            last_message = rows[-1][0]
            last_sent_at = last_message.sent_at
            last_id = int(last_message.id)

    def upsert_normalized(
        self,
        conversation_id: int,
        payload: dict[str, Any],
    ) -> tuple[Message, bool]:
        """按企业微信全局归档序号幂等保存一条规范消息。

        Args:
            conversation_id: 消息所属内部会话主键。
            payload: Integration 输出的规范消息，必须包含 ``archive_seq``。

        Returns:
            ``(消息实体, 是否新建)``。

        Raises:
            ValueError: 缺少有效归档序号。
        """

        archive_seq = int(payload.get("archive_seq") or payload.get("seq") or 0)
        if archive_seq <= 0:
            raise ValueError("企业微信归档消息缺少有效 archive_seq")
        existing = self.session.scalar(
            select(Message).where(Message.archive_seq == archive_seq)
        )
        if existing is not None:
            return existing, False

        sender_type = str(payload.get("sender_type") or "")
        sender_id = str(payload.get("from") or "")
        content_json = dict(payload.get("content_json") or {})
        content_json.setdefault("from", sender_id)
        content_json.setdefault("sender_type", sender_type)

        message = Message(
            conversation_id=conversation_id,
            archive_seq=archive_seq,
            sender_wecom_userid=sender_id if sender_type == "staff" else None,
            sender_external_userid=sender_id if sender_type == "customer" else None,
            message_type=str(payload.get("msgtype") or "text"),
            content_json=content_json,
            content_text=str(payload.get("content") or "") or None,
            sent_at=self._parse_datetime(payload.get("msgtime")),
        )
        self.session.add(message)
        self.session.flush()
        return message, True

    def get_archive_state(self, seed_seq: int) -> ArchiveSyncState:
        """读取并锁定唯一企业微信归档同步状态。

        Args:
            seed_seq: 首次运行时使用的官方归档起始序号。

        Returns:
            当前事务独占更新的单例状态。
        """

        state = self.session.scalar(
            select(ArchiveSyncState)
            .where(ArchiveSyncState.id == 1)
            .with_for_update()
        )
        if state is None:
            initial = max(0, int(seed_seq))
            state = ArchiveSyncState(
                id=1,
                lower_seq=initial,
                upper_seq=initial,
                reached_end=False,
            )
            self.session.add(state)
            self.session.flush()
        return state

    def conversation_coverage(self, conversation_id: int) -> dict[str, Any]:
        """计算当前会话已经落库的时间和归档序号范围。

        Args:
            conversation_id: 数据库会话主键。

        Returns:
            包含最早、最新时间与最小、最大归档序号的字典。
        """

        oldest_at, newest_at, lower_seq, upper_seq = self.session.execute(
            select(
                func.min(Message.sent_at),
                func.max(Message.sent_at),
                func.min(Message.archive_seq),
                func.max(Message.archive_seq),
            ).where(Message.conversation_id == conversation_id)
        ).one()
        return {
            "oldest_at": oldest_at,
            "newest_at": newest_at,
            "lower_seq": int(lower_seq or 0),
            "upper_seq": int(upper_seq or 0),
        }

    def public_list(self, messages: list[Message]) -> list[dict[str, Any]]:
        """批量补齐消息发送者姓名并转换为前端结构。

        Args:
            messages: 同一会话内待展示的消息实体。

        Returns:
            已使用员工表或客户表真实姓名补齐的消息字典列表。
        """

        names = self._sender_names(messages)
        return [
            self.public_dict(
                message,
                names.get(
                    message.sender_wecom_userid
                    or message.sender_external_userid
                    or "",
                    "",
                ),
            )
            for message in messages
        ]

    def _sender_names(self, messages: list[Message]) -> dict[str, str]:
        """批量加载一组消息涉及的员工和客户姓名。

        Args:
            messages: 待补齐姓名的消息实体。

        Returns:
            以企业微信官方 ID 为键的姓名字典。
        """

        staff_ids = {
            item.sender_wecom_userid
            for item in messages
            if item.sender_wecom_userid
        }
        customer_ids = {
            item.sender_external_userid
            for item in messages
            if item.sender_external_userid
        }
        names: dict[str, str] = {}
        if staff_ids:
            names.update(
                self.session.execute(
                    select(User.wecom_userid, User.name).where(
                        User.wecom_userid.in_(staff_ids)
                    )
                ).all()
            )
        if customer_ids:
            names.update(
                self.session.execute(
                    select(Customer.external_userid, Customer.name).where(
                        Customer.external_userid.in_(customer_ids)
                    )
                ).all()
            )
        return names

    @staticmethod
    def public_dict(message: Message, resolved_sender_name: str = "") -> dict[str, Any]:
        """转换为侧边栏使用的稳定消息结构。

        Args:
            message: ORM 消息实体。

        Returns:
            不暴露 ORM 对象的 JSON 字典。
        """

        content_json = message.content_json or {}
        sender_type = str(
            content_json.get("sender_type")
            or ("staff" if message.sender_wecom_userid else "customer")
        )
        sender_id = (
            message.sender_wecom_userid
            or message.sender_external_userid
            or str(content_json.get("from") or "")
        )
        return {
            "source_message_id": str(message.archive_seq),
            "archive_seq": message.archive_seq,
            "seq": message.archive_seq,
            "msgtime": message.sent_at.isoformat(),
            "msgtype": message.message_type,
            "sender_id": sender_id,
            "sender_name": str(
                resolved_sender_name
                or content_json.get("sender_name")
                or sender_id
            ),
            "sender_type": sender_type,
            "direction": "outgoing" if sender_type == "staff" else "incoming",
            "content": message.content_text or "",
            "content_json": content_json,
        }

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        """把 Integration 时间转换为 MySQL 使用的无时区时间。

        Args:
            value: ISO 时间文本或 ``datetime``。

        Returns:
            无时区 ``datetime``。

        Raises:
            ValueError: 非空文本无法解析时抛出。
        """

        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text = str(value or "").strip()
        if not text:
            return datetime.utcnow()
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).replace(tzinfo=None)
        except ValueError:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
