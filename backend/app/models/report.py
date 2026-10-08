"""汇报模型（D11 第 7 表，TASK-007 落地）。

★ 快照语义（D6）：content_json 在**生成时**把数字与 evidence_ref 就地写死，
  之后即使客户被改/被删，重新打开这份报告数字不许变化 —— 查询接口只读 content_json，
  绝不重新计算。
★ 时区固定 Asia/Shanghai（D6），timezone 列只是把这个事实记进快照里。
★ content_json 结构：{"metrics": {<metric_key>: {value/state/source_type/evidence_ref[/reason]}}}
  每个指标的值一律是 EvidenceValue 的字典形态 —— 前端不得自己算业务数字（D4）。
★ 默认只统计 source_type='REAL'（见 docs/IMPORT_RULES.md §六）；
  被排除的 TEST 条数记进 excluded_test_count，保证"排除了多少"可见。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from sqlalchemy import BigInteger, Date, DateTime, Enum as SAEnum, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_batch import _enum_values

# 汇报时区固定值（D6）。列里存下来是为了让快照自解释，值不允许改。
REPORT_TIMEZONE = "Asia/Shanghai"


class ReportType(str, Enum):
    """报告类型。

    ★ TASK-023 补 MONTHLY：需求 §十八 明确要求支持「本月汇报」，
      原先只有 DAILY/WEEKLY/MANUAL，无法表达月报。
      ★ MANUAL 保留：用于"非固定周期"的手工汇报（既有枚举值，删了会破坏数据）。
    """

    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    MANUAL = "MANUAL"


class ReportSourceType(str, Enum):
    """报告数字的来源类型（与 D4 SourceType 对齐）。

    ★ TASK-021 起与 SourceType 的 8 个取值保持一致（含 SYNC/WEB/TEST/DEMO），
      这样报告快照能如实标注"这份报告的数字来自哪类数据源"。
    """

    REAL = "REAL"
    IMPORT = "IMPORT"
    SYNC = "SYNC"
    WEB = "WEB"
    TEST = "TEST"
    DEMO = "DEMO"
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class Report(Base):
    """一份已生成的汇报快照。"""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    report_type: Mapped[ReportType] = mapped_column(
        SAEnum(ReportType, name="report_type", values_callable=_enum_values), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False, comment="统计期间起点（含）")
    period_end: Mapped[date] = mapped_column(Date, nullable=False, comment="统计期间终点（含）")
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=REPORT_TIMEZONE, comment="固定 Asia/Shanghai（D6）"
    )

    # ★ 快照本体：生成那一刻的数字与证据锚点
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    source_type: Mapped[ReportSourceType] = mapped_column(
        SAEnum(ReportSourceType, name="report_source_type", values_callable=_enum_values),
        nullable=False,
        default=ReportSourceType.SYSTEM,
    )
    generated_by: Mapped[str] = mapped_column(
        String(64), nullable=False, default="system", comment="生成者；TASK-019 起可记登录用户名"
    )
    excluded_test_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="本期被排除在业务数字之外的 TEST 来源客户条数（只计不藏）",
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<Report id={self.id} type={self.report_type} "
            f"{self.period_start}~{self.period_end}>"
        )
