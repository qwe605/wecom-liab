"""公开内容门禁本身的回归；疑似样例拼接构造，避免看起来像真实联系方式。"""

import unittest

from scripts.check_public_content import path_is_blocked, text_findings


class PublicContentPolicyTest(unittest.TestCase):
    """保证门禁拒绝数据快照/个人信息，同时不误拦必要配置与署名。"""

    def test_runtime_exports_and_credentials_are_rejected(self):
        """原始数据导出、私钥、非示例 env 均不能提交。"""
        for name in ("export.zip", "docs/plan.xlsx", "local-replica/data.sql", "backup.sql", ".env.production", "private.key", "data/messages.jsonl"):
            with self.subTest(name=name):
                self.assertTrue(path_is_blocked(name))

    def test_sources_and_configuration_examples_are_allowed(self):
        """迁移结构与前端配置应可正常提交，不能用全局通配符忽略。"""
        for name in ("frontend/package.json", "frontend/index.html", "deploy/local/.env.example", "backend/database/models/entities.py", "backend/database/migrations/001_initial_schema.sql"):
            with self.subTest(name=name):
                self.assertFalse(path_is_blocked(name))

    def test_contact_and_platform_patterns_are_detected_without_echo(self):
        """报告只含规则与行号，不把被检出的潜在隐私再次写入日志。"""
        phone = "139" + "1234" + "5678"
        platform_id = "ww" + "a" * 16
        for rule, text in (("phone-number", phone), ("personal-email", "staff" + "@internal" + ".test"), ("non-example-platform-id", platform_id)):
            result = text_findings(text)
            self.assertIn((rule, 1), result)
            self.assertNotIn(text, str(result))

    def test_reserved_samples_and_noreply_attribution_are_allowed(self):
        """虚构示例和上游来源署名不应被当作真实个人资料。"""
        self.assertEqual(text_findings("demo@example.invalid\nqwe605@users.noreply.github.com\n" + "ww" + "0" * 16), [])


if __name__ == "__main__":
    unittest.main()
