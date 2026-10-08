"""风险事件接口的 Pydantic 契约（TASK-009）。

★ 与其它模块同一套硬约束：
  · 所有业务数字是 EvidenceValue（D4）；
  · 风险证据锚点用 message_id（D5）→ evidence_ref = `customer_message:{id}`；
  · 等级由类别决定，不是模型打分（见 services/risk.py）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.risk_event import RiskEventType, RiskLevel, RiskStatus
from app.schemas.evidence import EvidenceValue


class RiskEventItem(BaseModel):
    """一条风险。"""

    id: int
    customer_id: int
    customer_name: str = Field(description="冗余客户名，列表页直接用，避免 N+1")
    event_type: RiskEventType
    level: RiskLevel
    status: RiskStatus
    source_type: str
    source_ref: str = Field(description="来源唯一标识，如 customer_message:346")
    trigger_text: str | None = Field(default=None, description="触发摘要（D5：只作摘要）")
    reason: str | None = None
    evidence_ref: str = Field(description="证据锚点（D5：= customer_message:{id}）")
    resolved_at: datetime | None = None
    resolved_note: str | None = None
    created_at: datetime


class RiskListResponse(BaseModel):
    items: list[RiskEventItem]
    page: int
    page_size: int
    total: EvidenceValue = Field(description="当前筛选下的风险条数（业务数字）")


class RiskSummaryResponse(BaseModel):
    """风险中心顶部汇总。★ 每个数字都是 EvidenceValue；没有 → NO_DATA，不是 0。"""

    total: EvidenceValue
    open_total: EvidenceValue
    high_open: EvidenceValue
    attention_open: EvidenceValue
    resolved_total: EvidenceValue
    by_type: list[dict] = Field(
        default_factory=list, description="按类型分布：[{event_type, label, count}]"
    )
    note: str


class RiskScanResponse(BaseModel):
    """扫描结果。"""

    scanned_messages: int
    created: int
    skipped_existing: int
    note: str


class RiskResolveRequest(BaseModel):
    """处置风险的入参。"""

    status: RiskStatus = Field(description="RESOLVED 已处理 / DISMISSED 误报忽略 / OPEN 重新打开")
    note: str | None = Field(default=None, max_length=500, description="处置说明")
