"""项目结构、路由与前端契约回归测试。"""

from pathlib import Path
import unittest

from backend.app import app


ROOT = Path(__file__).resolve().parents[2]


class ArchitectureContractTest(unittest.TestCase):
    """确保项目只暴露设计书定义的正式入口。"""

    def test_rest_routes_exist_without_legacy_routes(self):
        """正式 REST 路由应存在，旧版兼容路由应彻底移除。"""

        # 使用 OpenAPI 中最终暴露的路径检查契约，兼容 FastAPI 对 IncludedRouter
        # 内部存储方式的版本变化，同时确保测试看到的正是客户端可调用接口。
        paths = set(app.openapi().get("paths", {}))
        expected = {
            "/api/conversations/connect",
            "/api/conversations/{conversation_id}/messages",
            "/api/conversations/{conversation_id}/sync/latest",
            "/api/conversations/{conversation_id}/sync/history",
            "/api/conversations/{conversation_id}/ai-replies",
            "/api/conversations/{conversation_id}/ai-replies/latest",
            "/api/conversations/{conversation_id}/summary",
            "/api/ai-replies/{reply_id}",
            "/api/admin/skills/{skill_id}/publish",
            "/api/admin/skills",
            "/api/admin/skills/{skill_id}",
            "/api/admin/skill-wiki/tree",
            "/api/admin/skill-wiki/pages",
            "/api/admin/review-policy/compile",
            "/api/admin/review-policy/rules",
            "/api/admin/review-policy/rules/{policy_skill_id}",
            "/api/admin/chat-records/conversations",
            "/api/admin/chat-records/conversations/{conversation_id}/messages",
            "/api/admin/chat-records/export",
            "/api/admin/users",
            "/api/admin/users/{wecom_userid}",
            "/api/auth/change-password",
        }
        self.assertTrue(expected.issubset(paths))
        self.assertFalse(
            {
                "/api/wecom/sidebar/session",
                "/api/wecom/conversation/messages",
                "/api/wecom/miniprogram/login",
                "/api/wecom/android/events",
                "/api/ai/replies/action",
            }
            & paths
        )

    def test_vue_uses_only_current_api_contract(self):
        """Vue 页面不得继续调用上一版兼容 API。"""

        source = (ROOT / "frontend" / "src" / "views" / "SidebarView.vue").read_text(
            encoding="utf-8"
        )
        self.assertIn("/api/conversations/connect", source)
        self.assertIn("/sync/latest", source)
        self.assertIn("/ai-replies", source)
        self.assertIn("/api/conversations/${conversationId.value}/summary", source)
        self.assertNotIn("/api/wecom/sidebar/session", source)
        self.assertNotIn("/api/wecom/conversation/messages", source)
        self.assertNotIn("/api/ai/replies/action", source)

    def test_chat_record_admin_is_a_separate_vue_page(self):
        """聊天记录管理必须独立于客服侧边栏并复用正式管理接口。"""

        router_source = (ROOT / "frontend" / "src" / "router" / "index.js").read_text(
            encoding="utf-8"
        )
        records_source = (
            ROOT / "frontend" / "src" / "views" / "ChatRecordsView.vue"
        ).read_text(encoding="utf-8")
        sidebar_source = (
            ROOT / "frontend" / "src" / "views" / "SidebarView.vue"
        ).read_text(encoding="utf-8")
        self.assertIn('path: "/records/"', router_source)
        # 根路径已经统一进入登录页；保持正式契约，不恢复旧的审核台直达入口。
        self.assertIn('{ path: "/", redirect: "/login/" }', router_source)
        self.assertIn("/api/admin/chat-records/conversations", records_source)
        self.assertIn("/api/admin/chat-records/export", records_source)
        self.assertIn('to="/review/"', records_source)
        review_source = (
            ROOT / "frontend" / "src" / "views" / "ReviewView.vue"
        ).read_text(encoding="utf-8")
        self.assertIn('to="/records/"', review_source)
        self.assertIn('/api/auth/me', review_source)
        self.assertNotIn("/api/admin/chat-records", sidebar_source)

    def test_user_management_is_admin_only_separate_page(self):
        """账号注册和管理应位于独立管理员页面并使用正式接口。"""

        router_source = (ROOT / "frontend" / "src" / "router" / "index.js").read_text(
            encoding="utf-8"
        )
        users_source = (
            ROOT / "frontend" / "src" / "views" / "UserManagementView.vue"
        ).read_text(encoding="utf-8")
        self.assertIn('path: "/users/"', router_source)
        self.assertIn("/api/admin/users", users_source)
        self.assertIn("/api/auth/me", users_source)
        self.assertIn('role === "admin"', users_source)
        self.assertIn('result.role === "reviewer"', users_source)
        self.assertIn('router.push("/review/")', users_source)
        change_source = (
            ROOT / "frontend" / "src" / "views" / "ChangePasswordView.vue"
        ).read_text(encoding="utf-8")
        self.assertIn('path: "/change-password/"', router_source)
        self.assertIn("/api/auth/change-password", change_source)
        self.assertIn("must_change_password", change_source)

    def test_removed_frontends_do_not_return(self):
        """原生静态页面和小程序代码不得重新混入 Vue 工程。"""

        removed_files = (
            "auth_server.py",
            "conversation_cache_repository.py",
            "sidebar/index.html",
            "review/index.html",
            "pages/index/index.js",
            "style/weui.wxss",
        )
        for relative in removed_files:
            self.assertFalse((ROOT / relative).is_file(), relative)

    def test_top_level_structure_follows_project_design(self):
        """禁止在正式分层之外恢复旁路业务目录。"""

        forbidden = {"ai_reply", "tools", "database", "pages", "sidebar", "review"}
        actual = {path.name for path in ROOT.iterdir() if path.is_dir()}
        self.assertFalse(forbidden & actual, forbidden & actual)
        required = {"backend", "frontend", "deploy", "docs", "scripts"}
        self.assertTrue(required.issubset(actual))

    def test_repository_layer_does_not_depend_on_services(self):
        """Repository 不得反向依赖 Service，保持单向调用。"""

        for path in (ROOT / "backend" / "database" / "repositories").glob("*.py"):
            self.assertNotIn("backend.services", path.read_text(encoding="utf-8"), path.name)

    def test_runtime_has_only_official_wecom_archive_source(self):
        """运行时代码和部署配置不得重新出现探马消息链路。"""

        forbidden_word = "tan" + "ma"
        roots = (ROOT / "backend", ROOT / "frontend", ROOT / "deploy")
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or "alembic" in path.parts:
                    continue
                if path.suffix.lower() not in {".py", ".js", ".vue", ".sql", ".example"}:
                    continue
                source = path.read_text(encoding="utf-8", errors="ignore").lower()
                self.assertNotIn(forbidden_word, source, str(path))

    def test_summary_runtime_uses_unified_conversation_names(self):
        """正式运行代码不得恢复仅支持群聊的摘要类或旧表名。"""

        roots = (
            ROOT / "backend" / "controllers",
            ROOT / "backend" / "services",
            ROOT / "backend" / "database" / "models",
            ROOT / "backend" / "database" / "repositories",
        )
        for root in roots:
            for path in root.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("GroupSummary", source, str(path))
                self.assertNotIn("group_summaries", source, str(path))

    def test_messages_are_not_removed_by_retention_jobs(self):
        """正式代码和部署配置不得恢复按时间自动删除聊天消息的旧策略。"""

        removed = (
            ROOT / "backend" / "services" / "cache_cleanup_service.py",
            ROOT / "deploy" / "tencentcloud" / "kefubao-cache-cleanup.service",
            ROOT / "deploy" / "tencentcloud" / "kefubao-cache-cleanup.timer",
        )
        for path in removed:
            self.assertFalse(path.exists(), str(path))

        runtime_files = (
            ROOT / "backend" / "config.py",
            ROOT / "backend" / "services" / "message_sync_service.py",
            ROOT / "backend" / "database" / "repositories" / "message_repository.py",
            ROOT / "deploy" / "local" / ".env.example",
            ROOT / "deploy" / "tencentcloud" / "env.example",
            ROOT / "deploy" / "tencentcloud" / "README_DEPLOY.md",
        )
        forbidden = (
            "KEFUBAO_HOT_RETENTION_DAYS",
            "KEFUBAO_HISTORY_CACHE_HOURS",
            "delete_before(",
            "cache_cleanup_service",
        )
        for path in runtime_files:
            source = path.read_text(encoding="utf-8")
            for word in forbidden:
                self.assertNotIn(word, source, str(path))


if __name__ == "__main__":
    unittest.main()
