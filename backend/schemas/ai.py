"""AI 回复链路使用的稳定领域数据结构。

本模块定义了跨服务、跨存储读写的核心数据结构。
这些类都是 frozen dataclass（不可变对象），确保在链路中不会被意外修改，
适合作为跨函数传递的"值对象"而不是"引用对象"。

主要领域对象：
- ChatMessage         ：单条聊天消息（发送者、发送时间、文本内容）
- ConversationContext：一次 AI 回复生成所需的完整上下文
- IntentAnalysis      ：结构化意图、型号证据和澄清状态
- KnowledgeReference ：从知识库检索回来的单条知识证据
- GeneratedReply     ：LLM 生成结果（摘要、意图、建议回复、风险等级）
- ReplyRequest       ：前端发起的 AI 回复请求（会话 ID、客户 ID 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    """单条聊天消息的领域对象。

    来自企业微信会话内容存档 SDK，经过归一化后的统一结构。
    sender_kind 区分"客服员工（internal_staff）"和"客户（external_contact）"，
    前端根据这个字段决定消息气泡靠左还是靠右显示。
    """

    id: int                              # 消息在数据库中的主键
    sender_kind: str                     # 'internal_staff'（员工）或 'external_contact'（客户）
    sender_name: str                     # 发送者显示名（如"张经理"），可能为空
    content: str                         # 消息文本内容
    created_at: datetime                 # 消息原始发送时间（企业微信时间戳）
    sender_id: str = ""                  # 企业微信员工 ID 或外部联系人 ID
    message_type: str = "text"           # 消息类型：text / image / file 等

    @property
    def speaker(self) -> str:
        """人类可读的发送者角色名，用于 AI prompt 构造和前端展示。"""
        return "客服" if self.sender_kind == "internal_staff" else "客户"


@dataclass(frozen=True)
class ConversationContext:
    """一次 AI 回复生成所需的完整上下文。

    在 generate() 调用时从数据库加载，包含：
    - 会话基本信息（客服 ID、客户名、群/单聊类型）
    - 最近 N 条消息（用于生成回复的原料）
    - 已有的 AI 摘要（避免重复摘要已有内容）
    - 最新一条客户消息（用于判断"客户在问什么"）

    这个对象是 AI 生成链路的核心输入，贯穿 retrieve → generate → validate 全流程。
    """

    database_conversation_id: int        # 数据库 conversation 表主键（非 public_conversation_id）
    staff_id: int                       # 当前客服在 staff_accounts 表的 ID
    staff_userid: str                   # 当前客服的企业微信 userid
    conversation_type: str              # 'external_single'（单聊）或 'external_group'（群聊）
    customer_name: str                   # 客户名称（来自 external_contacts 表的 display_name）
    messages: list[ChatMessage]          # 最近的消息列表，按时间正序（ oldest → newest）
    existing_summary: str = ""          # 已有的 AI 摘要（上次生成时保存的），本次可参考避免重复
    trigger_message_id: int | None = None  # 最新一条客户消息的 ID，用于关联本次生成结果
    requester_external_userid: str = "" # 本轮问题发起人的外部联系人 ID
    customer_message_count: int = 0      # 当前窗口包含该客户原始消息的数量

    @property
    def context_from_message_id(self) -> int | None:
        """本次上下文从哪条消息开始（即 messages 中的第一条）。"""
        return self.messages[0].id if self.messages else None

    @property
    def context_to_message_id(self) -> int | None:
        """本次上下文到哪条消息结束（即 messages 中的最后一条）。"""
        return self.messages[-1].id if self.messages else None

    @property
    def latest_customer_message(self) -> ChatMessage | None:
        """最新一条客户消息。

        用于：
        - 构造知识检索 query（客户在问什么？）
        - 判断"回复目标"是谁（reply_target_name）
        - 决定是否需要生成回复（没有客户消息时不应生成）
        """
        return next(
            (item for item in reversed(self.messages) if item.sender_kind == "external_contact"),
            None,
        )


@dataclass(frozen=True)
class IntentAnalysis:
    """意图识别节点输出的稳定结构。

    该对象只在一次回复执行期间流转，不直接映射数据库表。识别失败分为语义信息
    不足和上下文冲突，供工作流决定扩大消息窗口或生成澄清问题。
    """

    recognition_status: str
    reply_target_message_id: int
    requester_external_userid: str
    intent: str
    question: str
    background: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    product_category: str = ""
    product_subcategory: str = ""
    product_model: str = ""
    model_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence_message_ids: list[int] = field(default_factory=list)
    customer_message_count: int = 0
    need_clarification: bool = False
    clarification_question: str = ""


@dataclass(frozen=True)
class KnowledgeReference:
    """从知识库检索回来的单条知识证据。

    知识来源可能是：
    - SQLAlchemyKnowledgeProvider：MySQL 中人工审核发布的 Skill 知识
    - YuqueMcpKnowledgeProvider：通过语雀 MCP 实时查询的产品资料

    AI 生成时应把这条知识的 reference_id 填入 used_knowledge_ids，
    表示这条回复是有证据支撑的，不是凭空编造的。
    """

    reference_id: str                    # 知识唯一标识符（格式如 "db:chunk-key" 或纯数字 chunk_id）
    title: str                           # 知识标题（如 FAQ 问题标题）
    content: str                         # 知识正文内容（截取前 3500 字传入 prompt）
    source_url: str = ""                 # 原始文档 URL，可选
    product_model: str = ""              # 关联的产品型号，用于判断是否匹配客户询问的型号
    risk_level: str = "normal"           # 风险等级：normal / high / critical
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展字段（文档 ID、content_hash 等）

    def public_dict(self) -> dict[str, Any]:
        """返回可暴露给前端的安全字典（不含内部 metadata）。"""
        return {
            "reference_id": self.reference_id,
            "title": self.title,
            "source_url": self.source_url,
            "product_model": self.product_model,
            "risk_level": self.risk_level,
            **self.metadata,
        }


@dataclass(frozen=True)
class GeneratedReply:
    """LLM 生成结果。

    这是 model_client.generate() 的返回值，包含 AI 生成的全部输出字段。
    """

    summary: str                         # 本次会话摘要（"客户问的是 XXX，之前提到YYY"）
    customer_intent: str                 # 客户意图分类（"咨询产品参数"/"投诉质量"等）
    suggested_reply: str                 # 建议回复文本（客服可直接复制/发送）
    risk_level: str                      # 风险等级：low / medium / high
    used_knowledge_ids: list[str]        # AI 参考了的知识 reference_id 列表
    model_name: str                     # 实际调用的模型名称（如 deepseek-chat）
    latency_ms: int                      # 模型调用耗时（毫秒）


@dataclass(frozen=True)
class ReplyRequest:
    """前端发起 AI 回复请求时的请求体结构。

    这些字段全部来自前端传入，不需要从数据库查询。
    public_conversation_id 是前端已建立的会话 ID（conv_xxx 格式）；
    配合 staff_userid 可唯一确定一个会话上下文。
    """

    public_conversation_id: str          # 前端 conversation_id（conv_xxx），非数据库主键
    staff_userid: str                    # 当前客服的企业微信 userid（用于鉴权）
    external_userid: str = ""            # 当前客户的 external_userid（单聊时必填）
    chat_id: str = ""                    # 客户群的 chat_id（群聊时必填）
    regenerate: bool = False             # 是否为重新生成（将基于已有 context 再次生成）
