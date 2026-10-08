"""个人中心契约（TASK-037）。

★ `ai_key_configured` 是**布尔**，接口永远不返回 key 本身 —— 结构性保证，
  不靠"记得别填"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user_profile import AiProviderKind, PresenceStatus, ThemePreference


class ProfileMe(BaseModel):
    """当前登录者的完整资料。"""

    id: int
    username: str = Field(description="登录名。★ 个人中心不允许改它（改登录凭据要走管理员）")
    display_name: str
    role: str
    role_label: str
    is_active: bool
    avatar_url: str | None = Field(
        default=None, description="头像可访问地址；未上传时为 null（前端显示首字母）"
    )
    presence: PresenceStatus
    presence_label: str
    # ── TASK-040 真实在线状态 ──
    presence_reason: str = Field(
        description="状态依据；如「页面开着，但已 7 分钟没有操作」——只说观测到的事实"
    )
    presence_is_override: bool = Field(
        default=False, description="是否来自手动覆盖（覆盖有到期时间，过期自动恢复推算）"
    )
    presence_override_until: datetime | None = None
    last_active_at: datetime | None = Field(default=None, description="最近一次真实操作时间")
    theme: ThemePreference
    email: str | None = None
    phone: str | None = None
    bio: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    """改个人资料。

    ★ 刻意**不包含** username / role / is_active ——
      把它们开放给"改自己资料"会造成提权（用户能把自己改成管理员）。
    """

    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=300)


class HeartbeatRequest(BaseModel):
    """心跳。★ active 表示这次心跳携带了真实用户操作（前端活动监听触发）。"""

    active: bool = Field(
        default=False,
        description="true=最近有真实操作（鼠标/键盘/滚动/切回标签页）；false=只是页面还开着",
    )
    leaving: bool = Field(
        default=False,
        description="★ 页面正在关闭时发的那一次；为真则立即判为离线，不必等心跳超时",
    )


class PresenceUpdateRequest(BaseModel):
    """手动覆盖状态（带到期时间）。"""

    presence: PresenceStatus
    minutes: int = Field(
        default=60,
        description="覆盖时长（分钟），只能是 30 / 60 / 180 之一；到期自动恢复推算状态",
    )


class ThemeUpdateRequest(BaseModel):
    theme: ThemePreference


class AvatarUploadResponse(BaseModel):
    profile: ProfileMe
    message: str


class AvatarLimits(BaseModel):
    """头像上传限制（前端据此做前置提示，避免用户传完才被拒）。"""

    max_bytes: int
    max_mb: float
    allowed_types: list[str]
    note: str


# ─── AI 供应商 ───

class AiProviderItem(BaseModel):
    kind: AiProviderKind
    label: str
    protocol: str = Field(description="请求协议；决定了适配器怎么发请求")
    docs: str = Field(default="", description="官方文档地址（便于用户查模型名与额度）")
    base_url: str
    model: str
    is_active: bool = Field(description="当前生效的供应商；全局只会有一个")
    has_key: bool = Field(description="★ 是否已配置 API Key；永不返回 key 内容")
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_error: str | None = None


class AiProviderListResponse(BaseModel):
    items: list[AiProviderItem]
    active_kind: AiProviderKind | None = Field(
        default=None, description="当前生效的供应商；null 表示尚未在界面里配置（在用 .env 兜底或不可用）"
    )
    active_source: str = Field(description="DATABASE = 界面里配的；ENV = 回退 .env；NONE = 都没有")
    current_model: str | None = Field(default=None, description="当前实际调用的模型名")
    note: str


class AiProviderUpdateRequest(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(
        default=None,
        description="★ None=不改动（界面留空提交）；空串=清空；非空=替换并作废上次测试结论",
    )


class AiProviderTestResponse(BaseModel):
    provider: AiProviderItem
    ok: bool
    message: str
    elapsed_ms: int
