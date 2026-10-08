"""客户访问追踪的接口契约（TASK-010）。

═══════════════════════════════════════════════════════════════════════
【为什么访问记在 customer_events 而不是新建表】
═══════════════════════════════════════════════════════════════════════
  · D11 冻结 V1 为 7 张表，新增 customer_visits 超出冻结范围；
  · 访问本身就是「客户发生了什么」，与状态变更 / 消息 / 接管同类，
    已有 customer_events 就是为这类事件准备的；
  · metadata_json 足以承载 page / channel / referrer 这几个结构化字段。

═══════════════════════════════════════════════════════════════════════
【只记可识别的访问（§十）】
═══════════════════════════════════════════════════════════════════════
  ★ 必须给 customer_id：匿名访问**不落库**。
    理由：匿名访问无法归属到任何客户，记下来既不能算指标也不能驱动跟进，
    还会污染"客户动态"。等有真实客户渠道（D9）再谈匿名识别。

★ 所有业务数字仍是 EvidenceValue（D4）。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.customer_event import ActorType, CustomerEventType, EventSourceType
from app.schemas.evidence import EvidenceValue

#: 访问来源渠道。★ 冻结枚举，禁止自由文本（与参考图「客户来源」一致）
VISIT_CHANNELS: dict[str, str] = {
    "WEBSITE": "官网",
    "CONTENT": "内容宣传",
    "OUTBOUND": "主动开发",
    "REFERRAL": "转介绍",
    "CRM_IMPORT": "CRM 导入",
    "EXCEL_IMPORT": "Excel 导入",
    "OTHER": "其他",
}


class VisitCreateRequest(BaseModel):
    """记录一次访问。★ customer_id 走路径参数，请求体只描述"这次访问做了什么"。"""

    page: str = Field(min_length=1, max_length=255, description="访问的页面/路径，如 /pricing")
    channel: str = Field(default="WEBSITE", description=f"来源渠道：{' / '.join(VISIT_CHANNELS)}")
    referrer: str | None = Field(default=None, max_length=255, description="来源页/来源说明")
    note: str | None = Field(default=None, max_length=500, description="可选备注")


class VisitItem(BaseModel):
    """一条访问记录（由 customer_events 的 VISIT 事件投影而来）。"""

    event_id: int
    customer_id: int
    customer_name: str = Field(description="冗余客户名，列表页直接用，避免 N+1")
    page: str
    channel: str = Field(description="渠道代码，如 WEBSITE")
    channel_label: str = Field(description="渠道中文名，如 官网")
    referrer: str | None = None
    note: str | None = None
    occurred_at: datetime
    evidence_ref: str


class VisitListResponse(BaseModel):
    items: list[VisitItem]
    page: int
    page_size: int
    total: EvidenceValue = Field(description="当前筛选下的访问条数（业务数字）")


class VisitPageSlice(BaseModel):
    """按页面聚合的一片（"客户最常看什么"）。"""

    page: str
    count: int


class VisitChannelSlice(BaseModel):
    """按渠道聚合的一片。"""

    channel: str
    label: str
    count: int


class VisitSummaryResponse(BaseModel):
    """访问概览。★ 每个数字都是 EvidenceValue；没有 → NO_DATA，不是 0。"""

    total: EvidenceValue = Field(description="访问总次数")
    today: EvidenceValue = Field(description="今天的访问次数")
    visited_customers: EvidenceValue = Field(description="有访问记录的客户数")
    top_pages: list[VisitPageSlice] = Field(default_factory=list)
    by_channel: list[VisitChannelSlice] = Field(default_factory=list)
    daily: list[dict] = Field(default_factory=list, description="最近 7 天：[{day, count}]")
    note: str


class VisitCreateResponse(BaseModel):
    """记录访问的结果。"""

    customer_id: int
    visit: VisitItem
    next_handover_state: str = Field(description="记录后客户的接管状态（未改动，仅回显）")
