"""用内存数据库验证隐私边界，不包含员工实名、生产标识或真实消息。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app import app
from backend.database.base import Base
from backend.database.models import Conversation, ConversationUser, User
from backend.database.repositories import UserRepository
from backend.database.session import get_db
from backend.dependencies.auth import current_user
from backend.services.auth_service import AuthService
from backend.services.chat_record_service import ChatRecordService
from backend.services.conversation_service import ConversationService


class ConversationPrivacyTest(unittest.TestCase):
    """浏览器提供的群 ID/成员字典不是授权证据，连接请求不得自行加群。"""

    def setUp(self):
        """建立三个虚构账号和一个仅有一名授权成员的演示群。"""
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all([
            User(wecom_userid="demo-member", name="演示成员", is_enabled=True),
            User(wecom_userid="demo-outsider", name="演示非成员", is_enabled=True),
            User(wecom_userid="demo-disabled", name="演示禁用账号", is_enabled=False),
            Conversation(id=1, wecom_chat_id="wr-demo-private", conversation_type="group"),
            ConversationUser(conversation_id=1, wecom_userid="demo-member"),
        ])
        self.session.commit()
        self.service = ConversationService(self.session, SimpleNamespace(wecom_corp_id="demo-corp"))

    def tearDown(self):
        """关闭内存会话并释放测试引擎。"""
        self.session.close()
        self.engine.dispose()

    def test_existing_member_can_connect(self):
        """可信同步已建立的成员可以连接会话。"""
        result = self.service.connect("demo-member", "group", chat_id="wr-demo-private")
        self.assertEqual(result["conversation"]["database_id"], 1)

    def test_outsider_cannot_self_grant_with_forged_context(self):
        """伪造客户端成员信息不能创建会话授权。"""
        with self.assertRaises(PermissionError):
            self.service.connect("demo-outsider", "group", chat_id="wr-demo-private",
                                 context={"raw_chat": {"member_list": [{"userid": "demo-outsider"}]}})
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ConversationUser)), 1)
        self.assertFalse(self.session.new)

    def test_unknown_group_does_not_create_conversation(self):
        """客户端不能通过未知群 ID 创建会话或成员。"""
        with self.assertRaises(PermissionError):
            self.service.connect("demo-member", "group", chat_id="wr-demo-unknown")
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Conversation)), 1)

    def test_single_chat_cannot_be_accessed_as_a_different_employee(self):
        """单聊按员工与客户同时定位，不能仅凭客户 ID 访问另一员工的会话。"""
        self.session.add_all([
            Conversation(id=2, wecom_chat_id="single:demo-member:demo-customer", conversation_type="single"),
            ConversationUser(conversation_id=2, wecom_userid="demo-member"),
        ])
        self.session.flush()
        with self.assertRaises(PermissionError):
            self.service.connect("demo-outsider", "single", external_userid="demo-customer")

    def test_disabled_account_stays_disabled_on_connect(self):
        """禁用账号不能由连接请求重新启用。"""
        with self.assertRaises(PermissionError):
            self.service.connect("demo-disabled", "group", chat_id="wr-demo-private")
        self.assertFalse(self.session.get(User, "demo-disabled").is_enabled)

    def test_oauth_cannot_reenable_disabled_account(self):
        """有效 OAuth 身份仍需遵守管理员设置的禁用状态。"""
        auth = AuthService(self.session, SimpleNamespace(app_secret="synthetic-test-value"), object())
        with self.assertRaises(PermissionError):
            auth._login_wecom_identity({"wecom_userid": "demo-disabled"})
        self.assertFalse(self.session.get(User, "demo-disabled").is_enabled)

    def test_archived_employee_is_not_silently_activated(self):
        """归档发现的未开通员工必须由管理员明确启用。"""
        repo = UserRepository(self.session)
        user = repo.ensure_wecom_user("demo-archive-only", allow_login=False)
        repo.ensure_wecom_user(user.wecom_userid, allow_login=True)
        self.assertFalse(user.is_enabled)


class EndpointPrivacyTest(unittest.TestCase):
    """首次改密状态必须由后端阻止访问敏感接口，而非仅靠前端跳转。"""

    def test_first_password_user_cannot_read_or_mutate_conversations(self):
        """敏感接口在触发数据库或模型之前拒绝待改密账号。"""
        def pending_user():
            """提供合成的、尚未完成首次改密的用户。"""
            return {"wecom_userid": "demo-member", "must_change_password": True, "role": "staff"}

        def no_database():
            """注入空会话，确保回归不依赖真实数据库。"""
            yield None

        old = dict(app.dependency_overrides)
        app.dependency_overrides.update({current_user: pending_user, get_db: no_database})
        try:
            with TestClient(app) as client:
                for path in ("/api/conversations/1/messages", "/api/conversations/1/ai-replies/latest", "/api/conversations/1/summary"):
                    with self.subTest(path=path):
                        self.assertEqual(client.get(path).status_code, 403)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)

    def test_csv_control_prefixes_cannot_hide_formulas(self):
        """空格、制表符、换行不能绕过 CSV 导出公式保护。"""
        for value in ("=1+1", " +1", "\t=1+1", "\r@demo", "\n-demo", "  @demo"):
            with self.subTest(value=value):
                self.assertTrue(ChatRecordService._safe_csv(value).startswith("'"))
        self.assertEqual(ChatRecordService._safe_csv("合成文本"), "合成文本")


if __name__ == "__main__":
    unittest.main()
