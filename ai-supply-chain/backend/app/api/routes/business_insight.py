"""商业洞察接口（TASK-031，需求 §八 AI 能力 → 商业洞察）。

    GET /api/business-insight/overview

★ 本页只做两件事：
   ① 用系统里**真实存在的业务数据**做跨模块洞察（每条带判定规则与证据锚点）
   ② **明确列出做不了的洞察**以及需要接入什么数据

  缺数据时返回 NO_DATA 并说明原因，**不猜、不估算、不编"行业经验值"**（§二/§二十六）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.business_insight import (
    BusinessInsightResponse,
    DataCoverage,
    InsightFinding,
    MissingCapability,
)
from app.schemas.evidence import SourceType, build_evidence, no_data_evidence
from app.services.business_insight import STALE_OPPORTUNITY_DAYS, overview

router = APIRouter(tags=["business-insight"])


@router.get(
    "/business-insight/overview",
    response_model=BusinessInsightResponse,
    summary="商业洞察（真实数据 + 明确声明做不了的部分）",
)
def business_insight_overview(db: Session = Depends(get_db)) -> BusinessInsightResponse:
    data = overview(db)
    findings = data["findings"]

    findings_ev = (
        build_evidence(
            len(findings), source_type=SourceType.SYSTEM, evidence_ref="business_insight:count"
        )
        if findings
        else no_data_evidence(
            "没有可分析的业务数据",
            source_type=SourceType.SYSTEM,
            evidence_ref="business_insight:count",
        )
    )

    return BusinessInsightResponse(
        findings=[
            InsightFinding(
                key=f.key,
                title=f.title,
                finding=f.finding,
                value=f.value,
                unit=f.unit,
                severity=f.severity,
                rule=f.rule,
                evidence_ref=f.evidence_ref,
            )
            for f in findings
        ],
        missing=[
            MissingCapability(
                key=m.key,
                title=m.title,
                why_missing=m.why_missing,
                would_need=m.would_need,
            )
            for m in data["missing"]
        ],
        coverage=DataCoverage(**data["data_coverage"]),
        findings_total=findings_ev,
        note=(
            "本页只用系统里「真实存在」的业务数据做分析，每条洞察都写明判定规则与证据锚点。"
            "★ 市场、竞对、渠道转化、客户利润这四类需要「外部数据」的洞察，"
            "系统内没有数据源，因此不做 —— 已在下方如实列出「需要接入什么」。"
            f"（商机停滞的判定阈值：{STALE_OPPORTUNITY_DAYS} 天）"
        ),
    )
