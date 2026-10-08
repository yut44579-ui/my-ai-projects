"""AI 辅助填写（TASK-042）。

═══════════════════════════════════════════════════════════════════════
【用户需求】
═══════════════════════════════════════════════════════════════════════
    「这些填写内容的都需要有 ai 生成的功能」（指各新建表单的文本字段）

═══════════════════════════════════════════════════════════════════════
【核心取舍：只生成「描述性文字」，不生成「事实性输入」】
═══════════════════════════════════════════════════════════════════════
    这条线必须划清，否则整个系统「不编造」的根基就没了：

    ✅ 可以生成（描述性 / 组织性文字）
       · 项目说明      —— 对已知信息的组织表述
       · 商机备注      —— 同上
       · 方案正文      —— 基于**用户写的需求**扩写（已有，见 services/proposals.py）
       · 内容摘要/正文 —— 市场文案（已有，见 services/content_gen.py）

    ❌ 不能生成（事实 / 证据 / 判断）
       · 线索研究的「公开资料原文」——
         ★★ 这一条最关键：整条链是「AI 只从**你贴的真实原文**里抽事实，
            每条引用逐字回验，原文里找不到的一律删除」。
            如果原文本身是 AI 写的，回验就变成自己验证自己编的东西，
            那份研究、以及基于它的所有结论，全部失去意义。
       · 金额 / 进度 / 概率 / 联系方式 —— 是事实或人的判断，不是文字组织
       · 客户需求 —— 是客户真实说过的话，编了就是伪造需求

    → 所以本模块的每个 purpose 都只对应「描述性文字」类字段；
      界面上给不能生成的字段**不显示按钮**（而不是显示了再拒绝），
      并在旁边说明原因 —— 用户看不到按钮就不会问"为什么这里没有"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeDocType
from app.services.knowledge import search
from app.services.llm import LLMUnavailable, generate_reply


class DraftError(Exception):
    """辅助填写相关错误（路由层翻译成 400）。"""


@dataclass
class DraftField:
    """一个可生成字段的定义。"""

    key: str
    label: str
    #: 需要上下文里的哪些字段（缺失时提示用户先填）
    needs: tuple[str, ...]
    #: 输出篇幅（字）
    length: str
    #: 写作要求（拼进提示词）
    guide: str


#: 可生成的字段登记表。
#: ★ 只登记「描述性文字」。事实性字段**不进这张表**，也就不可能有按钮。
FIELDS: dict[str, DraftField] = {
    "PROJECT_DESCRIPTION": DraftField(
        key="PROJECT_DESCRIPTION",
        label="项目说明",
        needs=("name",),
        length="80~160 字",
        guide=(
            "写一段项目说明：说清这个项目要交付什么、涉及哪一方、当前处在什么阶段。"
            "只根据给定信息写，不要编造工期、金额、技术指标、参与人数。"
            "如果某项信息没给，就**不要提它**，不要用「预计」「大约」去补一个数。"
        ),
    ),
    "OPPORTUNITY_NOTE": DraftField(
        key="OPPORTUNITY_NOTE",
        label="商机备注",
        needs=("title",),
        length="60~120 字",
        guide=(
            "写一段商机备注：说清这次机会的背景、目前在谈什么、下一步该做什么。"
            "★ 绝对不要提金额、折扣、账期、成交概率、报价 —— "
            "这些是事实数字与商务条件，必须人工填，编了会误导判断。"
            "下一步要提到商务条件时，只写「需转人工审批」。"
        ),
    ),
    "RESEARCH_FOCUS": DraftField(
        key="RESEARCH_FOCUS",
        label="研究要点",
        needs=("target_name",),
        length="60~120 字",
        guide=(
            "写一段「这次研究想弄清什么」的要点清单（3~5 条，用分号隔开）。"
            "只能提出**可以从公开资料里核实**的问题"
            "（如主营业务、产品线、公开的合作与招聘方向、公开报道过的动作）。"
            "★ 不许写任何**结论或判断**（不要写「该公司很有潜力」这类），"
            "只列要查证的问题 —— 结论必须由真实资料支撑。"
        ),
    ),
    "RISK_ACTION": DraftField(
        key="RISK_ACTION",
        label="建议动作",
        needs=("title",),
        length="50~100 字",
        guide=(
            "写一段处理建议：说清这件事该谁在什么时候做什么。"
            "★ 不许写「保证解决」「一定能」这类承诺；"
            "涉及报价/折扣/合同/退款的一律写「转人工审批」。"
        ),
    ),
    "CUSTOMER_NOTE": DraftField(
        key="CUSTOMER_NOTE",
        label="跟进记录",
        needs=(),
        length="50~100 字",
        guide=(
            "写一段跟进记录草稿。★ 这是给人改的**草稿**，"
            "不许编造客户说过的话、电话内容、会面结果 —— 没给的就不写。"
        ),
    ),
}

SYSTEM_PROMPT = """你是企业业务系统的填写助手。你只做一件事：把用户已经给出的信息，
组织成一段通顺、具体、可以直接用的中文文字。

【铁律】
1. **只使用给定的信息**。没用到的细节一律不许补 —— 不许编：
   金额、折扣、工期、技术指标、客户名称、联系人、电话、成交概率、
   客户说过的话、会谈结果。
2. **不要用模糊词填空**：不写「预计」「大约」「相关人员」「适当时机」这类凑字数的话。
   信息不足时，宁可写短，也不要注水。
3. 不要承诺效果（不写「保证」「一定能」）。
4. ★ 直接输出正文。**不要任何前缀或标题**：
   不要写「商机备注：」「项目说明：」这类标签，也不要「好的，以下是…」这类话。
   不要 markdown 记号（不要 **加粗**、# 标题、` 代码）、不要用引号把整段包起来。
5. ★ 提到报价 / 折扣 / 合同条款 / 收款账户 / 退款 / 赔偿时，
   一律写成「需转人工审批」，不要描述这些内容本身。
6. 篇幅按要求的字数区间；把话说清楚比凑够字数重要。"""


@dataclass
class DraftOutcome:
    text: str = ""
    llm_called: bool = False
    llm_error: str | None = None
    hits: list = field(default_factory=list)


def list_fields() -> list[dict]:
    """可生成字段清单（前端据此决定哪里显示按钮）。"""
    return [
        {"key": f.key, "label": f.label, "needs": list(f.needs), "length": f.length}
        for f in FIELDS.values()
    ]


def generate_draft(
    db: Session,
    *,
    purpose: str,
    context: dict,
    hint: str | None = None,
    use_knowledge: bool = False,
    doc_type: KnowledgeDocType | None = None,
    top_k: int = 3,
) -> DraftOutcome:
    """生成一段可填入表单的文字。"""
    spec = FIELDS.get(purpose)
    if spec is None:
        raise DraftError(f"不支持的填写类型：{purpose}")

    missing = [k for k in spec.needs if not str(context.get(k) or "").strip()]
    if missing:
        raise DraftError(f"请先填写：{'、'.join(missing)}")

    outcome = DraftOutcome()

    if use_knowledge:
        # 用已有信息做检索词，把知识库里的资料作为表述素材
        query = " ".join(str(context.get(k) or "") for k in ("name", "title", "target_name"))
        outcome.hits = search(db, query.strip(), doc_type=doc_type, top_k=top_k)

    ctx_lines = "\n".join(f"{k}：{v}" for k, v in context.items() if str(v or "").strip())
    ref_block = (
        "\n".join(f"[资料{i + 1}｜{h.document_title}] {h.content}" for i, h in enumerate(outcome.hits))
        if outcome.hits
        else "（未提供参考资料，只根据上面的信息写）"
    )

    prompt = (
        f"要写的内容：{spec.label}\n"
        f"篇幅要求：{spec.length}\n"
        f"写作要求：{spec.guide}\n\n"
        f"=== 已知信息（只能用这些）===\n{ctx_lines}\n"
    )
    if hint:
        prompt += f"\n用户补充说明：{hint}\n"
    prompt += f"\n=== 参考资料（可用于表述，不得从中编造数字）===\n{ref_block}"

    try:
        outcome.text = generate_reply(prompt, system_prompt=SYSTEM_PROMPT).strip()
        outcome.llm_called = True
    except LLMUnavailable as exc:
        outcome.llm_called = True
        outcome.llm_error = exc.reason

    return outcome


__all__ = [
    "FIELDS",
    "SYSTEM_PROMPT",
    "DraftError",
    "DraftField",
    "DraftOutcome",
    "generate_draft",
    "list_fields",
]
