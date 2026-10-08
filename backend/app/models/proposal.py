"""方案模型（TASK-030，需求 §八 输出中心 → 方案）。

═══════════════════════════════════════════════════════════════════════
【方案是什么，不是什么】
═══════════════════════════════════════════════════════════════════════
    §二十八 要求"更快形成方案"。所以方案 = **面向某个客户/商机的建议书草稿**。

    ★ 它不是：
      · 不是自动发给客户的（§十六：AI建议 ≠ 自动发送）
      · 不是凭空写的（必须基于**知识库里的真实产品资料** + **真实业务数据**）
      · 不是可以自动生效的（必须人工确认，见 status）

═══════════════════════════════════════════════════════════════════════
【沿用已经验证过的两条纪律】
═══════════════════════════════════════════════════════════════════════
    ① `inputs_json` **冻结生成时用到的输入**（引用了哪些知识片段、哪些业务数字）。
       理由同 D6（报告是生成时快照）：方案生成后资料可能被改，
       不冻结的话"这份方案当初依据什么"就查不到了（§十九 可追溯）。
    ② 沿用获客话术的模式：AI 只产出候选，`status` 只能由**人工接口**推进
       （GENERATED → CONFIRMED / REJECTED）。**不存在 AI 自己确认的路径**。

═══════════════════════════════════════════════════════════════════════
【不许编造】
═══════════════════════════════════════════════════════════════════════
    提示词里明确禁止：编造产品参数、编造案例、给价格/折扣/交期承诺。
    知识库命中片段是**产品资料**，业务数据是**客户事实**，两者分开进入提示词。
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


class ProposalStatus(str, Enum):
    """方案状态。★ 只能由人工接口推进 GENERATED → CONFIRMED / REJECTED。"""

    NONE = "NONE"            # 还没生成内容
    GENERATED = "GENERATED"  # AI 已生成草稿，等人工确认
    CONFIRMED = "CONFIRMED"  # 人工确认可用
    REJECTED = "REJECTED"    # 人工驳回


class ProposalKind(str, Enum):
    """方案类型（§八 是"方案"，但实际业务里方案有不同用途）。"""

    SOLUTION = "SOLUTION"        # 解决方案建议书
    QUOTATION_NOTE = "QUOTATION_NOTE"  # 报价说明（★ 不含具体价格，价格走人工）
    SERVICE_PLAN = "SERVICE_PLAN"      # 服务方案
    COOPERATION = "COOPERATION"        # 合作建议
    OTHER = "OTHER"


class Proposal(Base):
    """一份方案草稿。"""

    __tablename__ = "proposals"

    __table_args__ = (
        Index("ix_proposals_customer", "customer_id"),
        Index("ix_proposals_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[ProposalKind] = mapped_column(
        SAEnum(ProposalKind, name="proposal_kind", values_callable=_enum_values),
        nullable=False,
        default=ProposalKind.SOLUTION,
    )
    status: Mapped[ProposalStatus] = mapped_column(
        SAEnum(ProposalStatus, name="proposal_status", values_callable=_enum_values),
        nullable=False,
        default=ProposalStatus.NONE,
    )

    #: 面向哪个客户（可为空：潜在客户还没建档时也能写方案）
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    #: 关联商机（可为空）
    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    #: 没有客户档案时的对象名
    target_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: 人工填写的需求背景（生成方案的输入之一）
    requirement: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="客户需求/背景，由人工填写；AI 不得添加原文没有的需求"
    )

    #: ★ 冻结生成时用到的输入：命中的知识片段 + 业务数据快照（§十九 可追溯）
    inputs_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="生成时冻结的输入：{knowledge_hits:[...], business_data:{...}}",
    )
    #: 生成内容（分节，便于前端展示与人工作业）
    content_json: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="[{heading, body, basis}]"
    )

    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(Stamp6, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    llm_called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(Stamp6, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Stamp6, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Proposal id={self.id} {self.title!r} {self.status}>"
