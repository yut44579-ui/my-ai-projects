"""策略闸门 —— 出站检查 + 转人工判定。

★ 为什么这些必须写在代码里，而不是提示词里：
    提示词是"请求"模型别说，代码是"保证"它说不了。
    模型会漏，而且漏的时候你看不出来——它答得很通顺。

★ 本文件里的规则，有一部分是从真实评审意见里来的（见 docs/决策记录.md）：
    · "转人工就直接转，别暴露岗位和姓名"      → check_identity_leak
    · "AI 别解释自己做了什么"                  → check_self_narration
    · "不能说'我叫个同事'" —— 那还是废话       → 同上
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

# ══════════════════════════════════════════════════════════════════════
# 一、出站检查：答案发出去之前必须过这几关
# ══════════════════════════════════════════════════════════════════════

# ★★ 出处/来源标注：**一律删掉**，不管模型怎么写出来的。
#
#    用户的明确要求：「不要标明出处，来源泄露信息」。
#
#    ★ 为什么这不是小事：
#      那些"出处"是**内部文档名**（比如《报价底线》《客户分级标准》）。
#      光看名字就能猜出这家公司有哪些内部制度 ——
#      对一个外部客户来说，这就是在泄露内部信息。
#
#    ★ 而"可溯源"这条纪律并没有破：
#      溯源是**内部**的事。qa_log 里照样存着 citations，
#      控制台和人工工作台会单独一栏显示给客服看。
#      **只是不写进给客户看的那句话里。**
#
#    ★ 所以这里做的是"兜底"：提示词已经明确禁止了，
#      但模型不一定每次都听 —— 出站检查必须再删一遍。
CITATION_PATTERNS = [
    re.compile(r"[〔\[（(]?\s*出处\s*[:：][^〕\]）)\n]*[〕\]）)]?"),
    re.compile(r"[〔\[（(]?\s*来源\s*[:：][^〕\]）)\n]*[〕\]）)]?"),
    re.compile(r"[〔\[（(]?\s*参考\s*[:：][^〕\]）)\n]*[〕\]）)]?"),
    re.compile(r"根据\s*《[^》]{1,40}》[，,]?\s*"),
    re.compile(r"依据\s*《[^》]{1,40}》[，,]?\s*"),
    re.compile(r"《[^》]{1,40}》\s*(?:第[\d.]+条|规定|里|中|写明|写的)"),
]


def strip_citations(text: str) -> tuple[str, bool]:
    """删掉回答里的出处/来源标注。

    返回 (清理后的文本, 有没有删过)。
    ★ 只删"标注"，不删正常内容里的书名号 ——
      比如"退款要走流程"没问题，但"《退款管理办法》第3条写着"要删掉来源部分。
    """
    out = text or ""
    hit = False
    for p in CITATION_PATTERNS:
        new = p.sub("", out)
        if new != out:
            hit = True
            out = new
    # 清理因为删除产生的空行和行首空格
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, hit


# 身份泄漏：分两种，处理方式**完全不同** —— 这个区分是踩过坑才有的：
#
#   第一版把"部门名/职位词单独出现"也当成泄漏**直接拦下**，
#   结果「请假需直属主管审批」这种完全正常的流程说明被拦掉，
#   整个问题被转人工。检索明明是好的（质量 0.9）。
#
#   真正的区别在于**语境**：
#     ✗ 泄漏：「已经转给财务部李姐处理」  ← 在告诉客户**谁在处理他这一单**
#     ✓ 正常：「请假需直属主管审批」      ← 在描述**他要走的流程**
#
#   所以：
#     硬拦 —— 出现"转交/让谁处理/我叫了"这类**交接动词**，并带上部门或职位
#     仅提醒 —— 光秃秃的职位词/部门名（它可能就是资料原文的写法）
HANDOVER_LEAK = [
    # 交接动词 + 部门/职位：「已经转给财务部」「让法务处理」「我叫了售后」
    re.compile(r"(已|已经)?(转交|转给|转到|交给|移交给).{0,8}(财务|法务|人事|售后|技术|采购|行政|运营|主管|经理|专员)"),
    re.compile(r"(让|叫|找|通知).{0,4}(财务|法务|人事|售后|技术|采购|行政|运营)(部|部门|组|团队)?.{0,2}(处理|接手|看|回)"),
    re.compile(r"我(已经)?(叫|找|联系)(了)?.{0,6}(同事|财务|法务|售后|技术|主管|经理)"),
    # 明确指名到人（"李姐""王工"这类）+ 交接语气
    re.compile(r"(已|已经).{0,4}(转|交|派).{0,6}[，,]?\s*\S{1,4}(姐|哥|工|老师)"),
]

# 仅提醒：光秃秃的部门和职位词。★ 不直接拦 ——
# 它可能只是资料里就是这么写的（"财务部应于5个工作日内付款"）。
ROLE_MENTION = [
    re.compile(r"(财务|法务|人事|售后|技术|采购|行政|运营)(部|部门|组|团队)"),
    re.compile(r"(主管|经理|总监|专员|助理|负责人)"),
    re.compile(r"工号\s*[:：]?\s*\w+"),
]

# ★ AI 自解释：模型总想"说明一下自己做了什么"。
#   真人客服不会这么说 —— 会答就答，不会答就叫人来，不解释过程。
#   "我叫个同事帮你看下"表面看不像客套，但它和"亲，您好呢"是同一类病：
#   **都在说 AI 自己，而不是在回答用户。**
SELF_NARRATION = [
    re.compile(r"我(已经)?(帮)?(你)?(查|找|问|叫|联系|转)(了|一下|个)?"),
    re.compile(r"我(给|帮)你(走|处理|跟|盯)"),
    re.compile(r"我已经?(把|将).{0,8}(转|提交|发)"),
    re.compile(r"(正在|已经)(为|帮)你(查询|处理|联系)"),
    re.compile(r"让我(帮你)?(查|看|问)一下"),
]

# ★ 承诺性说法：客服不能替公司做承诺（出了事是公司担）
COMMITMENT = [
    re.compile(r"(一定|保证|百分之百|百分百|绝对|肯定)能"),
    re.compile(r"(最低价|内部价|底价|成本价)"),
    re.compile(r"(马上|立刻|立刻马上)就(能|会|可以)"),
    re.compile(r"我(保证|承诺)"),
]

# ★ markdown 残留：企微/飞书都是纯文本，星号井号会原样显示出来
MD_RESIDUE = [
    re.compile(r"\*\*[^*]+\*\*"),          # **粗体**
    re.compile(r"^#{1,6}\s", re.M),        # # 标题
    re.compile(r"^\s*[-*+]\s", re.M),      # - 列表
    re.compile(r"^\s*\d+\.\s", re.M),      # 1. 列表（这个要小心，见下）
    re.compile(r"`[^`]+`"),                # `代码`
    re.compile(r"\[([^\]]+)\]\([^)]+\)"),  # [文字](链接)
]

# 客服腔（开场白/结束语）
FILLER = [
    # ★ 结尾的标点要吃干净。
    #   第一版只写了 [，,。!！~]? 匹配一个字符，
    #   结果 "您好！！" 被清成 "！！"，留下一个孤零零的标点 —— 比不清还难看。
    # ★ 还必须带 re.M：开场白常常在自己那一行（"...。\n\n您好！"），
    #   不加 re.M 时 ^ 只匹配整串开头，行首的"您好"清不掉。
    re.compile(r"^(您好|你好|亲)\s*[，,。.!！~～]*", re.M),
    re.compile(r"感谢(您|你)的(咨询|提问|耐心等待)\s*[，,。.!！~～]*"),
    re.compile(r"(如有|如果)(其他|任何)问题.{0,10}(随时|欢迎)[^。！？!?]*[。！？!?]?"),
    re.compile(r"希望(能)?(对您|对你)有帮助\s*[，,。.!！~～]*"),
    re.compile(r"(很抱歉|抱歉)[，,]?(给您|给你)带来[^。！？!?]*[。！？!?]?"),
]


@dataclass
class CheckResult:
    ok: bool
    cleaned: str
    problems: list[str] = field(default_factory=list)
    must_escalate: bool = False

    def why(self) -> str:
        return "；".join(self.problems) if self.problems else "通过"


def check_outbound(text: str, *, is_internal: bool = False) -> CheckResult:
    """答案出站前的统一检查。

    is_internal=True 时放宽"身份泄漏"这一条 ——
    内部同事之间知道是财务在处理，是正常且有用的信息。
    ★ 但"AI 自解释"和"承诺性说法"两条**内外都拦**，那是话术问题不是保密问题。
    """
    problems: list[str] = []
    out = text or ""
    escalate = False

    # ⓪ ★★ 出处/来源标注：**一律删掉**（见 CITATION_PATTERNS 的说明）
    #    这是兜底 —— 提示词里已经明确禁止了，但模型不一定每次都听。
    #    ★ 内部也删：给客户的回答里不该有，内部要看溯源有单独的一栏。
    out, had_cite = strip_citations(out)
    if had_cite:
        problems.append("删掉了出处标注（不向客户暴露内部资料名）")

    # ① 身份泄漏：**只在出现了交接动词时硬拦**（见 HANDOVER_LEAK 的说明）
    if not is_internal:
        for pat in HANDOVER_LEAK:
            m = pat.search(out)
            if m:
                problems.append(f"泄漏交接对象「{m.group(0)}」")
                # ★ 不是替换掉了事 —— 说明这句答案是围绕"谁在处理"组织的，
                #   整段都不该发，直接转人工更安全。
                escalate = True
        # 光秃秃的职位/部门词 → 只提醒，不拦。
        # ★ 为什么不能拦：资料原文里就是这么写的（"财务部应于5个工作日内付款"），
        #   拦掉等于这类问题永远答不了。真正的风险在交接语境里，那由上面负责。
        for pat in ROLE_MENTION:
            m = pat.search(out)
            if m:
                problems.append(f"提到内部称谓「{m.group(0)}」（仅提醒）")

    # ② AI 自解释 → 直接删掉那句话，不必转人工（删掉不影响事实）
    for pat in SELF_NARRATION:
        m = pat.search(out)
        if m:
            problems.append(f"AI 在解释自己「{m.group(0)}」")
    out = _drop_sentences(out, SELF_NARRATION)

    # ③ 承诺性说法 → 必须转人工（不能只删词，语义已经错了）
    for pat in COMMITMENT:
        m = pat.search(out)
        if m:
            problems.append(f"越权承诺「{m.group(0)}」")
            escalate = True

    # ④ markdown 残留 → 清掉
    for pat in MD_RESIDUE:
        if pat.search(out):
            problems.append("含 markdown 记号")
    out = _strip_markdown(out)

    # ⑤ 客服腔 → 清掉
    for pat in FILLER:
        if pat.search(out):
            problems.append("客服腔（开场白/结束语）")
    out = _strip_filler(out)

    out = out.strip()
    if not out:
        problems.append("清理后为空")
        escalate = True

    # "有严重问题"时，即使文字看着还行也不发 —— 交给人工
    hard = escalate or any(p.startswith("泄漏") or p.startswith("越权") for p in problems)
    return CheckResult(ok=not hard, cleaned=out, problems=problems, must_escalate=hard)


def _drop_sentences(text: str, patterns: list[re.Pattern[str]]) -> str:
    """把命中模式的**整句**去掉，而不是只删词。

    ★ 只删词会留下残句（"我叫个同事帮你看下" → "帮你看下"），
      比不删更糟。整句删掉才干净。
    """
    parts = re.split(r"(?<=[。！？!?\n])", text)
    kept = []
    for s in parts:
        if any(p.search(s) for p in patterns):
            continue
        kept.append(s)
    return "".join(kept)


def _strip_markdown(text: str) -> str:
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.M)
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.M)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    # ★ 编号列表保留：客服场景里"三步："后面的 1/2/3 是好读的，
    #   硬删掉反而变成一坨。所以这里不处理 \d+\.（上面 MD_RESIDUE 只是告警）。
    return out


def _strip_filler(text: str) -> str:
    out = text
    for pat in FILLER:
        out = pat.sub("", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ══════════════════════════════════════════════════════════════════════
# 二、转人工判定：哪些话根本不该让模型答
# ══════════════════════════════════════════════════════════════════════

# ★ 命中即转人工，**不调模型**。
#   这是"代码层拦"而不是"提示词求它别说"——
#   报价、合同、退款这些答错的代价太大，不能赌模型听话。
ESCALATE_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("报价", re.compile(r"(多少钱|价格|报价|便宜|优惠|折扣|打折|底价|返点)")),
    ("合同", re.compile(r"(合同|条款|盖章|签约|协议|违约|赔偿)")),
    ("付款", re.compile(r"(收款账户|银行账号|打款账户|汇款|发票抬头)")),
    ("退款", re.compile(r"(退款|退货|赔偿|索赔|补偿)")),
    ("投诉", re.compile(r"(投诉|举报|曝光|起诉|律师|工商|12315)")),
    ("法律", re.compile(r"(法律|诉讼|仲裁|合规审查)")),
    ("要求转人", re.compile(r"(转人工|找客服|要人工|叫真人|人工服务)")),
]


@dataclass
class IntentVerdict:
    escalate: bool
    intent: str | None = None
    reason: str = ""


def judge_intent(question: str) -> IntentVerdict:
    """在调用模型之前先判定：这句话该不该让 AI 答。

    ★ 放在最前面（而不是生成完再检查）有两个好处：
      ① 省钱：不该答的直接不调模型
      ② 安全：模型压根没机会说出越权的话
    """
    q = question or ""
    for name, pat in ESCALATE_INTENTS:
        m = pat.search(q)
        if m:
            return IntentVerdict(
                True, name, f"命中「{m.group(0)}」→ 属于{name}类，按规则转人工"
            )
    return IntentVerdict(False)


# ══════════════════════════════════════════════════════════════════════
# 三、分级：自动发 / 起草 / 转人工
# ══════════════════════════════════════════════════════════════════════

# 档位 → (自动门槛, 起草门槛)
# ★ 客户只选一个词，不调数字。
TIERS: dict[str, tuple[float, float]] = {
    "conservative": (0.86, 0.60),
    "standard": (0.72, 0.45),
    "aggressive": (0.58, 0.32),
}


def tier_thresholds(tier: str) -> tuple[float, float]:
    return TIERS.get(tier, TIERS["standard"])


def decide(quality: float, *, tier: str = "standard", intent_escalate: bool = False) -> str:
    """定这一条走哪条路。

    ★ 判定顺序很重要：意图转人工**优先于**一切，
      因为那类问题分数再高也不能自动发。
    """
    if intent_escalate:
        return "escalate"
    auto, draft = tier_thresholds(tier)
    if quality >= auto:
        return "auto"
    if quality >= draft:
        return "draft"
    return "escalate"
