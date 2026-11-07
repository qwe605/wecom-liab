"""管理员账号注册和用户管理接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.dependencies.auth import AdminUser
from backend.schemas.users import (
    UserAccountItem,
    UserAccountPage,
    UserCreateRequest,
    UserUpdateRequest,
)
from backend.services.user_admin_service import UserAdminService


router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


@router.get("", response_model=UserAccountPage)
def list_users(
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
    keyword: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """分页查询已经开通系统登录名的账号。"""

    return UserAdminService(session).list_accounts(keyword, page, page_size)


@router.post("", response_model=UserAccountItem)
def create_user(
    body: UserCreateRequest,
    _: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """由管理员为指定企业微信员工注册系统账号。"""

    return UserAdminService(session).create_account(body)


@router.patch("/{wecom_userid}", response_model=UserAccountItem)
def update_user(
    wecom_userid: str,
    body: UserUpdateRequest,
    administrator: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """修改账号资料、角色、启用状态或重置密码。"""

    return UserAdminService(session).update_account(
        administrator["wecom_userid"],
        wecom_userid,
        body,
    )

@router.delete("/{wecom_userid}", status_code=204)
def delete_user(
    wecom_userid: str,
    administrator: AdminUser,
    session: Annotated[Session, Depends(get_db)],
):
    """撤销员工登录账号，保留历史消息数据。"""
    UserAdminService(session).delete_account(administrator["wecom_userid"], wecom_userid)
