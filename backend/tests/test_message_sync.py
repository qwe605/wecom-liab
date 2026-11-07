"""企业微信全局消息页路由和游标事务规则测试。"""

import unittest
from types import SimpleNamespace

from backend.services.message_sync_service import MessageSyncService


class FakeArchive:
    """返回一页包含未知群聊的企业微信规范消息。"""

    def __init__(self):
        """初始化调用计数。"""

        self.calls = 0

    def fetch_page(self, start_seq, limit):
        """模拟 SDK 单次全局分页。"""

        self.calls += 1
        del limit
        return [
            {
                "archive_seq": start_seq + 1,
                "from": "demo_staff_01",
                "participant_ids": ["demo_staff_01", "wma_customer"],
                "chat_id": "wr_new_group",
                "msgtype": "text",
                "content": "第一条",
                "content_json": {},
                "msgtime": "2026-08-05T10:00:00+08:00",
                "sender_type": "staff",
            },
            {
                "archive_seq": start_seq + 2,
                "from": "wma_customer",
                "participant_ids": ["demo_staff_01", "wma_customer"],
                "chat_id": "wr_new_group",
                "msgtype": "text",
                "content": "第二条",
                "content_json": {},
                "msgtime": "2026-08-05T10:00:01+08:00",
                "sender_type": "customer",
            },
        ]


class FakeConversationRepository:
    """记录由归档消息自动发现的群聊。"""

    def __init__(self):
        """初始化群聊和成员关联记录。"""

        self.groups = []
        self.users = []
        self.customers = []

    def get_or_create_group(self, room_id):
        """返回固定数据库会话。"""

        self.groups.append(room_id)
        return SimpleNamespace(id=7)

    def link_user(self, conversation_id, user):
        """记录员工关联。"""

        self.users.append((conversation_id, user.wecom_userid))

    def ensure_customer(self, external_userid):
        """返回模拟客户。"""

        return SimpleNamespace(external_userid=external_userid)

    def link_customer(self, conversation_id, customer):
        """记录客户关联。"""

        self.customers.append((conversation_id, customer.external_userid))


class FakeUserRepository:
    """返回归档自动发现的禁用登录员工。"""

    def ensure_wecom_user(self, userid, allow_login=False):
        """验证自动发现不会直接授予登录权。"""

        if allow_login:
            raise AssertionError("归档发现员工不得自动授权登录")
        return SimpleNamespace(wecom_userid=userid)


class FakeMessageRepository:
    """保存内存游标和消息序号。"""

    def __init__(self, fail=False):
        """构造全局游标 100；可选择模拟写入失败。"""

        self.state = SimpleNamespace(lower_seq=100, upper_seq=100, reached_end=False)
        self.saved = []
        self.fail = fail

    def get_archive_state(self, seed_seq):
        """返回唯一全局同步状态。"""

        del seed_seq
        return self.state

    def upsert_normalized(self, conversation_id, payload):
        """记录消息，或在首条写入时模拟数据库错误。"""

        if self.fail:
            raise RuntimeError("write failed")
        self.saved.append((conversation_id, payload["archive_seq"]))
        return SimpleNamespace(), True


class MessageSyncGlobalPageTest(unittest.TestCase):
    """验证全局分页、自动建群和游标提交顺序。"""

    def build_service(self, fail=False):
        """组装不访问真实数据库的服务实例。"""

        settings = SimpleNamespace(
            wecom_archive_seed_seq=0,
            wecom_archive_page_size=1000,
            wecom_archive_window_step=1000,
        )
        archive = FakeArchive()
        service = MessageSyncService(None, settings, archive)
        service.conversations = FakeConversationRepository()
        service.users = FakeUserRepository()
        service.messages = FakeMessageRepository(fail=fail)
        return service, archive

    def test_unknown_group_is_created_and_entire_page_is_saved_once(self):
        """未知群聊不得被过滤，同一页 SDK 只能调用一次。"""

        service, archive = self.build_service()
        result = service.sync_next_page()

        self.assertEqual(archive.calls, 1)
        self.assertEqual(service.messages.saved, [(7, 101), (7, 102)])
        self.assertEqual(service.messages.state.upper_seq, 102)
        self.assertEqual(result["imported"], 2)
        self.assertIn("wr_new_group", service.conversations.groups)

    def test_cursor_does_not_advance_before_page_is_fully_saved(self):
        """页面写入异常时全局上界必须停留在旧值。"""

        service, _ = self.build_service(fail=True)
        with self.assertRaises(RuntimeError):
            service.sync_next_page()
        self.assertEqual(service.messages.state.upper_seq, 100)

    def test_history_import_reads_pages_until_current_lower_boundary(self):
        """历史导入必须连续翻页，并且不得被首次启动 seed_seq 阻断。"""

        service, archive = self.build_service()
        archive.fetch_page = lambda start_seq, limit: [
            {
                "archive_seq": start_seq + offset,
                "from": "demo_staff_01",
                "participant_ids": ["demo_staff_01", "wma_customer"],
                "chat_id": "wr_new_group",
                "msgtype": "text",
                "content": "历史消息",
                "content_json": {},
                "msgtime": "2026-08-04T10:00:00+08:00",
                "sender_type": "staff",
            }
            for offset in (400, 800)
        ]
        service.conversations.get_authorized = lambda conversation_id, staff_userid: (
            SimpleNamespace(id=conversation_id)
        )
        service.messages.state.lower_seq = 3100
        service.messages.state.upper_seq = 5000

        result = service.import_history(7, "demo_staff_01", 1)

        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["lower_cursor"], 2500)
        self.assertEqual(result["imported"], 2)


if __name__ == "__main__":
    unittest.main()
