"""管理员用户管理接口的数据契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


UserRole = Literal["staff", "reviewer", "admin"]


class UserCreateRequest(BaseModel):
    """管理员为企业员工开通系统账号的请求。"""

    wecom_userid: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=255)
    role: UserRole = "staff"


class UserUpdateRequest(BaseModel):
    """管理员修改账号资料、权限、状态或密码的请求。"""

    username: str | None = Field(default=None, min_length=3, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=255)
    role: UserRole | None = None
    is_enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UserUpdateRequest":
        """拒绝没有任何修改字段的空请求。"""

        if not self.model_fields_set:
            raise ValueError("至少需要提供一个修改字段")
        return self


class UserAccountItem(BaseModel):
    """管理页面展示的已开通账号。"""

    wecom_userid: str
    username: str
    name: str
    role: UserRole
    is_enabled: bool
    must_change_password: bool


class UserAccountPage(BaseModel):
    """已开通账号分页。"""

    items: list[UserAccountItem]
    page: int
    page_size: int
    total: int
    pages: int
