"""第三方 HTTP JSON 解码容错测试。"""

import unittest

from backend.integrations.http import _decode_json_object


class HttpIntegrationTest(unittest.TestCase):
    """验证企业微信异常名称字节不会中断整批资料同步。"""

    def test_replaces_invalid_utf8_inside_json_string(self):
        """非法 UTF-8 字节应被替换，其余 ID 和名称仍可解析。"""

        raw = b'{"errcode":0,"external_contact":{"external_userid":"wm-1","name":"A\x88B"}}'

        result = _decode_json_object(raw)

        self.assertEqual(result["errcode"], 0)
        self.assertEqual(
            result["external_contact"]["external_userid"],
            "wm-1",
        )
        self.assertEqual(result["external_contact"]["name"], "A\ufffdB")

    def test_rejects_non_object_json(self):
        """顶层数组不符合项目第三方接口契约，应继续明确报错。"""

        with self.assertRaisesRegex(RuntimeError, "没有返回 JSON 对象"):
            _decode_json_object(b"[]")


if __name__ == "__main__":
    unittest.main()
