"""企业微信签名、会话连接和消息同步接口。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.session import get_db
from backend.dependencies.auth import PasswordReadyUser
from backend.integrations.message_archive import build_message_archive
from backend.integrations.wecom import WeComClient
from backend.schemas.requests import ConversationConnectRequest
from backend.services.conversation_service import ConversationService
from backend.services.message_sync_service import MessageSyncService


router = APIRouter(tags=["wecom"])


@router.get("/api/wecom/js-signature")
def js_signature(
    url: str = Query(min_length=1),
    settings: Settings = Depends(get_settings),
):
    """返回企业微信 JS-SDK 双签名。"""

    return WeComClient(settings).js_signature(unquote(url))


@router.post("/api/conversations/connect")
def connect_conversation(
    body: ConversationConnectRequest,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """识别并持久化当前单聊或群聊。"""

    context = {
        "entry": body.entry,
        "raw_context": body.raw_context,
        "raw_contact": body.raw_contact,
        "raw_chat": body.raw_chat,
        "detect_errors": body.detect_errors,
    }
    return ConversationService(session, settings).connect(
        staff_userid=user["wecom_userid"],
        conversation_type=body.conversation_type,
        external_userid=body.external_userid,
        chat_id=body.chat_id,
        context=context,
    )


@router.get("/api/conversations/{conversation_id}/messages")
def messages(
    conversation_id: int,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int = Query(default=100, ge=1, le=1000),
):
    """查询当前用户有权访问的缓存消息。"""

    return MessageSyncService(session, settings).cached(
        conversation_id, user["wecom_userid"], limit
    )


@router.post("/api/conversations/{conversation_id}/sync/latest")
def sync_latest(
    conversation_id: int,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """导入当前会话最新消息。"""

    return MessageSyncService(
        session, settings, build_message_archive(settings)
    ).import_latest(
        conversation_id, user["wecom_userid"]
    )


@router.post("/api/conversations/{conversation_id}/sync/history")
def sync_history(
    conversation_id: int,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    additional_days: int = Query(default=7, ge=1, le=30),
):
    """向前扩展当前会话历史消息。"""

    return MessageSyncService(
        session, settings, build_message_archive(settings)
    ).import_history(
        conversation_id, user["wecom_userid"], additional_days
    )
