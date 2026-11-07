"""请求鉴权依赖。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.session import get_db
from backend.integrations.wecom import WeComClient
from backend.services.auth_service import AuthService


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    """从 Authorization 请求头提取 Bearer token。"""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization[7:].strip()


def current_user(
    token: Annotated[str, Depends(bearer_token)],
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """验证令牌并返回当前用户。"""

    try:
        return AuthService(session, settings, WeComClient(settings)).current_user(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def password_ready_user(user: CurrentUser) -> dict[str, Any]:
    """要求密码账号已经完成首次登录改密。"""

    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="请先修改初始密码")
    return user


PasswordReadyUser = Annotated[dict[str, Any], Depends(password_ready_user)]


def admin_user(user: PasswordReadyUser) -> dict[str, Any]:
    """要求当前登录用户具备管理员角色。

    Args:
        user: 已由 ``current_user`` 验证的用户信息。

    Returns:
        通过管理员校验的原始用户信息。

    Raises:
        HTTPException: 当前用户不是管理员时返回 HTTP 403。
    """

    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以访问该管理功能")
    return user


AdminUser = Annotated[dict[str, Any], Depends(admin_user)]
