"""管理员聊天记录查询、详情和 CSV 导出接口。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database.repositories.summary_repository import SummaryRepository
from backend.database.session import get_db
from backend.dependencies.auth import AdminUser
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.schemas.chat_records import (
    ChatRecordFilters,
    ConversationPage,
    MessagePage,
    SummaryEditRequest,
    SummarySkillCandidateRequest,
)
from backend.services.chat_record_service import ChatRecordService
from backend.services.summary_service import ConversationSummaryService


router = APIRouter(prefix="/api/admin/chat-records", tags=["admin-chat-records"])


def admin_summary_service(session: Session) -> ConversationSummaryService:
    """按项目分层在当前 Controller 构造请求级摘要服务。"""

    settings = get_settings()
    model = OpenAICompatibleChatClient(
        base_url=settings.ai_model_base_url,
        api_key=settings.ai_model_api_key,
        model_name=settings.ai_model_name,
        timeout_seconds=settings.ai_model_timeout_seconds,
    )
    return ConversationSummaryService(SummaryRepository(session), model)


def filters_from_query(
    conversation_type: Literal["single", "group"] | None = Query(default=None),
    keyword: str = Query(default="", max_length=255),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
) -> ChatRecordFilters:
    """把多个 GET 查询参数转换为统一筛选 Schema。

    Returns:
        页面查询和导出共用的筛选对象。
    """

    return ChatRecordFilters(
        conversation_type=conversation_type,
        keyword=keyword,
        start_at=start_at,
        end_at=end_at,
    )


@router.get("/conversations", response_model=ConversationPage)
def conversation_list(
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
    filters: Annotated[ChatRecordFilters, Depends(filters_from_query)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """分页查询数据库中的客户单聊和外部群聊。"""

    return ChatRecordService(session).list_conversations(filters, page, page_size)


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def message_list(
    conversation_id: int,
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
):
    """分页查询指定会话的消息详情。"""

    filters = ChatRecordFilters(start_at=start_at, end_at=end_at)
    return ChatRecordService(session).list_messages(
        conversation_id,
        filters.start_at,
        filters.end_at,
        page,
        page_size,
    )


@router.get("/conversations/{conversation_id}/summary")
def get_conversation_summary(
    conversation_id: int,
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """读取会话纪要和当前未覆盖消息数量。"""

    return admin_summary_service(session).get_for_admin(conversation_id)


@router.post("/conversations/{conversation_id}/summary/refresh")
def refresh_conversation_summary(
    conversation_id: int,
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """按需增量刷新纪要；模型失败时请求事务不会推进摘要边界。"""

    result = admin_summary_service(session).refresh_for_admin(conversation_id)
    return admin_summary_service(session).get_for_admin(conversation_id) | {
        "skipped": result.get("skipped", False)
    }


@router.patch("/conversations/{conversation_id}/summary")
def edit_conversation_summary(
    conversation_id: int,
    body: SummaryEditRequest,
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """保存管理员人工修订，不改变原始聊天或摘要边界。"""

    return admin_summary_service(session).edit_for_admin(
        conversation_id,
        body.last_message_id,
        body.summary_text,
        body.analysis,
    )


@router.post("/conversations/{conversation_id}/skill-candidates")
def create_summary_skill_candidate(
    conversation_id: int,
    body: SummarySkillCandidateRequest,
    user: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """把优秀做法和精确消息证据提交到统一 Skill 审核队列。"""

    return ChatRecordService(session).create_skill_candidate(
        conversation_id,
        body.title,
        body.content,
        body.taxonomy_path,
        body.source_message_ids,
        user["wecom_userid"],
    )


@router.get("/export")
def export_chat_records(
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
    filters: Annotated[ChatRecordFilters, Depends(filters_from_query)],
    conversation_id: int | None = Query(default=None, ge=1),
) -> StreamingResponse:
    """按管理页面当前筛选条件流式导出 UTF-8 CSV。"""

    service = ChatRecordService(session)
    service.validate_export_target(conversation_id)
    # 文件名只包含时间范围和导出时间，不写入客户姓名等敏感信息。
    range_parts = []
    if filters.start_at:
        range_parts.append(filters.start_at.strftime("from-%Y%m%d%H%M%S"))
    if filters.end_at:
        range_parts.append(filters.end_at.strftime("to-%Y%m%d%H%M%S"))
    range_label = "-".join(range_parts) or "all-current"
    filename = datetime.now(timezone.utc).strftime(
        f"chat-records-{range_label}-%Y%m%d-%H%M%S.csv"
    )
    return StreamingResponse(
        service.stream_csv(filters, conversation_id),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
