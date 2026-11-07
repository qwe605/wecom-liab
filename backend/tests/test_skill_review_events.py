"""Skill 审核事件、软删除和乐观锁测试。"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.base import Base
from backend.database.models import Skill, SkillReviewEvent, User
from backend.database.repositories.skill_repository import SkillRepository
from backend.errors import ConcurrencyConflictError


PATH = [
    {"level": "knowledge_domain", "code": "service_script", "name": "客服话术"},
    {"level": "topic", "code": "greeting", "name": "通用问候"},
]


class SkillReviewEventsTest(unittest.TestCase):
    """验证审核状态和不可变事件在一个事务内提交。"""

    def setUp(self):
        """为每个用例创建独立 SQLite 数据库和待审核候选。"""

        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory.begin() as session:
            session.add(User(
                wecom_userid="reviewer-1", name="审核员", role="reviewer", is_enabled=True
            ))
            session.add(Skill(
                id=1,
                source_message_ids=[101],
                title="问候话术",
                content="您好，请问有什么可以帮您？",
                taxonomy_path=PATH,
                status="pending",
                version=1,
            ))
        self.repository = SkillRepository(self.factory)

    def test_review_updates_skill_and_appends_event(self):
        """发布后版本递增，并保存操作前后快照。"""

        result = self.repository.save_review(
            1, "reviewer-1", "published", "证据完整", "问候话术", "您好，请问有什么可以帮您？",
            PATH, expected_version=1, action="publish",
            reason_codes=["evidence_complete"],
            governance_snapshot={"duplicates": [], "conflicts": []},
        )

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["version"], 2)
        with self.factory() as session:
            event = session.query(SkillReviewEvent).one()
            self.assertEqual(event.before_snapshot["status"], "pending")
            self.assertEqual(event.after_snapshot["status"], "published")
            self.assertEqual(event.after_snapshot["version"], 2)

    def test_stale_version_does_not_write_second_event(self):
        """旧版本请求必须整体失败，不能覆盖内容或写入事件。"""

        self.repository.save_review(
            1, "reviewer-1", "rejected", "仅适用于当前客户", "", "", PATH,
            expected_version=1, action="reject",
            reason_codes=["not_generalizable"],
        )
        with self.assertRaises(ConcurrencyConflictError):
            self.repository.save_review(
                1, "reviewer-1", "published", "", "", "", PATH,
                expected_version=1, action="publish", reason_codes=[],
            )

        with self.factory() as session:
            self.assertEqual(session.query(SkillReviewEvent).count(), 1)
            self.assertEqual(session.get(Skill, 1).status, "rejected")


if __name__ == "__main__":
    unittest.main()
