"""方案生成服务（TASK-030，需求 §八 输出中心 → 方案）。

═══════════════════════════════════════════════════════════════════════
【复用，不重造（§四）】
═══════════════════════════════════════════════════════════════════════
    方案的生成链路与「获客话术」（TASK-025）本质相同，所以**复用同一套纪律**：
      · AI 只产出候选 → 人工确认闸门（confirm/reject 只能人工调）
      · 生成时把输入**冻结**下来，事后可回溯（§十九）
      · 本模块**没有任何发送能力**

    但它**不重造检索**：直接调 `services.knowledge.search()` 拿产品资料片段，
    调 `services.knowledge.collect_business_data()` 拿真实业务数字。

═══════════════════════════════════════════════════════════════════════
【两条硬约束写进提示词】
═══════════════════════════════════════════════════════════════════════
    ① 不许编造：产品参数只能用知识库片段里的；客户情况只能用业务数据里的。
       知识库没命中就**明说资料不足**，不要硬写。
    ② 不许给承诺：价格/折扣/交期/账期/赔偿一律不写。
       方案里出现"报价 100 万""保证 30 天交付"这类内容即是错误。
       价格类方案（QUOTATION_NOTE）只写"报价构成与口径"，不写数字。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.knowledge import KnowledgeDocType
from app.models.opportunity import Opportunity
from app.models.proposal import Proposal, ProposalKind, ProposalStatus
from app.services.knowledge import collect_business_data, search
from app.services.llm import LLMUnavailable, generate_reply

#: 方案生成的 system prompt。★ 与获客话术同样写成"规则清单"，不写"你是专家"。
PROPOSAL_SYSTEM_PROMPT = """你是业务人员的方案写作助手。你要把**给定的资料**整理成一份方案草稿。

你会收到：
【A. 产品/服务资料】—— 来自公司知识库的片段，是**唯一**可以引用的产品信息来源。
【B. 客户情况】—— 系统里的**真实业务数据**，可以陈述，但不许改动或推算。
【C. 客户需求】—— 业务人员手写的需求背景。**不许添加它没提到的需求。**

【铁律】
1. **不许编造**：
   · 产品参数、功能、案例、资质，只能用【A】里有的。A 里没有就不要写。
   · 客户情况只能用【B】里有的。
   · 如果【A】没有命中资料，section 里要**明确写"知识库暂无相关资料"**，不要硬写。
2. **不许给任何承诺**：不写价格、折扣、交期、账期、赔偿、保证性措辞
   （"保证""一定能""100%"）。价格相关只写口径与构成，不写数字。
3. **不许把推断说成事实**：不确定的用"建议核实""需进一步确认"。
4. 语言：面向业务人员的书面中文，简洁、分节、可直接改。
   不要写"综上所述""赋能""闭环"这类空话。

【输出格式】只输出 JSON：
{
  "sections": [
    {"heading": "章节标题", "body": "章节正文", "basis": "这一节依据什么（引用 A 的哪段或 B 的哪个数字）"}
  ],
  "missing_info": ["还需要什么信息才能把方案写完整"]
}
给 3~6 节。"""


class ProposalError(Exception):
    """方案相关业务错误（路由层翻译成 400）。"""


@dataclass
class GenerateOutcome:
    sections: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    hits: list = field(default_factory=list)
    business_data: dict = field(default_factory=dict)
    llm_called: bool = False
    llm_error: str | None = None


def _customer_block(db: Session, proposal: Proposal) -> dict:
    """收集该方案关联的真实业务数据（客户 / 商机）。**取不到就不放进去**，不填 0。"""
    data: dict = {}

    if proposal.customer_id:
        c = db.get(Customer, proposal.customer_id)
        if c is not None:
            data["客户名称"] = c.name
            if c.company_name:
                data["公司"] = c.company_name
            if c.region:
                data["地区"] = c.region
            data["客户阶段"] = (
                c.lifecycle_status.value
                if hasattr(c.lifecycle_status, "value")
                else str(c.lifecycle_status)
            )
            # ★ 明确告知模型数据来源类型：测试数据不能当真实客户情况写
            data["数据来源类型"] = (
                c.source_type.value if hasattr(c.source_type, "value") else str(c.source_type)
            )

    if proposal.opportunity_id:
        o = db.get(Opportunity, proposal.opportunity_id)
        if o is not None:
            data["商机名称"] = o.title
            data["商机阶段"] = o.stage.value if hasattr(o.stage, "value") else str(o.stage)
            if o.amount is not None:
                data["商机金额"] = f"{o.amount} {o.currency or ''}".strip()

    return data


def generate(
    db: Session,
    proposal: Proposal,
    *,
    doc_type: KnowledgeDocType | None = None,
    top_k: int = 5,
) -> GenerateOutcome:
    """生成方案草稿。

    ★ 输入 = 知识库检索命中片段 + 真实业务数据 + 人工填的需求背景。
      三者分开标注进入提示词；生成后把输入**冻结**进 inputs_json。
    """
    outcome = GenerateOutcome()

    # 检索词：优先用人工填的需求；没有就用方案标题
    query = (proposal.requirement or proposal.title or "").strip()
    outcome.hits = search(db, query, doc_type=doc_type, top_k=top_k)
    outcome.business_data = _customer_block(db, proposal)

    knowledge_block = "\n".join(
        f"[资料{i + 1}｜{h.document_title}] {h.content}" for i, h in enumerate(outcome.hits)
    ) or "（知识库没有命中任何资料 —— 涉及产品的内容必须说明资料不足，不要编）"
    business_block = (
        "\n".join(f"- {k}: {v}" for k, v in outcome.business_data.items())
        or "（没有关联客户/商机数据）"
    )

    prompt = (
        f"方案标题：{proposal.title}\n"
        f"方案类型：{proposal.kind.value}\n\n"
        f"=== A. 产品/服务资料（唯一可引用的产品信息来源）===\n{knowledge_block}\n\n"
        f"=== B. 客户情况（真实业务数据）===\n{business_block}\n\n"
        f"=== C. 客户需求（业务人员填写，不许添加它没提到的）===\n"
        f"{proposal.requirement or '（未填写需求背景）'}"
    )

    try:
        raw = generate_reply(prompt, system_prompt=PROPOSAL_SYSTEM_PROMPT)
        outcome.llm_called = True
    except LLMUnavailable as exc:
        outcome.llm_called = True
        outcome.llm_error = exc.reason
        proposal.llm_called = True
        proposal.llm_error = exc.reason[:500]
        db.commit()
        return outcome

    data = _extract_json(raw)
    outcome.sections = [
        {
            "heading": str(s.get("heading") or "").strip(),
            "body": str(s.get("body") or "").strip(),
            "basis": str(s.get("basis") or "").strip(),
        }
        for s in (data.get("sections") or [])
        if isinstance(s, dict) and str(s.get("body") or "").strip()
    ]
    outcome.missing_info = [str(x) for x in (data.get("missing_info") or [])]

    if not outcome.sections:
        raise ProposalError("模型没有产出可用方案，请重试或补充需求背景")

    proposal.content_json = outcome.sections
    # ★ 冻结输入（§十九 可追溯：事后能查这份方案当初依据什么）
    proposal.inputs_json = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_hits": [
            {
                "evidence_ref": h.evidence_ref,
                "document_title": h.document_title,
                "seq": h.seq,
                "content": h.content[:300],
                "score": h.score,
            }
            for h in outcome.hits
        ],
        "business_data": outcome.business_data,
        "requirement": proposal.requirement,
        "missing_info": outcome.missing_info,
    }
    # ★ 只能到 GENERATED：等人工确认，AI 不许自己批准自己
    proposal.status = ProposalStatus.GENERATED
    proposal.llm_called = True
    proposal.llm_error = None
    db.commit()
    db.refresh(proposal)
    return outcome


def _extract_json(text: str) -> dict:
    """从模型输出取 JSON（容错 ```json 包裹与前后杂话）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProposalError(f"模型输出不是合法 JSON：{exc}") from exc
    raise ProposalError("模型输出里找不到 JSON")


# ══════════════════════════════════════════════════════════════════════
# CRUD 与人工确认
# ══════════════════════════════════════════════════════════════════════

def create_proposal(
    db: Session,
    *,
    title: str,
    kind: ProposalKind,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    target_name: str | None = None,
    requirement: str | None = None,
) -> Proposal:
    """新建方案（还只是空壳，内容要生成）。"""
    if customer_id is not None and db.get(Customer, customer_id) is None:
        raise ProposalError(f"客户 {customer_id} 不存在")
    if opportunity_id is not None and db.get(Opportunity, opportunity_id) is None:
        raise ProposalError(f"商机 {opportunity_id} 不存在")
    if customer_id is None and opportunity_id is None and not (target_name or "").strip():
        raise ProposalError("至少要指定一个对象：客户、商机，或填写对象名称")

    row = Proposal(
        title=title.strip(),
        kind=kind,
        status=ProposalStatus.NONE,
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        target_name=(target_name or "").strip() or None,
        requirement=(requirement or "").strip() or None,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_proposal(db: Session, proposal_id: int) -> Proposal | None:
    row = db.get(Proposal, proposal_id)
    if row is None or not row.is_active:
        return None
    return row


def list_proposals(
    db: Session,
    *,
    status: ProposalStatus | None = None,
    customer_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Proposal], int]:
    conditions = [Proposal.is_active.is_(True)]
    if status is not None:
        conditions.append(Proposal.status == status)
    if customer_id is not None:
        conditions.append(Proposal.customer_id == customer_id)

    base = select(Proposal)
    counter = select(func.count(Proposal.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(Proposal.created_at.desc(), Proposal.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def update_proposal(
    db: Session,
    row: Proposal,
    *,
    title: str | None = None,
    requirement: str | None = None,
    kind: ProposalKind | None = None,
) -> Proposal:
    """改标题/需求/类型。

    ★ 改了需求背景会把已生成的方案退回 NONE —— 因为内容是基于旧需求生成的，
      留着旧的 CONFIRMED/GENERATED 状态会误导（同 D40 改凭据退回未测试的道理）。
    """
    if title is not None:
        row.title = title.strip()
    if kind is not None:
        row.kind = kind
    if requirement is not None:
        new_req = requirement.strip() or None
        if new_req != row.requirement:
            row.requirement = new_req
            row.status = ProposalStatus.NONE
            row.content_json = None
            row.inputs_json = None
            row.confirmed_by = None
            row.confirmed_at = None
            row.reject_reason = None
    db.commit()
    db.refresh(row)
    return row


def confirm_proposal(db: Session, row: Proposal, *, confirmed_by: str) -> Proposal:
    """★ 人工确认方案可用（§十六：AI建议 ≠ 自动发送，本操作不发送任何内容）。"""
    if row.status != ProposalStatus.GENERATED:
        raise ProposalError(
            f"当前状态是 {row.status.value}，只有 GENERATED 状态的方案才能被确认"
        )
    row.status = ProposalStatus.CONFIRMED
    row.confirmed_by = confirmed_by
    row.confirmed_at = datetime.now(timezone.utc)
    row.reject_reason = None
    db.commit()
    db.refresh(row)
    return row


def reject_proposal(
    db: Session, row: Proposal, *, confirmed_by: str, reason: str
) -> Proposal:
    """人工驳回。"""
    if row.status != ProposalStatus.GENERATED:
        raise ProposalError(
            f"当前状态是 {row.status.value}，只有 GENERATED 状态的方案才能被驳回"
        )
    row.status = ProposalStatus.REJECTED
    row.confirmed_by = confirmed_by
    row.confirmed_at = datetime.now(timezone.utc)
    row.reject_reason = reason[:500]
    db.commit()
    db.refresh(row)
    return row


def stage_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Proposal.status, func.count(Proposal.id))
        .where(Proposal.is_active.is_(True))
        .group_by(Proposal.status)
    ).all()
    counts = {s.value: 0 for s in ProposalStatus}
    for st, n in rows:
        counts[st.value if hasattr(st, "value") else str(st)] = int(n)
    return counts


__all__ = [
    "PROPOSAL_SYSTEM_PROMPT",
    "GenerateOutcome",
    "ProposalError",
    "confirm_proposal",
    "create_proposal",
    "generate",
    "get_proposal",
    "list_proposals",
    "reject_proposal",
    "stage_counts",
    "update_proposal",
]
