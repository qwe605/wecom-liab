"""审核、检索与回复效果指标仓储。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.database.models import AIReplyEvent, RetrievalRun, SkillReviewEvent, User
from backend.database.session import get_session_factory


class EffectRepository:
    """从不可变事件计算可复现的基础指标，不把采纳率当正确率。"""

    def __init__(self, session_factory=None):
        """注入统一数据库会话工厂。"""

        self.session_factory = session_factory or get_session_factory()

    def get_user(self, userid: str) -> dict | None:
        """返回效果看板权限判断需要的用户字段。"""

        with self.session_factory() as session:
            row = session.get(User, userid)
            if row is None:
                return None
            return {"role": row.role, "is_enabled": row.is_enabled}

    def overview(self, days: int) -> dict:
        """按统一时间窗口聚合回复、检索和审核事件。"""

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        with self.session_factory() as session:
            reply_events = list(session.scalars(
                select(AIReplyEvent)
                .where(AIReplyEvent.created_at >= cutoff)
                .order_by(AIReplyEvent.created_at.asc(), AIReplyEvent.id.asc())
            ))
            retrieval_runs = list(session.scalars(
                select(RetrievalRun).where(RetrievalRun.created_at >= cutoff)
            ))
            review_events = list(session.scalars(
                select(SkillReviewEvent).where(SkillReviewEvent.created_at >= cutoff)
            ))
        reply_counts = Counter(item.event_type for item in reply_events)
        displayed_reply_ids = {
            item.ai_reply_id for item in reply_events if item.event_type == "displayed"
        }
        adoption_events = [
            item
            for item in reply_events
            if item.event_type in {"adopted_edited", "adopted_unchanged", "sent"}
        ]
        adopted_reply_ids = {item.ai_reply_id for item in adoption_events}
        # 同一建议可能先“采用”再“确认发送”。指标按建议去重，编辑比例则取
        # 时间线上最后一次采纳/发送事件，避免一次完整操作被重复计数。
        terminal_adoption = {}
        for item in adoption_events:
            terminal_adoption[item.ai_reply_id] = item
        edit_ratios = [
            float((item.payload_json or {}).get("edit_ratio") or 0)
            for item in terminal_adoption.values()
        ]
        unchanged_reply_ids = {
            reply_id
            for reply_id, item in terminal_adoption.items()
            if float((item.payload_json or {}).get("edit_ratio") or 0) <= 0.01
        }
        displayed = len(displayed_reply_ids)
        adopted = len(adopted_reply_ids)
        review_counts = Counter(item.action for item in review_events)
        reason_counts = Counter(
            reason for item in review_events for reason in (item.reason_codes or [])
        )
        return {
            "window_days": days,
            "reply": {
                "events": dict(reply_counts),
                "adoption_rate": round(adopted / displayed, 4) if displayed else None,
                "unchanged_adoption_rate": (
                    round(len(unchanged_reply_ids) / displayed, 4)
                    if displayed else None
                ),
                "average_edit_ratio": (
                    round(sum(edit_ratios) / len(edit_ratios), 4) if edit_ratios else None
                ),
            },
            "retrieval": {
                "runs": len(retrieval_runs),
                "fallback_runs": sum(bool(item.fallback_reason) for item in retrieval_runs),
                "vector_runs": sum(bool(item.embedding_model) for item in retrieval_runs),
                "strategies": dict(Counter(item.strategy_version for item in retrieval_runs)),
            },
            "review": {
                "events": dict(review_counts),
                "reason_codes": dict(reason_counts),
            },
            "metric_note": "采纳率只衡量可用程度，正确性必须结合固定评测集和引用校验。",
        }
