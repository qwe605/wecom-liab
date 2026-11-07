"""公开 API 请求体定义。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LoginRequest(BaseModel):
    """账号密码登录请求。"""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=255)


class CodeLoginRequest(BaseModel):
    """企业微信 H5 OAuth 登录请求。"""

    code: str = Field(min_length=1, max_length=255)


class ChangePasswordRequest(BaseModel):
    """当前登录用户修改自己密码的请求。"""

    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)


class ConversationConnectRequest(BaseModel):
    """连接当前企业微信会话。"""

    conversation_type: Literal["single", "single_external_contact", "group", "external_group_chat"] = "single_external_contact"
    external_userid: str = ""
    chat_id: str = ""
    entry: str = ""
    raw_context: dict[str, Any] = Field(default_factory=dict)
    raw_contact: dict[str, Any] = Field(default_factory=dict)
    raw_chat: dict[str, Any] = Field(default_factory=dict)
    detect_errors: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_subject(self) -> "ConversationConnectRequest":
        """确保单聊或群聊标识存在。"""

        if self.conversation_type in {"group", "external_group_chat"}:
            if not self.chat_id:
                raise ValueError("群聊缺少 chat_id")
        elif not self.external_userid:
            raise ValueError("单聊缺少 external_userid")
        return self


class ReplyGenerateRequest(BaseModel):
    """生成或重新生成 AI 回复。"""

    regenerate: bool = False


class ReplyActionRequest(BaseModel):
    """客服对 AI 回复的操作。"""

    action: str
    edited_text: str = ""


class SkillSubmitRequest(BaseModel):
    """将采纳回复提交为 Skill 候选。"""

    conversation_id: int = Field(gt=0)
    suggestion_id: int = Field(gt=0)
    answer_text: str = Field(min_length=1)
    product_model: str = ""
    taxonomy_path: list[dict[str, str]] = Field(default_factory=list)


class SkillReviewRequest(BaseModel):
    """人工审核请求。"""

    decision: str
    note: str = ""
    question_text: str = ""
    answer_text: str = ""
    taxonomy_path: list[dict[str, str]] = Field(default_factory=list)
    expires_at: datetime | None = None
    expected_version: int = Field(ge=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=10)
