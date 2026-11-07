"""企业微信当前会话连接服务。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.repositories import ConversationRepository, UserRepository


class ConversationService:
    """识别会话、维护成员关联并返回侧边栏上下文。"""

    def __init__(self, session: Session, settings: Settings):
        """注入请求级数据库事务和企业微信配置。"""

        self.settings = settings
        self.conversations = ConversationRepository(session)
        self.users = UserRepository(session)

    def connect(
        self,
        staff_userid: str,
        conversation_type: str,
        external_userid: str = "",
        chat_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """连接可信同步已授权的会话；浏览器传入的 ID 和上下文不能授予权限。"""

        if conversation_type not in {"group", "external_group_chat", "single", "single_external_contact"}:
            raise ValueError("会话类型无效")
        normalized_type = "group" if conversation_type in {
            "group",
            "external_group_chat",
        } else "single"
        user = self.users.get_by_wecom_userid(staff_userid)
        if user is None or not user.is_enabled:
            raise PermissionError("当前账号不存在或已被禁用")
        conversation = self.conversations.get_authorized_by_identity(
            wecom_userid=staff_userid,
            conversation_type=normalized_type,
            external_userid=external_userid,
            chat_id=chat_id,
        )
        subject_id = chat_id if normalized_type == "group" else external_userid
        return {
            "service_user": {
                "corp_id": self.settings.wecom_corp_id,
                "staff_userid": staff_userid,
                "name": user.name,
            },
            "customer": {"external_userid": external_userid},
            "group_chat": {
                "chat_id": chat_id,
                "name": conversation.name or "",
            },
            "conversation": {
                "id": f"conv_{conversation.id}",
                "database_id": conversation.id,
                "conversation_key": conversation.wecom_chat_id,
                "conversation_type": (
                    "external_group_chat" if normalized_type == "group" else "single_external_contact"
                ),
                "subject_id": subject_id,
                "name": conversation.name or "",
                "channel_type": "wecom",
            },
            "context": context or {},
            "match": {
                "method": (
                    "wecom_staff_userid_plus_external_chat_id"
                    if normalized_type == "group"
                    else "wecom_staff_userid_plus_external_userid"
                ),
                "confidence": 1.0,
                "needs_ocr": False,
            },
        }
