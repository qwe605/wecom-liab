"""聊天记录管理页面、分页查询和导出的公开数据契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChatRecordFilters(BaseModel):
    """会话列表和 CSV 导出共同使用的筛选条件。"""

    conversation_type: Literal["single", "group"] | None = None
    keyword: str = Field(default="", max_length=255)
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ChatRecordFilters":
        """统一时间为无时区 UTC，并校验时间范围。

        Returns:
            已规范化的筛选对象。

        Raises:
            ValueError: 开始时间晚于结束时间。
        """

        self.keyword = self.keyword.strip()
        self.start_at = self._as_utc_naive(self.start_at)
        self.end_at = self._as_utc_naive(self.end_at)
        if self.start_at and self.end_at and self.start_at > self.end_at:
            raise ValueError("开始时间不能晚于结束时间")
        return self

    @staticmethod
    def _as_utc_naive(value: datetime | None) -> datetime | None:
        """把带时区时间转换为 MySQL 使用的无时区 UTC 时间。

        Args:
            value: FastAPI 解析后的可选时间。

        Returns:
            无时区 UTC 时间；空值保持为空。
        """

        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class ParticipantItem(BaseModel):
    """会话列表中的一名员工或客户。"""

    id: str
    name: str


class ConversationListItem(BaseModel):
    """管理后台会话列表的一行。"""

    id: int
    wecom_chat_id: str
    conversation_type: Literal["single", "group"]
    name: str | None = None
    users: list[ParticipantItem]
    customers: list[ParticipantItem]
    message_count: int
    oldest_message_at: datetime
    newest_message_at: datetime


class ConversationPage(BaseModel):
    """会话分页响应。"""

    items: list[ConversationListItem]
    page: int
    page_size: int
    total: int
    pages: int


class AdminMessageItem(BaseModel):
    """管理后台消息详情的一行。"""

    id: int
    conversation_id: int
    archive_seq: int
    sent_at: datetime
    sender_type: Literal["staff", "customer"]
    sender_id: str
    sender_name: str
    message_type: str
    content: str
    content_json: dict[str, Any]


class MessagePage(BaseModel):
    """指定会话的消息分页响应。"""

    conversation: ConversationListItem
    items: list[AdminMessageItem]
    page: int
    page_size: int
    total: int
    pages: int


class SummaryEditRequest(BaseModel):
    """管理员保存人工修订纪要时使用的并发安全请求。"""

    summary_text: str = Field(min_length=1)
    analysis: dict[str, list[str]] = Field(default_factory=dict)
    last_message_id: int = Field(gt=0)


class SummarySkillCandidateRequest(BaseModel):
    """从会话优秀做法提交到统一 Skill 审核队列。"""

    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    taxonomy_path: list[dict[str, str]] = Field(default_factory=list)
    source_message_ids: list[int] = Field(min_length=1)
