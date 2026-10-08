"""鉴权依赖（TASK-019）。规格见 docs/AUTH_SPEC.md §4~§7。

═══════════════════════════════════════════════════════════════════════
【为什么"无状态"令牌还要查一次库】
═══════════════════════════════════════════════════════════════════════
    纯无状态令牌的弱点是：登出/停用/改密码后，旧令牌在过期前依然有效。
    所以校验时比一次 users.token_version 与 is_active，不符即 401。
    ★ 代价：每次鉴权多一次主键查询。这是**刻意**取舍，换"停用立即生效"。

═══════════════════════════════════════════════════════════════════════
【401 / 403 的分工】
═══════════════════════════════════════════════════════════════════════
    401 = 没登录 / 令牌无效 / 令牌已失效（token_version 不符、账号停用）
    403 = 已登录但角色不足（如 MEMBER 访问管理员接口）
"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.errors import ApiErrorCode, ApiFailure

#: 未配置密钥时的统一说明。★ 不提供默认密钥：偷偷用弱默认值比不做鉴权更危险。
NO_SECRET_HINT = (
    "服务端未配置 AUTH_SECRET_KEY，无法校验登录状态。"
    "请在 .env 中设置 AUTH_SECRET_KEY（随机长字符串）后重启服务。"
)


def _bearer_token(authorization: str | None) -> str:
    """从 Authorization 头取出 Bearer 令牌。"""
    if not authorization:
        raise ApiFailure(ApiErrorCode.UNAUTHORIZED, "需要登录：缺少 Authorization 头", http_status=401)
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise ApiFailure(
            ApiErrorCode.UNAUTHORIZED,
            "Authorization 头格式应为：Bearer <token>",
            http_status=401,
        )
    return parts[1].strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """当前登录用户。任何业务接口都依赖它。"""
    secret = (settings.auth_secret_key or "").strip()
    if not secret:
        # ★ 没配密钥时如实报错（500 语义由接口层决定），绝不"放行所有请求"
        raise ApiFailure(ApiErrorCode.AUTH_NOT_CONFIGURED, NO_SECRET_HINT, http_status=500)

    token = _bearer_token(authorization)
    try:
        payload = decode_token(token, secret)
    except TokenError as exc:
        raise ApiFailure(ApiErrorCode.UNAUTHORIZED, exc.reason, http_status=401)

    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise ApiFailure(ApiErrorCode.UNAUTHORIZED, "登录用户不存在，请重新登录", http_status=401)

    # ★ 强制失效：改密码/停用/踢下线都会让 token_version 自增
    if int(payload.get("tv", -1)) != user.token_version:
        raise ApiFailure(
            ApiErrorCode.UNAUTHORIZED,
            "登录状态已失效（密码已修改或账号被停用），请重新登录",
            http_status=401,
        )
    if not user.is_active:
        raise ApiFailure(ApiErrorCode.UNAUTHORIZED, "账号已停用", http_status=401)

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员专属接口。★ 已登录但角色不足 → 403（不是 401）。"""
    if user.role != UserRole.ADMIN:
        raise ApiFailure(
            ApiErrorCode.FORBIDDEN,
            "需要管理员权限",
            http_status=403,
        )
    return user


__all__ = ["NO_SECRET_HINT", "get_current_user", "require_admin"]
