"""管理员聊天记录查询和流式导出业务编排。"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from backend.database.repositories import ConversationRepository, MessageRepository
from backend.schemas.chat_records import ChatRecordFilters
from backend.schemas.skill_taxonomy import normalize_taxonomy_path
from backend.database.repositories.summary_repository import SummaryRepository


class ChatRecordService:
    """统一管理会话分页、消息分页和 CSV 导出。"""

    CSV_HEADERS = (
        "会话数据库ID",
        "企业微信会话ID",
        "会话类型",
        "消息归档序号",
        "消息时间",
        "发送者类型",
        "发送者企业微信ID",
        "发送者姓名",
        "消息类型",
        "消息正文",
        "消息JSON",
    )

    def __init__(self, session: Session):
        """绑定请求级数据库会话和现有 Repository。"""

        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.summaries = SummaryRepository(session)

    def create_skill_candidate(
        self,
        conversation_id: int,
        title: str,
        content: str,
        taxonomy_path: list[dict[str, str]],
        source_message_ids: list[int],
        submitted_by: str,
    ) -> dict[str, Any]:
        """从管理员确认的优秀做法创建可追溯待审核候选。"""

        path = normalize_taxonomy_path(taxonomy_path, require_publishable=True)
        row = self.summaries.create_manual_skill_candidate(
            conversation_id,
            title.strip(),
            content.strip(),
            path,
            source_message_ids,
            submitted_by,
        )
        return {"id": row.id, "status": row.status, "title": row.title}

    def list_conversations(
        self,
        filters: ChatRecordFilters,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """返回满足筛选条件的会话分页。

        Args:
            filters: 页面和导出共用筛选条件。
            page: 从 1 开始的页码。
            page_size: 每页会话数量。

        Returns:
            符合 ``ConversationPage`` 的分页字典。
        """

        items, total = self.conversations.list_for_admin(
            filters.conversation_type,
            filters.keyword,
            filters.start_at,
            filters.end_at,
            page,
            page_size,
        )
        return self._page(items, page, page_size, total)

    def list_messages(
        self,
        conversation_id: int,
        start_at,
        end_at,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """返回指定会话的消息分页和会话标题信息。

        Args:
            conversation_id: 会话数据库主键。
            start_at: 可选消息开始时间。
            end_at: 可选消息结束时间。
            page: 从 1 开始的页码。
            page_size: 每页消息数量。

        Returns:
            符合 ``MessagePage`` 的分页字典。

        Raises:
            LookupError: 会话不存在或当前没有消息。
        """

        conversation = self.conversations.admin_conversation_summary(conversation_id)
        if conversation is None:
            raise LookupError("会话不存在或当前没有消息")
        rows, total = self.messages.list_for_admin(
            conversation_id,
            start_at,
            end_at,
            page,
            page_size,
        )
        return {
            "conversation": conversation,
            **self._page(
                self.messages.admin_public_list(rows),
                page,
                page_size,
                total,
            ),
        }

    def stream_csv(
        self,
        filters: ChatRecordFilters,
        conversation_id: int | None = None,
    ) -> Iterator[bytes]:
        """逐批查询并生成带 UTF-8 BOM 的 CSV 字节流。

        Args:
            filters: 与管理页面相同的会话筛选条件。
            conversation_id: 可选的单个会话数据库主键。

        Yields:
            可直接交给 FastAPI ``StreamingResponse`` 的 CSV 字节块。

        """
        yield b"\xef\xbb\xbf"
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(self.CSV_HEADERS)
        yield self._take_buffer(buffer)
        for rows in self.messages.iter_admin_export(
            filters.conversation_type,
            filters.keyword,
            filters.start_at,
            filters.end_at,
            conversation_id,
        ):
            messages = [row[0] for row in rows]
            details = self.messages.admin_public_list(messages)
            for detail, row in zip(details, rows, strict=True):
                writer.writerow(
                    (
                        detail["conversation_id"],
                        self._safe_csv(row[1]),
                        "客户单聊" if row[2] == "single" else "外部群聊",
                        detail["archive_seq"],
                        detail["sent_at"].isoformat(),
                        "员工" if detail["sender_type"] == "staff" else "客户",
                        self._safe_csv(detail["sender_id"]),
                        self._safe_csv(detail["sender_name"]),
                        self._safe_csv(detail["message_type"]),
                        self._safe_csv(detail["content"]),
                        self._safe_csv(
                            json.dumps(
                                detail["content_json"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                                default=str,
                            )
                        ),
                    )
                )
            yield self._take_buffer(buffer)

    def validate_export_target(self, conversation_id: int | None) -> None:
        """在建立流式响应前校验可选的单会话导出目标。

        Args:
            conversation_id: 可选的会话数据库主键。

        Raises:
            LookupError: 指定会话不存在。

        Notes:
            流式生成器只有在响应开始发送后才会执行。校验必须独立出来，才能让
            FastAPI 在响应头发出前稳定返回 HTTP 404。
        """

        if (
            conversation_id is not None
            and self.conversations.get_by_id(conversation_id) is None
        ):
            raise LookupError("会话不存在")

    @staticmethod
    def _page(
        items: list[dict[str, Any]],
        page: int,
        page_size: int,
        total: int,
    ) -> dict[str, Any]:
        """构造统一分页响应。

        Args:
            items: 当前页数据。
            page: 当前页码。
            page_size: 每页数量。
            total: 筛选结果总数。

        Returns:
            含总页数的分页字典。
        """

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    @staticmethod
    def _safe_csv(value: Any) -> Any:
        """阻止用户文本被 Excel 当作公式执行。

        Args:
            value: 待写入 CSV 的值。

        Returns:
            对公式前缀增加单引号后的安全值。
        """

        if not isinstance(value, str):
            return value
        # Excel 可能忽略公式前的空格/控制字符；原始聊天不变，仅保护导出表示。
        if value.startswith(("\t", "\r", "\n")) or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

    @staticmethod
    def _take_buffer(buffer: io.StringIO) -> bytes:
        """取出当前 CSV 缓冲区并立即清空。

        Args:
            buffer: 当前批次共用的文本缓冲区。

        Returns:
            UTF-8 编码的当前批次内容。
        """

        value = buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
        return value
