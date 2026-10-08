"""用户模型（TASK-019 认证）。★ D11 冻结 7 表中的最后一张，至此 7/7 全部落地。

规格见 docs/AUTH_SPEC.md（范围/口令/会话/失效/受保护范围）。

═══════════════════════════════════════════════════════════════════════
【口令存储】
═══════════════════════════════════════════════════════════════════════
    PBKDF2-HMAC-SHA256，格式：`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`
    ★ 迭代次数写进哈希串，将来上调迭代时**旧哈希仍可验证**（平滑升级）。
    ★ 绝不存明文、绝不存可逆密文。

═══════════════════════════════════════════════════════════════════════
【token_version：无状态令牌的代价必须补上】
═══════════════════════════════════════════════════════════════════════
    会话用自签 HMAC 令牌（不建 sessions 表）。无状态令牌的弱点：
    登出后令牌在过期前仍然有效。所以：
      改密码 / 停用账号 / 管理员踢下线 → token_version += 1
    校验令牌时比对 payload.tv 与库里的值，不符即拒绝。
    ★ 代价是每次鉴权多一次主键查询 —— 这是刻意取舍，换"停用立即生效"。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values
# ★ TASK-037：PresenceStatus / ThemePreference 定义在 user_profile.py，
#   但**挂在 users 表上**（一对一到用户、生命周期一致的字段不单独建表，见该模块说明）。
from app.models.user_profile import PresenceStatus, ThemePreference

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class UserRole(str, Enum):
    """角色。★ 只做两级（AUTH_SPEC §1：不做权限矩阵）。"""

    ADMIN = "ADMIN"    # 可管理账号
    MEMBER = "MEMBER"  # 只能用业务功能


class User(Base):
    """一个登录账号。

    ★ V1 单租户内的多用户（D3 不变：不做 tenants）。
    """

    __tablename__ = "users"

    __table_args__ = (Index("ix_users_username", "username", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: 登录名；★ 统一小写存储，避免 "Admin" 与 "admin" 变成两个账号
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)

    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="pbkdf2_sha256$迭代$盐$哈希；绝不存明文"
    )

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", values_callable=_enum_values),
        nullable=False,
        default=UserRole.MEMBER,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="停用后不许登录，且已发令牌立即失效"
    )

    #: 令牌版本；改密码/停用/踢下线时 +1，使旧令牌立即失效
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    last_login_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)

    # ── TASK-037 个人资料 ──
    #: 头像文件的项目内相对路径（如 uploads/avatars/u1_ab12cd.png）。
    #: ★ 只存路径不存二进制：图片落盘、数据库只记指针，避免大字段进业务表。
    avatar_path: Mapped[str | None] = mapped_column(String(300), nullable=True)

    #: ★ 状态的**手动覆盖**值。TASK-040 起真实状态改为按心跳与活动时间推算
    #:   （见 services/presence.py）；本字段只在 `presence_override_until` 未过期时生效。
    #:   这样既支持"我在开会，先设成忙碌"，又不会因为忘了改而永远显示错误状态。
    presence: Mapped[PresenceStatus] = mapped_column(
        SAEnum(PresenceStatus, name="user_presence", values_callable=_enum_values),
        nullable=False,
        default=PresenceStatus.OFFLINE,
        server_default=PresenceStatus.OFFLINE.value,
    )

    # ── TASK-040 真实在线状态（心跳 + 活动检测）──
    #: 最近一次心跳时间。前端在页面打开期间每 30 秒发一次。
    #: ★ 这是"页面还开着"的证据；超过 OFFLINE_AFTER 没有心跳即判定离线。
    last_seen_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)

    #: 最近一次**真实用户操作**时间（鼠标 / 键盘 / 滚动 / 切回标签页）。
    #: ★ 与 last_seen_at 必须分开：挂机时心跳照发但没人操作，
    #:   只有两者结合才能区分「在线」（人在用）与「忙碌」（页面开着但人不在）。
    last_active_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)

    #: 手动覆盖的到期时间。过期后自动回到按心跳推算的状态。
    #: ★ 必须有过期：手动设的"忙碌"若永不过期，人会忘了改，状态就永远是错的 ——
    #:   那比没有这个功能更糟。
    presence_override_until: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)

    #: 界面主题偏好（跟随系统 / 浅色 / 深色）。存后端是为了换设备也一致。
    theme: Mapped[ThemePreference] = mapped_column(
        SAEnum(ThemePreference, name="user_theme", values_callable=_enum_values),
        nullable=False,
        default=ThemePreference.SYSTEM,
        server_default=ThemePreference.SYSTEM.value,
    )

    #: 联系方式与简介（都可空；不强制填，避免为了"资料完整"逼用户造数据）
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bio: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} {self.username!r} {self.role}>"
