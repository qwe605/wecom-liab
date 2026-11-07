"""企业微信资料接口适配测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.integrations.wecom import WeComClient


class WeComIntegrationTest(unittest.TestCase):
    """验证资料接口使用统一 Token 回退规则。"""

    @patch("backend.integrations.wecom.post_json")
    def test_group_profile_uses_application_secret_fallback(self, post_json_mock):
        """未配置独立客户联系 Secret 时仍应尝试读取外部群详情。"""

        post_json_mock.return_value = {
            "errcode": 0,
            "group_chat": {
                "name": "项目客户群",
                "member_list": [
                    {"userid": "staff-1", "name": "员工甲", "type": 1},
                    {"userid": "wm-1", "name": "客户甲", "type": 2},
                ],
            },
        }
        settings = SimpleNamespace(
            wecom_corp_id="ww-corp",
            wecom_corp_secret="application-secret",
            wecom_customer_contact_secret="",
        )
        client = WeComClient(settings)

        with patch.object(
            client,
            "customer_contact_access_token",
            return_value="application-token",
        ) as token_mock:
            result = client.external_group_profiles(["wr-group"])

        token_mock.assert_called_once_with()
        called_url, called_payload = post_json_mock.call_args.args
        self.assertIn("access_token=application-token", called_url)
        self.assertEqual(called_payload, {"chat_id": "wr-group", "need_name": 1})
        self.assertEqual(result[0]["name"], "项目客户群")
        self.assertEqual(result[0]["members"][1]["name"], "客户甲")

    @patch("backend.integrations.wecom.post_json")
    def test_group_profile_skips_non_external_group_and_continues(
        self,
        post_json_mock,
    ):
        """内部群或跨企业群不可读时不应阻断后续合法外部群。"""

        post_json_mock.side_effect = [
            {"errcode": 90501, "errmsg": "chat is not external group chat"},
            {
                "errcode": 0,
                "errmsg": "ok",
                "group_chat": {"name": "后续合法客户群", "member_list": []},
            },
        ]
        settings = SimpleNamespace(
            wecom_corp_id="ww-corp",
            wecom_corp_secret="application-secret",
            wecom_customer_contact_secret="",
        )
        client = WeComClient(settings)

        with patch.object(
            client,
            "customer_contact_access_token",
            return_value="application-token",
        ):
            result = client.external_group_profiles(["wr-internal", "wr-external"])

        self.assertEqual(post_json_mock.call_count, 2)
        self.assertEqual(result[0]["chat_id"], "wr-external")
        self.assertEqual(result[0]["name"], "后续合法客户群")


if __name__ == "__main__":
    unittest.main()
