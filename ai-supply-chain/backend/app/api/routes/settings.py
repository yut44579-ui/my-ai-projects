"""系统与安全设置接口（TASK-032，需求 §八 系统管理）。

    GET  /api/settings/system      系统运行信息（只读）
    GET  /api/settings/security    安全策略与自检（只读）
    POST /api/auth/change-password 自助修改口令（★ 需要验证旧口令）

═══════════════════════════════════════════════════════════════════════
【为什么这两页是"只读展示"而不是"配置表单"】
═══════════════════════════════════════════════════════════════════════
    V1 的配置项全部走 `.env`（见 core/config.py），页面上**没有可编辑项**。
    按 §二十六（不编造功能），不做假的"保存"按钮 ——
    而是把**当前真实生效的值**展示出来，并标明来源，让人能核对与排查。

    ★ 但有一件事是**真的可以改**的，而且之前缺失：
      **用户修改自己的口令**。此前只有管理员能重置别人的口令，
      普通用户没有自助入口。本模块补上（需验证旧口令）。

═══════════════════════════════════════════════════════════════════════
【安全：绝不回显密钥】
═══════════════════════════════════════════════════════════════════════
    AUTH_SECRET_KEY / DEEPSEEK_API_KEY / 数据库口令 **只返回"是否已配置"**，
    永远不返回值本身，连掩码后几位都不给（掩码也可能被拼凑）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import (
    HASH_BYTES,
    PBKDF2_ALGO,
    PBKDF2_ITERATIONS,
    SALT_BYTES,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.customer import Customer
from app.models.customer_message import CustomerMessage
from app.models.data_connection import DataConnection
from app.models.knowledge import KnowledgeDocument
from app.models.opportunity import Opportunity
from app.models.project import Project
from app.models.proposal import Proposal
from app.models.report import Report
from app.models.risk_event import RiskEvent
from app.models.user import User, UserRole
from app.schemas.settings import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CurrentSessionInfo,
    SecurityCheckItem,
    SecurityPolicyItem,
    SecuritySettingsResponse,
)
from app.services.errors import ApiErrorCode, ApiFailure

router = APIRouter(tags=["settings"])


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


# ══════════════════════════════════════════════════════════════════════
# 系统设置（只读运行信息）
# ══════════════════════════════════════════════════════════════════════

#: 统计各表记录数的清单（与前端展示顺序一致）
_TABLE_COUNTS = [
    ("users", "账号", User),
    ("customers", "客户", Customer),
    ("customer_messages", "沟通消息", CustomerMessage),
    ("opportunities", "商机", Opportunity),
    ("projects", "项目", Project),
    ("risk_events", "风险事件", RiskEvent),
    ("reports", "报告", Report),
    ("knowledge_documents", "知识库资料", KnowledgeDocument),
    ("proposals", "方案", Proposal),
    ("data_connections", "数据连接", DataConnection),
]


@router.get("/settings/system", summary="系统运行信息（只读）")
def system_settings(db: Session = Depends(get_db)) -> dict:
    """系统运行信息。

    ★ 全部是**真实读取**的运行值：版本 / 环境 / 时区 / 数据库 / 依赖是否配置 / 各表记录数。
      **不回显任何密钥**，只返回"是否已配置"。
    ★ 页面没有"保存"按钮：V1 配置走 `.env`（§二十六：不做假功能）。
    """
    db_info: dict = {"dialect": None, "version": None, "connected": False, "error": None}
    try:
        row = db.execute(text("SELECT VERSION()")).scalar()
        db_info["version"] = str(row)
        db_info["dialect"] = db.bind.dialect.name if db.bind is not None else None
        db_info["connected"] = True
    except Exception as exc:  # noqa: BLE001 - 如实报告连接失败
        db_info["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    counts = []
    for table, label, model in _TABLE_COUNTS:
        try:
            n = db.scalar(select(func.count(model.id))) or 0
        except Exception:  # noqa: BLE001
            n = None
        counts.append({"table": table, "label": label, "count": n})

    return {
        "app": {
            "name": settings.app_name,
            "version": "0.1.0",
            "env": settings.app_env,
            "host": settings.app_host,
            "port": settings.app_port,
            "api_prefix": settings.api_prefix,
            "timezone": settings.timezone,
        },
        "database": {
            **db_info,
            "host": settings.db_host,
            "port": settings.db_port,
            "database": settings.db_name,
            "user": settings.db_user,
            # ★ 口令只报是否配置
            "password_configured": _configured(settings.db_password),
            "echo": settings.db_echo,
        },
        "dependencies": [
            {
                "key": "deepseek",
                "label": "AI 模型（DeepSeek）",
                "configured": _configured(settings.deepseek_api_key),
                "detail": f"模型 {settings.deepseek_model}｜超时 {settings.llm_timeout_seconds}s",
                "impact": "未配置时：AI 回复、获客话术、知识库分析、方案生成都不可用",
            },
            {
                "key": "auth_secret",
                "label": "登录令牌签名密钥",
                "configured": _configured(settings.auth_secret_key),
                "detail": "同时用于加密数据连接凭据",
                "impact": "未配置时：无法登录，也无法保存数据连接凭据",
            },
        ],
        "table_counts": counts,
        "note": (
            "本页只展示当前运行配置，不提供修改 —— V1 的配置全部通过 .env 管理。"
            "所有密钥类配置只显示「是否已配置」，不回显内容。"
        ),
    }


# ══════════════════════════════════════════════════════════════════════
# 安全设置
# ══════════════════════════════════════════════════════════════════════

@router.get(
    "/settings/security",
    response_model=SecuritySettingsResponse,
    summary="安全策略与自检（只读）",
)
def security_settings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SecuritySettingsResponse:
    """安全策略现状。

    ★ 每一项都来自**代码里的实际常量或运行配置**，并标明来源，可核对。
      不编"安全评分"那种无法验证的东西。
    """
    policies = [
        SecurityPolicyItem(
            key="password_hash",
            label="口令存储方式",
            value=f"{PBKDF2_ALGO.upper()}-HMAC-SHA256，{PBKDF2_ITERATIONS:,} 次迭代，"
            f"每账号独立随机盐（{SALT_BYTES} 字节），输出 {HASH_BYTES} 字节",
            source="app/core/security.py 的 PBKDF2_ITERATIONS / SALT_BYTES",
            note="口令永不明文存储，接口永不回显；不可逆哈希，管理员也无法查看原口令",
        ),
        SecurityPolicyItem(
            key="token_ttl",
            label="登录令牌有效期",
            value=f"{settings.auth_token_ttl_hours} 小时，到期需重新登录",
            source=".env 的 AUTH_TOKEN_TTL_HOURS",
            note="V1 不做 refresh token —— 少一条更长的攻击面，代价是到期要重新登录",
        ),
        SecurityPolicyItem(
            key="token_invalidation",
            label="令牌强制失效",
            value="重置口令 / 停用账号时，账号的 token_version +1，已发出的令牌立即失效",
            source="users.token_version（AUTH_SPEC §5）",
            note="不是「等它过期」，而是马上踢下线",
        ),
        SecurityPolicyItem(
            key="roles",
            label="权限模型",
            value="两级：管理员（可管理账号）/ 成员（只能用业务功能）",
            source="app/models/user.py 的 UserRole；AUTH_SPEC §1",
            note="★ V1 刻意不做权限矩阵（按资源/按动作的细粒度授权）——需求没要求，避免过度设计",
        ),
        SecurityPolicyItem(
            key="registration",
            label="账号开通方式",
            value="只能由管理员创建，没有注册入口",
            source="AUTH_SPEC §1/§9",
            note="避免部署后被人抢先注册管理员",
        ),
        SecurityPolicyItem(
            key="token_storage",
            label="前端令牌存放",
            value="localStorage",
            source="frontend/src/api/client.ts 的 TOKEN_KEY",
            note="★ 已知取舍：localStorage 可被 XSS 读取。V1 接受，因为同源脚本已可发起任意请求；"
            "若要更严需要改成 HttpOnly Cookie + CSRF 防护（当前未做）",
            level="attention",
        ),
    ]

    # ── 自检：只做能真实判断的检查 ──
    checks: list[SecurityCheckItem] = []

    secret_ok = _configured(settings.auth_secret_key)
    checks.append(
        SecurityCheckItem(
            key="auth_secret",
            label="已配置登录令牌签名密钥",
            passed=secret_ok,
            detail="已配置" if secret_ok else "未配置 —— 无法登录，也无法加密数据连接凭据",
        )
    )

    admin_count = db.scalar(
        select(func.count(User.id)).where(
            User.role == UserRole.ADMIN, User.is_active.is_(True)
        )
    ) or 0
    checks.append(
        SecurityCheckItem(
            key="admin_exists",
            label="至少有一个启用的管理员",
            passed=admin_count > 0,
            detail=f"当前启用的管理员：{admin_count} 个"
            + ("" if admin_count > 0 else " —— 没有管理员将无法创建账号"),
        )
    )

    # ★ 测试弱口令自检：这是我一直提示用户要改的
    weak = db.scalar(
        select(func.count(User.id)).where(User.username == "admin", User.is_active.is_(True))
    ) or 0
    known_weak = False
    if weak:
        admin_user = db.scalars(
            select(User).where(User.username == "admin", User.is_active.is_(True)).limit(1)
        ).first()
        if admin_user is not None:
            known_weak = verify_password("Admin@2026!", admin_user.password_hash)
    checks.append(
        SecurityCheckItem(
            key="weak_admin_password",
            label="管理员口令不是初始弱口令",
            passed=not known_weak,
            detail=(
                "★ 检测到 admin 仍在使用初始弱口令 Admin@2026!，请立即修改"
                if known_weak
                else "未检测到使用初始弱口令"
            ),
        )
    )

    # ── 当前会话 ──
    session_info = CurrentSessionInfo(
        username=user.username,
        display_name=user.display_name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        last_login_at=user.last_login_at,
    )

    return SecuritySettingsResponse(
        policies=policies,
        checks=checks,
        session=session_info,
        note=(
            "本页展示的是当前真实生效的安全策略，每一项都标明来源以便核对。"
            "V1 不提供在线修改这些参数（走 .env）；但「修改我的口令」是可以自助完成的。"
        ),
    )


# ══════════════════════════════════════════════════════════════════════
# 自助修改口令（★ 本轮补上的真功能）
# ══════════════════════════════════════════════════════════════════════

@router.post(
    "/auth/change-password",
    response_model=ChangePasswordResponse,
    summary="修改我的口令（需验证旧口令）",
)
def change_my_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChangePasswordResponse:
    """用户自助修改口令。

    ★ 必须验证**旧口令**：防止令牌被盗后，攻击者直接把口令改掉、把真正的用户锁在门外。
    ★ 改完 token_version +1 → 所有旧令牌（包括当前这个）立即失效，需要重新登录。
      这与管理员重置口令的行为一致，语义统一。
    ★ 新旧口令相同则拒绝 —— 那等于没改。
    """
    if not verify_password(payload.old_password, user.password_hash):
        raise ApiFailure(ApiErrorCode.INVALID_CREDENTIALS, "当前口令不正确")

    if payload.old_password == payload.new_password:
        raise ApiFailure(ApiErrorCode.INVALID_CREDENTIALS, "新口令不能与当前口令相同")

    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    db.commit()
    db.refresh(user)

    return ChangePasswordResponse(
        message="口令已修改；包括当前会话在内的所有登录令牌已失效，请重新登录",
        token_invalidated=True,
    )
