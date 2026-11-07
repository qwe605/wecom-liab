"""Skill 发布前重复、冲突和有效期治理测试。"""

import unittest
from types import SimpleNamespace

from backend.services.skill_service import SkillService


PATH = [
    {"level": "knowledge_domain", "code": "service_script", "name": "客服话术"},
    {"level": "topic", "code": "delay", "name": "等待话术"},
]


class GovernanceRepository:
    """仅实现发布治理测试需要的仓储方法。"""

    def __init__(self, existing, *, status="pending", role="reviewer"):
        self.existing = existing
        self.status = status
        self.role = role
        self.saved = None

    def get_user(self, userid):
        return {"wecom_userid": userid, "name": "审核员", "role": self.role, "is_enabled": True}

    def review_detail(self, skill_id):
        return {"id": skill_id, "title": "请稍候", "content": "您好，请稍候，我马上为您查询。", "taxonomy_path": PATH, "status": self.status, "version": 1, "has_source_evidence": True}

    def published_in_path(self, path, exclude_skill_id):
        self.path = path
        return self.existing

    def save_review(self, *args, **kwargs):
        self.saved = (args, kwargs)
        return {"id": args[0], "status": args[2]}


class SkillGovernanceTest(unittest.TestCase):
    """验证重复阻断与冲突人工确认规则。"""

    def service(self, existing):
        return SkillService(GovernanceRepository(existing), SimpleNamespace(reviewer_userids=[]))

    def test_duplicate_blocks_publish(self):
        existing = [{"id": 8, "title": "请稍候", "content": "您好，请稍候，我马上为您查询。"}]
        with self.assertRaisesRegex(ValueError, "重复知识 skill:8"):
            self.service(existing).publish("reviewer", 9, taxonomy_path=PATH, expected_version=1)

    def test_conflict_requires_review_note(self):
        existing = [{"id": 8, "title": "等待话术", "content": "我们会在明天给您答复。"}]
        with self.assertRaisesRegex(ValueError, "潜在冲突 skill:8"):
            self.service(existing).publish("reviewer", 9, taxonomy_path=PATH, expected_version=1)

    def test_conflict_with_note_can_publish(self):
        existing = [{"id": 8, "title": "等待话术", "content": "我们会在明天给您答复。"}]
        service = self.service(existing)
        result = service.publish(
            "reviewer", 9, note="适用于无法承诺具体时间的场景", taxonomy_path=PATH,
            expected_version=1,
        )
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["governance"]["conflicts"][0]["skill_id"], 8)
        self.assertEqual(
            service.repository.saved[1]["policy_draft"]["taxonomy_path"][0]["code"],
            "review_policy",
        )

    def test_reject_is_terminal_soft_delete(self):
        """驳回候选必须形成终态，不能重新提交或退回侧边栏。"""

        service = self.service([])
        result = service.review(
            "reviewer", 9, decision="rejected", taxonomy_path=PATH,
            expected_version=1, reason_codes=["not_generalizable"],
        )

        self.assertEqual(result["status"], "rejected")
        args, kwargs = service.repository.saved
        self.assertEqual(kwargs["action"], "reject")
        self.assertEqual(kwargs["reason_codes"], ["not_generalizable"])
        self.assertEqual(kwargs["policy_draft"]["taxonomy_path"][0]["code"], "review_policy")
        self.assertIn("不应获取", kwargs["policy_draft"]["content"])

    def test_conflict_rejection_requires_specific_opinion(self):
        """冲突原因不能只打标签，必须保存可供后续治理的人工说明。"""

        with self.assertRaisesRegex(ValueError, "必须填写具体审核意见"):
            self.service([]).review(
                "reviewer", 9, decision="rejected", taxonomy_path=PATH,
                expected_version=1, reason_codes=["conflict"],
            )

    def test_only_admin_can_reopen_archived_skill(self):
        """归档恢复必须由管理员申请，且目标状态只能是 pending。"""

        reviewer_service = SkillService(
            GovernanceRepository([], status="archived", role="reviewer"),
            SimpleNamespace(reviewer_userids=[]),
        )
        with self.assertRaisesRegex(PermissionError, "只有管理员"):
            reviewer_service.review(
                "reviewer", 9, decision="pending", taxonomy_path=PATH,
                expected_version=1,
            )

        admin_service = SkillService(
            GovernanceRepository([], status="archived", role="admin"),
            SimpleNamespace(reviewer_userids=[]),
        )
        result = admin_service.review(
            "admin", 9, decision="pending", taxonomy_path=PATH,
            expected_version=1,
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(admin_service.repository.saved[1]["action"], "reopen")

    def test_archived_skill_can_be_edited_without_restoring(self):
        """归档知识可以保存修订，但保持归档且不重新进入客服检索。"""

        service = SkillService(
            GovernanceRepository([], status="archived", role="reviewer"),
            SimpleNamespace(reviewer_userids=[]),
        )
        result = service.review(
            "reviewer", 9, decision="archived", note="修正归档内容",
            title="修订标题", content="修订后的归档知识正文", taxonomy_path=PATH,
            expected_version=1, reason_codes=["outdated"],
        )

        self.assertEqual(result["status"], "archived")
        self.assertEqual(service.repository.saved[1]["action"], "edit_archived")

    def test_archived_skill_delete_is_terminal_soft_delete(self):
        """删除归档结果只转为 rejected，并继续保留不可变审核链。"""

        service = SkillService(
            GovernanceRepository([], status="archived", role="reviewer"),
            SimpleNamespace(reviewer_userids=[]),
        )
        result = service.review(
            "reviewer", 9, decision="rejected", note="归档后确认无需保留在列表",
            taxonomy_path=PATH, expected_version=1, reason_codes=["outdated"],
        )

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(service.repository.saved[1]["action"], "delete_archived")


if __name__ == "__main__":
    unittest.main()
