"""认证与账号管理的 Pydantic 契约（TASK-019）。规格见 docs/AUTH_SPEC.md。

★ 安全铁律：`password_hash` **绝不出现在任何响应契约里**。
  下面没有任何一个模型包含这个字段——这是结构性的，不是靠"记得别填"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import UserRole
from app.schemas.evidence import EvidenceValue


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserItem(BaseModel):
    """账号公开信息。★ 没有 password_hash，也没有 token_version（内部字段不外露）。"""

    id: int
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    # ── TASK-040 实时在线状态 ──
    #: 头像地址（列表里显示，避免每个人都用首字母）
    avatar_url: str | None = None
    presence: str = Field(default="OFFLINE", description="按心跳与操作自动推算，非手动设置")
    presence_label: str = Field(default="离线")
    presence_reason: str = Field(default="", description="状态依据；只说观测到的那个事实")
    last_active_at: datetime | None = Field(default=None, description="最近一次真实操作时间")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime = Field(description="令牌过期时间；到期需重新登录（不做 refresh）")
    user: UserItem


class UserListResponse(BaseModel):
    items: list[UserItem]
    total: EvidenceValue


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="登录名；统一按小写存储")
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256, description="至少 8 位")
    role: UserRole = UserRole.MEMBER


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class UserToggleResponse(BaseModel):
    user: UserItem
    message: str
