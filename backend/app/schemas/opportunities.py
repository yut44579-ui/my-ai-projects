"""商机接口的 Pydantic 契约（TASK-016）。

★ 与其它模块同一套硬约束：
  · 金额 / 计数等业务数字一律 EvidenceValue（D4）；
  · 阶段、优先级、日期、负责人是元数据，返回裸值；
  · 赢单概率**由人工填写**，不是模型预测（§三）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.opportunity import OpportunityPriority, OpportunityStage
from app.schemas.evidence import EvidenceValue


class OpportunityItem(BaseModel):
    """一条商机。"""

    id: int
    customer_id: int
    customer_name: str = Field(description="冗余客户名，看板直接用，避免 N+1")
    title: str
    stage: OpportunityStage
    stage_label: str
    priority: OpportunityPriority
    amount: EvidenceValue = Field(description="预计金额（业务数字）")
    currency: str
    won_amount: EvidenceValue = Field(description="实际成交金额；未赢单为 NO_DATA")
    probability: int | None = Field(default=None, description="赢单概率 0~100（人工填写）")
    expected_close_date: date | None = None
    closed_at: datetime | None = None
    owner: str | None = None
    note: str | None = None
    is_active: bool
    evidence_ref: str


class OpportunityCreateRequest(BaseModel):
    """新建商机。"""

    customer_id: int = Field(description="必须挂在一个真实客户上")
    title: str = Field(min_length=1, max_length=200)
    stage: OpportunityStage = OpportunityStage.NEW
    priority: OpportunityPriority = OpportunityPriority.MEDIUM
    amount: Decimal | None = Field(default=None, ge=0, description="预计金额；不填 = 未估出，不是 0")
    currency: str = Field(default="CNY", max_length=8)
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    owner: str | None = Field(default=None, max_length=64)
    note: str | None = None


class OpportunityUpdateRequest(BaseModel):
    """更新商机（只传要改的字段）。★ stage 请走 /stage 接口，以便校验流转合法性。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: OpportunityPriority | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: date | None = None
    owner: str | None = Field(default=None, max_length=64)
    note: str | None = None


class OpportunityStageRequest(BaseModel):
    """推进 / 关闭商机。"""

    stage: OpportunityStage
    won_amount: Decimal | None = Field(
        default=None, ge=0, description="赢单时的实际成交金额（stage=WON 时可用）"
    )
    note: str | None = Field(default=None, max_length=500, description="本次流转说明")


class OpportunityStageResponse(BaseModel):
    """阶段流转结果。"""

    opportunity: OpportunityItem
    changed: bool = Field(description="false = 阶段本来就等于目标值（no-op，未写任何变更）")
    message: str


class OpportunityListResponse(BaseModel):
    items: list[OpportunityItem]
    page: int
    page_size: int
    total: EvidenceValue


class OpportunityStageColumn(BaseModel):
    """看板的一列 = 一个阶段。"""

    stage: OpportunityStage
    label: str
    count: int = Field(description="该阶段的商机数（分页前的全量计数）")
    amount_total: EvidenceValue = Field(description="该阶段金额合计；没有已估金额则 NO_DATA")
    items: list[OpportunityItem] = Field(description="该阶段的商机（每个阶段最多返回 limit 条）")


class OpportunityBoardResponse(BaseModel):
    """商机看板：按阶段分列。"""

    columns: list[OpportunityStageColumn]
    total: EvidenceValue = Field(description="商机总数")
    open_total: EvidenceValue = Field(description="进行中（未结束）的商机数")
    won_total: EvidenceValue = Field(description="赢单数")
    pipeline_amount: EvidenceValue = Field(
        description="进行中商机的金额合计（管道金额）；没有已估金额 → NO_DATA"
    )
    won_amount_total: EvidenceValue = Field(description="赢单金额合计")
    note: str
