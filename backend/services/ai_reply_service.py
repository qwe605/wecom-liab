"""AI 回复业务服务。"""

from __future__ import annotations

from typing import Any, Protocol

from backend.config import Settings
from backend.database.repositories.ai_reply_repository import AIReplyRepository, SQLAlchemyAIReplyRepository
from backend.database.repositories.summary_repository import SummaryRepository
from backend.database.session import session_scope
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.schemas.ai import ReplyRequest
from backend.schemas.identifiers import parse_public_conversation_id
from backend.services.ai_reply_workflow import PROMPT_VERSION, ReplyGraph
from backend.services.knowledge_service import build_knowledge_provider
from backend.services.summary_service import ConversationSummaryService
from backend.schemas.skill_taxonomy import KNOWLEDGE_DOMAINS


class SummaryRefresher(Protocol):
    """AI 回复开始前按需刷新摘要的最小服务契约。"""

    def refresh(self, request: ReplyRequest) -> dict[str, Any]:
        """刷新当前会话摘要并在成功后提交增量边界。"""


class DatabaseSummaryRefresher:
    """使用独立事务完成回复前摘要更新。"""

    def __init__(self, model: OpenAICompatibleChatClient):
        """保存可复用的模型适配器，不保存请求级 Session。"""

        self.model = model

    def refresh(self, request: ReplyRequest) -> dict[str, Any]:
        """按需刷新摘要；失败时由事务上下文回滚 ``last_message_id``。"""

        conversation_id = parse_public_conversation_id(request.public_conversation_id)
        with session_scope() as session:
            return ConversationSummaryService(
                SummaryRepository(session),
                self.model,
            ).refresh_for_reply(conversation_id, request.staff_userid)


class AIReplyService:
    """编排上下文读取、知识检索、模型生成和结果持久化。"""

    def __init__(
        self,
        repository: AIReplyRepository,
        graph: ReplyGraph,
        summary_refresher: SummaryRefresher | None = None,
    ):
        """注入 AI 仓储、渐进工作流和可选的摘要刷新服务。"""
        self.repository = repository
        self.graph = graph
        self.summary_refresher = summary_refresher

    def latest(self, request: ReplyRequest) -> dict[str, Any]:
        """读取当前客服在指定会话中最近保存的建议，不调用模型。"""
        latest = self.repository.latest_suggestion(request)
        if latest:
            return {"conversation_id": request.public_conversation_id, **latest}
        return {
            "conversation_id": request.public_conversation_id,
            "customer_name": "当前客户",
            "summary": "会话已连接，点击生成建议后读取真实聊天记录。",
            "suggested_reply": "",
            "status": "ready",
            "knowledge_refs": [],
        }

    def generate(self, request: ReplyRequest) -> dict[str, Any]:
        """读取上下文、校验客户消息、执行 AI 工作流并保存建议。

        Raises:
            ValueError: 当前会话没有消息或没有可回复的客户消息。
            RuntimeError: 模型或知识集成调用失败。
        """
        if self.summary_refresher is not None:
            self.summary_refresher.refresh(request)

        contexts = []
        for customer_message_limit in (5, 10, 20):
            context = self.repository.load_context(
                request,
                limit=customer_message_limit,
            )
            # 客户历史不足当前档位时，Repository 已经返回其全部原文；后续更大
            # 档位不会产生新信息，因此不重复调用模型。
            if not contexts or context.customer_message_count != contexts[-1].customer_message_count:
                contexts.append(context)
            if context.customer_message_count < customer_message_limit:
                break

        if not contexts or not contexts[0].messages:
            raise ValueError("当前会话暂无可用于生成回复的聊天记录")
        if contexts[0].latest_customer_message is None:
            raise ValueError("当前上下文中没有客户消息，暂不生成回复")
        state = self.graph.invoke(contexts)
        context = state["context"]
        intent = state["intent"]
        generated = state["generated"]
        references = state.get("references", [])
        saved = self.repository.save_suggestion(
            request, context, generated, references, PROMPT_VERSION,
            state.get("retrieval_trace", {}),
        )
        used_ids = set(generated.used_knowledge_ids)
        used_references = [
            item.public_dict() for item in references if item.reference_id in used_ids
        ]
        searched_domains = state.get("searched_domains", [])
        warnings = []
        if intent.need_clarification:
            warnings.append("当前意图或型号信息不足，已生成澄清问题")
        elif not used_references:
            warnings.append("本次回复未采用已审核知识引用，请人工核对")
        return {
            "conversation_id": request.public_conversation_id,
            "customer_name": context.customer_name,
            "summary": generated.summary,
            "customer_intent": generated.customer_intent,
            "recognition_status": intent.recognition_status,
            "customer_message_count": intent.customer_message_count,
            "product_category": intent.product_category,
            "product_subcategory": intent.product_subcategory,
            "product_model": intent.product_model,
            "need_clarification": intent.need_clarification,
            "reply_target_name": context.latest_customer_message.sender_name or context.customer_name,
            "suggested_reply": generated.suggested_reply,
            "risk_level": generated.risk_level,
            "model_name": generated.model_name,
            "latency_ms": generated.latency_ms,
            "diagnostics": {
                "intent": intent.intent,
                "recognition_status": intent.recognition_status,
                "primary_domain": state.get("primary_domain", ""),
                "primary_domain_name": KNOWLEDGE_DOMAINS.get(state.get("primary_domain", ""), ""),
                "supplemental_domains": state.get("supplemental_domains", []),
                "searched_domains": searched_domains,
                "searched_domain_names": [KNOWLEDGE_DOMAINS.get(item, item) for item in searched_domains],
                "supplement_reason": state.get("supplement_reason", ""),
                "product_model": intent.product_model,
                "reference_count": len(used_references),
                "used_references": used_references,
                "warnings": warnings,
            },
            **saved,
        }

    def record_action(
        self, staff_userid: str, suggestion_id: int, action: str, edited_text: str = ""
    ) -> dict[str, Any]:
        """记录客服采纳、编辑或拒绝结果，并返回最新建议状态。"""
        return self.repository.record_action(staff_userid, suggestion_id, action, edited_text)


def build_ai_reply_service(settings: Settings) -> AIReplyService:
    """按集中配置装配 AI 回复服务。"""
    model = OpenAICompatibleChatClient(
        base_url=settings.ai_model_base_url,
        api_key=settings.ai_model_api_key,
        model_name=settings.ai_model_name,
        timeout_seconds=settings.ai_model_timeout_seconds,
    )
    return AIReplyService(
        SQLAlchemyAIReplyRepository(),
        ReplyGraph(model, build_knowledge_provider(settings)),
        DatabaseSummaryRefresher(model),
    )
