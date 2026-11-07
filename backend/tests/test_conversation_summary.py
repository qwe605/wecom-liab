"""会话私有纪要与全局通用 Skill 提炼的业务回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from backend.services.summary_service import (
    ANALYSIS_KEYS,
    SUMMARY_SYSTEM_PROMPT,
    ConversationSummaryService,
)


class FakeModel:
    """按顺序返回预设 JSON，便于确认两个模型阶段互相独立。"""

    def __init__(self, responses: list[dict]):
        """保存预设响应并记录每次模型调用。"""

        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """返回下一份预设响应；响应不足时让测试立即失败。"""

        self.calls.append((system_prompt, user_prompt))
        return self.responses.pop(0)


class FakeSummaryRepository:
    """模拟摘要仓储，分别记录纪要保存和 Skill 候选保存。"""

    def __init__(self, conversation_type: str = "single"):
        """构造一个单聊或群聊及两条文本消息。"""

        self.conversation = SimpleNamespace(
            id=7,
            conversation_type=conversation_type,
            name="客户甲项目群",
        )
        self.current = None
        self.messages = [
            SimpleNamespace(
                id=11,
                content_json={"sender_name": "客服乙"},
                sender_wecom_userid="staff-1",
                content_text="请确认设备离线的排查顺序",
            ),
            SimpleNamespace(
                id=12,
                content_json={"sender_name": "客户甲"},
                sender_wecom_userid=None,
                content_text="先检查供电和网络后已经恢复",
            ),
        ]
        self.saved_summaries: list[dict] = []
        self.saved_candidates: list[dict] = []

    def context(self, conversation_id: int, staff_userid: str):
        """返回客服请求使用的增量上下文。"""

        messages = self.messages
        if (
            self.current is not None
            and messages
            and self.current.last_message_id >= messages[-1].id
        ):
            messages = []
        return self.conversation, self.current, messages

    def context_for_system(self, conversation_id: int):
        """返回后台任务使用的增量上下文。"""

        return self.conversation, self.current, self.messages

    def sensitive_terms(self, conversation) -> set[str]:
        """返回当前会话不能进入通用知识的专属名称。"""

        return {"客户甲", "客服乙", "客户甲项目群"}

    def published_review_policies(self) -> list[dict[str, str]]:
        """返回一条已人工发布的审核规则供候选提炼 Prompt 使用。"""

        return [{
            "id": "901",
            "title": "证据完整性",
            "content": "缺少精确原始消息证据时不得创建候选。",
        }]

    def save_summary(self, conversation, current, summary_text, analysis, last_message_id):
        """记录纪要保存参数并返回与 ORM 实体相同的必要字段。"""

        self.saved_summaries.append(
            {
                "summary_text": summary_text,
                "analysis": analysis,
                "last_message_id": last_message_id,
            }
        )
        self.current = SimpleNamespace(
            summary_text=summary_text,
            analysis_json=analysis,
            last_message_id=last_message_id,
        )
        return self.current

    def save_skill_candidates(self, conversation, candidates, submitted_by):
        """记录已通过 Service 校验的候选并生成测试主键。"""

        self.saved_candidates.extend(candidates)
        return [SimpleNamespace(id=index + 1) for index, _ in enumerate(candidates)]


def summary_payload() -> dict:
    """返回包含会话专属卡点的标准纪要响应。"""

    return {
        "summary_text": "客户设备离线，完成供电和网络检查后恢复。",
        "analysis": {
            "project_progress": ["设备已恢复在线"],
            "blockers": ["现场网络曾中断"],
            "resolution_process": ["检查供电后检查网络"],
        },
    }


class ConversationSummaryServiceTest(unittest.TestCase):
    """验证单聊/群聊摘要和跨会话 Skill 的边界。"""

    def test_summary_prompt_defines_all_seven_analysis_dimensions(self):
        """摘要 Prompt 必须解释七项语义边界，不能只向模型提供字段名。

        该测试锁定设计文档已经承诺的七项分类和关键防编造规则，避免后续
        重构时把详细 Prompt 退化为无语义的 JSON 字段清单。
        """

        for key in ANALYSIS_KEYS:
            self.assertIn(key, SUMMARY_SYSTEM_PROMPT)
        for rule in (
            "当前事情已经进行到哪一步",
            "本轮双方重点讨论和确认了什么",
            "目前是什么问题阻止事情继续推进",
            "为解决问题已经做过哪些排查，结果如何",
            "接下来由谁做什么",
            "哪些方法经事实证明有效且值得复用",
            "本次服务还有哪些地方可以做得更好",
            "原文无法支持某一项时返回空数组",
            "不得为了填满七项而猜测",
        ):
            self.assertIn(rule, SUMMARY_SYSTEM_PROMPT)

    def test_summary_generation_uses_the_seven_dimension_prompt(self):
        """正式摘要调用必须实际传入七项复盘 Prompt，而非闲置常量。"""

        repository = FakeSummaryRepository()
        model = FakeModel([summary_payload(), {"skills": []}])

        ConversationSummaryService(repository, model).update(7, "staff-1")

        self.assertEqual(model.calls[0][0], SUMMARY_SYSTEM_PROMPT)

    def test_single_chat_summary_and_skill_are_two_stages(self):
        """单聊应先保存私有纪要，再通过第二次模型调用保存通用候选。"""

        repository = FakeSummaryRepository("single")
        model = FakeModel(
            [
                summary_payload(),
                {
                    "skills": [
                        {
                            "category": "troubleshooting",
                            "taxonomy_path": [
                                {"level": "knowledge_domain", "code": "troubleshooting", "name": "排障步骤"},
                                {"level": "product_category", "code": "device", "name": "设备"},
                                {"level": "product_subcategory", "code": "general_device", "name": "通用设备"},
                                {"level": "product_model", "code": "general", "name": "通用型号"},
                                {"level": "topic", "code": "offline", "name": "离线排查"},
                            ],
                            "title": "设备离线基础排查",
                            "content": "先检查设备供电状态，再检查网络连接和平台在线状态。",
                            "evidence_message_ids": [11, 12],
                        }
                    ]
                },
            ]
        )

        result = ConversationSummaryService(repository, model).update(7, "staff-1")

        self.assertEqual(result["conversation_type"], "single")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(len(repository.saved_summaries), 1)
        self.assertEqual(len(repository.saved_candidates), 1)
        self.assertEqual(repository.saved_candidates[0]["title"], "设备离线基础排查")
        self.assertEqual(repository.saved_candidates[0]["taxonomy_path"][0]["code"], "troubleshooting")
        self.assertEqual(repository.saved_candidates[0]["source_message_ids"], [11, 12])
        self.assertIn("[审核规则 skill:901]", model.calls[1][1])
        self.assertIn("缺少精确原始消息证据时不得创建候选", model.calls[1][1])

    def test_private_blockers_do_not_become_skill_without_reusable_content(self):
        """项目卡点只保存在纪要中，二次提炼为空时不得创建 Skill。"""

        repository = FakeSummaryRepository()
        model = FakeModel([summary_payload(), {"skills": []}])

        result = ConversationSummaryService(repository, model).update(7, "staff-1")

        self.assertEqual(result["analysis"]["blockers"], ["现场网络曾中断"])
        self.assertEqual(repository.saved_candidates, [])
        self.assertEqual(result["skill_candidate_ids"], [])

    def test_invalid_or_conversation_specific_candidates_are_rejected(self):
        """非法类别和包含客户名的内容应被丢弃，仅保留真正通用的候选。"""

        repository = FakeSummaryRepository()
        model = FakeModel(
            [
                summary_payload(),
                {
                    "skills": [
                        {"category": "project_note", "taxonomy_path": [], "title": "本项目进度", "content": "已经完成现场调试工作。", "evidence_message_ids": [12]},
                        {"category": "reply_script", "taxonomy_path": [{"level": "knowledge_domain", "code": "service_script", "name": "客服话术"}, {"level": "topic", "code": "delay", "name": "等待话术"}], "title": "客户甲回复", "content": "请客户甲稍等，我们马上处理。", "evidence_message_ids": [12]},
                        {
                            "category": "communication_method",
                            "taxonomy_path": [
                                {"level": "knowledge_domain", "code": "communication_method", "name": "沟通方法"},
                                {"level": "topic", "code": "fault_confirmation", "name": "故障确认"},
                            ],
                            "title": "故障沟通顺序",
                            "content": "先复述现象确认理解一致，再说明排查步骤和预计反馈节点。",
                            "evidence_message_ids": [12, 999],
                        },
                    ]
                },
            ]
        )

        ConversationSummaryService(repository, model).update(7, "staff-1")

        self.assertEqual(len(repository.saved_candidates), 1)
        self.assertIn("故障沟通顺序", repository.saved_candidates[0]["title"])
        self.assertEqual(repository.saved_candidates[0]["source_message_ids"], [12])

    def test_candidate_without_publishable_taxonomy_is_rejected(self):
        """模型遗漏分类路径时不得继续生成无体系候选。"""

        repository = FakeSummaryRepository()
        model = FakeModel(
            [
                summary_payload(),
                {
                    "skills": [
                        {
                            "category": "troubleshooting",
                            "title": "设备离线基础排查",
                            "content": "先检查设备供电状态，再检查网络连接状态。",
                            "evidence_message_ids": [11, 12],
                        }
                    ]
                },
            ]
        )

        ConversationSummaryService(repository, model).update(7, "staff-1")

        self.assertEqual(repository.saved_candidates, [])

    def test_background_task_skips_small_increment_without_model_call(self):
        """后台新增消息不足阈值时只返回跳过状态，不浪费模型调用。"""

        repository = FakeSummaryRepository()
        model = FakeModel([])

        result = ConversationSummaryService(repository, model).update_system(
            7,
            min_new_messages=3,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["new_message_count"], 2)
        self.assertEqual(model.calls, [])

    def test_reply_refresh_skips_cleanly_when_summary_is_current(self):
        """回复前没有新增消息时应复用摘要，不调用模型或推进边界。"""

        repository = FakeSummaryRepository()
        repository.current = SimpleNamespace(
            summary_text="已有摘要",
            analysis_json={"blockers": []},
            last_message_id=12,
        )
        repository.messages = []
        model = FakeModel([])

        result = ConversationSummaryService(repository, model).refresh_for_reply(
            7,
            "staff-1",
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["last_message_id"], 12)
        self.assertEqual(model.calls, [])

    def test_reply_refresh_advances_boundary_to_last_processed_message(self):
        """回复前摘要成功后应把边界推进到本批最后一条消息。"""

        repository = FakeSummaryRepository()
        model = FakeModel([summary_payload(), {"skills": []}])

        result = ConversationSummaryService(repository, model).refresh_for_reply(
            7,
            "staff-1",
        )

        self.assertFalse(result["skipped"])
        self.assertEqual(result["last_message_id"], 12)
        self.assertEqual(repository.saved_summaries[0]["last_message_id"], 12)


if __name__ == "__main__":
    unittest.main()
