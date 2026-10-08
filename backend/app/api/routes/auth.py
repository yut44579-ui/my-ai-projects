"""认证与账号管理接口（TASK-019）。规格见 docs/AUTH_SPEC.md。

    POST /api/auth/login                     登录（不需要鉴权）
    GET  /api/auth/me                        当前登录者信息
    GET  /api/users                          账号列表（仅 ADMIN，★ 绝不返回 password_hash）
    POST /api/users                          创建账号（仅 ADMIN）
    POST /api/users/{id}/reset-password      重置口令（仅 ADMIN，token_version +1）
    POST /api/users/{id}/toggle-active       停用/启用（仅 ADMIN，停用时 token_version +1）

★ 刻意**不提供注册接口**：账号由管理员创建（AUTH_SPEC §1/§9），
  避免部署后被别人抢先注册管理员。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.security import hash_password, issue_token, verify_password
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.evidence import SourceType, build_evidence
from app.schemas.users import (
    LoginRequest,
    LoginResponse,
    PasswordResetRequest,
    UserCreateRequest,
    UserItem,
    UserListResponse,
    UserToggleResponse,
)
from app.services.presence import compute_presence
from app.services.errors import ApiErrorCode, ApiFailure

router = APIRouter(tags=["auth"])

#: 登录失败的统一文案。★ 不区分"用户不存在"与"口令错误"：
#: 区分会泄露"哪些用户名存在"，等于给撞库的人送情报。
LOGIN_FAILED_TEXT = "用户名或密码不正确"


def to_item(user: User) -> UserItem:
    """★ 只暴露安全字段：password_hash 永远不出现在任何响应里。

    ★ TASK-040：带上**实时推算**的状态（见 services/presence.py），
      让用户列表能显示同事当前在不在线。算的是当下，不是存下来的旧值。
    """
    info = compute_presence(user)
    return UserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        avatar_url=f"/{user.avatar_path.lstrip('/')}" if user.avatar_path else None,
        presence=info.status.value,
        presence_label=info.label,
        presence_reason=info.reason,
        last_active_at=info.last_active_at,
    )


@router.post("/auth/login", response_model=LoginResponse, summary="登录")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """登录。★ 用户名不存在与口令错误返回**同一句话**，不泄露用户名是否存在。"""
    secret = (settings.auth_secret_key or "").strip()
    if not secret:
        from app.api.deps import NO_SECRET_HINT

        raise ApiFailure(ApiErrorCode.AUTH_NOT_CONFIGURED, NO_SECRET_HINT, http_status=500)

    username = payload.username.strip().lower()
    user = db.scalar(select(User).where(User.username == username))

    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiFailure(ApiErrorCode.INVALID_CREDENTIALS, LOGIN_FAILED_TEXT, http_status=401)
    if not user.is_active:
        raise ApiFailure(ApiErrorCode.FORBIDDEN, "账号已停用，请联系管理员", http_status=403)

    token, exp = issue_token(
        user_id=user.id,
        username=user.username,
        role=user.role.value,
        token_version=user.token_version,
        secret=secret,
        ttl_hours=settings.auth_token_ttl_hours,
    )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
        user=to_item(user),
    )


@router.get("/auth/me", response_model=UserItem, summary="当前登录者信息")
def me(user: User = Depends(get_current_user)) -> UserItem:
    return to_item(user)


@router.get("/users", response_model=UserListResponse, summary="账号列表（仅 ADMIN）")
def list_users(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> UserListResponse:
    rows = db.scalars(select(User).order_by(User.id.asc())).all()
    total = db.scalar(select(func.count(User.id))) or 0
    return UserListResponse(
        items=[to_item(u) for u in rows],
        total=build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="users:count"),
    )


@router.post("/users", response_model=UserItem, status_code=201, summary="创建账号（仅 ADMIN）")
def create_user(
    payload: UserCreateRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserItem:
    username = payload.username.strip().lower()
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise ApiFailure(ApiErrorCode.USERNAME_TAKEN, f"用户名 {username} 已存在", http_status=400)

    user = User(
        username=username,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        token_version=1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return to_item(user)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=UserToggleResponse,
    summary="重置口令（仅 ADMIN；旧令牌立即失效）",
)
def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserToggleResponse:
    """重置口令。★ token_version +1，使该用户已发出的令牌**立即失效**。"""
    user = db.get(User, user_id)
    if user is None:
        raise ApiFailure(ApiErrorCode.USER_NOT_FOUND, f"账号 {user_id} 不存在", http_status=404)

    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    db.commit()
    db.refresh(user)
    return UserToggleResponse(
        user=to_item(user),
        message="口令已重置；该账号已发出的登录令牌已全部失效，需要重新登录",
    )


@router.post(
    "/users/{user_id}/toggle-active",
    response_model=UserToggleResponse,
    summary="停用 / 启用账号（仅 ADMIN）",
)
def toggle_active(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserToggleResponse:
    """停用/启用。★ 停用时 token_version +1，使已发令牌立即失效。

    ★ 不允许停用自己：否则管理员会把自己锁在门外，且没有其它途径恢复。
    """
    user = db.get(User, user_id)
    if user is None:
        raise ApiFailure(ApiErrorCode.USER_NOT_FOUND, f"账号 {user_id} 不存在", http_status=404)
    if user.id == admin.id:
        raise ApiFailure(
            ApiErrorCode.FORBIDDEN, "不能停用当前登录的管理员账号", http_status=403
        )

    user.is_active = not user.is_active
    if not user.is_active:
        user.token_version += 1  # 停用即踢下线
    db.commit()
    db.refresh(user)
    return UserToggleResponse(
        user=to_item(user),
        message="账号已停用，其登录令牌已失效" if not user.is_active else "账号已启用",
    )
