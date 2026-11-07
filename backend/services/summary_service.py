"""单聊与群聊增量摘要、会话复盘和通用 Skill 二次提炼。"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.database.repositories.summary_repository import SummaryRepository
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.schemas.skill_taxonomy import normalize_taxonomy_path
from backend.schemas.yuque_taxonomy import YUQUE_PRODUCT_CATALOG


# 匹配过度承诺/绝对化用语（保质、时效、退款承诺等）
RISKY_COMMITMENT_PATTERN = re.compile(
    r"(保证|一定|百分之百|绝对|永久|当天必到|肯定能|包您|无条件退款)",
    re.IGNORECASE,
)


ANALYSIS_KEYS = (
    "project_progress",
    "key_communications",
    "blockers",
    "resolution_process",
    "next_actions",
    "good_practices",
    "improvements",
)

# 会话纪要模型的唯一系统提示词。七项复盘的语义边界集中定义在这里，避免
# 设计文档写得很细，而不同摘要入口只给模型一份没有解释的字段名清单。
SUMMARY_SYSTEM_PROMPT = """你是企业客服会话纪要助手。请只依据输入中的已有纪要和新增聊天原文，更新当前会话纪要；不得编造原文没有出现的事实、完成状态、故障原因或承诺。

仅返回一个 JSON 对象，结构如下：
{
  "summary_text": "可连续阅读的整体会话纪要",
  "analysis": {
    "project_progress": [],
    "key_communications": [],
    "blockers": [],
    "resolution_process": [],
    "next_actions": [],
    "good_practices": [],
    "improvements": []
  }
}

analysis 的七个字段必须全部返回，值必须是字符串数组，每个数组元素只表达一项内容。请严格按以下边界整理：
1. project_progress（项目进度）：回答“当前事情已经进行到哪一步”。记录已确认的需求、已完成的操作、当前阶段和已经验证的结果；不得把尚未执行的方案、没有证据的完成状态或尚未兑现的承诺写成进度。
2. key_communications（沟通重点）：回答“本轮双方重点讨论和确认了什么”。记录客户核心诉求、关键参数、产品型号、使用环境和双方明确确认的结论；忽略寒暄、重复表达和无关闲聊。
3. blockers（当前卡点）：回答“目前是什么问题阻止事情继续推进”。记录尚未解决的报错、缺失条件、权限问题、信息冲突和待确认事项；已经解决的问题不得继续列为当前卡点，也不得猜测原文未验证的故障原因。
4. resolution_process（解决过程）：回答“为解决问题已经做过哪些排查，结果如何”。按时间或因果关系记录已经实际执行的操作、反馈结果及已排除的可能性；尚未执行的建议不得写入此项，应写入 next_actions。
5. next_actions（待办事项）：回答“接下来由谁做什么”。记录明确的后续动作、执行方、需要补充的资料和等待反馈的事项；不得重复已完成动作，不写没有责任主体和具体动作的空泛建议。
6. good_practices（优秀做法）：回答“本次沟通中哪些方法经事实证明有效且值得复用”。记录有原始消息依据的排障顺序、客服话术、服务流程或沟通方法；不得包含客户姓名、群名、订单号、当前项目进度等只适用于本会话的信息。该字段只是复盘材料，不代表自动发布为通用 Skill。
7. improvements（改进建议）：回答“本次服务还有哪些地方可以做得更好”。记录基于聊天事实发现的遗漏确认项、表达不清、重复沟通原因和可执行改进；不得进行情绪化评价、人格判断或没有事实依据的批评。

补充规则：
- 当前卡点描述现在为什么无法继续；解决过程描述已经做了什么及结果；待办事项描述下一步还要做什么，三者不得混淆。
- 项目进度和沟通重点用于还原当前会话，不要擅自改写为通用知识。
- 原文无法支持某一项时返回空数组，不得为了填满七项而猜测。
- 合并已有纪要与新增消息时，以新增消息中的最新事实为准；已经被后续消息解决或推翻的旧状态必须更新。
"""

ALLOWED_SKILL_CATEGORIES = {
    "product_knowledge": "产品知识",
    "reply_script": "客服话术",
    "troubleshooting": "排障步骤",
    "service_process": "服务流程",
    "communication_method": "沟通方法",
}


class ConversationSummaryService:
    """把会话私有纪要与可跨会话复用的 Skill 候选严格分离。"""

    def __init__(
        self,
        repository: SummaryRepository,
        model: OpenAICompatibleChatClient,
    ):
        """注入摘要仓储与模型适配器。"""

        self.repository = repository
        self.model = model

    def get(self, conversation_id: int, staff_userid: str) -> dict[str, Any]:
        """查询客服有权访问的当前会话摘要，不触发模型。"""

        conversation, summary = self.repository.get(conversation_id, staff_userid)
        return self._public(conversation, summary)

    def get_for_admin(self, conversation_id: int) -> dict[str, Any]:
        """查询管理后台纪要、结构化复盘和未覆盖消息数量。"""

        conversation, summary, uncovered = self.repository.get_for_admin(conversation_id)
        result = self._public(conversation, summary)
        result["uncovered_message_count"] = uncovered
        return result

    def edit_for_admin(
        self,
        conversation_id: int,
        expected_last_message_id: int,
        summary_text: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """保存人工修订的派生纪要，且不推进增量边界。"""

        normalized = self._normalize_analysis({"analysis": analysis})
        summary = self.repository.save_manual_summary(
            conversation_id,
            expected_last_message_id,
            summary_text.strip(),
            normalized,
        )
        conversation, _, uncovered = self.repository.get_for_admin(conversation_id)
        result = self._public(conversation, summary)
        result["uncovered_message_count"] = uncovered
        return result

    def update(self, conversation_id: int, staff_userid: str) -> dict[str, Any]:
        """增量更新有权访问的会话摘要，并二次提炼通用候选。"""

        context = self.repository.context(conversation_id, staff_userid)
        return self._update(context, submitted_by=staff_userid)

    def refresh_for_reply(
        self,
        conversation_id: int,
        staff_userid: str,
    ) -> dict[str, Any]:
        """生成回复前按需刷新摘要，没有新增消息时直接复用现有结果。

        Args:
            conversation_id: 当前单聊或群聊的数据库主键。
            staff_userid: 当前操作客服的企业微信员工 ID。

        Returns:
            最新摘要；没有新增消息时额外包含 ``skipped=True``。

        Raises:
            PermissionError: 当前客服无权访问会话。
            ValueError: 模型没有生成有效摘要。

        该入口不受后台 20 条阈值限制。摘要、分析、候选和
        ``last_message_id`` 仍在调用方同一事务内提交，任一阶段失败都会回滚。
        """

        context = self.repository.context(conversation_id, staff_userid)
        if not context[2]:
            result = self._public(context[0], context[1])
            result["skipped"] = True
            result["new_message_count"] = 0
            return result

        total_messages = 0
        result: dict[str, Any] | None = None
        # Repository 每批最多返回 200 条。回复前需要追到当前最新边界，因此在
        # 同一事务中连续处理积压；任一批失败会让此前批次和边界一起回滚。
        for _ in range(20):
            messages = context[2]
            if not messages:
                break
            total_messages += len(messages)
            result = self._update(context, submitted_by=staff_userid)
            context = self.repository.context(conversation_id, staff_userid)
        if context[2]:
            raise RuntimeError("会话摘要积压超过单次安全上限，请稍后重试")
        if result is None:
            raise RuntimeError("会话摘要刷新未生成结果")
        result["skipped"] = False
        result["new_message_count"] = total_messages
        return result

    def update_system(
        self,
        conversation_id: int,
        min_new_messages: int = 20,
    ) -> dict[str, Any]:
        """由后台任务更新摘要；增量不足时跳过模型且不冒用客服身份。"""

        context = self.repository.context_for_system(conversation_id)
        if len(context[2]) < max(int(min_new_messages), 1):
            result = self._public(context[0], context[1])
            result["skipped"] = True
            result["new_message_count"] = len(context[2])
            return result
        return self._update(context, submitted_by=None)

    def refresh_for_admin(self, conversation_id: int) -> dict[str, Any]:
        """管理员按需刷新时连续处理积压，直到当前数据库最新边界。"""

        context = self.repository.context_for_system(conversation_id)
        if not context[2]:
            result = self._public(context[0], context[1])
            result.update({"skipped": True, "new_message_count": 0})
            return result
        total_messages = 0
        result: dict[str, Any] | None = None
        for _ in range(20):
            if not context[2]:
                break
            total_messages += len(context[2])
            result = self._update(context, submitted_by=None)
            context = self.repository.context_for_system(conversation_id)
        if context[2]:
            raise RuntimeError("会话摘要积压超过单次安全上限，请稍后重试")
        if result is None:
            raise RuntimeError("会话摘要刷新未生成结果")
        result.update({"skipped": False, "new_message_count": total_messages})
        return result

    def _update(
        self,
        context: tuple[Any, Any | None, list[Any]],
        submitted_by: str | None,
    ) -> dict[str, Any]:
        """执行纪要生成、通用知识提炼和原子持久化编排。"""

        conversation, current, messages = context
        if not messages:
            raise ValueError("当前会话没有新增消息")
        transcript = self._transcript(messages)
        summary_payload = self.model.generate_json(
            SUMMARY_SYSTEM_PROMPT,
            f"已有纪要：\n{current.summary_text if current else '无'}\n\n新增消息：\n{transcript}",
        )
        summary_text = str(
            summary_payload.get("summary_text")
            or summary_payload.get("summary")
            or ""
        ).strip()
        if not summary_text:
            raise ValueError("模型未返回有效会话纪要")
        analysis = self._normalize_analysis(summary_payload)
        summary = self.repository.save_summary(
            conversation,
            current,
            summary_text,
            analysis,
            messages[-1].id,
        )

        # Skill 提炼是独立的第二阶段。完整纪要、项目进度和卡点不会直接写入 Skill。
        review_policies = self.repository.published_review_policies()
        policy_text = "\n\n".join(
            f"[审核规则 skill:{item['id']}] {item['title']}\n{item['content']}"
            for item in review_policies
        )[-12000:] or "当前没有已发布审核规则，按系统基础边界判断。"
        skill_payload = self.model.generate_json(
            "你是企业客服通用知识提炼助手。只提取换一个客户、客服或会话后仍然成立的知识。"
            "必须删除客户姓名、员工姓名、群名、具体项目进度、临时状态、订单信息和未经证实的承诺。"
            "允许类别仅为 product_knowledge、reply_script、troubleshooting、service_process、communication_method。"
            "每条候选必须输出 taxonomy_path。产品知识和排障步骤使用 knowledge_domain、product_category、"
            "product_subcategory、product_model、topic 五级完整路径；无产品维度的客服话术、服务流程和沟通方法"
            "可以使用 knowledge_domain、topic 两级短路径。每个节点包含 level、code、name，禁止产品系列层。"
            "product_category 必须从专业人员确认的语雀一级目录中选择，不得自行创造产品大类；当前目录为："
            + json.dumps({code: item["name"] for code, item in YUQUE_PRODUCT_CATALOG.items()}, ensure_ascii=False)
            + "。product_subcategory 必须对应所选一级目录下的语雀二级文件夹。"
            "只能从本次新增消息提炼，每条候选必须填写 evidence_message_ids，且只能引用输入中的消息 ID。"
            "每条候选还必须输出 ai_score（0-100 的保守自评）和 ai_score_reason；ai_score 不得无依据给满分。"
            "必须遵守输入中已经人工发布的审核规则；规则只影响是否提炼、建议分类和证据要求，不能替你发布候选。"
            "没有可复用知识时返回 {\"skills\":[]}；否则返回 {\"skills\":[{\"category\":\"...\"," 
            "\"taxonomy_path\":[{\"level\":\"...\",\"code\":\"...\",\"name\":\"...\"}],"
            "\"title\":\"...\",\"content\":\"...\",\"evidence_message_ids\":[1,2]}]}，最多 5 条。",
            "已发布审核规则：\n"
            + policy_text
            + "\n\n会话纪要：\n"
            + summary_text
            + "\n\n复盘信息：\n"
            + json.dumps(analysis, ensure_ascii=False)
            + "\n\n本次新增消息：\n"
            + transcript,
        )
        candidates = self._normalize_candidates(
            skill_payload,
            self.repository.sensitive_terms(conversation),
            analysis,
            {item.id for item in messages},
        )
        saved = self.repository.save_skill_candidates(
            conversation,
            candidates,
            submitted_by,
        )
        result = self._public(conversation, summary)
        result["skill_candidate_ids"] = [item.id for item in saved]
        return result

    @staticmethod
    def _transcript(messages: list[Any]) -> str:
        """构造受长度限制的可读增量消息文本。"""

        lines = []
        for item in messages:
            metadata = item.content_json or {}
            sender_name = str(metadata.get("sender_name") or "").strip()
            sender = sender_name or ("员工" if item.sender_wecom_userid else "客户")
            lines.append(
                f"[消息ID={item.id}] {sender}：{item.content_text or '[非文本消息]'}"
            )
        return "\n".join(lines)[-30000:]

    @staticmethod
    def _normalize_analysis(payload: dict[str, Any]) -> dict[str, list[str]]:
        """把模型分析字段收敛为固定的字符串数组结构。"""

        source = payload.get("analysis")
        source = source if isinstance(source, dict) else payload
        normalized: dict[str, list[str]] = {}
        for key in ANALYSIS_KEYS:
            value = source.get(key, [])
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                value = []
            normalized[key] = [str(item).strip() for item in value if str(item).strip()][
                :20
            ]
        return normalized

    @staticmethod
    def _score_candidate(
        title: str,
        content: str,
        category: str,
        analysis: dict[str, list[str]],
        ai_score: float | None = None,
    ) -> float:
        """基于内容维度而非长度的 Skill 候选初评分，仅用于审核排序。

        维度设计思路：
        - 标题精炼（4-20 字）：太短无法概括，太长客服扫不到重点
        - 内容可执行（≥20 字且包含步骤或示例）：说明不只是泛泛而谈
        - 来自真实卡点：群聊中真出现过的问题，提炼方案更有价值
        - 来自成功经验：客服做得好的地方，标准化后可复用
        - 无风险承诺：避免把客服随口保证写进正式知识库
        - 类别维度加权：排障步骤 > 客服话术 > 服务流程 > 沟通方法 > 产品知识
        """

        checks: dict[str, bool] = {
            "title_concise": 4 <= len(title) <= 20,
            "content_actionable": len(content) >= 20,
            "from_blockers": bool(analysis.get("blockers")),
            "from_good_practices": bool(analysis.get("good_practices")),
            "avoids_risky_commitment": not bool(
                RISKY_COMMITMENT_PATTERN.search(content)
            ),
        }
        weights = {
            "title_concise": 15,
            "content_actionable": 25,
            "from_blockers": 25,
            "from_good_practices": 20,
            "avoids_risky_commitment": 15,
        }
        category_bonus = {
            "troubleshooting": 1.0,
            "reply_script": 0.95,
            "service_process": 0.85,
            "communication_method": 0.80,
            "product_knowledge": 0.75,
        }
        raw = sum(
            weights[key] for key, passed in checks.items() if passed
        )
        multiplier = category_bonus.get(category, 0.80)
        rule_score = raw * multiplier
        # AI 评估和透明规则各承担一部分，最终分数不允许由单一模型决定。
        # 缺少 AI 分数时使用中性值，避免历史候选被默认为 100 分。
        model_score = 50.0 if ai_score is None else max(0.0, min(100.0, float(ai_score)))
        return min(99.0, round(rule_score * 0.6 + model_score * 0.4, 2))

    @staticmethod
    def _normalize_candidates(
        payload: dict[str, Any],
        sensitive_terms: set[str],
        analysis: dict[str, list[str]],
        allowed_message_ids: set[int],
    ) -> list[dict[str, Any]]:
        """校验类别、长度和会话专属名称，拒绝伪通用候选。"""

        raw_items = payload.get("skills", [])
        if not isinstance(raw_items, list):
            return []
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        folded_sensitive = {item.casefold() for item in sensitive_terms}
        for raw in raw_items[:5]:
            if not isinstance(raw, dict):
                continue
            category = str(raw.get("category") or "").strip()
            title = str(raw.get("title") or "").strip()
            content = str(raw.get("content") or "").strip()
            evidence_message_ids: list[int] = []
            raw_evidence = raw.get("evidence_message_ids", [])
            if isinstance(raw_evidence, list):
                for value in raw_evidence:
                    try:
                        message_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if (
                        message_id in allowed_message_ids
                        and message_id not in evidence_message_ids
                    ):
                        evidence_message_ids.append(message_id)
            if category not in ALLOWED_SKILL_CATEGORIES:
                continue
            try:
                taxonomy_path = normalize_taxonomy_path(
                    raw.get("taxonomy_path"), require_publishable=True
                )
            except ValueError:
                continue
            expected_domain = (
                "service_script" if category == "reply_script" else category
            )
            if taxonomy_path[0]["code"] != expected_domain:
                continue
            if len(title) < 4 or len(content) < 12 or not evidence_message_ids:
                continue
            try:
                ai_score = float(raw.get("ai_score"))
            except (TypeError, ValueError):
                ai_score = None
            combined = f"{title}\n{content}".casefold()
            if any(term in combined for term in folded_sensitive):
                continue
            identity = (title.casefold(), content.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "title": title,
                    "content": content,
                    "taxonomy_path": taxonomy_path,
                    "source_message_ids": evidence_message_ids,
                    "quality_score": ConversationSummaryService._score_candidate(
                        title, content, category, analysis, ai_score
                    ),
                }
            )
        return candidates

    @staticmethod
    def _public(conversation: Any, summary: Any | None) -> dict[str, Any]:
        """把会话摘要转换为侧边栏稳定响应。"""

        return {
            "conversation_id": conversation.id,
            "conversation_type": conversation.conversation_type,
            "summary_text": summary.summary_text if summary else "",
            "analysis": summary.analysis_json if summary else {},
            "last_message_id": summary.last_message_id if summary else None,
        }
