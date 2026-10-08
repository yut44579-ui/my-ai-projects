"""客户工作台（客户详情页）扩展契约。

═══════════════════════════════════════════════════════════════════════
【为什么单独一个聚合接口】
═══════════════════════════════════════════════════════════════════════
客户详情页需要「状态 + 下一步 + 风险提醒」三块。它们跨 4 张表
（customers / customer_handover_events / customer_messages / customer_events），
如果前端自己拼会变成「前端算业务结论」——违反 D4。
所以由后端一次算好，前端只显示。

═══════════════════════════════════════════════════════════════════════
【风险提醒的判定依据（只有两条，都可复现）】
═══════════════════════════════════════════════════════════════════════
  · POLICY_BLOCKED —— 消息命中敏感策略（报价/折扣/合同/收款账户…），
                      这是**代码层闸门的真实结论**，不是模型打分
  · AI_FAILED      —— AI 回复失败，需要人工接手
★ 刻意**不做**「高风险/87% 概率」这类推断打分：没有依据的精确数字一律不产出。
  risk_level 只是把上面两条映射成展示等级，不是模型判断。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.customer import HandoverState, LifecycleStatus
from app.schemas.evidence import EvidenceValue


class NextActionCode(str, Enum):
    """下一步动作。每个取值背后是一条确定的判定规则（见 services 里的说明）。"""

    TAKE_OVER = "TAKE_OVER"        # 需人工处理 → 点「人工接管」
    REPLY = "REPLY"                # 人工接管中 → 去回复
    WAIT_CUSTOMER = "WAIT_CUSTOMER"  # 已回复/已报价 → 等客户
    FOLLOW_UP = "FOLLOW_UP"        # 已联系未回复 / 新客户 → 跟进
    VERIFY_DUPLICATE = "VERIFY_DUPLICATE"  # 去重冲突待裁决
    RECORD_NOTE = "RECORD_NOTE"    # 已成交/已失效 → 记录结论
    NONE = "NONE"


ACTION_LABELS: dict[NextActionCode, str] = {
    NextActionCode.TAKE_OVER: "人工接管",
    NextActionCode.REPLY: "回复客户",
    NextActionCode.WAIT_CUSTOMER: "等客户回复",
    NextActionCode.FOLLOW_UP: "跟进客户",
    NextActionCode.VERIFY_DUPLICATE: "裁决重复客户",
    NextActionCode.RECORD_NOTE: "记录结论",
    NextActionCode.NONE: "无需动作",
}


class NextAction(BaseModel):
    """下一步建议。★ 依据是**真实字段**，不是模型推测。"""

    code: NextActionCode
    label: str
    reason: str = Field(description="为什么是这一步（引用真实字段值）")
    evidence_ref: str


class RiskAlertKind(str, Enum):
    POLICY_BLOCKED = "POLICY_BLOCKED"  # 命中敏感策略（闸门结论）
    AI_FAILED = "AI_FAILED"            # AI 回复失败


class RiskLevel(str, Enum):
    """风险等级。★ 只是把上面两条映射成展示用等级，不是推断打分。"""

    NONE = "NONE"
    ATTENTION = "ATTENTION"      # 需要关注
    HIGH = "HIGH"                # 高风险（资金/合规相关）


class RiskAlert(BaseModel):
    """一条风险提醒。"""

    kind: RiskAlertKind
    level: RiskLevel
    title: str
    detail: str = Field(description="原始证据摘录（来自真实消息内容），便于人工核对")
    occurred_at: datetime
    evidence_ref: str


class HandoverHistoryItem(BaseModel):
    id: int
    event_type: str
    from_state: str | None = None
    to_state: str
    actor_type: str
    reason: str | None = None
    evidence_ref: str
    created_at: datetime


class CustomerWorkspaceResponse(BaseModel):
    """客户详情页的状态 / 下一步 / 风险三块。"""

    customer_id: int
    lifecycle_status: LifecycleStatus
    lifecyle_label: str = Field(description="生命周期状态的中文展示名")
    handover_state: HandoverState
    handover_label: str = Field(description="接管状态的中文展示名")
    ai_auto_reply_allowed: bool = Field(
        description="★ 统一判据：只有 AUTO 为 true。前端据此决定「AI 起草回复」是否可用"
    )
    next_action: NextAction
    risks: list[RiskAlert] = Field(default_factory=list)
    risk_total: EvidenceValue = Field(description="风险条数（业务数字）；没有 → NO_DATA")
    handover_history: list[HandoverHistoryItem] = Field(default_factory=list)
    note: str = Field(description="如实说明这些结论的来源")
