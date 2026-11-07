"""语雀 MCP 产品知识路由测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.integrations.yuque_mcp import YuqueMcpKnowledgeProvider
from backend.schemas.ai import ChatMessage, ConversationContext


class FakeMcpCaller:
    """按工具名和参数返回预设 MCP 结果，并记录调用顺序。"""

    def __init__(self, responses: dict[tuple[str, str], object]):
        """保存以工具名和文档 ID 或查询词为键的响应。"""

        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, arguments: dict):
        """返回当前工具调用的预设结果。"""

        self.calls.append((name, arguments))
        identity = str(arguments.get("doc_id") or arguments.get("query") or "")
        return self.responses[(name, identity)]


def sample_context() -> ConversationContext:
    """构造知识提供者协议要求的最小会话上下文。"""

    return ConversationContext(
        database_conversation_id=42,
        staff_id=7,
        staff_userid="staff-1",
        conversation_type="external_single",
        customer_name="测试客户",
        messages=[
            ChatMessage(
                id=100,
                sender_kind="external_contact",
                sender_name="测试客户",
                content="磁场校准步骤",
                created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
            )
        ],
    )


def provider(caller: FakeMcpCaller) -> YuqueMcpKnowledgeProvider:
    """构造关闭缓存的测试语雀知识提供者。"""

    return YuqueMcpKnowledgeProvider(
        mcp_url="http://127.0.0.1:8003/mcp",
        timeout_seconds=5,
        product_index_repo="demo/products",
        product_index_doc="product-index",
        product_docs_repo="demo/manuals",
        caller=caller,
        cache_seconds=0,
    )


class YuqueMcpKnowledgeProviderTest(unittest.TestCase):
    """验证型号优先、FAQ 补充和全文搜索回退。"""

    def test_locked_model_reads_its_own_product_document(self):
        """明确型号时必须先从汇总表定位并读取该型号资料。"""

        caller = FakeMcpCaller(
            {
                ("yuque_get_doc", "product-index"): {
                    "title": "产品统计表",
                    "body": "| DEMO901A | 九轴姿态传感器 | https://www.yuque.com/demo/manuals/demo901a |",
                },
                ("yuque_get_doc", "demo901a"): {
                    "id": 101,
                    "slug": "demo901a",
                    "title": "DEMO901A 产品资料",
                    "body": "DEMO901A 磁场校准前需要远离磁性物体。" * 60,
                },
                ("yuque_get_sub_doc", "demo901a"): {"sub_docs": []},
            }
        )

        result = provider(caller).search(
            sample_context(),
            "DEMO901A 如何进行磁场校准",
            limit=5,
        )

        self.assertEqual(result[0].reference_id, "yuque:demo/manuals:demo901a")
        self.assertEqual(result[0].product_model, "DEMO901A")
        self.assertEqual(
            [item[0] for item in caller.calls],
            ["yuque_get_doc", "yuque_get_doc", "yuque_get_sub_doc"],
        )

    def test_configuration_question_adds_target_model_faq(self):
        """支持性或配置问题必须继续读取目标型号 FAQ/规格书。"""

        caller = FakeMcpCaller(
            {
                ("yuque_get_doc", "product-index"): {
                    "title": "产品统计表",
                    "body": "| DEMO901D | 姿态传感器 | https://www.yuque.com/demo/manuals/demo901d |",
                },
                ("yuque_get_doc", "demo901d"): {
                    "slug": "demo901d",
                    "title": "DEMO901D 资料目录",
                    "body": "资料目录",
                },
                ("yuque_get_sub_doc", "demo901d"): {
                    "sub_docs": [
                        {
                            "doc_name": "DEMO901D 常见问题 FAQ",
                            "doc_desc": "输出速率与配置支持说明",
                            "repo_id": "demo/manuals",
                            "doc_id": "demo901d-faq",
                        }
                    ]
                },
                ("yuque_get_doc", "demo901d-faq"): {
                    "slug": "demo901d-faq",
                    "title": "DEMO901D 常见问题",
                    "body": "FAQ 原文：DEMO901D 支持在上位机中设置输出速率。",
                },
            }
        )

        result = provider(caller).search(
            sample_context(),
            "DEMO901D 是否支持设置输出速率",
            limit=5,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[1].reference_id, "yuque:demo/manuals:demo901d-faq")
        self.assertIn("FAQ 原文", result[1].content)

    def test_unknown_model_uses_yuque_search(self):
        """没有型号时直接使用语雀全文搜索，不读取本地 JSON 文件。"""

        caller = FakeMcpCaller(
            {
                ("yuque_search", "磁场校准步骤"): {
                    "items": [
                        {
                            "id": 202,
                            "title": "磁场校准说明",
                            "url": "https://www.yuque.com/demo/manuals/calibration",
                            "body": "执行磁场校准前，请先远离磁铁和电机。",
                            "book": {"namespace": "demo/manuals"},
                        }
                    ]
                }
            }
        )

        result = provider(caller).search(sample_context(), "磁场校准步骤", limit=5)

        self.assertEqual(result[0].reference_id, "yuque:demo/manuals:calibration")
        self.assertEqual(caller.calls[0][0], "yuque_search")


if __name__ == "__main__":
    unittest.main()
