"""AI 回复业务测试。"""

import unittest
import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.services.skill_service import quality_score
from backend.integrations.minimax import OpenAICompatibleChatClient, _extract_json_object
from backend.schemas.ai import (
    ChatMessage,
    ConversationContext,
    GeneratedReply,
    KnowledgeReference,
    ReplyRequest,
)
from backend.services.ai_reply_service import AIReplyService
from backend.services.ai_reply_workflow import ReplyGraph


def sample_context() -> ConversationContext:
    """构造一组最小聊天上下文，供 AI 回复测试复用。"""
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
                sender_id="customer-1",
                content="DEMO901A 怎么进行磁场校准？",
                created_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
            )
        ],
        trigger_message_id=100,
        requester_external_userid="customer-1",
        customer_message_count=1,
    )


class StaticKnowledgeProvider:
    """返回固定已审核知识，避免工作流测试依赖外部 MCP 服务。"""

    def search(self, context, query, limit=5, taxonomy_filters=None):
        """返回一条 DEMO901A 校准证据。"""

        del context, query, limit, taxonomy_filters
        return [
            KnowledgeReference(
                reference_id="chunk-1",
                title="DEMO901A 使用说明",
                content="校准前远离磁铁，并按上位机菜单执行磁场校准。",
                source_url="https://example.invalid/demo901a",
                product_model="DEMO901A",
            )
        ]


class RoutedKnowledgeProvider:
    """记录知识域查询顺序，并只在指定知识域返回证据。"""

    def __init__(self, hit_domain):
        self.hit_domain = hit_domain
        self.calls = []

    def search(self, context, query, limit=5, taxonomy_filters=None):
        del context, query, limit
        domain = (taxonomy_filters or {}).get("knowledge_domain")
        self.calls.append(domain)
        if domain != self.hit_domain:
            return []
        return [KnowledgeReference("chunk-1", "产品资料", "已审核知识", product_model="DEMO901A")]


class FakeModel:
    """模拟大模型客户端，避免单元测试访问真实 MiniMax 服务。"""

    model_name = "fake-model"

    def __init__(self, intent_responses=None):
        """保存可选的渐进意图响应，并记录意图和回复调用。"""

        self.intent_responses = list(
            intent_responses
            or [
                {
                    "recognition_status": "resolved",
                    "intent": "安装配置",
                    "question": "客户询问 DEMO901A 磁场校准步骤。",
                    "background": [],
                    "blockers": [],
                    "product_category": "姿态传感器",
                    "product_subcategory": "姿态传感器模组类",
                    "product_model": "DEMO901A",
                    "model_candidates": [],
                    "evidence_message_ids": [100],
                    "clarification_question": "",
                }
            ]
        )
        self.intent_calls = []
        self.reply_calls = []

    def generate_json(self, system_prompt, user_prompt):
        """模拟结构化意图识别，并按顺序返回预设结果。"""

        self.intent_calls.append((system_prompt, user_prompt))
        if not self.intent_responses:
            raise AssertionError("缺少预设意图识别响应")
        return self.intent_responses.pop(0)

    def generate(self, system_prompt, user_prompt):
        """记录提示词并返回固定结构的模型生成结果。"""
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.reply_calls.append((system_prompt, user_prompt))
        used_ids = ["chunk-1"] if "chunk-1" in user_prompt else []
        return GeneratedReply(
            summary="客户询问 DEMO901A 磁场校准。",
            customer_intent="获取校准步骤",
            suggested_reply="请先远离磁性物体，再按资料中的校准流程操作。",
            risk_level="low",
            used_knowledge_ids=used_ids,
            model_name=self.model_name,
            latency_ms=12,
        )


class FakeRepository:
    """使用内存字段模拟 AI 回复仓储的读写行为。"""

    def __init__(self, context=None, contexts=None):
        """初始化会话上下文及测试断言需要的状态。"""
        self.context = context or sample_context()
        self.contexts = contexts or {}
        self.saved = None
        self.latest_value = None
        self.action = None
        self.limits = []

    def load_context(self, request, limit=30):
        """返回预设上下文，并记录本次查询参数。"""
        self.request = request
        self.limit = limit
        self.limits.append(limit)
        return self.contexts.get(limit, self.context)

    def save_suggestion(
        self, request, context, generated, references, prompt_version, retrieval_trace
    ):
        """模拟保存 AI 建议，并返回前端需要的建议摘要。"""
        self.saved = (request, context, generated, references, prompt_version, retrieval_trace)
        return {
            "suggestion_id": 9,
            "status": "generated",
            "created_at": "2026-07-29T10:00:01+00:00",
            "knowledge_refs": [item.public_dict() for item in references],
        }

    def latest_suggestion(self, request):
        """返回预设的最近一条回复建议。"""
        return self.latest_value

    def record_action(self, request, suggestion_id, action, edited_text=""):
        """记录客服对回复建议执行的操作。"""
        self.action = (request, suggestion_id, action, edited_text)
        return {"suggestion_id": suggestion_id, "status": "copied"}


class AIReplyServiceTest(unittest.TestCase):
    """验证 AI 回复解析、生成、校验和持久化流程。"""

    def test_minimax_thinking_block_is_removed_before_json_parse(self):
        """模型思考标签不应干扰最终 JSON 结果解析。"""
        result = _extract_json_object(
            '<think>Analyze the request and return JSON.</think>\n'
            '{"summary":"ok","suggested_reply":"reply"}'
        )

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["suggested_reply"], "reply")

    def test_minimax_malformed_json_is_repaired(self):
        """常见的代码块和尾逗号问题应被容错修复。"""
        result = _extract_json_object(
            "以下是回复建议：\n"
            '```json\n{"summary":"客户询问参数",'
            '"customer_intent":"获取参数",'
            '"suggested_reply":"您好，建议先确认型号",'
            '"risk_level":"low",'
            '"used_knowledge_ids":["chunk-1",],}\n```'
        )

        self.assertEqual(result["summary"], "客户询问参数")
        self.assertEqual(result["suggested_reply"], "您好，建议先确认型号")
        self.assertEqual(result["used_knowledge_ids"], ["chunk-1"])

    def test_minimax_parser_keeps_escaped_backslashes(self):
        """JSON 中的 Windows 路径和转义字符不能被解析前的清洗逻辑破坏。"""
        result = _extract_json_object(
            '{"summary":"ok","suggested_reply":"请打开 C:\\Program Files\\DemoApp"}'
        )

        self.assertEqual(result["suggested_reply"], r"请打开 C:\Program Files\DemoApp")

    def test_minimax_parser_accepts_text_block_content(self):
        """兼容网关把 content 返回为文本块数组时仍应提取 JSON。"""
        result = _extract_json_object(
            [
                {"type": "thinking", "text": '{"summary":"不应作为答案解析"}'},
                {"type": "text", "text": '{"summary":"ok","suggested_reply":"reply"}'},
            ]
        )

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["suggested_reply"], "reply")

    def test_minimax_retries_once_after_unparseable_json(self):
        """首次输出非 JSON 时只重试一次，并使用更严格的 JSON 指令。"""
        def response(content):
            """构造一个可供 ``urlopen`` 模拟器读取的响应上下文。"""
            mocked = MagicMock()
            mocked.read.return_value = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode("utf-8")
            mocked.__enter__.return_value = mocked
            return mocked

        with patch(
            "backend.integrations.minimax.urlopen",
            side_effect=[response("我先解释一下。"), response('{"summary":"ok"}')],
        ) as mocked_urlopen:
            client = OpenAICompatibleChatClient("https://model.invalid/v1", "key", "small")
            result, _ = client._complete_json("system", "user")

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(mocked_urlopen.call_count, 2)
        retry_request = mocked_urlopen.call_args_list[1].args[0]
        retry_body = json.loads(retry_request.data.decode("utf-8"))
        self.assertIn("上一次输出无法解析", retry_body["messages"][0]["content"])
        self.assertEqual(retry_body["temperature"], 0.0)

    def test_generate_runs_graph_and_persists_result(self):
        """生成流程应检索知识、调用模型并保存回复建议。"""
        repository = FakeRepository()
        model = FakeModel()
        graph = ReplyGraph(model, StaticKnowledgeProvider())
        service = AIReplyService(repository, graph)

        result = service.generate(
            ReplyRequest(
                public_conversation_id="conv-1",
                staff_userid="staff-1",
                external_userid="customer-1",
            )
        )

        self.assertEqual(result["suggestion_id"], 9)
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["knowledge_refs"][0]["reference_id"], "chunk-1")
        self.assertIn("DEMO901A", model.user_prompt)
        self.assertEqual(repository.limits, [5])
        self.assertEqual(result["recognition_status"], "resolved")
        self.assertEqual(result["diagnostics"]["primary_domain"], "troubleshooting")
        self.assertEqual(result["diagnostics"]["used_references"][0]["reference_id"], "chunk-1")

    def test_primary_miss_only_searches_configured_supplement(self):
        """排障主域无证据时只能补查产品知识，不能扫描其他知识域。"""

        provider = RoutedKnowledgeProvider("product_knowledge")
        service = AIReplyService(FakeRepository(), ReplyGraph(FakeModel(), provider))

        result = service.generate(ReplyRequest("conv-1", "staff-1"))

        self.assertEqual(provider.calls, ["troubleshooting", "product_knowledge"])
        self.assertEqual(result["diagnostics"]["searched_domains"], provider.calls)
        self.assertEqual(result["diagnostics"]["supplement_reason"], "主知识域未检索到证据")

    def test_generate_rejects_context_without_customer_message(self):
        """上下文没有客户消息时不应生成无依据回复。"""
        context = replace(
            sample_context(),
            messages=[
                replace(
                    sample_context().messages[0],
                    sender_kind="internal_staff",
                )
            ],
        )
        service = AIReplyService(
            FakeRepository(context),
            ReplyGraph(FakeModel(), StaticKnowledgeProvider()),
        )
        with self.assertRaisesRegex(ValueError, "没有客户消息"):
            service.generate(
                ReplyRequest("conv-1", "staff-1", external_userid="customer-1")
            )

    def test_graph_rejects_unknown_knowledge_reference(self):
        """模型引用未提供的知识编号时应拒绝该结果。"""
        model = FakeModel()
        model.generate = lambda *_: GeneratedReply(
            summary="摘要",
            customer_intent="意图",
            suggested_reply="回复",
            risk_level="low",
            used_knowledge_ids=["invented-reference"],
            model_name="fake",
            latency_ms=1,
        )
        graph = ReplyGraph(model, StaticKnowledgeProvider())
        with self.assertRaisesRegex(ValueError, "未提供的知识证据"):
            graph.invoke(sample_context())

    def test_intent_expands_from_five_to_ten_customer_messages(self):
        """首轮信息不足时应扩到十条原文重新识别，而不是直接兜底。"""

        contexts = {
            5: replace(sample_context(), customer_message_count=5),
            10: replace(sample_context(), customer_message_count=10),
            20: replace(sample_context(), customer_message_count=12),
        }
        repository = FakeRepository(contexts=contexts)
        model = FakeModel(
            [
                {
                    "recognition_status": "insufficient",
                    "intent": "安装配置",
                    "question": "客户咨询设备配置。",
                    "blockers": ["缺少具体型号"],
                    "evidence_message_ids": [100],
                    "clarification_question": "请确认具体型号。",
                },
                {
                    "recognition_status": "resolved",
                    "intent": "安装配置",
                    "question": "客户询问 DEMO901A 磁场校准步骤。",
                    "product_model": "DEMO901A",
                    "evidence_message_ids": [100],
                },
            ]
        )
        service = AIReplyService(
            repository,
            ReplyGraph(model, StaticKnowledgeProvider()),
        )

        result = service.generate(ReplyRequest("conv-1", "staff-1"))

        self.assertEqual(repository.limits, [5, 10, 20])
        self.assertEqual(len(model.intent_calls), 2)
        self.assertEqual(result["customer_message_count"], 10)
        self.assertFalse(result["need_clarification"])

    def test_twenty_messages_still_unresolved_returns_clarification_only(self):
        """三档上下文均无法识别时只能返回澄清问题，不调用回答模型。"""

        contexts = {
            size: replace(sample_context(), customer_message_count=size)
            for size in (5, 10, 20)
        }
        unresolved = {
            "recognition_status": "conflict",
            "intent": "参数咨询",
            "question": "客户咨询参数，但上下文出现多个型号。",
            "blockers": ["需要确认当前设备型号"],
            "model_candidates": [{"model": "DEMO901A"}, {"model": "DEMO61B"}],
            "evidence_message_ids": [100],
            "clarification_question": "请确认您当前咨询的是 DEMO901A 还是 DEMO61B？",
        }
        model = FakeModel([unresolved, unresolved, unresolved])
        service = AIReplyService(
            FakeRepository(contexts=contexts),
            ReplyGraph(model, StaticKnowledgeProvider()),
        )

        result = service.generate(ReplyRequest("conv-1", "staff-1"))

        self.assertEqual(len(model.intent_calls), 3)
        self.assertEqual(model.reply_calls, [])
        self.assertTrue(result["need_clarification"])
        self.assertIn("DEMO901A", result["suggested_reply"])

    def test_summary_refresh_runs_before_context_loading(self):
        """生成回复必须先刷新摘要，再读取渐进意图上下文。"""

        events = []

        class OrderedRepository(FakeRepository):
            """记录上下文加载顺序的仓储。"""

            def load_context(self, request, limit=5):
                events.append("context")
                return super().load_context(request, limit)

        class FakeSummaryRefresher:
            """记录摘要刷新顺序的服务。"""

            def refresh(self, request):
                events.append("summary")
                return {"skipped": False}

        model = FakeModel()
        service = AIReplyService(
            OrderedRepository(),
            ReplyGraph(model, StaticKnowledgeProvider()),
            FakeSummaryRefresher(),
        )

        service.generate(ReplyRequest("conv-1", "staff-1"))

        self.assertEqual(events[:2], ["summary", "context"])

    def test_latest_returns_ready_state_when_no_saved_suggestion(self):
        """尚无历史建议时应返回可生成回复的就绪状态。"""
        service = AIReplyService(
            FakeRepository(),
            ReplyGraph(FakeModel(), StaticKnowledgeProvider()),
        )
        result = service.latest(
            ReplyRequest("conv-1", "staff-1", external_userid="customer-1")
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["suggested_reply"], "")


class FAQQualityScoreTest(unittest.TestCase):
    """验证 FAQ 候选质量评分的奖励和扣分规则。"""

    def test_complete_evidenced_candidate_scores_high(self):
        """信息完整且带知识依据的候选应获得高分。"""
        score, details = quality_score(
            "DEMO901A 应该如何进行磁场校准？",
            "请先远离磁性物体，再按照已审核说明书中的校准流程操作。",
            "DEMO901A",
            [{"reference_id": "chunk-1"}],
        )

        self.assertEqual(score, 100)
        self.assertTrue(all(details["checks"].values()))

    def test_unverified_commitment_is_penalized(self):
        """缺少依据的绝对承诺应触发明显扣分。"""
        score, details = quality_score(
            "多久一定能修好？",
            "保证当天一定修好并当天寄回。",
            "",
            [],
        )

        self.assertLess(score, 60)
        self.assertFalse(details["checks"]["avoids_unverified_commitment"])


if __name__ == "__main__":
    unittest.main()
