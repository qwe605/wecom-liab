#!/usr/bin/env python3
"""分批维护单聊与群聊摘要，并提炼可跨会话复用的 Skill 候选。

该进程由轻量 systemd timer 定期检查阈值，与高频企业微信消息同步彻底隔离。
只有新增消息达到配置阈值的群聊才调用模型；单个会话异常只回滚自身事务。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import Settings, get_settings
from backend.database.repositories.summary_repository import SummaryRepository
from backend.database.session import session_scope
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.services.summary_service import ConversationSummaryService


LOGGER = logging.getLogger("kefubao.summary_maintenance")


def _service(settings: Settings, repository: SummaryRepository) -> ConversationSummaryService:
    """使用环境配置构造单个事务内的会话摘要服务。"""

    model = OpenAICompatibleChatClient(
        base_url=settings.ai_model_base_url,
        api_key=settings.ai_model_api_key,
        model_name=settings.ai_model_name,
        timeout_seconds=settings.ai_model_timeout_seconds,
    )
    return ConversationSummaryService(repository, model)


def refresh_stale_summaries() -> dict[str, Any]:
    """扫描一小批新增消息达到阈值的群聊，并逐会话独立提交摘要。

    Returns:
        本轮扫描、更新、跳过和失败数量。失败会话 ID 仅用于运维排查。
    """

    settings = get_settings()
    # 候选 ID 查询使用短事务，避免模型调用期间长期占用同一个数据库事务。
    with session_scope() as session:
        conversation_ids = SummaryRepository(session).stale_conversation_ids(
            settings.summary_batch_size,
            settings.summary_min_new_messages,
        )

    stats: dict[str, Any] = {
        "scanned": len(conversation_ids),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_conversation_ids": [],
    }
    for conversation_id in conversation_ids:
        try:
            # 每个会话独立事务：后续会话不会被前一个模型调用失败连带回滚。
            with session_scope() as session:
                result = _service(settings, SummaryRepository(session)).update_system(
                    conversation_id,
                    min_new_messages=settings.summary_min_new_messages,
                )
            key = "skipped" if result.get("skipped") else "updated"
            stats[key] += 1
        except Exception:
            stats["failed"] += 1
            stats["failed_conversation_ids"].append(conversation_id)
            LOGGER.exception("会话摘要更新失败: conversation_id=%s", conversation_id)
    return stats


def main() -> int:
    """执行一轮群聊消息阈值检查，供 systemd oneshot 服务调用。"""

    logging.basicConfig(level=logging.INFO)
    print(refresh_stale_summaries())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
