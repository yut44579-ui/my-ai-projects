"""导入接口（TASK-001 §三）：preview 只解析、commit 才入库。

路由层只做 HTTP 出入参转换，判定逻辑全在 services/ 里（可被单测直接调用）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.customer import Customer
from app.models.import_batch import ImportBatch
from app.schemas.evidence import SourceType, build_evidence
from app.schemas.imports import (
    BatchCustomerItem,
    BatchDetailResponse,
    BatchListResponse,
    BatchSummary,
    ImportCommitResponse,
    ImportPreviewResponse,
)
from app.services.errors import ImportErrorCode, ImportFailure
from app.services.importer import commit_import, preview_import

router = APIRouter(tags=["imports"])


def _count_evidence(value: int, evidence_ref: str):
    """批次里的行数都是业务数字 → 一律包 EvidenceValue（AC6）。"""
    return build_evidence(value, source_type=SourceType.IMPORT, evidence_ref=evidence_ref)


def _batch_summary(batch: ImportBatch) -> BatchSummary:
    ref = batch.evidence_ref or f"import_batch:{batch.id}"
    return BatchSummary(
        batch_id=batch.id,
        filename=batch.filename,
        file_sha256=batch.file_sha256,
        status=batch.status,
        source_type=batch.source_type.value,
        evidence_ref=ref,
        rows_total=_count_evidence(batch.rows_total, ref),
        rows_created=_count_evidence(batch.rows_created, ref),
        rows_deduplicated=_count_evidence(batch.rows_deduplicated, ref),
        rows_skipped=_count_evidence(batch.rows_skipped, ref),
        imported_at=batch.imported_at,
    )


@router.post("/imports/preview", response_model=ImportPreviewResponse, summary="预览解析（不入库）")
async def preview(
    file: UploadFile = File(..., description="CSV / XLSX 文件"),
    header_row: int = Form(1, description="第几行是表头（1 起）"),
    sheet: str | None = Form(None, description="xlsx 指定 sheet，缺省读第一个"),
) -> ImportPreviewResponse:
    """只解析、不入库：返回列名/自动映射/置信度/前 3 行样本/编码/sheet/表头行号/sha256。"""
    raw = await file.read()
    data = preview_import(raw, file.filename or "", header_row=header_row, sheet=sheet)
    ref = f"import_preview:{data['file_sha256'][:12]}"
    data["rows_total"] = build_evidence(
        data["rows_total"], source_type=SourceType.IMPORT, evidence_ref=ref
    )
    return ImportPreviewResponse(**data)


@router.post("/imports/commit", response_model=ImportCommitResponse, summary="确认映射并入库")
async def commit(
    file: UploadFile = File(..., description="与预览时同一份文件"),
    sha256: str = Form(..., description="预览返回的 file_sha256，用于确认没换文件"),
    source_type: str = Form(
        "TEST",
        description="数据来源 REAL/TEST/MANUAL。缺省 TEST —— 宁可把真实数据当测试，"
        "也不能让测试数据污染业务数字（§六）",
    ),
    header_row: int = Form(1),
    mapping: str | None = Form(None, description="用户确认后的映射表 JSON 数组"),
    sheet: str | None = Form(None),
    encoding: str | None = Form(None, description="预览里确认过的编码"),
    db: Session = Depends(get_db),
) -> ImportCommitResponse:
    """带「用户确认后的映射表 + sha256」真正入库。"""
    parsed_mapping: list[dict] | None = None
    if mapping:
        try:
            parsed_mapping = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise ImportFailure(
                ImportErrorCode.INVALID_MAPPING, f"mapping 不是合法 JSON：{exc}"
            ) from exc
        if not isinstance(parsed_mapping, list):
            raise ImportFailure(ImportErrorCode.INVALID_MAPPING, "mapping 必须是数组")

    raw = await file.read()
    result = commit_import(
        db,
        raw,
        file.filename or "",
        sha256=sha256,
        source_type=source_type,
        mapping=parsed_mapping,
        header_row=header_row,
        sheet=sheet,
        encoding=encoding,
    )
    batch = result.batch
    ref = batch.evidence_ref
    return ImportCommitResponse(
        batch_id=batch.id,
        filename=batch.filename,
        file_sha256=batch.file_sha256,
        status=batch.status,
        source_type=batch.source_type.value,
        evidence_ref=ref,
        rows_total=_count_evidence(batch.rows_total, ref),
        rows_created=_count_evidence(batch.rows_created, ref),
        rows_deduplicated=_count_evidence(batch.rows_deduplicated, ref),
        rows_skipped=_count_evidence(batch.rows_skipped, ref),
        skipped_reasons=result.skipped_reasons,
        skipped_columns=result.skipped_columns,
        conflicts=result.conflicts,
        imported_at=batch.imported_at,
    )


@router.get("/imports", response_model=BatchListResponse, summary="导入批次列表")
def list_batches(db: Session = Depends(get_db)) -> BatchListResponse:
    batches = db.execute(select(ImportBatch).order_by(ImportBatch.id.desc())).scalars().all()
    total = db.execute(select(func.count()).select_from(ImportBatch)).scalar_one()
    return BatchListResponse(
        items=[_batch_summary(b) for b in batches],
        total=build_evidence(total, source_type=SourceType.IMPORT, evidence_ref="import_batches:count"),
    )


@router.get("/imports/{batch_id}", response_model=BatchDetailResponse, summary="批次详情（追溯入口）")
def batch_detail(batch_id: int, db: Session = Depends(get_db)) -> BatchDetailResponse:
    """V1 不做撤销导入，"追溯"就靠这个接口：跳过明细 + 该批次导入的客户 id 列表。"""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")

    customers = (
        db.execute(select(Customer).where(Customer.batch_id == batch_id).order_by(Customer.id))
        .scalars()
        .all()
    )
    summary = _batch_summary(batch)
    return BatchDetailResponse(
        **summary.model_dump(),
        skipped_reasons=batch.skipped_reasons_json or [],
        skipped_columns=batch.skipped_columns_json or [],
        customer_ids=[c.id for c in customers],
        customers=[
            BatchCustomerItem(
                id=c.id,
                name=c.name,
                company_name=c.company_name,
                phone=c.phone,
                email=c.email,
                dedupe_state=c.dedupe_state,
                source_type=c.source_type,
                evidence_ref=c.evidence_ref,
            )
            for c in customers
        ],
        pending_review_customer_ids=[
            c.id for c in customers if c.dedupe_state.value == "PENDING_REVIEW"
        ],
    )
