"""审核结果生成、查看、编辑和发布审核规则 Skill 的闭环测试。"""

from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.base import Base
from backend.database.models import ReviewPolicySource, Skill, SkillReviewEvent, User
from backend.database.repositories.skill_repository import SkillRepository
from backend.database.repositories.summary_repository import SummaryRepository
from backend.services.skill_service import SkillService


BUSINESS_PATH = [
    {"level": "knowledge_domain", "code": "service_script", "name": "客服话术"},
    {"level": "topic", "code": "greeting", "name": "通用问候"},
]


class ReviewPolicyFlowTest(unittest.TestCase):
    """验证业务审核事件不会再成为只能看日志、看不到 Skill 的死数据。"""

    def setUp(self):
        """创建 reviewer/admin 和一条带精确证据的待审核业务 Skill。"""

        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory.begin() as session:
            session.add_all([
                User(wecom_userid="reviewer", name="审核员", role="reviewer", is_enabled=True),
                User(wecom_userid="admin", name="管理员", role="admin", is_enabled=True),
                Skill(
                    id=1,
                    title="项目专属问候",
                    content="仅适用于当前客户项目的问候内容。",
                    taxonomy_path=BUSINESS_PATH,
                    source_message_ids=[101],
                    status="pending",
                    version=1,
                ),
            ])
        self.repository = SkillRepository(self.factory)
        self.service = SkillService(
            self.repository, SimpleNamespace(reviewer_userids=[])
        )

    def test_reject_creates_traceable_policy_draft_and_admin_can_publish(self):
        """驳回与规则草案同事务落库，规则可查看编辑且发布权限仅属于 admin。"""

        result = self.service.review(
            "reviewer",
            1,
            decision="rejected",
            note="只适用于当前客户",
            taxonomy_path=BUSINESS_PATH,
            expected_version=1,
            reason_codes=["not_generalizable"],
        )
        policy_id = result["review_policy_draft_id"]
        self.assertIsNotNone(policy_id)

        detail = self.service.review_policy_detail("reviewer", policy_id)
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["taxonomy_path"][0]["code"], "review_policy")
        self.assertEqual(detail["source_review_events"][0]["skill_id"], 1)
        self.assertIn("不应获取", detail["content"])

        with self.assertRaisesRegex(PermissionError, "只有管理员"):
            self.service.update_review_policy(
                "reviewer", policy_id, "published", detail["title"],
                detail["content"], "", detail["version"],
            )

        published = self.service.update_review_policy(
            "admin", policy_id, "published", detail["title"],
            detail["content"], "人工确认规则边界", detail["version"],
        )
        self.assertEqual(published["status"], "published")

        with self.factory() as session:
            self.assertEqual(session.query(ReviewPolicySource).count(), 1)
            self.assertEqual(session.query(SkillReviewEvent).count(), 2)
            policies = SummaryRepository(session).published_review_policies()
        self.assertEqual([item["id"] for item in policies], [str(policy_id)])

    def test_compile_history_is_idempotent(self):
        """历史补编译重复执行时不得为同一个审核事件生成多个规则 Skill。"""

        # 模拟功能上线前已经存在、但尚未关联审核规则草案的历史事件。
        self.repository.save_review(
            1, "reviewer", "rejected", "重复知识", "", "", BUSINESS_PATH,
            expected_version=1, action="reject", reason_codes=["duplicate"],
        )
        first = self.service.compile_review_policy_drafts("reviewer")
        second = self.service.compile_review_policy_drafts("reviewer")
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        with self.factory() as session:
            policy_count = sum(
                bool(item.taxonomy_path and item.taxonomy_path[0].get("code") == "review_policy")
                for item in session.query(Skill).all()
            )
        self.assertEqual(policy_count, 1)


if __name__ == "__main__":
    unittest.main()
