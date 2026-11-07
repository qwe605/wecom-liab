"""AI 回复操作事件和检索轨迹持久化测试。"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.base import Base
from backend.database.models import (
    AIReplyEvent,
    Conversation,
    RetrievalCandidate,
    RetrievalRun,
    User,
)
from backend.database.repositories.ai_reply_repository import SQLAlchemyAIReplyRepository
from backend.database.repositories.effect_repository import EffectRepository
from backend.schemas.ai import (
    ChatMessage,
    ConversationContext,
    GeneratedReply,
    KnowledgeReference,
    ReplyRequest,
)


class EffectEventsTest(unittest.TestCase):
    """验证生成、检索和采纳记录处于统一回复事务。"""

    def setUp(self):
        """创建最小会话、员工和 SQLite Session。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(engine, expire_on_commit=False)
        with self.factory.begin() as session:
            session.add(User(wecom_userid="staff-1", name="客服", role="staff", is_enabled=True))
            session.add(Conversation(id=1, wecom_chat_id="chat-1", conversation_type="single"))
        self.repository = SQLAlchemyAIReplyRepository(self.factory)

    def test_generation_persists_trace_and_feedback_events(self):
        """生成回复必须落检索运行、候选阶段和生成/展示事件。"""

        context = ConversationContext(
            database_conversation_id=1,
            staff_id=0,
            staff_userid="staff-1",
            conversation_type="external_single",
            customer_name="客户",
            messages=[ChatMessage(
                id=10, sender_kind="external_contact", sender_name="客户",
                sender_id="customer-1", content="怎么连接？",
                created_at=datetime.now(timezone.utc),
            )],
        )
        generated = GeneratedReply(
            summary="连接问题", customer_intent="连接通信", suggested_reply="请检查接线。",
            risk_level="low", used_knowledge_ids=["skill:2"], model_name="fake", latency_ms=12,
        )
        references = [KnowledgeReference(
            "skill:2", "连接排查", "请检查接线。",
            metadata={"retrieval": {"lexical_rank": 1, "lexical_score": 4, "final_rank": 1, "rrf_score": 0.016, "strategy_version": "hybrid-small-v1"}},
        )]
        result = self.repository.save_suggestion(
            ReplyRequest("conv_1", "staff-1"), context, generated, references, "prompt-v1",
            {
                "query": "连接",
                "intent": {"intent": "连接通信"},
                "scope": {"product_model": ""},
                "candidates": [{"source_ref": "skill:2", "source_type": "skill", "final_rank": 1, "title": "连接排查", "retrieval": references[0].metadata["retrieval"]}],
                "timings": {"total_retrieval_ms": 5},
            },
        )
        self.repository.record_action("staff-1", result["suggestion_id"], "use", "请先检查接线。")

        with self.factory() as session:
            self.assertEqual(session.query(RetrievalRun).count(), 1)
            self.assertEqual(session.query(RetrievalCandidate).count(), 4)
            event_types = [item.event_type for item in session.query(AIReplyEvent).order_by(AIReplyEvent.id)]
            self.assertEqual(event_types, ["generated", "displayed", "adopted_edited"])

    def test_send_counts_as_adoption_without_double_counting(self):
        """直接发送属于采纳；先采用再发送仍只能计算一条建议。"""

        context = ConversationContext(
            database_conversation_id=1,
            staff_id=0,
            staff_userid="staff-1",
            conversation_type="external_single",
            customer_name="客户",
            messages=[ChatMessage(
                id=11, sender_kind="external_contact", sender_name="客户",
                sender_id="customer-1", content="怎么连接？",
                created_at=datetime.now(timezone.utc),
            )],
        )
        generated = GeneratedReply(
            summary="连接问题", customer_intent="连接通信", suggested_reply="请检查接线。",
            risk_level="low", used_knowledge_ids=[], model_name="fake", latency_ms=1,
        )
        saved = self.repository.save_suggestion(
            ReplyRequest("conv_1", "staff-1"), context, generated, [], "prompt-v1", {}
        )
        self.repository.record_action(
            "staff-1", saved["suggestion_id"], "use", "请检查接线。"
        )
        self.repository.record_action(
            "staff-1", saved["suggestion_id"], "send", "请检查接线。"
        )

        overview = EffectRepository(self.factory).overview(30)
        self.assertEqual(overview["reply"]["adoption_rate"], 1.0)
        self.assertEqual(overview["reply"]["unchanged_adoption_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
