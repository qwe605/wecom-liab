"""登录鉴权和无状态访问令牌服务。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from sqlalchemy.orm import Session

from backend.config import Settings
from backend.database.repositories import UserRepository
from backend.integrations.wecom import WeComClient


class AuthService:
    """统一处理企业微信 OAuth 和账号密码登录。"""

    def __init__(self, session: Session, settings: Settings, wecom: WeComClient):
        """注入请求级 Session、集中配置和企业微信适配器。"""
        self.session = session
        self.settings = settings
        self.wecom = wecom
        self.users = UserRepository(session)

    def login_with_h5_code(self, code: str) -> dict[str, Any]:
        """使用企业微信 H5 OAuth code 登录。"""

        identity = self.wecom.oauth_user(code)
        return self._login_wecom_identity(identity)

    def login_with_password(self, username: str, password: str) -> dict[str, Any]:
        """使用管理后台账号密码登录。

        Raises:
            PermissionError: 账号不存在、密码错误或账号被禁用。
        """

        user = self.users.get_by_username(username.strip())
        if (
            user is None
            or not user.is_enabled
            or not verify_password(password, user.password_hash or "")
        ):
            raise PermissionError("账号或密码错误")
        return self._session_payload(
            user.wecom_userid,
            user.role,
            user.name,
            user.must_change_password,
        )

    def current_user(self, token: str) -> dict[str, Any]:
        """校验令牌并读取当前启用用户。"""

        claims = decode_token(token, self.settings.app_secret)
        user = self.users.get_by_wecom_userid(str(claims["sub"]))
        if user is None or not user.is_enabled:
            raise PermissionError("用户不存在或已被禁用")
        return {
            "wecom_userid": user.wecom_userid,
            "name": user.name,
            "role": user.role,
            "is_enabled": user.is_enabled,
            "must_change_password": user.must_change_password,
        }

    def change_password(
        self,
        wecom_userid: str,
        current_password: str,
        new_password: str,
    ) -> dict[str, Any]:
        """校验当前密码后更新密码，并解除首次改密状态。

        Raises:
            PermissionError: 当前密码不正确。
            ValueError: 新密码与当前密码相同。
            LookupError: 当前登录用户不存在。
        """

        user = self.users.get_by_wecom_userid(wecom_userid)
        if user is None or not user.password_hash:
            raise LookupError("当前账号没有密码登录能力")
        if not verify_password(current_password, user.password_hash):
            raise PermissionError("当前密码错误")
        if verify_password(new_password, user.password_hash):
            raise ValueError("新密码不能与当前密码相同")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        return {"ok": True, "must_change_password": False}

    def _login_wecom_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        """保存 OAuth 员工映射并生成应用令牌。"""

        userid = str(identity["wecom_userid"])
        user = self.users.ensure_wecom_user(userid)
        if not user.is_enabled:
            raise PermissionError("当前账号已被禁用")
        self.session.flush()
        return self._session_payload(
            user.wecom_userid,
            user.role,
            user.name,
            user.must_change_password,
        )

    def _session_payload(
        self,
        userid: str,
        role: str,
        name: str,
        must_change_password: bool,
    ) -> dict[str, Any]:
        """构造前端使用的统一登录响应。"""

        expires_at = int(time.time()) + 8 * 3600
        token = encode_token(
            {"sub": userid, "role": role, "exp": expires_at},
            self.settings.app_secret,
        )
        return {
            "ok": True,
            "token": token,
            "corp_id": self.settings.wecom_corp_id,
            "staff_userid": userid,
            "name": name,
            "role": role,
            "must_change_password": must_change_password,
            "expires_at": expires_at,
        }


def hash_password(password: str, salt: bytes | None = None) -> str:
    """使用 PBKDF2-SHA256 生成密码哈希。

    Args:
        password: 明文密码。
        salt: 测试或迁移时可传入固定盐；生产默认随机生成。
    """

    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    actual_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), actual_salt, 310_000)
    return "pbkdf2_sha256$310000$%s$%s" % (
        base64.urlsafe_b64encode(actual_salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    """验证 PBKDF2 密码哈希。OAuth-only 账号始终返回 False。"""

    if not encoded.startswith("pbkdf2_sha256$"):
        return False
    try:
        _, rounds, salt, expected = encoded.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            base64.urlsafe_b64decode(salt.encode()),
            int(rounds),
        )
        return hmac.compare_digest(
            base64.urlsafe_b64encode(digest).decode(), expected
        )
    except (ValueError, TypeError):
        return False


def encode_token(claims: dict[str, Any], secret: str) -> str:
    """生成 HMAC 签名的轻量无状态令牌。"""

    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"kefu_{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def decode_token(token: str, secret: str) -> dict[str, Any]:
    """校验令牌签名和有效期。

    Raises:
        PermissionError: 令牌格式、签名或有效期无效。
    """

    try:
        raw = token.removeprefix("kefu_")
        payload, signature = raw.split(".", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        supplied = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise PermissionError("invalid token")
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        )
        if int(claims.get("exp") or 0) <= int(time.time()):
            raise PermissionError("token expired")
        return claims
    except PermissionError:
        raise
    except Exception as exc:
        raise PermissionError("invalid token") from exc


def create_random_password() -> str:
    """为初始化管理员生成一次性随机密码。"""

    return secrets.token_urlsafe(18)
