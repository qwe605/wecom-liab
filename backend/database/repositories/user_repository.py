"""用户、登录身份和角色的 ORM 访问。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.database.models import User


class UserRepository:
    """封装用户表的查询和保存，不处理 HTTP 或密码算法。"""

    def __init__(self, session: Session):
        """绑定请求级 Session，统一管理员与企业微信员工的持久化。"""
        self.session = session

    def get_by_wecom_userid(self, wecom_userid: str) -> User | None:
        """按企业微信员工 ID 查询用户。"""

        return self.session.get(User, wecom_userid)

    def get_by_username(self, username: str) -> User | None:
        """按登录账号查询用户。"""

        return self.session.scalar(select(User).where(User.username == username))

    def list_wecom_userids(self) -> list[str]:
        """返回归档消息和登录流程已经发现的全部员工 ID。"""

        return list(self.session.scalars(select(User.wecom_userid)))

    def list_accounts(
        self,
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[User], int]:
        """分页查询已经开通登录名的账号。

        Args:
            keyword: 可选的姓名、登录名或企业微信员工 ID 关键字。
            page: 从 1 开始的页码。
            page_size: 每页账号数。

        Returns:
            当前页实体列表和总数量。
        """

        conditions = [User.username.is_not(None)]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    User.wecom_userid.like(pattern),
                    User.username.like(pattern),
                    User.name.like(pattern),
                )
            )
        total = int(
            self.session.scalar(select(func.count()).select_from(User).where(*conditions))
            or 0
        )
        rows = list(
            self.session.scalars(
                select(User)
                .where(*conditions)
                .order_by(User.name.asc(), User.wecom_userid.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def sync_display_name(self, wecom_userid: str, name: str) -> bool:
        """使用企业微信通讯录姓名新增或修正员工资料。

        Args:
            wecom_userid: 企业微信员工 ID。
            name: 企业微信官方姓名。

        Returns:
            本次是否新增员工或修改姓名。
        """

        normalized_name = str(name or "").strip()
        if not wecom_userid or not normalized_name:
            return False
        user = self.get_by_wecom_userid(wecom_userid)
        if user is None:
            self.ensure_wecom_user(
                wecom_userid,
                name=normalized_name,
                allow_login=False,
            )
            return True
        if user.name == normalized_name:
            return False
        user.name = normalized_name
        return True

    def ensure_wecom_user(
        self,
        wecom_userid: str,
        name: str = "",
        allow_login: bool = True,
    ) -> User:
        """确保 OAuth 识别到的员工存在。

        Args:
            wecom_userid: 企业微信员工 ID。
            name: 可选员工姓名。
            allow_login: OAuth 登录识别的员工为 ``True``；归档消息自动发现的普通
                群成员为 ``False``，防止仅因出现在消息中就获得系统访问权。

        Returns:
            已存在或新建的员工实体。
        """

        user = self.get_by_wecom_userid(wecom_userid)
        if user is None:
            user = User(
                wecom_userid=wecom_userid,
                username=wecom_userid if allow_login else None,
                password_hash=None,
                name=name or wecom_userid,
                role="staff",
                is_enabled=allow_login,
            )
            self.session.add(user)
            self.session.flush()
        elif name and (not user.name or user.name == user.wecom_userid):
            user.name = name
        # 已存在的禁用账号只能由管理员重新启用，OAuth 或会话请求不得覆盖禁用状态。
        # 归档先发现的未开通员工也需要管理员明确授权，避免隐式扩大登录权限。
        return user

    def save_admin(
        self,
        wecom_userid: str,
        username: str,
        password_hash: str,
        name: str,
    ) -> User:
        """幂等创建或更新管理员账号。"""

        user = self.get_by_wecom_userid(wecom_userid)
        if user is None:
            user = User(wecom_userid=wecom_userid)
            self.session.add(user)
        user.username = username
        user.password_hash = password_hash
        user.name = name
        user.role = "admin"
        user.is_enabled = True
        self.session.flush()
        return user

    def save_account(
        self,
        wecom_userid: str,
        username: str,
        password_hash: str,
        name: str,
        role: str,
        is_enabled: bool,
        must_change_password: bool,
    ) -> User:
        """新增账号或为已同步的企业微信员工补齐登录信息。"""

        user = self.get_by_wecom_userid(wecom_userid)
        if user is None:
            user = User(wecom_userid=wecom_userid)
            self.session.add(user)
        user.username = username
        user.password_hash = password_hash
        user.name = name
        user.role = role
        user.is_enabled = is_enabled
        user.must_change_password = must_change_password
        self.session.flush()
        return user

    def delete_account(self, wecom_userid: str) -> User | None:
        """撤销系统账号但保留员工与历史消息关联。"""
        user = self.get_by_wecom_userid(wecom_userid)
        if user is None:
            return None
        user.username = None
        user.password_hash = None
        user.must_change_password = False
        user.role = "staff"
        user.is_enabled = False
        self.session.flush()
        return user
