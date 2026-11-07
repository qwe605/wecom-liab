"""登录和当前用户接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.session import get_db
from backend.dependencies.auth import CurrentUser
from backend.integrations.wecom import WeComClient
from backend.schemas.requests import ChangePasswordRequest, CodeLoginRequest, LoginRequest
from backend.services.auth_service import AuthService


router = APIRouter(tags=["auth"])


def service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    """构造请求级鉴权服务。"""

    return AuthService(session, settings, WeComClient(settings))


@router.post("/api/auth/login")
def password_login(body: LoginRequest, auth: Annotated[AuthService, Depends(service)]):
    """管理后台账号密码登录。"""

    return auth.login_with_password(body.username, body.password)


@router.get("/api/auth/me")
def me(user: CurrentUser):
    """返回当前登录用户。"""

    return user


@router.post("/api/auth/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    auth: Annotated[AuthService, Depends(service)],
):
    """校验原密码后修改当前登录账号的密码。"""

    return auth.change_password(
        user["wecom_userid"],
        body.current_password,
        body.new_password,
    )


@router.post("/api/wecom/h5/login")
def h5_login(body: CodeLoginRequest, auth: Annotated[AuthService, Depends(service)]):
    """使用企业微信 H5 OAuth code 登录侧边栏。"""

    return auth.login_with_h5_code(body.code)
