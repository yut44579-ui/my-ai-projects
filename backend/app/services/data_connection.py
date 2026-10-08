"""数据连接服务（TASK-024，需求 §二十）。

═══════════════════════════════════════════════════════════════════════
【本模块最重要的原则：不造假连接状态】
═══════════════════════════════════════════════════════════════════════
    §二十 明确禁止"只有'已连接'三个字的假连接页面"。所以：
      · `status` **只能**由 `test_connection()` 或 `sync()` 的**真实结果**改写；
        没有任何接口允许人工把状态直接写成 OK。
      · 测试失败时把**真实错误**写进 last_error（如"拒绝连接/认证失败/库不存在"）。
      · 没有凭据的连接器状态是 UNCONFIGURED，界面如实显示"未配置"。

═══════════════════════════════════════════════════════════════════════
【本次真正实现的连接器】
═══════════════════════════════════════════════════════════════════════
    EXCEL_CSV  —— 复用 TASK-001 的导入能力（真解析、真入库、真统计）
    MYSQL      —— 真建连接 + 真跑 `SELECT 1`（凭据由用户在界面填）
    POSTGRESQL —— 同上
    ★ 其余（CRM/ERP/企业微信/官网/API/Webhook）**只建配置框架**，
      状态如实显示"未配置"，**不提供假测试**。它们需要外部系统的
      授权与协议对接，没有凭据时无法做"真测试"，硬做一个返回"成功"的假测试
      就等于造假。

═══════════════════════════════════════════════════════════════════════
【凭据】
═══════════════════════════════════════════════════════════════════════
    加密存 `secret_encrypted`（core/crypto.py），对外**永不回显**，
    只返回"是否已配置凭据"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import (
    CredentialError,
    CredentialKeyMissing,
    decrypt_credential,
    encrypt_credential,
)
from app.models.data_connection import (
    ConnectionStatus,
    ConnectorKind,
    DataConnection,
    SyncRecord,
    SyncResult,
    SyncTrigger,
)

#: 真正实现了"真连接测试"的连接器
LIVE_TESTABLE: frozenset[ConnectorKind] = frozenset(
    {ConnectorKind.MYSQL, ConnectorKind.POSTGRESQL}
)

#: 只建配置框架的连接器（需要外部系统授权，V1 不提供假测试）
FRAMEWORK_ONLY: frozenset[ConnectorKind] = frozenset(
    {
        ConnectorKind.CRM,
        ConnectorKind.ERP,
        ConnectorKind.WECHAT_WORK,
        ConnectorKind.WEBSITE,
        ConnectorKind.API,
        ConnectorKind.WEBHOOK,
    }
)

#: 每个连接器的说明（界面直接显示，避免"三个字的假连接页"）
KIND_NOTES: dict[ConnectorKind, str] = {
    ConnectorKind.EXCEL_CSV: "上传 Excel/CSV 即完成一次同步（复用文件导入能力）",
    ConnectorKind.MYSQL: "填连接信息后可做真实连接测试；同步能力待接入",
    ConnectorKind.POSTGRESQL: "填连接信息后可做真实连接测试；同步能力待接入",
    ConnectorKind.CRM: "需要 CRM 系统的接口地址与授权，V1 未接入",
    ConnectorKind.ERP: "需要 ERP 系统的接口地址与授权，V1 未接入",
    ConnectorKind.WECHAT_WORK: "需要企业微信应用凭据与回调配置，V1 未接入",
    ConnectorKind.WEBSITE: "需要官网埋点/表单回调地址，V1 未接入",
    ConnectorKind.API: "需要对方 API 文档与鉴权方式，V1 未接入",
    ConnectorKind.WEBHOOK: "需要提供可接收推送的地址与签名密钥，V1 未接入",
}


class ConnectionError(Exception):
    """连接相关业务错误（路由层翻译成 400/404）。"""


@dataclass
class TestOutcome:
    """一次连接测试的真实结果。"""

    ok: bool
    message: str
    elapsed_ms: int


@dataclass
class SyncOutcome:
    """一次同步的真实结果。"""

    result: SyncResult
    rows_total: int
    rows_created: int
    rows_skipped: int
    message: str


def _secret() -> str:
    return (settings.auth_secret_key or "").strip()


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

def list_connections(db: Session) -> list[DataConnection]:
    return list(
        db.scalars(
            select(DataConnection)
            .where(DataConnection.is_active.is_(True))
            .order_by(DataConnection.id.asc())
        ).all()
    )


def get_connection(db: Session, connection_id: int) -> DataConnection | None:
    row = db.get(DataConnection, connection_id)
    if row is None or not row.is_active:
        return None
    return row


def create_connection(
    db: Session,
    *,
    kind: ConnectorKind,
    name: str,
    config: dict | None = None,
    secret: str | None = None,
    scope_note: str | None = None,
    permission_note: str | None = None,
) -> DataConnection:
    """新建连接。★ 初始状态由"有没有凭据"决定，不是直接给 OK。"""
    encrypted = None
    if secret:
        try:
            encrypted = encrypt_credential(secret, secret=_secret())
        except CredentialKeyMissing as exc:
            raise ConnectionError(str(exc)) from exc

    # ★ 状态如实：
    #   给了凭据 → CONFIGURED（**还没测试**，不是 OK）
    #   没给凭据 → UNCONFIGURED
    status = ConnectionStatus.CONFIGURED if encrypted else ConnectionStatus.UNCONFIGURED

    row = DataConnection(
        kind=kind,
        name=name.strip(),
        status=status,
        config_json=config or None,
        secret_encrypted=encrypted,
        scope_note=scope_note,
        permission_note=permission_note,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_connection(
    db: Session,
    row: DataConnection,
    *,
    name: str | None = None,
    config: dict | None = None,
    secret: str | None = None,
    scope_note: str | None = None,
    permission_note: str | None = None,
) -> DataConnection:
    """更新连接。★ 改了凭据会退回 CONFIGURED（必须重新测试，不许沿用旧的 OK）。"""
    if name:
        row.name = name.strip()
    if config is not None:
        row.config_json = config or None
    if scope_note is not None:
        row.scope_note = scope_note
    if permission_note is not None:
        row.permission_note = permission_note
    if secret is not None:
        row.secret_encrypted = (
            encrypt_credential(secret, secret=_secret()) if secret.strip() else None
        )
        # ★ 凭据变了，之前"测试通过"的结论就作废了
        row.status = (
            ConnectionStatus.CONFIGURED
            if row.secret_encrypted
            else ConnectionStatus.UNCONFIGURED
        )
        row.last_error = None
    db.commit()
    db.refresh(row)
    return row


def set_enabled(db: Session, row: DataConnection, enabled: bool) -> DataConnection:
    """停用/启用。★ 停用不改写"最近一次真实结果"，但状态显示为 DISABLED。"""
    row.is_active = True  # 保持行存在（历史同步记录要能查）
    row.status = (
        ConnectionStatus.CONFIGURED
        if enabled and row.secret_encrypted
        else (ConnectionStatus.UNCONFIGURED if enabled else ConnectionStatus.DISABLED)
    )
    db.commit()
    db.refresh(row)
    return row


def has_credential(row: DataConnection) -> bool:
    return bool(row.secret_encrypted)


# ══════════════════════════════════════════════════════════════════════
# 测试连接（真发请求）
# ══════════════════════════════════════════════════════════════════════

def _dsn(row: DataConnection) -> str:
    """从 config + 解密后的凭据拼 DSN。★ 解密失败一律报错，绝不回退成空密码去连。"""
    cfg = row.config_json or {}
    host = cfg.get("host")
    port = cfg.get("port")
    database = cfg.get("database")
    user = cfg.get("user")
    if not all([host, port, database, user]):
        raise ConnectionError("连接信息不完整：需要 host / port / database / user")
    if not row.secret_encrypted:
        raise ConnectionError("还没有配置密码凭据")

    try:
        password = decrypt_credential(row.secret_encrypted, secret=_secret())
    except CredentialKeyMissing as exc:
        raise ConnectionError(str(exc)) from exc
    except CredentialError as exc:
        raise ConnectionError(f"凭据无法解密：{exc}") from exc

    # ★ 密码做 URL 编码，避免特殊字符把 DSN 拼坏
    from urllib.parse import quote_plus

    pwd = quote_plus(password)
    if row.kind == ConnectorKind.MYSQL:
        return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{database}?charset=utf8mb4"
    if row.kind == ConnectorKind.POSTGRESQL:
        return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{database}"
    raise ConnectionError(f"{row.kind.value} 不支持数据库连接测试")


def test_connection(db: Session, row: DataConnection, *, timeout_seconds: int = 8) -> TestOutcome:
    """真实连接测试。★ 结果直接决定 status，不允许人工写 OK。

    · MySQL/PostgreSQL：真建连接 + 真跑 `SELECT 1`
    · Excel/CSV：不需要连接（文件上传即同步），测试视为"通道可用"
    · 仅框架类：如实返回"未接入"，**不假装成功**
    """
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    try:
        if row.kind == ConnectorKind.EXCEL_CSV:
            outcome = TestOutcome(True, "文件导入通道可用（上传文件即完成同步）", 0)

        elif row.kind in LIVE_TESTABLE:
            if not has_credential(row):
                raise ConnectionError("还没有配置密码凭据")
            dsn = _dsn(row)
            engine = create_engine(
                dsn,
                pool_pre_ping=False,
                connect_args={"connect_timeout": timeout_seconds}
                if row.kind == ConnectorKind.MYSQL
                else {"connect_timeout": timeout_seconds},
            )
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            finally:
                engine.dispose()
            elapsed = int((time.perf_counter() - started) * 1000)
            outcome = TestOutcome(True, "连接成功（已执行 SELECT 1）", elapsed)

        elif row.kind in FRAMEWORK_ONLY:
            # ★ 如实：不提供假测试
            outcome = TestOutcome(
                False,
                f"{KIND_NOTES.get(row.kind, '该连接器')}；没有真实凭据时无法做连接测试，"
                "本系统不会返回假的“成功”",
                0,
            )
        else:  # pragma: no cover
            outcome = TestOutcome(False, f"未知连接器类型 {row.kind.value}", 0)

    except Exception as exc:  # noqa: BLE001 - 要把真实错误如实写回
        elapsed = int((time.perf_counter() - started) * 1000)
        outcome = TestOutcome(False, f"{type(exc).__name__}: {str(exc)[:300]}", elapsed)

    # 状态由真实结果驱动
    row.last_test_at = now
    if outcome.ok and row.status != ConnectionStatus.DISABLED:
        row.status = ConnectionStatus.OK
        row.last_error = None
    elif not outcome.ok:
        row.status = ConnectionStatus.ERROR
        row.last_error = outcome.message[:512]
    db.commit()
    db.refresh(row)
    return outcome


# ══════════════════════════════════════════════════════════════════════
# 同步与同步记录
# ══════════════════════════════════════════════════════════════════════

def record_sync(
    db: Session,
    row: DataConnection,
    *,
    trigger: SyncTrigger,
    result: SyncResult,
    rows_total: int = 0,
    rows_created: int = 0,
    rows_skipped: int = 0,
    import_batch_id: int | None = None,
    message: str | None = None,
) -> SyncRecord:
    """写一条同步记录。★ 同时更新连接的 last_sync_at 与状态。"""
    now = datetime.now(timezone.utc)
    rec = SyncRecord(
        connection_id=row.id,
        trigger=trigger,
        result=result,
        rows_total=rows_total,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        import_batch_id=import_batch_id,
        message=message,
        started_at=now,
        finished_at=now,
    )
    db.add(rec)

    if result == SyncResult.FAILED:
        row.status = ConnectionStatus.ERROR
        row.last_error = (message or "同步失败")[:512]
    else:
        row.last_sync_at = now
        row.status = ConnectionStatus.OK
        row.last_error = None

    db.commit()
    db.refresh(rec)
    return rec


def list_sync_records(
    db: Session, *, connection_id: int | None = None, limit: int = 50
) -> tuple[list[SyncRecord], int]:
    conditions = []
    if connection_id is not None:
        conditions.append(SyncRecord.connection_id == connection_id)

    base = select(SyncRecord)
    counter = select(func.count(SyncRecord.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(SyncRecord.started_at.desc(), SyncRecord.id.desc()).limit(limit)
    ).all()
    return list(rows), total


def summary_counts(db: Session) -> dict[str, int]:
    """按状态统计连接数（供概览卡用）。"""
    rows = db.execute(
        select(DataConnection.status, func.count(DataConnection.id))
        .where(DataConnection.is_active.is_(True))
        .group_by(DataConnection.status)
    ).all()
    counts = {s.value: 0 for s in ConnectionStatus}
    for status, n in rows:
        counts[status.value if hasattr(status, "value") else str(status)] = int(n)
    return counts


__all__ = [
    "FRAMEWORK_ONLY",
    "KIND_NOTES",
    "LIVE_TESTABLE",
    "ConnectionError",
    "SyncOutcome",
    "TestOutcome",
    "create_connection",
    "get_connection",
    "has_credential",
    "list_connections",
    "list_sync_records",
    "record_sync",
    "set_enabled",
    "summary_counts",
    "test_connection",
    "update_connection",
]
