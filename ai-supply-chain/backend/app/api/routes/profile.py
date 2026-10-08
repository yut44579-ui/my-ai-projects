"""个人中心接口（TASK-037）。

    GET    /api/profile/me                    当前登录者完整资料
    PATCH  /api/profile/me                    改资料（显示名/邮箱/电话/简介）
    POST   /api/profile/avatar                上传头像（★ 按文件签名校验）
    DELETE /api/profile/avatar                移除头像
    GET    /api/profile/avatar-limits         头像限制（供前端前置提示）
    POST   /api/profile/presence              手动覆盖状态（带到期时间，到期自动恢复）
    DELETE /api/profile/presence              取消手动覆盖
    GET    /api/profile/presence-rules        实时状态的判定规则
    POST   /api/profile/heartbeat             心跳（前端每 30 秒一次；★ 实时状态的来源）
    POST   /api/profile/theme                 设置主题偏好（跟随系统/浅色/深色）

    GET    /api/profile/ai-providers          列出各供应商配置
    PATCH  /api/profile/ai-providers/{kind}   改某家配置（地址/模型/Key）
    POST   /api/profile/ai-providers/{kind}/activate  设为生效
    POST   /api/profile/ai-providers/{kind}/test      真实连通性测试

★ 所有接口都是"改自己"。改**别人**的账号走 `/api/users/*`（仅管理员）。
★ 路由顺序：静态段（/me、/avatar、/avatar-limits）必须先于任何 `{param}` 注册。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.user_profile import (
    PROVIDER_PRESETS,
    AiProviderKind,
    PresenceStatus,
    ThemePreference,
)
from app.schemas.profile import (
    HeartbeatRequest,
    AiProviderItem,
    AiProviderListResponse,
    AiProviderTestResponse,
    AiProviderUpdateRequest,
    AvatarLimits,
    AvatarUploadResponse,
    PresenceUpdateRequest,
    ProfileMe,
    ProfileUpdateRequest,
    ThemeUpdateRequest,
)
from app.services.ai_provider import (
    ProviderError,
    activate_provider,
    ensure_provider,
    has_key,
    list_providers,
    resolve_active,
    test_provider,
    upsert_provider,
)
from app.services.errors import ApiErrorCode, ApiFailure
from app.services.presence import (
    OFFLINE_AFTER_SECONDS,
    clear_override,
    compute_presence,
    presence_rules,
    record_heartbeat,
    set_override,
)
from app.services.profile import (
    MAX_AVATAR_BYTES,
    ProfileError,
    avatar_url,
    clear_avatar,
    save_avatar,
    set_theme,
    update_profile,
)

router = APIRouter(tags=["profile"])

ROLE_LABELS = {"ADMIN": "管理员", "MEMBER": "成员"}


#: ★ 反复出现的那句说明，抽成常量，保证每个返回资料的地方口径一致。
#: TASK-040 起状态是**系统按心跳与操作自动推算**的，不再是自我声明 ——
#: 所以这句话也改了：以前写"这是你自己设置的"，现在写清判定规则。
PRESENCE_NOTE = (
    "状态由系统按你的实际使用情况自动推算：最近有操作=在线，"
    "页面开着但一段时间没操作=忙碌，超过 90 秒没心跳=离线。"
    "你也可以手动覆盖一段时间（到期自动恢复）。"
)


def _to_me(user: User) -> ProfileMe:
    info = compute_presence(user)
    return ProfileMe(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        role_label=ROLE_LABELS.get(
            user.role.value if hasattr(user.role, "value") else str(user.role), str(user.role)
        ),
        is_active=user.is_active,
        avatar_url=avatar_url(user),
        presence=info.status,
        presence_label=info.label,
        presence_reason=info.reason,
        presence_is_override=info.is_override,
        presence_override_until=info.override_until,
        last_active_at=info.last_active_at,
        theme=user.theme,
        email=user.email,
        phone=user.phone,
        bio=user.bio,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("/profile/me", response_model=ProfileMe, summary="当前登录者完整资料")
def get_me(user: User = Depends(get_current_user)) -> ProfileMe:
    return _to_me(user)


@router.patch("/profile/me", response_model=ProfileMe, summary="修改个人资料")
def patch_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileMe:
    """改显示名 / 邮箱 / 电话 / 简介。

    ★ 不能改 username / role / is_active —— 那属于登录凭据与管理员权限，
      开放给"改自己资料"会造成提权。
    """
    try:
        updated = update_profile(
            db,
            user,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            bio=payload.bio,
        )
    except ProfileError as exc:
        raise ApiFailure(ApiErrorCode.PROFILE_INVALID, str(exc))
    return _to_me(updated)


@router.get(
    "/profile/avatar-limits", response_model=AvatarLimits, summary="头像上传限制（供前端前置提示）"
)
def get_avatar_limits() -> AvatarLimits:
    return AvatarLimits(
        max_bytes=MAX_AVATAR_BYTES,
        max_mb=round(MAX_AVATAR_BYTES / 1024 / 1024, 1),
        allowed_types=["PNG", "JPEG", "WebP"],
        note=(
            "系统按文件内容判断真实类型，改扩展名无效；"
            "不接受 SVG（可内嵌脚本，是 XSS 载体）"
        ),
    )


@router.post(
    "/profile/avatar", response_model=AvatarUploadResponse, summary="上传头像（按文件签名校验）"
)
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AvatarUploadResponse:
    """上传头像。

    ★ 校验顺序：大小 → **真实类型（读文件头魔数）** → 落盘 → 入库 → 删旧文件。
      只查扩展名是可绕过的（.exe 改名成 .png 就能进来），所以必须读魔数。
    """
    data = await file.read()
    try:
        info = save_avatar(db, user, data=data, declared_name=file.filename or "")
    except ProfileError as exc:
        raise ApiFailure(ApiErrorCode.PROFILE_INVALID, str(exc))
    return AvatarUploadResponse(
        profile=_to_me(user),
        message=f"头像已更新（{info.content_type}，{info.size / 1024:.0f} KB）",
    )


@router.delete("/profile/avatar", response_model=ProfileMe, summary="移除头像")
def delete_avatar(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProfileMe:
    clear_avatar(db, user)
    return _to_me(user)


@router.post("/profile/presence", response_model=ProfileMe, summary="手动覆盖我当前的状态")
def post_presence(
    payload: PresenceUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileMe:
    """手动覆盖状态（带到期时间）。

    ★ 为什么必须带到期时间：手动设的"忙碌"如果永不过期，人会忘了改，
      状态就永远是错的 —— 那比没有这个功能更糟。到期自动回到推算状态。
    """
    set_override(db, user, payload.presence, minutes=payload.minutes)
    return _to_me(user)


@router.delete("/profile/presence", response_model=ProfileMe, summary="取消手动覆盖")
def delete_presence(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProfileMe:
    clear_override(db, user)
    return _to_me(user)


@router.get("/profile/presence-rules", summary="实时状态的判定规则（界面要原文展示）")
def get_presence_rules() -> dict:
    """把判定规则给到前端。

    ★ 为什么要暴露规则：状态是系统推算的，用户看到自己"忙碌"时必须能查到
      "为什么"。只给一个颜色点而不给依据，用户会以为是 bug。
    """
    return presence_rules()


@router.post("/profile/heartbeat", summary="心跳（前端每 30 秒一次）")
def post_heartbeat(
    payload: HeartbeatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """记录一次心跳。

    ★ `active` 表示这次心跳**携带了真实用户操作**（前端监听鼠标/键盘/滚动等），
      只有它为真才刷新 `last_active_at`。
      把"页面还开着"与"人在操作"分开，正是「在线」与「忙碌」能区分开的原因。
    ★ 心跳**不会解除手动覆盖** —— 覆盖由到期时间决定何时恢复，
      否则用户设了"忙碌 1 小时"，一刷新页面就变回在线，设置形同虚设。
    ★ `leaving=True`（页面关闭时前端用 fetch+keepalive 发的那一次）：
      把 `last_seen_at` **回拨到离线阈值之前**，让状态立刻变离线，
      同事不用等 90 秒心跳超时。
      ★ 为什么不用 sendBeacon：它无法带 Authorization 头，
        只能把令牌放 URL，那会让凭据进访问日志。
    """
    if payload.leaving:
        # 回拨到刚好超过离线阈值，下一刻读到的就是 OFFLINE
        user.last_seen_at = datetime.now(timezone.utc) - timedelta(
            seconds=OFFLINE_AFTER_SECONDS + 1
        )
        db.commit()
        info = compute_presence(user)
        return {
            "status": info.status.value,
            "label": info.label,
            "reason": info.reason,
            "is_override": info.is_override,
            "seconds_since_active": info.seconds_since_active,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    info = record_heartbeat(db, user, active=payload.active)
    return {
        "status": info.status.value,
        "label": info.label,
        "reason": info.reason,
        "is_override": info.is_override,
        "seconds_since_active": info.seconds_since_active,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/profile/theme", response_model=ProfileMe, summary="设置界面主题偏好")
def post_theme(
    payload: ThemeUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileMe:
    return _to_me(set_theme(db, user, payload.theme))


# ══════════════════════════════════════════════════════════════════════
# AI 供应商
# ══════════════════════════════════════════════════════════════════════

def _to_provider_item(row) -> AiProviderItem:
    preset = PROVIDER_PRESETS[row.kind]
    return AiProviderItem(
        kind=row.kind,
        label=preset["label"],
        protocol=row.protocol.value,
        docs=preset.get("docs", ""),
        base_url=row.base_url,
        model=row.model,
        is_active=row.is_active,
        has_key=has_key(row),
        last_test_at=row.last_test_at,
        last_test_ok=row.last_test_ok,
        last_error=row.last_error,
    )


@router.get(
    "/profile/ai-providers", response_model=AiProviderListResponse, summary="AI 供应商配置"
)
def get_ai_providers(db: Session = Depends(get_db)) -> AiProviderListResponse:
    """列出各供应商配置。

    ★ 首次访问时按预置值把每一家登记出来（这样界面上能看到"有哪些可选"），
      **但不会替用户填 key** —— 没有 key 的显示"未配置"。
    """
    for kind in AiProviderKind:
        ensure_provider(db, kind)

    rows = list_providers(db)
    resolved = resolve_active(db)

    return AiProviderListResponse(
        items=[_to_provider_item(r) for r in rows],
        active_kind=resolved.kind if resolved else None,
        active_source=resolved.source if resolved else "NONE",
        current_model=resolved.model if resolved else None,
        note=(
            "★ API Key 加密存储、接口永不回显，只显示「是否已配置」。"
            "同一时间只有一家生效（「设为生效」会切换）。"
            "若一家都没配，系统会回退使用 .env 里的 DEEPSEEK_* 配置。"
        ),
    )


@router.patch(
    "/profile/ai-providers/{kind}",
    response_model=AiProviderItem,
    summary="修改某家 AI 供应商配置",
)
def patch_ai_provider(
    kind: AiProviderKind,
    payload: AiProviderUpdateRequest,
    db: Session = Depends(get_db),
) -> AiProviderItem:
    """改地址 / 模型 / API Key。

    ★ `api_key` 的三种语义（前端"留空=不改"的常见习惯）：
        · 不传（null）→ 不改动
        · 空串        → 清空该 key
        · 非空        → 替换，并**作废上次的测试结论**（换了 key 旧结论不算数）
    """
    try:
        row = upsert_provider(
            db,
            kind,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
        )
    except ProviderError as exc:
        raise ApiFailure(ApiErrorCode.PROVIDER_INVALID, str(exc))
    return _to_provider_item(row)


@router.post(
    "/profile/ai-providers/{kind}/activate",
    response_model=AiProviderItem,
    summary="设为生效的 AI 供应商",
)
def post_activate_provider(kind: AiProviderKind, db: Session = Depends(get_db)) -> AiProviderItem:
    row = activate_provider(db, kind)
    return _to_provider_item(row)


@router.post(
    "/profile/ai-providers/{kind}/test",
    response_model=AiProviderTestResponse,
    summary="测试连通性（★ 真发请求）",
)
def post_test_provider(kind: AiProviderKind, db: Session = Depends(get_db)) -> AiProviderTestResponse:
    """真实连通性测试。

    ★ 与数据连接一致：**状态只能由真实结果驱动**，不允许人工标"可用"。
      失败时把对方返回的原文带上，便于判断是 key 错、额度问题还是模型名不对。
    """
    ok, msg, elapsed = test_provider(db, kind)
    row = ensure_provider(db, kind)
    db.refresh(row)
    return AiProviderTestResponse(
        provider=_to_provider_item(row), ok=ok, message=msg, elapsed_ms=elapsed
    )
