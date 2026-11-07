"""聊天记录管理权限、筛选和流式导出的回归测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from backend.dependencies.auth import admin_user
from backend.schemas.chat_records import ChatRecordFilters
from backend.services.chat_record_service import ChatRecordService
from backend.services.summary_service import ConversationSummaryService


class FakeConversationRepository:
    """只实现导出校验所需行为的会话仓储替身。"""

    def __init__(self, existing_ids: set[int]):
        """记录测试中视为存在的会话主键。"""

        self.existing_ids = existing_ids

    def get_by_id(self, conversation_id: int):
        """模拟按主键查询会话。"""

        return object() if conversation_id in self.existing_ids else None


class FakeMessageRepository:
    """记录流式导出批次并返回稳定公开结构。"""

    def __init__(self):
        """创建两个独立批次，验证服务不会要求一次返回全量记录。"""

        self.batch_count = 0
        self.rows = [
            [
                (
                    SimpleNamespace(id=1),
                    "single:staff:customer",
                    "single",
                )
            ],
            [(SimpleNamespace(id=2), "room-1", "group")],
        ]

    def iter_admin_export(self, *args):
        """逐批产出测试消息并记录批次数。"""

        for rows in self.rows:
            self.batch_count += 1
            yield rows

    def admin_public_list(self, messages):
        """把测试消息转换成导出服务需要的公开结构。"""

        result = []
        for message in messages:
            formula = "=危险公式" if message.id == 1 else "正常内容"
            result.append(
                {
                    "conversation_id": message.id,
                    "archive_seq": 100 + message.id,
                    "sent_at": datetime(2026, 8, 5, 10, message.id),
                    "sender_type": "staff" if message.id == 1 else "customer",
                    "sender_id": f"sender-{message.id}",
                    "sender_name": formula,
                    "message_type": "text",
                    "content": formula,
                    "content_json": {"text": formula},
                }
            )
        return result


class ChatRecordContractTest(unittest.TestCase):
    """验证该模块最重要的权限与数据安全约束。"""

    def test_admin_dependency_rejects_non_admin(self):
        """普通客服和审核员不能进入聊天记录管理。"""

        with self.assertRaises(HTTPException) as context:
            admin_user({"role": "staff"})
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(admin_user({"role": "admin"})["role"], "admin")

    def test_filters_normalize_timezone_and_validate_range(self):
        """页面与导出共用的筛选 Schema 应统一时间并阻止反向范围。"""

        filters = ChatRecordFilters(
            keyword="  客户A  ",
            start_at=datetime(2026, 8, 5, 8, tzinfo=timezone.utc),
        )
        self.assertEqual(filters.keyword, "客户A")
        self.assertIsNone(filters.start_at.tzinfo)
        with self.assertRaises(ValidationError):
            ChatRecordFilters(
                start_at=datetime(2026, 8, 6),
                end_at=datetime(2026, 8, 5),
            )

    def test_stream_csv_is_batched_bom_encoded_and_formula_safe(self):
        """CSV 必须分批生成、含 BOM，且不能让用户文本触发 Excel 公式。"""

        service = ChatRecordService.__new__(ChatRecordService)
        service.conversations = FakeConversationRepository({1})
        service.messages = FakeMessageRepository()
        service.validate_export_target(1)
        chunks = list(service.stream_csv(ChatRecordFilters(), conversation_id=1))
        content = b"".join(chunks).decode("utf-8-sig")
        self.assertEqual(chunks[0], b"\xef\xbb\xbf")
        self.assertEqual(service.messages.batch_count, 2)
        self.assertIn("'=危险公式", content)
        self.assertIn("正常内容", content)

    def test_export_target_is_checked_before_streaming(self):
        """不存在的会话必须在响应建立前返回资源不存在。"""

        service = ChatRecordService.__new__(ChatRecordService)
        service.conversations = FakeConversationRepository(set())
        with self.assertRaises(LookupError):
            service.validate_export_target(999)

    def test_manual_summary_edit_keeps_message_boundary(self):
        """人工编辑纪要必须使用当前边界，且不能把边界推进到新消息。"""

        class Repository:
            def __init__(self):
                self.expected = None

            def save_manual_summary(self, conversation_id, boundary, text, analysis):
                self.expected = (conversation_id, boundary, text, analysis)
                return SimpleNamespace(
                    summary_text=text,
                    analysis_json=analysis,
                    last_message_id=88,
                )

            def get_for_admin(self, conversation_id):
                return SimpleNamespace(id=conversation_id, conversation_type="group"), None, 3

        repository = Repository()
        result = ConversationSummaryService(repository, object()).edit_for_admin(
            7,
            88,
            "人工修订纪要",
            {"good_practices": ["先复述客户问题"]},
        )

        self.assertEqual(repository.expected[1], 88)
        self.assertEqual(result["last_message_id"], 88)
        self.assertEqual(result["uncovered_message_count"], 3)

    def test_manual_candidate_is_always_pending(self):
        """聊天记录页提交内容只能进入统一 pending 审核队列。"""

        class SummaryRepository:
            def create_manual_skill_candidate(self, *args):
                return SimpleNamespace(id=9, status="pending", title=args[1])

        service = ChatRecordService.__new__(ChatRecordService)
        service.summaries = SummaryRepository()
        result = service.create_skill_candidate(
            7,
            "需求澄清方法",
            "先复述客户问题，再确认关键约束。",
            [
                {"level": "knowledge_domain", "code": "communication_method", "name": "沟通方法"},
                {"level": "topic", "code": "clarification", "name": "需求澄清"},
            ],
            [11],
            "admin-1",
        )

        self.assertEqual(result["status"], "pending")


if __name__ == "__main__":
    unittest.main()
