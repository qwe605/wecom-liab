"""会话、员工和客户关联的 ORM 访问。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from backend.database.models import (
    Conversation,
    ConversationCustomer,
    ConversationUser,
    Customer,
    Message,
    User,
)


class ConversationRepository:
    """封装单聊和外部群聊的统一持久化规则。"""

    def __init__(self, session: Session):
        """绑定请求级数据库 Session。"""
        self.session = session

    def get_authorized_by_identity(
        self,
        wecom_userid: str,
        conversation_type: str,
        external_userid: str = "",
        chat_id: str = "",
    ) -> Conversation:
        """按会话身份读取已有成员的会话，禁止浏览器请求自行授予权限。

        成员关系只能由可信归档/资料同步链路建立。标识未知、缺少成员关系或类型
        不匹配时拒绝访问，不创建会话、客户或成员关联，也不区分目标是否存在。
        """
        if conversation_type == "group" and chat_id:
            identity = chat_id
        elif conversation_type == "single" and external_userid:
            identity = f"single:{wecom_userid}:{external_userid}"
        else:
            raise ValueError("会话类型或必要标识无效")
        conversation = self.session.scalar(
            select(Conversation)
            .join(ConversationUser, ConversationUser.conversation_id == Conversation.id)
            .where(
                Conversation.wecom_chat_id == identity,
                Conversation.conversation_type == conversation_type,
                ConversationUser.wecom_userid == wecom_userid,
            )
        )
        if conversation is None:
            raise PermissionError("当前客服无权访问该会话，或可信同步尚未建立成员关系")
        return conversation

    def get_authorized(self, conversation_id: int, wecom_userid: str) -> Conversation:
        """读取客服有权访问的会话。

        Raises:
            PermissionError: 会话不存在或客服不是该会话成员。
        """

        conversation = self.session.scalar(
            select(Conversation)
            .join(
                ConversationUser,
                ConversationUser.conversation_id == Conversation.id,
            )
            .where(
                Conversation.id == conversation_id,
                ConversationUser.wecom_userid == wecom_userid,
            )
        )
        if conversation is None:
            raise PermissionError("当前客服无权访问该会话")
        return conversation

    def get_by_id(self, conversation_id: int) -> Conversation | None:
        """按数据库主键查询会话，不附加客服成员权限。

        Args:
            conversation_id: 会话数据库主键。

        Returns:
            会话实体；不存在时返回 ``None``。
        """

        return self.session.get(Conversation, conversation_id)

    def list_for_admin(
        self,
        conversation_type: str | None,
        keyword: str,
        start_at: datetime | None,
        end_at: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """聚合查询管理员可查看的会话列表和参与人。

        Args:
            conversation_type: 可选的 ``single`` 或 ``group``。
            keyword: 会话 ID、员工或客户 ID/姓名检索词。
            start_at: 消息开始时间。
            end_at: 消息结束时间。
            page: 从 1 开始的页码。
            page_size: 每页会话数量。

        Returns:
            ``(当前页会话字典, 筛选结果总数)``。
        """

        conditions = self.admin_filter_conditions(conversation_type, keyword)
        if start_at is not None:
            conditions.append(Message.sent_at >= start_at)
        if end_at is not None:
            conditions.append(Message.sent_at <= end_at)
        aggregate = (
            select(
                Conversation.id.label("id"),
                Conversation.wecom_chat_id.label("wecom_chat_id"),
                Conversation.conversation_type.label("conversation_type"),
                Conversation.name.label("name"),
                func.count(Message.id).label("message_count"),
                func.min(Message.sent_at).label("oldest_message_at"),
                func.max(Message.sent_at).label("newest_message_at"),
            )
            .join(Message, Message.conversation_id == Conversation.id)
            .where(*conditions)
            .group_by(
                Conversation.id,
                Conversation.wecom_chat_id,
                Conversation.conversation_type,
                Conversation.name,
            )
        )
        total = int(
            self.session.scalar(
                select(func.count()).select_from(aggregate.subquery())
            )
            or 0
        )
        rows = self.session.execute(
            aggregate.order_by(
                func.max(Message.sent_at).desc(),
                Conversation.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings().all()
        conversation_ids = [int(row["id"]) for row in rows]
        users, customers = self._participants_for(conversation_ids)
        items = [
            {
                **dict(row),
                "users": users.get(int(row["id"]), []),
                "customers": customers.get(int(row["id"]), []),
            }
            for row in rows
        ]
        return items, total

    def admin_conversation_summary(self, conversation_id: int) -> dict[str, Any] | None:
        """返回消息详情页标题使用的单个会话汇总。

        Args:
            conversation_id: 会话数据库主键。

        Returns:
            会话、参与人和全量消息范围；不存在或无消息时返回 ``None``。
        """

        row = self.session.execute(
            select(
                Conversation.id.label("id"),
                Conversation.wecom_chat_id.label("wecom_chat_id"),
                Conversation.conversation_type.label("conversation_type"),
                Conversation.name.label("name"),
                func.count(Message.id).label("message_count"),
                func.min(Message.sent_at).label("oldest_message_at"),
                func.max(Message.sent_at).label("newest_message_at"),
            )
            .join(Message, Message.conversation_id == Conversation.id)
            .where(Conversation.id == conversation_id)
            .group_by(
                Conversation.id,
                Conversation.wecom_chat_id,
                Conversation.conversation_type,
                Conversation.name,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        users, customers = self._participants_for([conversation_id])
        return {
            **dict(row),
            "users": users.get(conversation_id, []),
            "customers": customers.get(conversation_id, []),
        }

    @staticmethod
    def admin_filter_conditions(
        conversation_type: str | None,
        keyword: str,
    ) -> list[Any]:
        """构造会话管理页面和导出共用的会话筛选表达式。

        Args:
            conversation_type: 可选会话类型。
            keyword: 可选参与人或会话检索词。

        Returns:
            可直接传给 SQLAlchemy ``where`` 的表达式列表。
        """

        conditions: list[Any] = []
        if conversation_type:
            conditions.append(Conversation.conversation_type == conversation_type)
        normalized = keyword.strip()
        if normalized:
            pattern = f"%{normalized}%"
            user_match = exists(
                select(ConversationUser.conversation_id)
                .join(User, User.wecom_userid == ConversationUser.wecom_userid)
                .where(
                    ConversationUser.conversation_id == Conversation.id,
                    or_(
                        User.wecom_userid.like(pattern),
                        User.name.like(pattern),
                    ),
                )
            )
            customer_match = exists(
                select(ConversationCustomer.conversation_id)
                .join(
                    Customer,
                    Customer.external_userid == ConversationCustomer.external_userid,
                )
                .where(
                    ConversationCustomer.conversation_id == Conversation.id,
                    or_(
                        Customer.external_userid.like(pattern),
                        Customer.name.like(pattern),
                    ),
                )
            )
            conditions.append(
                or_(
                    Conversation.wecom_chat_id.like(pattern),
                    Conversation.name.like(pattern),
                    user_match,
                    customer_match,
                )
            )
        return conditions

    def _participants_for(
        self,
        conversation_ids: list[int],
    ) -> tuple[dict[int, list[dict[str, str]]], dict[int, list[dict[str, str]]]]:
        """使用两次批量查询加载一页会话的全部参与人。

        Args:
            conversation_ids: 当前页会话数据库主键。

        Returns:
            以会话主键分组的员工和客户字典。
        """

        users: dict[int, list[dict[str, str]]] = {}
        customers: dict[int, list[dict[str, str]]] = {}
        if not conversation_ids:
            return users, customers
        user_rows = self.session.execute(
            select(
                ConversationUser.conversation_id,
                User.wecom_userid,
                User.name,
            )
            .join(User, User.wecom_userid == ConversationUser.wecom_userid)
            .where(ConversationUser.conversation_id.in_(conversation_ids))
            .order_by(ConversationUser.conversation_id, User.name)
        ).all()
        for conversation_id, userid, name in user_rows:
            users.setdefault(int(conversation_id), []).append(
                {"id": str(userid), "name": str(name or userid)}
            )
        customer_rows = self.session.execute(
            select(
                ConversationCustomer.conversation_id,
                Customer.external_userid,
                Customer.name,
            )
            .join(
                Customer,
                Customer.external_userid == ConversationCustomer.external_userid,
            )
            .where(ConversationCustomer.conversation_id.in_(conversation_ids))
            .order_by(ConversationCustomer.conversation_id, Customer.name)
        ).all()
        for conversation_id, external_userid, name in customer_rows:
            customers.setdefault(int(conversation_id), []).append(
                {"id": str(external_userid), "name": str(name or external_userid)}
            )
        return users, customers

    def customer_ids(self, conversation_id: int) -> list[str]:
        """返回会话中的外部联系人 ID。"""

        return list(
            self.session.scalars(
                select(ConversationCustomer.external_userid).where(
                    ConversationCustomer.conversation_id == conversation_id
                )
            )
        )

    def find_for_sidebar(
        self,
        wecom_userid: str,
        external_userid: str = "",
        chat_id: str = "",
    ) -> Conversation | None:
        """根据侧边栏拿到的官方 ID 查找客服有权访问的会话。"""

        statement = (
            select(Conversation)
            .join(ConversationUser, ConversationUser.conversation_id == Conversation.id)
            .where(ConversationUser.wecom_userid == wecom_userid)
        )
        if chat_id:
            statement = statement.where(Conversation.wecom_chat_id == chat_id)
        elif external_userid:
            statement = statement.join(
                ConversationCustomer,
                ConversationCustomer.conversation_id == Conversation.id,
            ).where(ConversationCustomer.external_userid == external_userid)
        else:
            return None
        return self.session.scalar(statement.limit(1))

    def get_or_create_group(self, room_id: str) -> Conversation:
        """按企业微信官方 ``roomid`` 获取或创建外部群聊。

        Args:
            room_id: 企业微信会话内容存档返回的群聊 ID。

        Returns:
            已持久化的群聊实体。

        Raises:
            ValueError: 群聊 ID 为空。
        """

        if not room_id:
            raise ValueError("群聊归档消息缺少 roomid")
        conversation = self.session.scalar(
            select(Conversation).where(Conversation.wecom_chat_id == room_id)
        )
        if conversation is None:
            conversation = Conversation(
                wecom_chat_id=room_id,
                conversation_type="group",
            )
            self.session.add(conversation)
            self.session.flush()
        return conversation

    def get_or_create_single(
        self,
        wecom_userid: str,
        external_userid: str,
    ) -> Conversation:
        """按员工与外部联系人官方 ID 获取或创建单聊会话。

        Args:
            wecom_userid: 企业微信员工 ID。
            external_userid: 企业微信外部联系人 ID。

        Returns:
            已持久化的单聊实体。
        """

        key = f"single:{wecom_userid}:{external_userid}"
        conversation = self.session.scalar(
            select(Conversation).where(Conversation.wecom_chat_id == key)
        )
        if conversation is None:
            conversation = Conversation(
                wecom_chat_id=key,
                conversation_type="single",
            )
            self.session.add(conversation)
            self.session.flush()
        return conversation

    def link_user(self, conversation_id: int, user: User) -> None:
        """幂等关联会话与员工。

        Args:
            conversation_id: 内部会话主键。
            user: 已持久化员工实体。
        """

        key = {"conversation_id": conversation_id, "wecom_userid": user.wecom_userid}
        if self.session.get(ConversationUser, key) is None:
            self.session.add(ConversationUser(**key))

    def ensure_customer(self, external_userid: str) -> Customer:
        """确保归档消息中的外部联系人存在。

        Args:
            external_userid: 企业微信外部联系人 ID。

        Returns:
            已存在或新建的客户实体。
        """

        customer = self.session.get(Customer, external_userid)
        if customer is None:
            customer = Customer(external_userid=external_userid, name="当前客户")
            self.session.add(customer)
            self.session.flush()
        return customer

    def sync_customer_name(self, external_userid: str, name: str) -> bool:
        """使用企业微信客户详情新增或修正外部联系人姓名。

        Args:
            external_userid: 企业微信外部联系人 ID。
            name: 企业微信官方客户姓名。

        Returns:
            本次是否新增客户或修改姓名。
        """

        normalized_name = str(name or "").strip()
        if not external_userid or not normalized_name:
            return False
        customer = self.session.get(Customer, external_userid)
        if customer is None:
            customer = Customer(
                external_userid=external_userid,
                name=normalized_name,
            )
            self.session.add(customer)
            self.session.flush()
            return True
        if customer.name == normalized_name:
            return False
        customer.name = normalized_name
        return True

    def unresolved_customer_ids(self) -> list[str]:
        """返回仍缺少真实姓名的企业微信外部联系人 ID。

        Returns:
            姓名为空、仍为默认占位名或与官方 ID 相同的客户 ID。

        Notes:
            该查询只为每日资料同步提供候选，不修改会话关系或消息数据。
        """

        return list(
            self.session.scalars(
                select(Customer.external_userid).where(
                    or_(
                        Customer.name == "",
                        Customer.name == "当前客户",
                        Customer.name == Customer.external_userid,
                    )
                )
            )
        )

    def group_chat_ids(self) -> list[str]:
        """返回数据库中已由归档消息发现的全部外部群聊 ID。

        Returns:
            按会话主键稳定排序的企业微信群聊 ID。

        Notes:
            群 ID 来自会话内容存档 ``roomid``；本查询只为每日资料同步提供候选。
        """

        return list(
            self.session.scalars(
                select(Conversation.wecom_chat_id)
                .where(Conversation.conversation_type == "group")
                .order_by(Conversation.id)
            )
        )

    def sync_group_name(self, chat_id: str, name: str) -> bool:
        """使用企业微信客户群详情新增或修正群聊名称。

        Args:
            chat_id: 企业微信外部群聊 ID。
            name: 企业微信返回的真实群名。

        Returns:
            本次是否修改了数据库。
        """

        normalized_name = str(name or "").strip()
        if not chat_id or not normalized_name:
            return False
        conversation = self.session.scalar(
            select(Conversation).where(
                Conversation.wecom_chat_id == chat_id,
                Conversation.conversation_type == "group",
            )
        )
        if conversation is None or conversation.name == normalized_name:
            return False
        conversation.name = normalized_name
        return True

    def link_customer(self, conversation_id: int, customer: Customer) -> None:
        """幂等关联会话与外部联系人。

        Args:
            conversation_id: 内部会话主键。
            customer: 已持久化客户实体。
        """

        key = {
            "conversation_id": conversation_id,
            "external_userid": customer.external_userid,
        }
        if self.session.get(ConversationCustomer, key) is None:
            self.session.add(ConversationCustomer(**key))
