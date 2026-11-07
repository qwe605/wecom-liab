"""管理员注册和维护客服宝账号的业务服务。"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from backend.database.models import User
from backend.database.repositories import UserRepository
from backend.schemas.users import UserCreateRequest, UserUpdateRequest
from backend.services.auth_service import hash_password


class UserAdminService:
    """维护企业员工与客服宝登录账号之间的一对一映射。"""

    def __init__(self, session: Session):
        """绑定请求级数据库会话。"""

        self.users = UserRepository(session)

    def list_accounts(
        self,
        keyword: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """分页返回已开通用户名的系统账号。"""

        rows, total = self.users.list_accounts(keyword.strip(), page, page_size)
        return {
            "items": [self._public(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    def create_account(self, body: UserCreateRequest) -> dict[str, Any]:
        """为企业员工开通账号，并以密码哈希替代明文落库。"""

        wecom_userid = body.wecom_userid.strip()
        username = body.username.strip()
        name = body.name.strip()
        existing = self.users.get_by_wecom_userid(wecom_userid)
        if existing is not None and existing.username:
            raise ValueError("该企业微信员工已开通账号")
        self._ensure_username_available(username, wecom_userid)
        user = self.users.save_account(
            wecom_userid=wecom_userid,
            username=username,
            password_hash=hash_password(body.password),
            name=name,
            role=body.role,
            is_enabled=True,
            must_change_password=True,
        )
        return self._public(user)

    def update_account(
        self,
        actor_userid: str,
        target_userid: str,
        body: UserUpdateRequest,
    ) -> dict[str, Any]:
        """修改账号并阻止当前管理员误锁定自己的管理权限。"""

        user = self.users.get_by_wecom_userid(target_userid)
        if user is None or not user.username:
            raise LookupError("账号不存在")
        if actor_userid == target_userid:
            if body.is_enabled is False:
                raise ValueError("不能禁用当前登录的管理员账号")
            if body.role is not None and body.role != "admin":
                raise ValueError("不能移除当前登录账号的管理员角色")
        if body.username is not None:
            normalized_username = body.username.strip()
            self._ensure_username_available(normalized_username, target_userid)
            user.username = normalized_username
        if body.name is not None:
            user.name = body.name.strip()
        if body.role is not None:
            user.role = body.role
        if body.is_enabled is not None:
            user.is_enabled = body.is_enabled
        if body.password is not None:
            user.password_hash = hash_password(body.password)
            user.must_change_password = True
        return self._public(user)

    def _ensure_username_available(self, username: str, target_userid: str) -> None:
        """保证登录名没有被其他企业微信员工占用。"""

        owner = self.users.get_by_username(username)
        if owner is not None and owner.wecom_userid != target_userid:
            raise ValueError("登录账号已存在")

    @staticmethod
    def _public(user: User) -> dict[str, Any]:
        """转换为不包含密码哈希的管理页面公开结构。"""

        return {
            "wecom_userid": user.wecom_userid,
            "username": user.username or "",
            "name": user.name,
            "role": user.role,
            "is_enabled": user.is_enabled,
            "must_change_password": user.must_change_password,
        }

    def delete_account(self, actor_userid: str, target_userid: str) -> None:
        """撤销员工登录账号，保留历史数据与企业微信身份关联。"""
        if actor_userid == target_userid:
            raise ValueError("不能删除当前登录的管理员账号")
        user = self.users.get_by_wecom_userid(target_userid)
        if user is None or not user.username:
            raise LookupError("账号不存在")
        self.users.delete_account(target_userid)
