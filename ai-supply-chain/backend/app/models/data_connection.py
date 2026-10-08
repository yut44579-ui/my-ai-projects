"""数据连接与同步记录模型（TASK-024，需求 §二十 / §二十一）。

═══════════════════════════════════════════════════════════════════════
【为什么本次要建两张表】
═══════════════════════════════════════════════════════════════════════
需求 §二十 明确写：「数据连接必须是真正的产品能力…不要做一个只有"已连接"
三个字的假连接页面」，并要求每个连接显示：
    连接状态 / 最后同步时间 / 数据范围 / 权限 / 测试连接 / 同步记录 / 断开连接
§二十一 的表清单里也列了 `data_connections` 与 `sync_records`。

此前 `dashboard._connections()` 里的 `connected=False` 是**硬编码**——
正是文档禁止的那种假连接页。本次把它换成真实表驱动。

═══════════════════════════════════════════════════════════════════════
【状态如实：没有配置就是 UNCONFIGURED，不是"已连接"】
═══════════════════════════════════════════════════════════════════════
    UNCONFIGURED —— 还没填连接信息（**如实显示"未配置"**，不假装已连接）
    CONFIGURED   —— 填了信息但还没测试通过
    OK           —— 最近一次测试/同步成功
    ERROR        —— 最近一次测试/同步失败（带失败原因）
    DISABLED     —— 人工停用

★ 关键：**status 只能由真实的测试或同步结果驱动**，不允许人工直接写 OK。
  那等于把"测试通过"变成一个可以随便标的状态，就又变成假连接页了。

═══════════════════════════════════════════════════════════════════════
【凭据加密】
═══════════════════════════════════════════════════════════════════════
    连接串含密码，**绝不能明文入库**。用 core/crypto.py 做对称加密
    （标准库实现，不引新依赖），密钥从 AUTH_SECRET_KEY 派生。
    ★ 对外接口只返回"是否已配置凭据"，**永不回显凭据内容**。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

Stamp6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


class ConnectorKind(str, Enum):
    """连接器类型（需求 §二十 列出的接入方式）。

    ★ 本次**真正实现**的：
        EXCEL_CSV  —— 复用 TASK-001 的导入能力（文件上传即同步）
        MYSQL / POSTGRESQL —— 真发连接测试（凭据由用户在界面上填）
      **只建配置框架、状态如实显示 UNCONFIGURED** 的：
        CRM / ERP / WECHAT_WORK / WEBSITE / API / WEBHOOK ——
        它们需要外部系统的授权与协议对接，没有凭据时无法实现"真测试"。
        ★ 不做假测试（不返回假的"连接成功"）。
    """

    EXCEL_CSV = "EXCEL_CSV"
    MYSQL = "MYSQL"
    POSTGRESQL = "POSTGRESQL"
    CRM = "CRM"
    ERP = "ERP"
    WECHAT_WORK = "WECHAT_WORK"
    WEBSITE = "WEBSITE"
    API = "API"
    WEBHOOK = "WEBHOOK"


class ConnectionStatus(str, Enum):
    """连接状态。★ 只能由真实测试/同步结果驱动，不允许人工直接写 OK。"""

    UNCONFIGURED = "UNCONFIGURED"
    CONFIGURED = "CONFIGURED"
    OK = "OK"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


class SyncTrigger(str, Enum):
    """同步触发方式。"""

    MANUAL = "MANUAL"        # 人工点击
    SCHEDULED = "SCHEDULED"  # 定时任务
    WEBHOOK = "WEBHOOK"      # 外部推送
    UPLOAD = "UPLOAD"        # 文件上传（Excel/CSV）


class SyncResult(str, Enum):
    """同步结果。"""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"  # 部分成功（有行被跳过）
    FAILED = "FAILED"


class DataConnection(Base):
    """一个数据连接配置。"""

    __tablename__ = "data_connections"

    __table_args__ = (Index("ix_data_connections_kind", "kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    kind: Mapped[ConnectorKind] = mapped_column(
        SAEnum(ConnectorKind, name="connector_kind", values_callable=_enum_values),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名，如「生产库只读」")

    status: Mapped[ConnectionStatus] = mapped_column(
        SAEnum(ConnectionStatus, name="connection_status", values_callable=_enum_values),
        nullable=False,
        default=ConnectionStatus.UNCONFIGURED,
    )

    #: **非敏感**配置：主机、端口、库名、表名、文件类型……可明文存（不含密码）
    config_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="非敏感配置（host/port/database/table/file_type…）"
    )
    #: **敏感**配置（密码/连接串/密钥）：加密后存这里，**永不回显**
    secret_encrypted: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="加密后的凭据；对外只返回是否已配置，绝不回显"
    )

    #: 数据范围说明（需求 §二十 要求显示"数据范围"）
    scope_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 权限说明（需求 §二十 要求显示"权限"）
    permission_note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    #: 最近一次失败原因（成功时清空）——状态为 ERROR 时必须能说清为什么
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DataConnection id={self.id} {self.kind} {self.status}>"


class SyncRecord(Base):
    """一次同步/导入的记录（需求 §二十 要求"同步记录"）。

    ★ 文件类同步与 `import_batches` 是一一对应的：这里存 `import_batch_id` 指过去，
      不把行数统计复制一份（避免两个数字打架）。
      非文件类同步（数据库/API）没有批次行，统计就记在本表。
    """

    __tablename__ = "sync_records"

    __table_args__ = (
        Index("ix_sync_records_connection", "connection_id"),
        Index("ix_sync_records_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    connection_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("data_connections.id", ondelete="CASCADE"),
        nullable=False,
    )

    trigger: Mapped[SyncTrigger] = mapped_column(
        SAEnum(SyncTrigger, name="sync_trigger", values_callable=_enum_values),
        nullable=False,
        default=SyncTrigger.MANUAL,
    )
    result: Mapped[SyncResult] = mapped_column(
        SAEnum(SyncResult, name="sync_result", values_callable=_enum_values),
        nullable=False,
    )

    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: 文件类同步指向导入批次；非文件类为 NULL
    import_batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )

    message: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="结果说明/失败原因")

    started_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SyncRecord id={self.id} conn={self.connection_id} {self.result}>"
