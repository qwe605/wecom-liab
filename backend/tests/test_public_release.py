"""公开版本的安全回归；全部使用合成配置，不读取本地凭据或真实聊天。"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from backend.app import app
from backend.config import get_settings
from backend.integrations.yuque_readonly_server import ReadonlyYuqueClient, create_mcp
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.integrations.wecom import WeComClient


class PublicReleaseTest(unittest.TestCase):
    """防止公开版本恢复真实数据入口或固定生产签名密钥。"""

    def tearDown(self):
        """清除配置缓存，避免环境测试污染其他测试。"""
        get_settings.cache_clear()

    def test_anonymous_real_chat_debug_route_is_removed(self):
        """匿名真实聊天调试入口必须返回 404，不能接触数据库。"""
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/local-debug/sidebar").status_code, 404)

    def test_demo_does_not_request_real_chat(self):
        """前端演示只使用合成数据，不尝试读取真实会话。"""
        source = (Path(__file__).parents[2] / "frontend/src/views/SidebarView.vue").read_text(encoding="utf-8")
        self.assertNotIn("/api/local-debug", source)

    def test_production_requires_strong_secret(self):
        """生产环境缺少强密钥时应拒绝构造配置。"""
        for secret in ("", "short", "replace_with_long_random_application_secret", "local-only-change-me"):
            with self.subTest(secret=secret), patch.dict(os.environ, {"KEFUBAO_ENV": "production", "APP_SECRET": secret}):
                get_settings.cache_clear()
                with self.assertRaises(ValueError):
                    get_settings()

    def test_development_has_no_public_fixed_signing_key(self):
        """开发默认密钥为进程级随机值；不默认连接任何企业知识库。"""
        with patch.dict(os.environ, {"KEFUBAO_ENV": "development", "APP_SECRET": "",
                                   "YUQUE_PRODUCT_INDEX_REPO": "", "YUQUE_PRODUCT_DOCS_REPO": ""}):
            get_settings.cache_clear()
            settings = get_settings()
            self.assertGreaterEqual(len(settings.app_secret), 32)
            self.assertEqual(settings.yuque_product_index_repo, "")
            self.assertEqual(settings.yuque_product_docs_repo, "")

    def test_model_http_errors_do_not_echo_sensitive_body(self):
        """上游 HTTP 错误正文不能进入向客户端返回的异常。"""
        error = HTTPError("https://model.invalid", 401, "Unauthorized", {}, io.BytesIO(b"synthetic-private-body"))
        with patch("backend.integrations.minimax.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                OpenAICompatibleChatClient("https://model.invalid", "synthetic", "demo")._complete_json("system", "user")
        self.assertNotIn("synthetic-private-body", str(raised.exception))

    def test_invalid_model_payload_is_not_returned(self):
        """异常格式的模型返回可能包含聊天原文，不能拼到错误中。"""
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"private":"synthetic-private-body"}'
        with patch("backend.integrations.minimax.urlopen", return_value=response):
            with self.assertRaises(RuntimeError) as raised:
                OpenAICompatibleChatClient("https://model.invalid", "synthetic", "demo")._complete_json("system", "user")
        self.assertNotIn("synthetic-private-body", str(raised.exception))

    def test_wecom_error_only_exposes_error_code(self):
        """企业微信错误不透传上游附带字段与成员 ID。"""
        with self.assertRaises(RuntimeError) as raised:
            WeComClient._raise_on_error({"errcode": 40001, "errmsg": "synthetic-private-body"}, "get private-member")
        self.assertIn("40001", str(raised.exception))
        self.assertNotIn("synthetic-private-body", str(raised.exception))
        self.assertNotIn("private-member", str(raised.exception))


class ReadonlyKnowledgeTest(unittest.TestCase):
    """只读 MCP 的工具集合、授权范围和输出最小化测试。"""

    def setUp(self):
        """构造无效合成 Token；测试不触发网络请求。"""
        self.client = ReadonlyYuqueClient("synthetic-test-value", {"demo/manuals"})

    def test_only_three_read_tools_are_registered(self):
        """服务不得注册任何知识写入或成员管理工具。"""
        with patch.dict(os.environ, {"YUQUE_PERSONAL_TOKEN": "synthetic-test-value",
                                   "YUQUE_PRODUCT_INDEX_REPO": "demo/manuals",
                                   "YUQUE_PRODUCT_DOCS_REPO": "demo/manuals", "FASTMCP_HOST": "127.0.0.1"}):
            server = create_mcp()
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(names, {"yuque_get_doc", "yuque_get_sub_doc", "yuque_search"})

    def test_public_bind_is_rejected(self):
        """未鉴权的本机知识服务不能绑定公网。"""
        with patch.dict(os.environ, {"YUQUE_PERSONAL_TOKEN": "synthetic-test-value",
                                   "YUQUE_PRODUCT_INDEX_REPO": "demo/manuals", "FASTMCP_HOST": "0.0.0.0"}):
            with self.assertRaises(ValueError):
                create_mcp()

    def test_repository_scope_is_enforced_before_network(self):
        """越权知识库与路径穿越均在请求前拒绝。"""
        with patch.object(self.client, "_get") as request:
            with self.assertRaises(PermissionError):
                self.client.document("other/private", "doc")
            with self.assertRaises(ValueError):
                self.client.document("demo/manuals", "../secrets")
            request.assert_not_called()

    def test_subdocuments_filter_unauthorized_links(self):
        """文档交叉引用不能扩展授权范围。"""
        with patch.object(self.client, "document", return_value={"body":
            "| FAQ | https://www.yuque.com/demo/manuals/faq |\n"
            "| private | https://www.yuque.com/other/private/secret |"}):
            docs = self.client.subdocuments("demo/manuals", "index")["sub_docs"]
        self.assertEqual([doc["doc_id"] for doc in docs], ["faq"])

    def test_search_filters_scope_and_personal_metadata(self):
        """过滤未授权结果，并剔除附带用户资料。"""
        rows = [{"id": 1, "url": "https://www.yuque.com/demo/manuals/faq", "user": {"name": "not-for-output"},
                 "book": {"namespace": "demo/manuals"}},
                {"id": 2, "book": {"namespace": "other/private"}}]
        with patch.object(self.client, "_get", return_value=rows):
            items = self.client.search("demo")["items"]
        self.assertEqual(len(items), 1)
        self.assertNotIn("user", items[0])


if __name__ == "__main__":
    unittest.main()
