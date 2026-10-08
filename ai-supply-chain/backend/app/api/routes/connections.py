"""数据连接接口（TASK-024，需求 §二十）。

    GET    /api/connections                      连接列表
    GET    /api/connections/summary              概览（状态计数）
    POST   /api/connections                      新建连接
    GET    /api/connections/{id}                 详情
    PATCH  /api/connections/{id}                 更新（含替换凭据）
    POST   /api/connections/{id}/test            测试连接（★ 真发请求）
    POST   /api/connections/{id}/toggle          停用/启用
    GET    /api/connections/sync-records         同步记录（全部）
    DELETE /api/connections/{id}                 断开连接（软删除）

★ 路由顺序：/summary 与 /sync-records 必须注册在 /{connection_id} 之前
  （多处已踩过同一个坑）。
★ 没有任何接口能直接把 status 写成 OK —— 状态只能由 /test 的真实结果驱动。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.data_connection import (
    ConnectionStatus,
    ConnectorKind,
    DataConnection,
    SyncTrigger,
)
from app.schemas.data_connections import (
    ConnectionSummaryResponse,
    ConnectionTestResponse,
    DataConnectionCreateRequest,
    DataConnectionItem,
    DataConnectionListResponse,
    DataConnectionUpdateRequest,
    SyncRecordItem,
    SyncRecordListResponse,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.services.data_connection import (
    FRAMEWORK_ONLY,
    KIND_NOTES,
    LIVE_TESTABLE,
    ConnectionError,
    create_connection,
    get_connection,
    has_credential,
    list_connections,
    list_sync_records,
    set_enabled,
    summary_counts,
    test_connection,
    update_connection,
)
from app.services.errors import ApiErrorCode, ApiFailure

router = APIRouter(tags=["data-connections"])

KIND_LABELS: dict[ConnectorKind, str] = {
    ConnectorKind.EXCEL_CSV: "Excel / CSV",
    ConnectorKind.MYSQL: "MySQL",
    ConnectorKind.POSTGRESQL: "PostgreSQL",
    ConnectorKind.CRM: "CRM",
    ConnectorKind.ERP: "ERP",
    ConnectorKind.WECHAT_WORK: "企业微信",
    ConnectorKind.WEBSITE: "官网",
    ConnectorKind.API: "API 接口",
    ConnectorKind.WEBHOOK: "Webhook",
}

STATUS_LABELS: dict[ConnectionStatus, str] = {
    ConnectionStatus.UNCONFIGURED: "未配置",
    ConnectionStatus.CONFIGURED: "已配置·未测试",
    ConnectionStatus.OK: "连接正常",
    ConnectionStatus.ERROR: "连接失败",
    ConnectionStatus.DISABLED: "已停用",
}

TRIGGER_LABELS: dict[SyncTrigger, str] = {
    SyncTrigger.MANUAL: "人工触发",
    SyncTrigger.SCHEDULED: "定时任务",
    SyncTrigger.WEBHOOK: "外部推送",
    SyncTrigger.UPLOAD: "文件上传",
}


def _supported(kind: ConnectorKind) -> bool:
    """V1 是否真的实现了这个连接器。"""
    return kind == ConnectorKind.EXCEL_CSV or kind in LIVE_TESTABLE


def _to_item(row: DataConnection) -> DataConnectionItem:
    return DataConnectionItem(
        id=row.id,
        kind=row.kind,
        kind_label=KIND_LABELS.get(row.kind, row.kind.value),
        name=row.name,
        status=row.status,
        status_label=STATUS_LABELS.get(row.status, row.status.value),
        note=KIND_NOTES.get(row.kind, ""),
        config=row.config_json or {},
        has_credential=has_credential(row),
        scope_note=row.scope_note,
        permission_note=row.permission_note,
        last_sync_at=row.last_sync_at,
        last_test_at=row.last_test_at,
        last_error=row.last_error,
        supported=_supported(row.kind),
    )


def _require(db: Session, connection_id: int) -> DataConnection:
    row = get_connection(db, connection_id)
    if row is None:
        raise ApiFailure(
            ApiErrorCode.CONNECTION_NOT_FOUND,
            f"连接 {connection_id} 不存在",
            http_status=404,
        )
    return row


@router.get("/connections/summary", response_model=ConnectionSummaryResponse, summary="连接概览")
def connections_summary(db: Session = Depends(get_db)) -> ConnectionSummaryResponse:
    counts = summary_counts(db)
    total = sum(counts.values())

    def ev_or_nodata(n: int, ref: str, empty: str):
        if total:
            return build_evidence(n, source_type=SourceType.SYSTEM, evidence_ref=ref)
        return no_data_evidence(empty, source_type=SourceType.SYSTEM, evidence_ref=ref)

    return ConnectionSummaryResponse(
        total=ev_or_nodata(total, "connections:count", "还没有配置任何数据连接"),
        ok_total=ev_or_nodata(counts["OK"], "connections:ok", "还没有连接测试通过"),
        unconfigured_total=ev_or_nodata(
            counts["UNCONFIGURED"], "connections:unconfigured", "没有未配置的连接"
        ),
        error_total=ev_or_nodata(counts["ERROR"], "connections:error", "没有连接失败"),
        supported_kinds=[k.value for k in ConnectorKind if _supported(k)],
        framework_only_kinds=[k.value for k in ConnectorKind if k in FRAMEWORK_ONLY],
        note=(
            "连接状态只由真实的测试/同步结果驱动，没有任何接口能人工把它标成「连接正常」。"
            "没有凭据的连接如实显示「未配置」——本系统不做假连接页。"
        ),
    )


@router.get("/connections/sync-records", response_model=SyncRecordListResponse, summary="同步记录")
def sync_records(
    connection_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> SyncRecordListResponse:
    rows, total = list_sync_records(db, connection_id=connection_id, limit=limit)
    names = {
        r.id: r.name for r in list_connections(db)
    }
    total_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="sync_records:count")
        if total
        else no_data_evidence(
            "还没有同步记录", source_type=SourceType.SYSTEM, evidence_ref="sync_records:count"
        )
    )
    return SyncRecordListResponse(
        items=[
            SyncRecordItem(
                id=r.id,
                connection_id=r.connection_id,
                connection_name=names.get(r.connection_id, f"#{r.connection_id}"),
                trigger=r.trigger,
                trigger_label=TRIGGER_LABELS.get(r.trigger, r.trigger.value),
                result=r.result,
                result_label={
                    "SUCCESS": "成功",
                    "PARTIAL": "部分成功",
                    "FAILED": "失败",
                }.get(r.result.value, r.result.value),
                rows_total=r.rows_total,
                rows_created=r.rows_created,
                rows_skipped=r.rows_skipped,
                import_batch_id=r.import_batch_id,
                message=r.message,
                started_at=r.started_at,
                finished_at=r.finished_at,
            )
            for r in rows
        ],
        total=total_ev,
    )


@router.get("/connections", response_model=DataConnectionListResponse, summary="连接列表")
def connections_list(db: Session = Depends(get_db)) -> DataConnectionListResponse:
    rows = list_connections(db)
    total = len(rows)
    total_ev = (
        build_evidence(total, source_type=SourceType.SYSTEM, evidence_ref="connections:count")
        if total
        else no_data_evidence(
            "还没有配置任何数据连接",
            source_type=SourceType.SYSTEM,
            evidence_ref="connections:count",
        )
    )
    return DataConnectionListResponse(items=[_to_item(r) for r in rows], total=total_ev)


@router.post(
    "/connections", response_model=DataConnectionItem, status_code=201, summary="新建连接"
)
def connections_create(
    payload: DataConnectionCreateRequest, db: Session = Depends(get_db)
) -> DataConnectionItem:
    try:
        row = create_connection(
            db,
            kind=payload.kind,
            name=payload.name,
            config=payload.config,
            secret=payload.secret,
            scope_note=payload.scope_note,
            permission_note=payload.permission_note,
        )
    except ConnectionError as exc:
        raise ApiFailure(ApiErrorCode.CONNECTION_INVALID, str(exc))
    return _to_item(row)


@router.get("/connections/{connection_id}", response_model=DataConnectionItem, summary="连接详情")
def connections_detail(connection_id: int, db: Session = Depends(get_db)) -> DataConnectionItem:
    return _to_item(_require(db, connection_id))


@router.patch("/connections/{connection_id}", response_model=DataConnectionItem, summary="更新连接")
def connections_update(
    connection_id: int, payload: DataConnectionUpdateRequest, db: Session = Depends(get_db)
) -> DataConnectionItem:
    row = _require(db, connection_id)
    try:
        updated = update_connection(
            db,
            row,
            name=payload.name,
            config=payload.config,
            secret=payload.secret,
            scope_note=payload.scope_note,
            permission_note=payload.permission_note,
        )
    except ConnectionError as exc:
        raise ApiFailure(ApiErrorCode.CONNECTION_INVALID, str(exc))
    return _to_item(updated)


@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResponse,
    summary="测试连接（★ 真发请求，结果决定状态）",
)
def connections_test(
    connection_id: int, db: Session = Depends(get_db)
) -> ConnectionTestResponse:
    """真实连接测试。

    ★ ok 完全来自真实请求结果：
      · MySQL/PostgreSQL 会真建连接并跑 `SELECT 1`；
      · Excel/CSV 说明通道可用；
      · 仅框架类连接器如实返回失败与原因，**不返回假的"成功"**。
    """
    row = _require(db, connection_id)
    outcome = test_connection(db, row)
    db.refresh(row)
    return ConnectionTestResponse(
        connection=_to_item(row),
        ok=outcome.ok,
        message=outcome.message,
        elapsed_ms=outcome.elapsed_ms,
    )


@router.post(
    "/connections/{connection_id}/toggle",
    response_model=DataConnectionItem,
    summary="停用 / 启用连接",
)
def connections_toggle(
    connection_id: int,
    enabled: bool = Query(..., description="true=启用，false=停用"),
    db: Session = Depends(get_db),
) -> DataConnectionItem:
    row = _require(db, connection_id)
    return _to_item(set_enabled(db, row, enabled))


@router.delete("/connections/{connection_id}", response_model=DataConnectionItem, summary="断开连接")
def connections_delete(connection_id: int, db: Session = Depends(get_db)) -> DataConnectionItem:
    """断开连接（软删除：is_active=False）。

    ★ 不物理删除：同步记录要保留，否则"之前同步过什么"就查不到了。
    """
    row = _require(db, connection_id)
    row.is_active = False
    row.status = ConnectionStatus.DISABLED
    db.commit()
    db.refresh(row)
    return _to_item(row)
