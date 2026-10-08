"""线索研究服务（TASK-025，需求 §十七 获客与营销）。

═══════════════════════════════════════════════════════════════════════
【本模块的核心：把"公开事实"与"AI 推断"做成**结构上分离**，而不是靠措辞】
═══════════════════════════════════════════════════════════════════════
§十七 要求："AI 必须区分 公开事实 / AI推断"，并举例：
    可以说：「该公司近期大量招聘 AI 算法岗位，因此可能存在算力需求。」
    不能说：「该公司确定需要 100 张 GPU。」

靠"提示词让它注意"是不可靠的，所以这里用三道**结构性**约束：
    ① 输出格式强制分两段：{"facts": [...], "inferences": [...]}
    ② **每条 fact 必须带 quote**，且 quote 必须是 source_text 的**逐字子串**；
       服务端会**逐条回验**，验不过的事实被剔除并记录（不给用户看未经验证的"事实"）。
    ③ inference 必须带 basis（推断依据），前端与接口都把两段分开返回，
       **不会**混成一个列表让用户分不清哪句是事实。

★ 这是"防止幻觉"最实在的做法：不信任模型的自律，而是**验证它给的引用**。

═══════════════════════════════════════════════════════════════════════
【不联网抓取】
═══════════════════════════════════════════════════════════════════════
用户裁定 A 方案：资料由**用户提供原文**（粘贴/导入）。source_url 只作记录，
系统不会去访问它。这样满足 §二"经授权的公开互联网数据"与 §二十六"禁止编造"。

═══════════════════════════════════════════════════════════════════════
【人工确认是硬闸门】
═══════════════════════════════════════════════════════════════════════
生成的话术只能到 GENERATED 状态；`confirm` / `reject` 是**人工接口**，
且 §十六 明确"AI建议 ≠ 自动发送"——本模块**没有任何发送能力**，
确认只是标记"这条话术可用"，触达由人工自己去做。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.prospect_research import (
    DraftStatus,
    ProspectResearch,
    ResearchSourceType,
    ResearchTargetType,
)
from app.services.llm import LLMUnavailable, generate_reply

#: 事实抽取的 system prompt。
#: ★ 写成"规则清单"而不是"你是一个专家"—— 前者可执行，后者是气氛。
EXTRACT_SYSTEM_PROMPT = """你是资料整理助手。你的唯一任务是把用户提供的原文拆成两类内容。

【铁律】
1. 只允许使用原文中出现的信息，**绝不补充**原文没有的内容。
2. 每条「公开事实」必须给出一段 **原文里的逐字片段** 作为 quote。
   ★ quote 必须是原文中真实存在的连续文字，一个字都不能改、不能概括、不能翻译。
   ★ 如果某条信息你无法从原文里摘出一段逐字原文，就不要把它列为事实。
3. 原文没提到的内容，就不要写。宁可少写，不许编。
4. 「AI 推断」可以写，但必须：
   · 明确是推断（用"可能存在""或许""看起来"这类措辞）；
   · 给出 basis（依据哪条事实推出来的）；
   · **不许出现精确数字**（如"需要 100 张 GPU""87% 概率"）——
     没有依据的精确数字一律不许写。
5. 不要下商业结论（如"该公司一定会采购"），那属于人工判断。

【输出格式】只输出 JSON，不要任何解释文字：
{
  "facts": [
    {"statement": "事实陈述", "quote": "原文逐字片段"}
  ],
  "inferences": [
    {"statement": "推断陈述", "basis": "依据（引用上面的某条事实）"}
  ],
  "summary": "一段话总结，只陈述原文里有的信息"
}"""

#: 话术生成的 system prompt。§十三 要求"像人、不能很AI"。
DRAFT_SYSTEM_PROMPT = """你是业务人员的写作助手，帮他把「公开事实」写成可用于触达客户的开场话术。

【铁律】
1. 只能使用给出的「公开事实」。**绝不添加**事实里没有的信息。
2. 「AI 推断」只能出现在内部备注里，**不许写进给客户看的话术正文** ——
   话术里不能出现"我们认为你们可能有…需求"这种基于推断的断言。
3. 风格要求（§十三）：短、自然、有上下文、不啰嗦、不强行销售、不装懂；
   ★ 不要写"您好，感谢您的咨询，很高兴为您服务"这类机械套话；
   ★ 不要每次都重新介绍公司；不要写成一篇小作文（每条不超过 120 字）。
4. **不许给任何承诺**：不写价格、折扣、交期、账期、合同条款。
5. 不要假装已经了解对方没公开的信息。

【输出格式】只输出 JSON：
{
  "drafts": [
    {"channel": "渠道，如 邮件/微信/电话开场", "content": "话术正文", "note": "内部备注：这条是基于什么写的"}
  ]
}
给 2~3 条不同角度的候选。"""


class ResearchError(Exception):
    """研究相关业务错误（路由层翻译成 400）。"""


@dataclass
class ExtractOutcome:
    facts: list[dict] = field(default_factory=list)
    inferences: list[dict] = field(default_factory=list)
    summary: str | None = None
    llm_called: bool = False
    llm_error: str | None = None
    rejected_quotes: list[dict] = field(default_factory=list)
    """★ 引用回验没通过、被剔除的"事实"（供排查，不展示给用户）"""


def _extract_json(text: str) -> dict:
    """从模型输出里取 JSON。模型偶尔会包 ```json 或前后加话，这里容错。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 退一步：截取第一个 { 到最后一个 }
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ResearchError(f"模型输出不是合法 JSON：{exc}") from exc
    raise ResearchError("模型输出里找不到 JSON")


def _normalize(text: str) -> str:
    """归一化用于引用比对：去掉所有空白。

    ★ 只去空白，不改变任何字符 —— 允许模型因换行/空格差异导致的不精确匹配，
      但**不允许**它改字（改字就说明不是原文）。
    """
    return re.sub(r"\s+", "", text or "")


def verify_quotes(facts: list[dict], source_text: str) -> tuple[list[dict], list[dict]]:
    """★ 逐条回验事实引用是否真在原文里。

    返回 (通过的 facts, 被剔除的 facts)。
    通过的条件：quote 去空白后是 source_text 去空白后的**子串**。
    ★ 这是本模块防幻觉的核心手段 —— 不信任模型的自律，验证它给的引用。
    """
    normalized_source = _normalize(source_text)
    ok: list[dict] = []
    rejected: list[dict] = []

    for item in facts or []:
        if not isinstance(item, dict):
            rejected.append({"statement": str(item), "quote": None, "reason": "格式不正确"})
            continue
        statement = str(item.get("statement") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not statement:
            rejected.append({**item, "reason": "缺少 statement"})
            continue
        if not quote:
            rejected.append({**item, "reason": "缺少 quote（无法验证是否来自原文）"})
            continue
        if len(_normalize(quote)) < 4:
            # 引用太短（如"的"）没有验证意义
            rejected.append({**item, "reason": "quote 过短，不足以作为引用"})
            continue
        if _normalize(quote) not in normalized_source:
            rejected.append({**item, "reason": "quote 在原文中找不到（疑似编造）"})
            continue
        ok.append({"statement": statement, "quote": quote})

    return ok, rejected


def extract_facts(
    db: Session,
    row: ProspectResearch,
    *,
    question: str | None = None,
) -> ExtractOutcome:
    """从 source_text 抽取事实与推断，并**回验引用**。"""
    prompt = (
        f"研究对象：{row.target_name}\n"
        f"资料类型：{row.source_type.value}\n"
    )
    if question:
        prompt += f"关注点：{question}\n"
    prompt += f"\n=== 原文开始 ===\n{row.source_text}\n=== 原文结束 ==="

    outcome = ExtractOutcome()
    try:
        raw = generate_reply(prompt, system_prompt=EXTRACT_SYSTEM_PROMPT)
        outcome.llm_called = True
    except LLMUnavailable as exc:
        outcome.llm_called = True
        outcome.llm_error = exc.reason
        row.llm_called = True
        row.llm_error = exc.reason[:500]
        db.commit()
        return outcome

    data = _extract_json(raw)
    passed, rejected = verify_quotes(data.get("facts") or [], row.source_text)

    outcome.facts = passed
    outcome.rejected_quotes = rejected
    # 推断段做**形状校验**，但不做"引用原文"要求（推断本来就不在原文里）
    outcome.inferences = [
        {
            "statement": str(i.get("statement") or "").strip(),
            "basis": str(i.get("basis") or "").strip(),
        }
        for i in (data.get("inferences") or [])
        if isinstance(i, dict) and str(i.get("statement") or "").strip()
    ]
    outcome.summary = str(data.get("summary") or "").strip() or None

    row.facts_json = outcome.facts
    row.inferences_json = outcome.inferences
    row.summary = outcome.summary
    row.llm_called = True
    row.llm_error = None
    db.commit()
    db.refresh(row)
    return outcome


def generate_drafts(db: Session, row: ProspectResearch) -> tuple[list[dict], str | None]:
    """基于**已通过回验的事实**生成话术候选。返回 (drafts, llm_error)。"""
    facts = row.facts_json or []
    if not facts:
        raise ResearchError("还没有通过验证的公开事实，无法生成话术（先做资料抽取）")

    facts_block = "\n".join(
        f"- {f.get('statement')}（原文：{str(f.get('quote'))[:80]}）" for f in facts
    )
    inferences_block = "\n".join(
        f"- {i.get('statement')}（依据：{i.get('basis')}）" for i in (row.inferences_json or [])
    ) or "（无）"

    prompt = (
        f"研究对象：{row.target_name}\n\n"
        f"【公开事实】（只能使用这些）\n{facts_block}\n\n"
        f"【AI 推断】（仅供你理解背景，**不许写进话术正文**）\n{inferences_block}"
    )

    try:
        raw = generate_reply(prompt, system_prompt=DRAFT_SYSTEM_PROMPT)
    except LLMUnavailable as exc:
        row.llm_error = exc.reason[:500]
        db.commit()
        return [], exc.reason

    data = _extract_json(raw)
    drafts = [
        {
            "channel": str(d.get("channel") or "").strip() or "未指定",
            "content": str(d.get("content") or "").strip(),
            "note": str(d.get("note") or "").strip(),
        }
        for d in (data.get("drafts") or [])
        if isinstance(d, dict) and str(d.get("content") or "").strip()
    ]
    if not drafts:
        raise ResearchError("模型没有产出可用话术，请重试或换一段资料")

    row.drafts_json = drafts
    # ★ 只能是 GENERATED：等人工确认，AI 不许自己批准自己
    row.draft_status = DraftStatus.GENERATED
    row.llm_error = None
    db.commit()
    db.refresh(row)
    return drafts, None


# ══════════════════════════════════════════════════════════════════════
# CRUD 与人工确认
# ══════════════════════════════════════════════════════════════════════

def create_research(
    db: Session,
    *,
    target_type: ResearchTargetType,
    target_name: str,
    source_type: ResearchSourceType,
    source_text: str,
    source_url: str | None = None,
    customer_id: int | None = None,
    focus: str | None = None,
) -> ProspectResearch:
    """新建一次研究。

    ★ 原文不能为空：没有原文就无法验证事实，整个"事实/推断分离"就没有依据。
    ★ target_type=CUSTOMER 时必须给有效的 customer_id。
    ★ TASK-042：`focus`（研究要点）是选填的**提问**，允许 AI 起草；
      它和原文（证据）分开存 —— 混在一起会把"AI 编的问题"当成"已核实的资料"。
    """
    text = (source_text or "").strip()
    if len(text) < 20:
        raise ResearchError(
            "原文太短（至少 20 字）：没有足够的原文就无法验证抽取出来的事实是否真实"
        )

    if target_type == ResearchTargetType.CUSTOMER:
        if customer_id is None or db.get(Customer, customer_id) is None:
            raise ResearchError("target_type=CUSTOMER 时必须给出存在的 customer_id")

    row = ProspectResearch(
        target_type=target_type,
        target_name=target_name.strip(),
        customer_id=customer_id,
        source_type=source_type,
        source_url=source_url,
        source_text=text,
        focus=(focus or "").strip() or None,
        draft_status=DraftStatus.NONE,
        source_type_evidence="MANUAL",  # 资料由人工提供
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_research(db: Session, research_id: int) -> ProspectResearch | None:
    row = db.get(ProspectResearch, research_id)
    if row is None or not row.is_active:
        return None
    return row


def list_research(
    db: Session,
    *,
    draft_status: DraftStatus | None = None,
    customer_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ProspectResearch], int]:
    conditions = [ProspectResearch.is_active.is_(True)]
    if draft_status is not None:
        conditions.append(ProspectResearch.draft_status == draft_status)
    if customer_id is not None:
        conditions.append(ProspectResearch.customer_id == customer_id)

    base = select(ProspectResearch)
    counter = select(func.count(ProspectResearch.id))
    for c in conditions:
        base = base.where(c)
        counter = counter.where(c)

    total = db.scalar(counter) or 0
    rows = db.scalars(
        base.order_by(ProspectResearch.created_at.desc(), ProspectResearch.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def confirm_draft(
    db: Session, row: ProspectResearch, *, confirmed_by: str, accepted_indexes: list[int]
) -> ProspectResearch:
    """★ 人工确认话术可用（§十七 的"人工确认"闸门）。

    · 只能从 GENERATED → CONFIRMED；不存在"AI 自己确认"的路径。
    · accepted_indexes 指定哪几条被采纳；未采纳的从 drafts_json 中移除，
      保证确认后的候选列表**都是人工认可的**。
    · ★ 本操作**不发送任何东西**（§十六：AI建议 ≠ 自动发送）。
    """
    if row.draft_status != DraftStatus.GENERATED:
        raise ResearchError(
            f"当前状态是 {row.draft_status.value}，只有 GENERATED 状态的话术才能被确认"
        )
    drafts = row.drafts_json or []
    if not drafts:
        raise ResearchError("没有可确认的话术")

    accepted = [drafts[i] for i in accepted_indexes if 0 <= i < len(drafts)]
    if not accepted:
        raise ResearchError("至少要采纳一条话术（或改用「驳回」）")

    row.drafts_json = accepted
    row.draft_status = DraftStatus.CONFIRMED
    row.confirmed_by = confirmed_by
    row.confirmed_at = datetime.now(timezone.utc)
    row.reject_reason = None
    db.commit()
    db.refresh(row)
    return row


def reject_draft(
    db: Session, row: ProspectResearch, *, confirmed_by: str, reason: str
) -> ProspectResearch:
    """人工驳回话术。"""
    if row.draft_status != DraftStatus.GENERATED:
        raise ResearchError(
            f"当前状态是 {row.draft_status.value}，只有 GENERATED 状态的话术才能被驳回"
        )
    row.draft_status = DraftStatus.REJECTED
    row.confirmed_by = confirmed_by
    row.confirmed_at = datetime.now(timezone.utc)
    row.reject_reason = reason[:500]
    db.commit()
    db.refresh(row)
    return row


def stage_counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(ProspectResearch.draft_status, func.count(ProspectResearch.id))
        .where(ProspectResearch.is_active.is_(True))
        .group_by(ProspectResearch.draft_status)
    ).all()
    counts = {s.value: 0 for s in DraftStatus}
    for st, n in rows:
        counts[st.value if hasattr(st, "value") else str(st)] = int(n)
    return counts


__all__ = [
    "DRAFT_SYSTEM_PROMPT",
    "EXTRACT_SYSTEM_PROMPT",
    "ExtractOutcome",
    "ResearchError",
    "confirm_draft",
    "create_research",
    "extract_facts",
    "generate_drafts",
    "get_research",
    "list_research",
    "reject_draft",
    "stage_counts",
    "verify_quotes",
]
