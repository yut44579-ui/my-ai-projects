"""商业洞察契约（TASK-031，需求 §八 AI 能力 → 商业洞察）。

★ 两个字段分开返回，是本页诚信的核心：
    `findings` —— 用**真实业务数据**算出来的洞察（每条带 rule 与 evidence_ref）
    `missing`  —— **做不了的**洞察，以及需要接入什么数据才能做
  宁可页面"能说的不多"，也不编市场判断（§二 / §二十六）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.evidence import EvidenceValue


class InsightFinding(BaseModel):
    key: str
    title: str
    finding: str = Field(description="结论（人能读懂的话）")
    value: int | None = Field(default=None, description="支撑数字；null 表示取不到")
    unit: str = Field(description="数字单位或口径说明")
    severity: str = Field(description="info / attention / warning")
    rule: str = Field(description="★ 判定规则 —— 让人看懂为什么给出这条洞察")
    evidence_ref: str


class MissingCapability(BaseModel):
    """做不了的洞察。★ 如实列出，不糊弄。"""

    key: str
    title: str
    why_missing: str
    would_need: str


class DataCoverage(BaseModel):
    customers: int
    opportunities_open: int
    projects_active: int
    risks_open: int
    contents: int


class BusinessInsightResponse(BaseModel):
    findings: list[InsightFinding] = Field(
        default_factory=list, description="基于真实业务数据的洞察"
    )
    missing: list[MissingCapability] = Field(
        default_factory=list, description="做不了的洞察及所需数据"
    )
    coverage: DataCoverage
    findings_total: EvidenceValue = Field(description="洞察条数（EvidenceValue 口径）")
    note: str
