"""安全设置契约（TASK-032，需求 §八 系统管理 → 安全设置）。

★ 原则：**只显示真实存在的策略**，不编"安全评分"。
  每一项都来自代码里的实际常量或运行配置（可在源码核对）。
★ 不回显任何密钥：只返回"是否已配置"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SecurityPolicyItem(BaseModel):
    """一条安全策略（只读展示，值来自代码常量/运行配置）。"""

    key: str
    label: str
    value: str = Field(description="当前生效值")
    source: str = Field(description="★ 这个值来自哪里（便于核对）")
    note: str | None = None
    level: str = Field(default="info", description="info / attention / warning")


class SecurityCheckItem(BaseModel):
    """一条自检。★ 只做能真实判断的检查，不编评分。"""

    key: str
    label: str
    passed: bool
    detail: str


class SecuritySettingsResponse(BaseModel):
    policies: list[SecurityPolicyItem]
    checks: list[SecurityCheckItem]
    session: "CurrentSessionInfo"
    note: str


class CurrentSessionInfo(BaseModel):
    """当前登录会话信息（让用户知道自己什么时候会被登出）。"""

    username: str
    display_name: str
    role: str
    token_expires_at: datetime | None = Field(
        default=None, description="当前令牌过期时间；到期需重新登录"
    )
    remaining_hours: float | None = Field(default=None, description="还剩多少小时")
    last_login_at: datetime | None = None


class ChangePasswordRequest(BaseModel):
    """自助改口令。★ 必须验证旧口令 —— 防止令牌被盗后直接改掉密码锁死账号。"""

    old_password: str = Field(min_length=1, description="当前口令（用于验证是本人操作）")
    new_password: str = Field(min_length=8, max_length=128, description="新口令，至少 8 位")


class ChangePasswordResponse(BaseModel):
    message: str
    token_invalidated: bool = Field(description="是否已使旧令牌失效（需要重新登录）")


SecuritySettingsResponse.model_rebuild()
