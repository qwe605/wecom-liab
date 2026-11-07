"""账号注册和用户管理业务规则测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas.users import UserCreateRequest, UserUpdateRequest
from backend.dependencies.auth import password_ready_user
from backend.services.auth_service import AuthService, hash_password, verify_password
from backend.services.user_admin_service import UserAdminService


class FakeUserRepository:
    """以内存用户替代数据库，聚焦服务层权限和唯一性规则。"""

    def __init__(self, users=None):
        """按企业微信员工 ID 保存测试账号。"""

        self.users = {user.wecom_userid: user for user in (users or [])}

    def get_by_wecom_userid(self, wecom_userid):
        """按企业微信员工 ID 查询测试账号。"""

        return self.users.get(wecom_userid)

    def get_by_username(self, username):
        """按登录名查询测试账号。"""

        return next((user for user in self.users.values() if user.username == username), None)

    def save_account(self, **values):
        """创建或更新内存账号并返回实体。"""

        user = self.users.get(values["wecom_userid"]) or SimpleNamespace()
        for key, value in values.items():
            setattr(user, key, value)
        self.users[user.wecom_userid] = user
        return user

    def list_accounts(self, keyword, page, page_size):
        """返回含登录名的测试账号分页。"""

        rows = [user for user in self.users.values() if user.username]
        return rows[(page - 1) * page_size : page * page_size], len(rows)


class UserAdminServiceTest(unittest.TestCase):
    """验证管理员代注册与账号自我保护规则。"""

    def service(self, users=None):
        """构造注入内存 Repository 的用户管理服务。"""

        service = UserAdminService.__new__(UserAdminService)
        service.users = FakeUserRepository(users)
        return service

    def test_create_account_hashes_password(self):
        """注册账号只能落密码哈希，不能保留明文。"""

        service = self.service()
        result = service.create_account(
            UserCreateRequest(
                wecom_userid="staff-1",
                username="staff001",
                name="客服一号",
                password="secure123",
                role="staff",
            )
        )
        stored = service.users.get_by_wecom_userid("staff-1")
        self.assertEqual(result["username"], "staff001")
        self.assertNotEqual(stored.password_hash, "secure123")
        self.assertTrue(verify_password("secure123", stored.password_hash))
        self.assertTrue(stored.must_change_password)

    def test_duplicate_username_and_invalid_password_are_rejected(self):
        """重复登录名和少于八位的密码必须在写库前失败。"""

        existing = SimpleNamespace(
            wecom_userid="staff-1",
            username="existing",
            password_hash="hash",
            name="已有账号",
            role="staff",
            is_enabled=True,
        )
        service = self.service([existing])
        with self.assertRaises(ValueError):
            service.create_account(
                UserCreateRequest(
                    wecom_userid="staff-2",
                    username="existing",
                    name="新账号",
                    password="secure123",
                )
            )
        with self.assertRaises(ValidationError):
            UserCreateRequest(
                wecom_userid="staff-2",
                username="staff002",
                name="新账号",
                password="123",
            )

    def test_current_admin_cannot_disable_or_demote_self(self):
        """当前管理员不能通过管理页面锁死自己的管理权限。"""

        admin = SimpleNamespace(
            wecom_userid="admin-1",
            username="admin",
            password_hash="hash",
            name="管理员",
            role="admin",
            is_enabled=True,
        )
        service = self.service([admin])
        with self.assertRaises(ValueError):
            service.update_account("admin-1", "admin-1", UserUpdateRequest(is_enabled=False))
        with self.assertRaises(ValueError):
            service.update_account("admin-1", "admin-1", UserUpdateRequest(role="staff"))

    def test_initial_password_must_be_changed_before_management(self):
        """初始密码账号改密后才可以通过管理功能依赖。"""

        user = SimpleNamespace(
            wecom_userid="reviewer-1",
            username="reviewer",
            password_hash=hash_password("initial123"),
            name="审核员",
            role="reviewer",
            is_enabled=True,
            must_change_password=True,
        )
        with self.assertRaises(HTTPException) as context:
            password_ready_user(
                {"wecom_userid": user.wecom_userid, "must_change_password": True}
            )
        self.assertEqual(context.exception.status_code, 403)
        auth = AuthService.__new__(AuthService)
        auth.users = FakeUserRepository([user])
        result = auth.change_password("reviewer-1", "initial123", "changed123")
        self.assertFalse(result["must_change_password"])
        self.assertFalse(user.must_change_password)
        self.assertTrue(verify_password("changed123", user.password_hash))
        ready = password_ready_user(
            {"wecom_userid": user.wecom_userid, "must_change_password": False}
        )
        self.assertEqual(ready["wecom_userid"], "reviewer-1")


if __name__ == "__main__":
    unittest.main()
