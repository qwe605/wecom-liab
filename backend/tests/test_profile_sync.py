"""企业微信员工和客户资料同步编排测试。"""

import unittest
from types import SimpleNamespace

from backend.services.profile_sync_service import ProfileSyncService


class FakeWeCom:
    """提供固定员工和客户资料的企业微信适配器。"""

    def visible_employee_profiles(self, candidate_userids):
        """确认服务把数据库候选员工传给 Integration。"""

        self.candidates = candidate_userids
        return [{"wecom_userid": "staff-1", "name": "员工甲"}]

    def external_contact_profiles(self, employee_userids, candidate_external_userids):
        """返回当前员工可见的客户资料。"""

        self.employee_userids = employee_userids
        self.customer_candidates = candidate_external_userids
        return [{"external_userid": "wm-customer", "name": "客户甲"}]

    def external_group_profiles(self, chat_ids):
        """返回群名，并用群成员姓名补齐批量接口遗漏人员。"""

        self.group_candidates = chat_ids
        return [
            {
                "chat_id": "wr-group",
                "name": "客户项目群",
                "members": [
                    {"id": "staff-2", "name": "员工乙", "type": "staff"},
                    {"id": "wm-group", "name": "客户乙", "type": "customer"},
                ],
            }
        ]


class FakeUserRepository:
    """记录员工姓名更新。"""

    def list_wecom_userids(self):
        """返回消息归档已发现的员工。"""

        return ["staff-1", "staff-unknown"]

    def sync_display_name(self, userid, name):
        """记录一次员工姓名更新。"""

        self.updated = (userid, name)
        return True


class FakeConversationRepository:
    """记录客户姓名更新。"""

    def sync_customer_name(self, external_userid, name):
        """记录一次客户姓名更新。"""

        self.updated_customers = getattr(self, "updated_customers", [])
        self.updated_customers.append((external_userid, name))
        return True
    def unresolved_customer_ids(self):
        """返回归档消息已发现但尚未补齐姓名的客户。"""

        return ["wm-customer", "wm-unknown"]

    def group_chat_ids(self):
        """返回归档消息已发现的外部群聊。"""

        return ["wr-group"]

    def sync_group_name(self, chat_id, name):
        """记录群名更新。"""

        self.updated_group = (chat_id, name)
        return True


class FakeSession:
    """记录资料写入前是否刷新了数据库事务视图。"""

    def rollback(self):
        """标记旧只读事务已经结束。"""

        self.was_rolled_back = True


class ProfileSyncServiceTest(unittest.TestCase):
    """验证资料同步不改变消息或权限，只更新名称实体。"""

    def test_syncs_visible_employee_and_customer_names(self):
        """可见员工与客户应各写入一次，并返回可诊断统计。"""

        wecom = FakeWeCom()
        session = FakeSession()
        service = ProfileSyncService(session, SimpleNamespace(), wecom)
        service.users = FakeUserRepository()
        service.conversations = FakeConversationRepository()

        result = service.sync_all_visible_profiles()

        self.assertEqual(wecom.candidates, ["staff-1", "staff-unknown"])
        self.assertEqual(wecom.employee_userids, ["staff-1"])
        self.assertEqual(wecom.customer_candidates, ["wm-customer", "wm-unknown"])
        self.assertEqual(service.users.updated, ("staff-2", "员工乙"))
        self.assertEqual(wecom.group_candidates, ["wr-group"])
        self.assertEqual(
            service.conversations.updated_customers,
            [("wm-customer", "客户甲"), ("wm-group", "客户乙")],
        )
        self.assertEqual(service.conversations.updated_group, ("wr-group", "客户项目群"))
        self.assertEqual(result["employees_updated"], 2)
        self.assertEqual(result["customers_updated"], 2)
        self.assertEqual(result["customer_candidates"], 2)
        self.assertEqual(result["group_candidates"], 1)
        self.assertEqual(result["groups_updated"], 1)
        self.assertTrue(session.was_rolled_back)


if __name__ == "__main__":
    unittest.main()
