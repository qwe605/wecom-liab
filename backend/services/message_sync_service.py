"""企业微信全局归档消息同步与本地缓存读取服务。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.repositories import ConversationRepository, MessageRepository, UserRepository
from backend.integrations.message_archive import MessageArchive


class MessageSyncService:
    """把企业微信全局消息页完整路由并幂等写入 MySQL。"""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        archive: MessageArchive | None = None,
    ):
        """注入事务、配置和可选的官方归档适配器。"""

        self.session = session
        self.settings = settings
        self.archive = archive
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.users = UserRepository(session)

    def cached(self, conversation_id: int, staff_userid: str, limit: int = 500) -> dict[str, Any]:
        """只从 MySQL 返回当前客服有权访问的会话消息。"""

        conversation = self.conversations.get_authorized(conversation_id, staff_userid)
        rows = self.messages.list_recent(conversation.id, limit)
        coverage = self.messages.conversation_coverage(conversation.id)
        state = self.messages.get_archive_state(self.settings.wecom_archive_seed_seq)
        oldest_at = coverage["oldest_at"]
        newest_at = coverage["newest_at"]
        cached_days = 0.0
        if oldest_at and newest_at:
            cached_days = max(0.0, (newest_at - oldest_at).total_seconds() / 86400)
        return {
            "database_conversation_id": conversation.id,
            "messages": self.messages.public_list(rows),
            "coverage": {
                "cached_days": round(cached_days, 1),
                "oldest_message_at": oldest_at.isoformat() if oldest_at else None,
                "newest_message_at": newest_at.isoformat() if newest_at else None,
                "oldest_scanned_seq": coverage["lower_seq"],
                "newest_scanned_seq": coverage["upper_seq"],
                "global_lower_seq": int(state.lower_seq),
                "global_upper_seq": int(state.upper_seq),
                "is_latest": bool(state.reached_end),
                "latest_sync_status": "idle",
                "backfill_status": "idle",
            },
        }

    def sync_next_page(self) -> dict[str, Any]:
        """拉取并提交企业微信全局消息流的下一页。

        本方法不接收会话 ID。页面内所有可识别的外部单聊和群聊都会自动建档并
        落库。任何消息路由、关联或写入失败都会回滚当前事务，因此调用方只有在
        整页处理成功后提交事务，``upper_seq`` 才会真实前进。
        """

        if self.archive is None:
            raise RuntimeError("同步消息时必须提供企业微信归档适配器")
        state = self.messages.get_archive_state(self.settings.wecom_archive_seed_seq)
        start_seq = int(state.upper_seq)
        records = self.archive.fetch_page(start_seq, self.settings.wecom_archive_page_size)
        if not records:
            state.reached_end = True
            return {
                "mode": "latest",
                "scanned": 0,
                "imported": 0,
                "skipped": 0,
                "next_seq": start_seq,
                "is_latest": True,
                "conversation_imported": {},
            }

        result = self._ingest_records(records)
        page_upper = max(int(item["archive_seq"]) for item in records)
        if page_upper <= start_seq:
            raise RuntimeError("企业微信归档页未推进全局游标")
        if int(state.lower_seq) == int(state.upper_seq) == start_seq:
            state.lower_seq = min(int(item["archive_seq"]) for item in records)
        state.upper_seq = page_upper
        state.reached_end = False
        return {
            "mode": "latest",
            "scanned": len(records),
            "imported": result["imported"],
            "skipped": result["skipped"],
            "next_seq": page_upper,
            "is_latest": False,
            "conversation_imported": result["conversation_imported"],
        }

    def import_latest(self, conversation_id: int, staff_userid: str) -> dict[str, Any]:
        """保留侧边栏手动导入入口，并复用企业级全局同步逻辑。"""

        conversation = self.conversations.get_authorized(conversation_id, staff_userid)
        result = self.sync_next_page()
        counts = result.get("conversation_imported") or {}
        result["matched"] = int(counts.get(str(conversation.id), 0))
        return result

    def import_history(
        self,
        conversation_id: int,
        staff_userid: str,
        additional_days: int,
    ) -> dict[str, Any]:
        """保留人工补历史入口，从全局下界以前读取并幂等补库。

        官方接口使用序号而非日期分页，因此天数只用于估算回退窗口。所有补到的
        外部会话仍按全局路由规则落库，不会只保留当前页面会话。
        """

        self.conversations.get_authorized(conversation_id, staff_userid)
        if self.archive is None:
            raise RuntimeError("同步消息时必须提供企业微信归档适配器")
        state = self.messages.get_archive_state(self.settings.wecom_archive_seed_seq)
        current_lower = int(state.lower_seq)
        days = min(max(int(additional_days), 1), 30)
        step = max(1000, int(self.settings.wecom_archive_window_step))

        # seed_seq 只负责首次启动，不能同时充当历史回溯的硬下界。
        # 企业微信 GetChatData 只能从给定 seq 向后读取，因此需要从估算下界开始
        # 连续翻页，直到重新接上当前 lower_seq，才能形成完整的历史区间。
        cursor = max(0, current_lower - days * step)
        scanned = imported = skipped = pages = 0
        oldest_imported_seq: int | None = None
        max_pages = max(50, days * 50)
        while cursor < current_lower and pages < max_pages:
            records = self.archive.fetch_page(
                cursor,
                self.settings.wecom_archive_page_size,
            )
            pages += 1
            if not records:
                break
            scanned += len(records)
            historical = [
                item
                for item in records
                if cursor < int(item["archive_seq"]) < current_lower
            ]
            result = self._ingest_records(historical)
            imported += int(result["imported"])
            skipped += int(result["skipped"])
            if historical:
                page_lower = min(int(item["archive_seq"]) for item in historical)
                oldest_imported_seq = (
                    page_lower
                    if oldest_imported_seq is None
                    else min(oldest_imported_seq, page_lower)
                )

            next_cursor = max(int(item["archive_seq"]) for item in records)
            if next_cursor <= cursor:
                raise RuntimeError("企业微信历史归档页未推进游标")
            cursor = next_cursor

        if cursor < current_lower and pages >= max_pages:
            raise RuntimeError("历史消息页数超过安全上限，请缩小导入范围后重试")
        if oldest_imported_seq is not None:
            state.lower_seq = min(current_lower, oldest_imported_seq)
        return {
            "mode": "older",
            "scanned": scanned,
            "imported": imported,
            "skipped": skipped,
            "pages": pages,
            "lower_cursor": int(state.lower_seq),
        }

    def _ingest_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """路由一组规范消息并建立会话、成员和消息记录。"""

        imported = skipped = 0
        conversation_imported: dict[str, int] = {}
        for item in records:
            conversation = self._route_conversation(item)
            if conversation is None:
                skipped += 1
                continue
            _, created = self.messages.upsert_normalized(conversation.id, item)
            if created:
                imported += 1
                key = str(conversation.id)
                conversation_imported[key] = conversation_imported.get(key, 0) + 1
        return {
            "imported": imported,
            "skipped": skipped,
            "conversation_imported": conversation_imported,
        }

    def _route_conversation(self, item: dict[str, Any]):
        """根据官方 roomid 和参与者 ID 将消息归入唯一会话。"""

        participant_ids = {
            str(value).strip()
            for value in item.get("participant_ids") or []
            if str(value).strip()
        }
        sender_id = str(item.get("from") or "").strip()
        if sender_id:
            participant_ids.add(sender_id)
        customer_ids = sorted(value for value in participant_ids if self._is_external(value))
        staff_ids = sorted(value for value in participant_ids if not self._is_external(value))
        room_id = str(item.get("chat_id") or "").strip()

        if room_id:
            conversation = self.conversations.get_or_create_group(room_id)
        elif staff_ids and customer_ids:
            conversation = self.conversations.get_or_create_single(staff_ids[0], customer_ids[0])
        else:
            # 无 roomid 且不含外部联系人的内部消息不属于客服宝业务会话。
            return None

        for staff_id in staff_ids:
            user = self.users.ensure_wecom_user(staff_id, allow_login=False)
            self.conversations.link_user(conversation.id, user)
        for customer_id in customer_ids:
            customer = self.conversations.ensure_customer(customer_id)
            self.conversations.link_customer(conversation.id, customer)
        return conversation

    @staticmethod
    def _is_external(identifier: str) -> bool:
        """判断企业微信 ID 是否属于外部联系人。"""

        return identifier.startswith(("wm", "wo"))
