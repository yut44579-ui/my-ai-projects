"""首页 AI 助手问答（Copilot）。

═══════════════════════════════════════════════════════════════════════
【与 POST /customers/{id}/ai-reply 的区别（别混用）】
═══════════════════════════════════════════════════════════════════════
    /customers/{id}/ai-reply   ── 给**某一个客户**起草回复：
                                  闸门 → LLM → **落库成客户沟通记录**
    本模块 /dashboard/ai-ask   ── 首页 Copilot **提问**（没有客户上下文）：
                                  闸门 → 查真实事实 → LLM 组织语言
                                  **不写任何客户沟通记录**（它不是客户对话）

═══════════════════════════════════════════════════════════════════════
【三类边界（用户要求「聪明一点，但要有界限」）】
═══════════════════════════════════════════════════════════════════════
    ① 能答      客户/待办/来源/动态/数据连接/汇报/某个具体客户
                → 查**确定性事实**，交给模型组织成人话
    ② 礼貌拒绝  超出系统数据范围（竞对情报、库外数据、改写客户消息、系统外事实）
                → 明确说"这不在这套系统的数据范围内"，并**给出能问什么**
    ③ 硬拦      折扣/报价/合同/收款账户/退款/赔偿
                → PolicyGate 在**调模型之前**拦下，不给任何承诺

★ 界限是"事实边界"而不是"话术边界"：
  模型可以自由组织语言，但**数字只能来自代码查到的事实**；
  越界问题不是不能聊，而是**不能说没有依据的结论**。

═══════════════════════════════════════════════════════════════════════
【为什么模糊问题不调 LLM 分类，而是交给同一次调用】
═══════════════════════════════════════════════════════════════════════
  多一次 LLM 分类 = 多一处幻觉来源 + 多一次延迟。所以：
  关键词命中 → 直接查对应事实；
  关键词没命中 → 把「系统能提供什么数据」写进提示词，让**同一次**调用自己裁定
  （能答就答，超出范围就按②礼貌拒绝）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.routes.dashboard import (
    _categorize,
    _connections,
    _recent_activities,
    _region_distribution,
    _source_breakdown,
    _today_report,
    _week_start,
)
from app.db.session import get_db
from app.models.customer import Customer
from app.schemas.dashboard import AiAskKind, AiAskResponse, AiFact
from app.schemas.evidence import EvidenceValue, SourceType, ValueState
from app.services.capabilities import capability_text
from app.services.llm import LLMUnavailable, generate_reply
from app.services.policy_gate import evaluate_policy

router = APIRouter(prefix="/dashboard", tags=["dashboard-ai"])

#: 首页 AI 助手的人设。
#: ★ 与「客户沟通助理」人设刻意分开：这里没有"同事"可以转交，也不能给业务承诺。
COPILOT_SYSTEM_PROMPT = (
    "你是企业工作台里的数据分析助手，负责把系统查到的事实讲给业务人员听。要求：\n"
    "1) 用中文，简洁直接（1~3 句），像同事回话一样自然，不要客服腔；\n"
    "2) 只陈述下面给出的事实，绝不编造数字、绝不推测、绝不给百分比或概率；\n"
    "3) 绝不给业务承诺（价格、折扣、交期、赔偿）；\n"
    "4) 不要写“请同事确认”“转交相关人员”这类话 —— 你就是系统本身；\n"
    "5) 不要在结尾追问“还需要我做什么”。"
)

#: 模糊问题兜底人设：把「系统能提供什么」告诉模型，让它自己裁定能不能答。
#: ★ 这段是**边界声明**，不是话术模板 —— 模型据此判断"越界"并如实说明。
COPILOT_FALLBACK_PROMPT = (
    COPILOT_SYSTEM_PROMPT
    + "\n\n【本系统能提供的数据范围（超出即无法回答）】\n"
    "  · 客户：总数、新增、地区分布、来源构成、某个客户的资料与状态\n"
    "  · 待办：今天需要人工处理的客户（分四类：需人工/接管中/AI失败/待裁决/新客户）\n"
    "  · 风险：待处理风险数与明细（只来自命中策略闸门、AI 回复失败两条依据）\n"
    "  · 访问：今天的客户访问次数、有访问的客户数、客户最常看的页面\n"
    "  · 动态：客户事件流（状态变更、沟通记录、人工接管、客户访问），**只有系统内真实事件**\n"
    "  · 汇报：已生成的汇报快照指标\n"
    "  · 数据连接：各渠道是否已接入\n"
    "【明确无法回答的】\n"
    "  · 竞争对手情报、市场份额、胜率等——系统里没有这些数据\n"
    "  · 库外的任何事实（新闻、股价、客户私下情况等）\n"
    "  · 替你写发给客户的话术/邮件（那要在客户详情页做，且要人工确认）\n"
    "遇到这类问题：直接说清“这不在系统的数据范围内”，并告诉他可以问什么。"
    "**不要编造，也不要硬答。**"
)

#: 关键词 → 问题类型。★ 顺序即优先级，**窄意图必须排在宽泛词前面**：
#: 「今天有哪些客户需要我处理？」同时命中「客户」和「需要我处理」，
#: 若 CUSTOMERS 在前就会被误判成「查客户数量」——实测踩过这个 bug。
KEYWORDS: list[tuple[AiAskKind, tuple[str, ...]]] = [
    (AiAskKind.CAPABILITY, ("你能做什么", "你能干什么", "你会什么", "能帮我做什么", "有什么功能", "怎么用")),
    (AiAskKind.GREETING, ("你好", "您好", "hi", "hello", "在吗", "早上好", "下午好", "晚上好")),
    # ★ VISITS 必须排在 ACTIVITY 前面：「客户最近访问了什么」同时命中「最近」，
    #   若 ACTIVITY 在前会被抢走，答成泛泛的动态列表。
    (AiAskKind.VISITS, ("访问", "看了什么", "看了哪些", "浏览", "看了什么页面", "访问记录")),
    (AiAskKind.RISK, ("风险", "敏感", "异常", "有没有问题", "需要关注", "高风险", "违规")),
    (AiAskKind.PENDING, ("待处理", "要处理", "需要我处理", "需要人工", "待办", "跟进", "今天要", "有什么要")),
    (AiAskKind.REPORT, ("汇报", "报告", "老板", "总结", "周报", "日报")),
    # ★ TASK-041：商机/项目本来就在系统里（报告里也有），但此前**问不到** ——
    #   实测「现在有几个商机？」答成"不在能查的范围内"，而数据其实有 3 个。
    #   这是真实缺口，补上。★ 必须排在 CUSTOMERS 前面：
    #   「商机」不含「客户」二字，但「客户商机」这类问法会同时命中两边。
    (AiAskKind.OPPORTUNITY, ("商机", "项目", "订单", "赢单", "丢单", "成交", "签约", "进度")),
    (AiAskKind.SOURCES, ("来源", "渠道", "从哪来", "获客")),
    (AiAskKind.DATA, ("数据连接", "同步", "接入", "连接状态", "数据是否", "数据正常", "数据质量")),
    (AiAskKind.ACTIVITY, ("动态", "发生", "最近", "事件", "沟通记录")),
    (AiAskKind.CUSTOMERS, ("客户", "新增", "总数", "分布", "地区", "区域")),
]

#: 明确越界的信号词（命中就直接礼貌拒绝，不查库、不调模型）
OUT_OF_SCOPE_HINTS = (
    "竞争对手", "竞品", "市场份额", "胜率", "市占率", "行业排名",
    "股价", "新闻", "天气", "汇率",
    "帮我写", "写一封", "写个邮件", "话术", "发给他", "发给客户",
)

#: 「查某个具体客户」的口语句式线索。
#: ★ 为什么需要它：客户姓名不像关键词，没法穷举。
#:   先用这些句式判断"你是在问某个人"，再由 _find_customer 从库里找出是谁。
CUSTOMER_DETAIL_HINTS = (
    "什么情况", "是谁", "哪家", "哪家公司", "的资料", "联系方式",
    "的电话", "的邮箱", "状态如何", "怎么样了", "跟踪到哪", "他什么",
    "她什么", "这个客户", "那个客户", "的档案", "的进展",
)


class AiAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500, description="用户原话")
    #: 上一轮话题的客户 id（支持"他什么情况"这类追问）。★ 由前端透传，后端不猜。
    context_customer_id: int | None = Field(default=None)


def _classify(question: str) -> AiAskKind | None:
    """关键词分类。返回 None = 没命中，交给 LLM 兜底裁定。"""
    q = question.lower()
    if any(h in q for h in OUT_OF_SCOPE_HINTS):
        return AiAskKind.OUT_OF_SCOPE
    # ★ 「查某个具体客户」要排在前面：这类问句常带「客户」二字
    #   （"张伟这个客户什么情况"），若不优先会被 CUSTOMERS 抢走。
    if any(h in q for h in CUSTOMER_DETAIL_HINTS):
        return AiAskKind.CUSTOMER_DETAIL
    for kind, words in KEYWORDS:
        if any(w in q for w in words):
            return kind
    return None


def _ev(value: int | None, ref: str, reason: str | None = None) -> EvidenceValue:
    """构造一条事实的 EvidenceValue。

    ★ 统一入口：之前各处手写 dict，类型不一致（evidence 声明是 dict，
      但 visits_summary 返回的是 EvidenceValue 对象）→ 运行时 500。
      收敛到这里后，类型由 EvidenceValue 强制，不会再混。

    D4 三态：有值 → VALID（0 也是 VALID）；无值 → NO_DATA 且 value=None。
    """
    return EvidenceValue(
        value=value,
        state=ValueState.VALID if value is not None else ValueState.NO_DATA,
        source_type=SourceType.SYSTEM,
        evidence_ref=ref,
        reason=reason,
    )


def _note_ev(reason: str, ref: str) -> EvidenceValue:
    """只承载文字说明的 EvidenceValue（value=None，文字放 reason）。"""
    return _ev(None, ref, reason)


def _find_customer(db: Session, question: str, context_id: int | None) -> Customer | None:
    """在问题里找人名/公司名；也支持用上一轮的客户 id 追问。

    ★ 关键：用**库内客户名去问题里找**，而不是把整句问题当姓名去匹配。
      之前写成 `Customer.name.like('%' + question + '%')`，
      「林芳什么情况？」会去匹配姓名列里的"林芳什么情况？"——永远匹配不上。
    顺序：
      1) 有上下文 id 且问题里没提到别的客户 → 沿用上一轮（支持"他什么情况"）
      2) 问题里**包含**某个客户姓名 / 公司名 / 电话 / 邮箱 → 命中该客户
      3) 否则 None
    """
    q = question.strip()
    if not q:
        return db.get(Customer, context_id) if context_id is not None else None

    # 2) 用库内客户名/公司名/电话/邮箱去问题里找（只查必要的列，不做全表两遍）
    rows = db.execute(
        select(Customer.id, Customer.name, Customer.company_name, Customer.phone, Customer.email)
    ).all()
    for cid, name, company, phone, email in rows:
        for candidate in (phone, email):  # 电话/邮箱较长且唯一，优先精确包含
            if candidate and candidate in q:
                return db.get(Customer, cid)
        for candidate in (name, company):
            if candidate and len(candidate) >= 2 and candidate in q:
                return db.get(Customer, cid)

    # 1) 没提到具体客户但有上下文 → 沿用上一轮（"他什么情况"）
    if context_id is not None:
        return db.get(Customer, context_id)
    return None


#: 生命周期 / 接管状态的中文展示名。
#: ★ 本模块自己定义而不是从别处 import：避免跨模块 import 造成循环依赖，
#:   也避免以后别处改了文案把这里的表述悄悄带跑。
LIFECYCLE_LABELS: dict[str, str] = {
    "NEW": "未联系",
    "CONTACTED": "已联系",
    "REPLIED": "已回复",
    "ENGAGED": "持续沟通",
    "QUOTED": "已报价",
    "WON": "已成交",
    "LOST": "已失效",
}

HANDOVER_LABELS: dict[str, str] = {
    "AUTO": "AI 自动回复",
    "HUMAN_REQUIRED": "需人工处理",
    "HUMAN_ACTIVE": "人工接管中",
}


def _enum_key(value) -> str:
    """把枚举/字符串统一成 key（用于查上面的标签表）。"""
    return value.value if hasattr(value, "value") else str(value)


def _customer_facts(c: Customer) -> list[AiFact]:
    """某个客户的确定性事实。★ 全部来自 customers 表真实字段。

    ★ 这些字段是**文字/枚举**，不是"业务数字"，所以用 _note_ev（value=None、
      内容放 reason），不把"已联系"这种状态伪装成数值塞进 value。
    """
    lifecycle = _enum_key(c.lifecycle_status)
    handover = _enum_key(c.handover_state)

    rows: list[tuple[str, str, str]] = [
        ("客户姓名", c.name, "name"),
        ("公司", c.company_name or "未填写", "company_name"),
        ("生命周期状态", LIFECYCLE_LABELS.get(lifecycle, lifecycle), "lifecycle_status"),
        ("接管状态", HANDOVER_LABELS.get(handover, handover), "handover_state"),
        ("客户来源", _enum_key(c.source_type), "source_type"),
    ]
    if c.region:
        rows.append(("地区", c.region, "region"))

    facts: list[AiFact] = []
    for label, text, field in rows:
        facts.append(
            AiFact(
                label=label,
                evidence=_note_ev(text, f"customer:{c.id}:{field}"),
                source_note=f"customers.{field}",
            )
        )
    return facts


def _collect_facts(
    db: Session, kind: AiAskKind, question: str, context_id: int | None
) -> tuple[list[AiFact], str, int | None]:
    """按问题类型收集**确定性事实**。

    返回 (facts, 数据边界说明, 本轮话题客户 id)。
    """
    now = datetime.now(timezone.utc)
    week_start = _week_start(now)
    facts: list[AiFact] = []
    note = ""
    topic_customer: int | None = None

    if kind == AiAskKind.CUSTOMER_DETAIL:
        found = _find_customer(db, question, context_id)
        if found is None:
            return [], "系统里没有匹配到这个客户（按姓名/公司/电话/邮箱都查过了）。", None
        topic_customer = found.id
        facts = _customer_facts(found)
        note = f"这些字段来自客户档案（customer:{found.id}）。"

    elif kind == AiAskKind.PENDING:
        from app.schemas.dashboard import TASK_REASON_LABELS, TaskReason

        buckets = _categorize(db, week_start)
        total = sum(len(v) for v in buckets.values())
        facts.append(
            AiFact(
                label="今日需要人工处理的客户数",
                evidence=_ev(
                    total,
                    "dashboard:tasks",
                    None if total else "没有需要人工处理的客户",
                ),
                source_note="按 handover_state / AI 失败 / 去重待裁决 / 本周新增 四类条件统计",
            )
        )
        if total:
            detail = "、".join(
                f"{TASK_REASON_LABELS[r]} {len(buckets[r])} 个" for r in TaskReason if buckets[r]
            )
            facts.append(
                AiFact(
                    label="分类明细",
                    evidence=_note_ev(detail, "dashboard:tasks:counts"),
                    source_note=detail,
                )
            )
        note = "这些数字来自当前库内真实客户数据。"

    elif kind == AiAskKind.CUSTOMERS:
        total = db.scalar(select(func.count(Customer.id))) or 0
        new_week = (
            db.scalar(select(func.count(Customer.id)).where(Customer.first_seen_at >= week_start)) or 0
        )
        facts.append(AiFact(
            label="客户总数",
            evidence=_ev(total, "customers:count", None if total else "还没有客户数据"),
            source_note="customers 表全量计数",
        ))
        facts.append(AiFact(
            label="本周新增",
            evidence=_ev(
                new_week if total else None,
                "customers:count|first_seen_at>=week_start",
                None if total else "还没有客户数据",
            ),
            source_note="first_seen_at 在本周一 00:00 之后",
        ))

        region = _region_distribution(db)
        if region.slices:
            top = "、".join(f"{s.region} {s.count}" for s in region.slices[:5])
            facts.append(AiFact(
                label="地区分布（前 5）",
                evidence=_note_ev(top, "customers:region:distribution"),
                source_note=top,
            ))
        note = "客户指标来自 customers 表真实数据，不含推测。"

    elif kind == AiAskKind.SOURCES:
        sb = _source_breakdown(db)
        if sb.slices:
            detail = "、".join(f"{s.label} {s.count}" for s in sb.slices)
            facts.append(AiFact(
                label="客户来源构成",
                evidence=_note_ev(detail, "customers:source:breakdown"),
                source_note=detail,
            ))
        note = "来源按 customers.source_type 统计（REAL/TEST/MANUAL）。"

    elif kind == AiAskKind.REPORT:
        rd = _today_report(db)
        if rd.available:
            for m in rd.metrics:
                # rd.metrics 里已经是 EvidenceValue，直接透传（强类型，不再转 dict）
                facts.append(AiFact(label=m.label, evidence=m.evidence,
                                    source_note=f"报告 #{rd.report_id} 快照"))
            note = rd.note
        else:
            note = rd.note

    elif kind == AiAskKind.ACTIVITY:
        ra = _recent_activities(db)
        if ra.items:
            detail = "；".join(f"{a.customer_name} {a.title}" for a in ra.items[:5])
            facts.append(AiFact(
                label=f"最近 {len(ra.items)} 条客户动态",
                evidence=_note_ev(detail, "customer_events:count"),
                source_note=detail,
            ))
        note = "动态只来自 customer_events 真实事件表。"

    elif kind == AiAskKind.DATA:
        dc = _connections(db)
        detail = "、".join(f"{i.label}{'已连接' if i.connected else '未连接'}" for i in dc.items)
        facts.append(AiFact(
            label="数据连接状态",
            evidence=EvidenceValue(
                value=dc.connected_count.value,
                state=dc.connected_count.state,
                source_type=dc.connected_count.source_type,
                evidence_ref="dashboard:data_connections",
                reason=detail,
            ),
            source_note=detail,
        ))
        note = "V1 只有 Excel/CSV 文件导入这一条真实通路。"

    elif kind == AiAskKind.OPPORTUNITY:
        # ★ TASK-041：商机与项目本来就有数据（报告里也在用），此前却问不到。
        #   ★ 复用 services 里的 list_*，与商机/项目页**同源**，
        #     绝不在这里另算一套 —— 否则同一个问题在问答与页面里会给出两个数字。
        from app.services.opportunity import list_opportunities
        from app.services.project import list_projects

        # only_open=True 就是"未终结的商机"，不用自己维护一套阶段集合
        opp_rows, opp_total = list_opportunities(db, limit=200)
        open_rows, open_total = list_opportunities(db, only_open=True, limit=200)

        facts.append(AiFact(
            label="进行中的商机数",
            evidence=_ev(
                open_total, "opportunities:open",
                "没有进行中的商机" if not open_total else None,
            ),
            source_note="opportunities 表未终结阶段的计数（与商机管理页同源）",
        ))

        if opp_total:
            won = sum(1 for o in opp_rows if str(getattr(o.stage, "value", o.stage)) == "WON")
            lost = sum(
                1 for o in opp_rows if str(getattr(o.stage, "value", o.stage)) == "CLOSED_LOST"
            )
            # ★ 拆成两条数值事实，不拼成 "0 / 0" 字符串 ——
            #   EvidenceValue.value 只接受数字，塞字符串会被 Pydantic 拒掉（实测 500）。
            #   而且拆开后每个数字都能单独点开看证据，比一个合并字段更有用。
            facts.append(AiFact(
                label="赢单商机数",
                evidence=_ev(won, "opportunities:won", "还没有赢单" if not won else None),
                source_note="opportunities 表 stage=WON",
            ))
            facts.append(AiFact(
                label="丢单商机数",
                evidence=_ev(lost, "opportunities:lost", "还没有丢单" if not lost else None),
                source_note="opportunities 表 stage=CLOSED_LOST",
            ))
            # ★ 金额只在**确实填了**的时候给出；没填就如实说"还没填"，
            #   不能拿 0 冒充"金额为零"（§二）。
            #   ★ `o.amount` 是 EvidenceValue（D4 契约：业务数字都是证据值），
            #     取 `.value` 才是数字；直接相加会 TypeError（实测踩到）。
            amounts = [o.amount.value for o in open_rows if o.amount and o.amount.value is not None]
            facts.append(AiFact(
                label="进行中商机的已知金额合计",
                evidence=_ev(
                    float(sum(amounts)) if amounts else None,
                    "opportunities:amount_sum",
                    "进行中的商机都还没填金额" if not amounts else None,
                ),
                source_note="只统计已填金额的商机；未填的不计入，也不按 0 处理",
            ))

        proj_rows, proj_total = list_projects(db, limit=200)
        active_rows, active_total = list_projects(db, only_active=True, limit=200)
        facts.append(AiFact(
            label="进行中的项目数",
            evidence=_ev(
                active_total, "projects:active",
                "没有进行中的项目" if not active_total else None,
            ),
            source_note="projects 表进行中的计数（与项目管理页同源）",
        ))

    elif kind == AiAskKind.RISK:
        # 复用 services/risk.py（与风险中心同一来源，绝不另算一套）
        from app.models.risk_event import RiskLevel, RiskStatus
        from app.services.risk import list_risks

        open_rows, open_total = list_risks(db, status=RiskStatus.OPEN, limit=50)
        high_open = sum(1 for r in open_rows if r.level == RiskLevel.HIGH)

        facts.append(AiFact(
            label="待处理风险数",
            evidence=_ev(open_total, "risks:open", None if open_total else "没有待处理的风险"),
            source_note="risk_events 表 status=OPEN 计数（与风险中心同源）",
        ))

        if open_total:
            facts.append(AiFact(
                label="其中高风险",
                evidence=_ev(high_open, "risks:open:high",
                             None if high_open else "待处理风险里没有高风险"),
                source_note="事件类型为资金/不可逆类（银行账户/付款/退款/赔偿等）",
            ))
            detail = "；".join(
                f"客户 {r.customer_id}：{r.reason or r.event_type.value}"
                for r in open_rows[:5]
            )
            facts.append(AiFact(
                label="待处理风险明细（前 5）",
                evidence=_note_ev(detail, "risks:open:list"),
                source_note=detail,
            ))
        note = "风险只来自两条可复现依据：命中策略闸门（D7）、AI 回复失败（D10）。"

    elif kind == AiAskKind.VISITS:
        # 复用 services/visits.py（访问记在 customer_events 的 VISIT 事件上）
        from app.services.visits import visits_summary

        vs = visits_summary(db)
        # ★ vs.* 已经是 EvidenceValue，直接透传（强类型）
        facts.append(AiFact(
            label="今天的客户访问次数",
            evidence=vs.today,
            source_note="customer_events 中 event_type=VISIT 且按 Asia/Shanghai 归日",
        ))
        facts.append(AiFact(
            label="有访问记录的客户数",
            evidence=vs.visited_customers,
            source_note="按 customer_id 去重",
        ))
        if vs.total.value:
            facts.append(AiFact(
                label="访问总次数",
                evidence=vs.total,
                source_note="VISIT 事件全量计数",
            ))
        if vs.top_pages:
            top = "、".join(f"{p.page} {p.count} 次" for p in vs.top_pages[:5])
            facts.append(AiFact(
                label="客户最常看的页面（前 5）",
                evidence=_note_ev(top, "visits:summary:top_pages"),
                source_note=top,
            ))
        note = "只记录能识别到客户的访问；匿名访问不落库。"

    return facts, note, topic_customer


def _facts_to_prompt(facts: list[AiFact]) -> str:
    lines = []
    for f in facts:
        ev = f.evidence
        val = ev.value
        shown = f"{val}" if val is not None else (ev.reason or "无数据")
        lines.append(f"- {f.label}：{shown}（来源：{f.source_note}）")
    return "\n".join(lines)


CAPABILITY_TEXT = capability_text()


@router.post("/ai-ask", response_model=AiAskResponse, summary="首页 AI 助手问答（基于真实事实）")
def ai_ask(payload: AiAskRequest, db: Session = Depends(get_db)) -> AiAskResponse:
    """Copilot 问答。顺序固定：① 闸门 → ② 分类 → ③ 查事实 → ④ 组织语言。"""
    question = payload.question.strip()
    kind = _classify(question)

    # ① 闸门：敏感问题一律不调 LLM（复用 TASK-004 的策略闸门）
    decision = evaluate_policy(question)
    policy = {
        "allowed": decision.allowed,
        "label": decision.label,
        "matched": decision.matched,
        "reason": decision.reason,
        "tone": decision.tone,
    }
    if decision.human_required:
        return AiAskResponse(
            question=question,
            kind=kind or AiAskKind.GENERAL,
            answer=(
                f"这个问题涉及「{decision.label}」，属于必须人工处理的范围，我不能给答复。"
                "请转人工，或到客户详情页由人工接管处理。"
            ),
            basis="policy_blocked",
            facts=[],
            policy=policy,
            llm_called=False,
            llm_ok=False,
            note="命中敏感策略：按规则不调用模型、不给承诺（报价/折扣/合同/收款账户等）。",
        )

    # ② 明确越界：礼貌拒绝，不查库、不调模型（省一次调用，也不会胡说）
    if kind == AiAskKind.OUT_OF_SCOPE:
        return AiAskResponse(
            question=question,
            kind=kind,
            answer=(
                "这个不在我的数据范围内——我只能答系统里真实存在的数据，"
                "没有竞争对手情报、市场份额这类外部信息，也不会替你直接写发给客户的话。"
            ),
            basis="out_of_scope",
            facts=[],
            policy=policy,
            llm_called=False,
            llm_ok=False,
            note="可问：今天要处理什么 / 客户情况 / 客户来源 / 最近动态 / 数据连接 / 某个客户的状态。",
        )

    # ③ 打招呼 / 问能力：直接给固定答案（这些不需要查库，也不该烧模型）
    if kind == AiAskKind.GREETING:
        return AiAskResponse(
            question=question, kind=kind,
            answer="你好，我是这套工作台的数据助手。" + CAPABILITY_TEXT,
            basis="capability", facts=[], policy=policy, llm_called=False, llm_ok=False,
            note="这是固定说明，不是模型生成的内容。",
        )
    if kind == AiAskKind.CAPABILITY:
        return AiAskResponse(
            question=question, kind=kind, answer=CAPABILITY_TEXT,
            basis="capability", facts=[], policy=policy, llm_called=False, llm_ok=False,
            note="这是固定说明，不是模型生成的内容。",
        )

    # ④ 没命中关键词时：先试着当"查某个客户"处理（人名/公司名直查）
    if kind is None:
        kind = AiAskKind.CUSTOMER_DETAIL if _find_customer(db, question, payload.context_customer_id) else AiAskKind.GENERAL

    facts, note, topic_customer = _collect_facts(db, kind, question, payload.context_customer_id)

    # ⑤ 有命中事实 → 让模型组织语言；没命中 → 交模型按边界裁定（同一，避免多一次调用）
    if facts:
        prompt = (
            f"用户问题：{question}\n\n"
            f"系统查到的事实（只能使用这些）：\n{_facts_to_prompt(facts)}"
        )
        system = COPILOT_SYSTEM_PROMPT
        basis_when_llm_fails = "llm_failed"
    else:
        # 没有事实：把边界一起给模型，让它自己判断是"能答但没数据"还是"越界"
        prompt = f"用户问题：{question}\n\n（系统没有查到与之匹配的事实数据。）"
        system = COPILOT_FALLBACK_PROMPT
        basis_when_llm_fails = "no_data"

    try:
        answer = generate_reply(prompt, system_prompt=system)
        llm_ok, llm_called = True, True
        basis = "llm" if facts else "llm_no_facts"
    except LLMUnavailable as exc:
        llm_ok, llm_called = False, True
        basis = basis_when_llm_fails
        if facts:
            answer = "（模型暂时不可用，以下直接给你查到的事实）" + "；".join(
                f"{f.label}：{f.evidence.value if f.evidence.value is not None else (f.evidence.reason or f.source_note)}"
                for f in facts
            )
        else:
            answer = "我这边没有找到能支撑这个问题的真实数据，所以不给你推测结论。"
        note = f"{note}（LLM 不可用：{exc.reason}）"

    return AiAskResponse(
        question=question,
        kind=kind,
        answer=answer,
        basis=basis,
        facts=facts,
        policy=policy,
        llm_called=llm_called,
        llm_ok=llm_ok,
        note=note,
        context_customer_id=topic_customer,
    )
