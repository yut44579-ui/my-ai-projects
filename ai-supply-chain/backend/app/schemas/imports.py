"""导入接口的 Pydantic 契约。

★ 计数一律以 EvidenceValue 返回（AC6）：rows_total / rows_created / rows_deduplicated /
  rows_skipped 都是业务数字，绝不允许裸 int 直出。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.customer import CustomerSourceType, DedupeState
from app.models.import_batch import ImportBatchStatus
from app.schemas.evidence import EvidenceValue


class ColumnPreview(BaseModel):
    """一列的自动映射结果。"""

    column: str
    target: str | None = Field(default=None, description="命中的目标字段；null=没认出来")
    confidence: str = Field(description="auto=别名命中 / unknown=需要人工确认")


class TargetField(BaseModel):
    key: str
    label: str


class ImportPreviewResponse(BaseModel):
    """POST /api/imports/preview —— 只解析、不入库。"""

    filename: str
    file_sha256: str
    file_kind: str = Field(description="csv / xlsx")
    encoding: str | None = Field(default=None, description="csv 识别到的编码；xlsx 为 null")
    encoding_note: str | None = None
    sheet_names: list[str] = Field(default_factory=list)
    sheet_used: str | None = None
    sheet_note: str | None = Field(default=None, description="多 sheet 时明确告知只读了哪一个")
    header_row: int
    ignored_leading_rows: int
    headers: list[str]
    rows_total: EvidenceValue
    columns: list[ColumnPreview]
    unmapped_columns: list[str]
    sample_rows: list[dict[str, str]]
    target_fields: list[TargetField]


class DedupeConflict(BaseModel):
    """去重冲突记录（谁和谁撞了），供人工裁决用。"""

    customer_id: int | None = None
    conflict_with: list[int] = Field(default_factory=list)
    kind: str = Field(description="multiple_matches=命中多条 / no_match_key=没有可比键")


class SkippedRow(BaseModel):
    row: int = Field(description="文件里的真实行号（含表头，1 起）")
    reason: str
    raw: str = Field(description="原始值，只作报错上下文，不入业务字段")


class SkippedColumn(BaseModel):
    column: str
    reason: str = "unmapped_column"


class ImportCommitResponse(BaseModel):
    """POST /api/imports/commit —— 真入库后的结果。"""

    batch_id: int
    filename: str
    file_sha256: str
    status: ImportBatchStatus
    source_type: CustomerSourceType
    evidence_ref: str
    rows_total: EvidenceValue
    rows_created: EvidenceValue
    rows_deduplicated: EvidenceValue
    rows_skipped: EvidenceValue
    skipped_reasons: list[SkippedRow]
    skipped_columns: list[SkippedColumn]
    conflicts: list[DedupeConflict]
    imported_at: datetime


class BatchSummary(BaseModel):
    batch_id: int
    filename: str
    file_sha256: str
    status: ImportBatchStatus
    source_type: CustomerSourceType
    evidence_ref: str
    rows_total: EvidenceValue
    rows_created: EvidenceValue
    rows_deduplicated: EvidenceValue
    rows_skipped: EvidenceValue
    imported_at: datetime


class BatchListResponse(BaseModel):
    items: list[BatchSummary]
    total: EvidenceValue


class BatchCustomerItem(BaseModel):
    """批次详情里的客户条目（★ 带 dedupe_state，PENDING_REVIEW 就是等人工看的那些）。"""

    id: int
    name: str
    company_name: str | None = None
    phone: str | None = None
    email: str | None = None
    dedupe_state: DedupeState
    source_type: CustomerSourceType
    evidence_ref: str | None = None


class BatchDetailResponse(BatchSummary):
    skipped_reasons: list[SkippedRow]
    skipped_columns: list[SkippedColumn]
    customer_ids: list[int]
    customers: list[BatchCustomerItem]
    pending_review_customer_ids: list[int] = Field(
        default_factory=list, description="该批次里需要人工裁决的客户 id"
    )
