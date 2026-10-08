"""数据连接接口契约（TASK-024，需求 §二十）。

★ 安全铁律：`secret` 是**只写**字段。
  没有任何一个响应模型包含凭据内容 —— 只返回 `has_credential: bool`。
  这是**结构性**保证，不是靠"记得别填"。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.data_connection import (
    ConnectionStatus,
    ConnectorKind,
    SyncResult,
    SyncTrigger,
)
from app.schemas.evidence import EvidenceValue


class DataConnectionItem(BaseModel):
    """一个连接。★ 没有凭据内容，只有"是否已配置凭据"。"""

    id: int
    kind: ConnectorKind
    kind_label: str
    name: str
    status: ConnectionStatus
    status_label: str
    #: 说明这个连接器能做什么、V1 做到哪一步
    note: str
    #: 非敏感配置回显（host/port/database/user/table…，**不含密码**）
    config: dict = Field(default_factory=dict)
    has_credential: bool = Field(description="是否已配置凭据；★ 永远不返回凭据内容")
    scope_note: str | None = None
    permission_note: str | None = None
    last_sync_at: datetime | None = None
    last_test_at: datetime | None = None
    last_error: str | None = Field(default=None, description="最近一次真实失败原因")
    supported: bool = Field(
        description="V1 是否真的实现了这个连接器（false = 只有配置框架，未接入）"
    )


class DataConnectionListResponse(BaseModel):
    items: list[DataConnectionItem]
    total: EvidenceValue


class DataConnectionCreateRequest(BaseModel):
    kind: ConnectorKind
    name: str = Field(min_length=1, max_length=128)
    config: dict | None = Field(default=None, description="非敏感配置，如 host/port/database/user")
    secret: str | None = Field(
        default=None, description="凭据（密码/密钥）；★ 只写不读，加密入库后永不回显"
    )
    scope_note: str | None = Field(default=None, max_length=255, description="数据范围说明")
    permission_note: str | None = Field(default=None, max_length=255, description="权限说明")


class DataConnectionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict | None = None
    secret: str | None = Field(
        default=None, description="填了就替换凭据，并把状态退回「已配置未测试」"
    )
    scope_note: str | None = Field(default=None, max_length=255)
    permission_note: str | None = Field(default=None, max_length=255)


class ConnectionTestResponse(BaseModel):
    """连接测试结果。★ ok 完全来自真实请求结果。"""

    connection: DataConnectionItem
    ok: bool
    message: str
    elapsed_ms: int


class SyncRecordItem(BaseModel):
    id: int
    connection_id: int
    connection_name: str
    trigger: SyncTrigger
    trigger_label: str
    result: SyncResult
    result_label: str
    rows_total: int
    rows_created: int
    rows_skipped: int
    import_batch_id: int | None = None
    message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class SyncRecordListResponse(BaseModel):
    items: list[SyncRecordItem]
    total: EvidenceValue


class ConnectionSummaryResponse(BaseModel):
    """连接中心概览。"""

    total: EvidenceValue
    ok_total: EvidenceValue
    unconfigured_total: EvidenceValue
    error_total: EvidenceValue
    supported_kinds: list[str] = Field(description="V1 真正实现的连接器")
    framework_only_kinds: list[str] = Field(description="只有配置框架、V1 未接入的连接器")
    note: str
