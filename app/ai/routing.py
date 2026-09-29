"""routing.py · 响应路由：先判「用户想做什么」，再决定「怎么回答」（FR-007）。

════════════════════════════════════════════════════════════════════════
【这个模块解决的真实事故（实测复现，不是设想）】
════════════════════════════════════════════════════════════════════════
FR-007 施工前，把这三句话原样问进去：

    「我放的周报在哪里」  → 当成销售分析，算了一整张销售表      ❌
    「1+1等于多少？」     → 当成销售汇总，算了 7 行销售表        ❌
    「什么是毛利率？」    → unsupported（数据里没有毛利）        ❌

根因不是"模型不够聪明"，而是**链路里根本没有"非销售"这条路**：
既有 8 个 intent 全是销售类，关键词闸门里 `多少` 这种超高频词一出现就往销售上靠，
而回答渲染层无条件先拼一段"算出来的事实"。于是任何问题都被拖进销售流水线。

════════════════════════════════════════════════════════════════════════
【方向不许反（评审冻结）】
════════════════════════════════════════════════════════════════════════
    question → intent 分类 → intent 专属处理 → response_mode → 回答渲染
    ✗ 禁止：question → response_mode → 去猜该调哪个销售工具

`response_mode` 是"怎么回答"（渲染档位），**不是**"该调什么工具"。所以它永远排在
intent 之后：先由本模块判出 6 个 intent 之一，再由 service 走对应的处理分支。

════════════════════════════════════════════════════════════════════════
【分类顺序冻结（顺序本身就是规格）】
════════════════════════════════════════════════════════════════════════
    SYSTEM_HELP → ARITHMETIC → GENERAL_QA → REPORT_GENERATION
                → DATA_LOOKUP → SALES_ANALYSIS → clarify

为什么系统类/数学/概念要**排在销售前面**：业务关键词一旦先匹配，"1+1等于多少"这种句子
里的 `多少` 就会把问题吞进销售链路 —— 这正是 FR-007 要修的那个洞。

★ 分类失败（什么都没匹配上）的默认行为是**澄清**，绝不默认 sales_summary：
  一句"今天天气怎么样"落进销售汇总，会得到一个看着像答案、其实答另一个问题的数字。

════════════════════════════════════════════════════════════════════════
【关键词优先级重做（评审点名）】
════════════════════════════════════════════════════════════════════════
不许用「多少」这种**单关键词**决定销售意图。DATA_LOOKUP 必须同时满足三件事才成立：

    单值语义（"多少/是多少/一共…"） + 业务实体（"销售额/订单数…"） + 时间或维度（"11月/本周"）

并且不得含任何**分析/排行/明细类**信号（"趋势/对比/top/各国家/产品…"）。
    · 「销售额 + 多少」= DATA_LOOKUP（单值，direct 档）
    · 「1+1 + 多少」   = ARITHMETIC（根本进不了销售链路）
    · 「为什么本周比上周高」= SALES_ANALYSIS（有分析信号）
这是"把老问题换个名字"的那条歧路的分界：`多少` 只是一个必要条件，不是判据。

════════════════════════════════════════════════════════════════════════
【本模块是纯代码，不碰数据、不调模型】
════════════════════════════════════════════════════════════════════════
`classify()` 只做字符串判断：不读数据文件、不调 LLM、不查数据库。
这样它才能被"非销售分支不许碰销售数据"的测试直接钉住（见 tests/test_fr007_isolation.py）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai import arithmetic
from app.ai.report import report_period

# ════════════════════════════════════════════════════════════════════════
# ① 冻结：Intent（用户想做什么）× response_mode（怎么回答）
# ════════════════════════════════════════════════════════════════════════
INTENT_SALES_ANALYSIS = "sales_analysis"
INTENT_DATA_LOOKUP = "data_lookup"
INTENT_GENERAL_QA = "general_qa"
INTENT_ARITHMETIC = "arithmetic"
INTENT_REPORT_GENERATION = "report_generation"
INTENT_SYSTEM_HELP = "system_help"
# 「无法确定」不是第 7 个意图，而是分类失败的兜底档（施工指令 §三）
INTENT_CLARIFY = "clarify"

ALL_INTENTS: tuple[str, ...] = (
    INTENT_SALES_ANALYSIS,
    INTENT_DATA_LOOKUP,
    INTENT_GENERAL_QA,
    INTENT_ARITHMETIC,
    INTENT_REPORT_GENERATION,
    INTENT_SYSTEM_HELP,
    INTENT_CLARIFY,
)

MODE_ANALYSIS = "analysis"
MODE_DIRECT = "direct"
MODE_GENERAL = "general"
MODE_REPORT = "report"
MODE_HELP = "help"
MODE_CLARIFY = "clarify"

ALL_MODES: tuple[str, ...] = (MODE_ANALYSIS, MODE_DIRECT, MODE_GENERAL, MODE_REPORT, MODE_HELP, MODE_CLARIFY)

# ★ 冻结映射（施工指令 §三）。**不许加**：多一个 intent 就多一条要维护的分支。
MODE_BY_INTENT: dict[str, str] = {
    INTENT_SALES_ANALYSIS: MODE_ANALYSIS,
    INTENT_DATA_LOOKUP: MODE_DIRECT,
    INTENT_GENERAL_QA: MODE_GENERAL,
    INTENT_ARITHMETIC: MODE_DIRECT,
    INTENT_REPORT_GENERATION: MODE_REPORT,
    INTENT_SYSTEM_HELP: MODE_HELP,
    INTENT_CLARIFY: MODE_CLARIFY,
}

# ★ 这三类**必须在销售工具调用之前结束分支**（评审 GO 条件 4）：
#   它们的处理函数在代码调用图上就不 import tools / executor / loader，
#   所以"隔离"是结构性的，不靠提示词里写一句"请不要查销售数据"。
NON_SALES_INTENTS: tuple[str, ...] = (INTENT_SYSTEM_HELP, INTENT_ARITHMETIC, INTENT_GENERAL_QA)

# ════════════════════════════════════════════════════════════════════════
# ② 每个 mode 允许出现的分段（**后端就不生成**不属于它的段）
# ════════════════════════════════════════════════════════════════════════
# 施工指令 §五给的是**概念名**（answer / facts / trend / attribution / summary …）。
# 落到代码里要区分两种情况：
#   · 新档（direct / help / general）—— 本 TASK 新建的渲染，直接用规格里的名字：
#       direct → answer（单值）        help → help（帮助正文）      general → answer（概念说明）
#   · 既有两档（analysis / report）—— **TASK-004/005/010 已交付并冻结**的四段契约：
#       what（事实，含答案+数据）/ contribution（归因）/ why（推断）/ actions（建议）
#     它就是规格里 [answer, facts, trend, attribution] 的具体形态（答案与事实同段、趋势并进事实里），
#     改名字会同时破坏前端、导出文件与既有验收 —— 本 TASK 不做重命名，如实记在报告里。
SECTIONS_BY_MODE: dict[str, tuple[str, ...]] = {
    MODE_DIRECT: ("answer",),
    MODE_HELP: ("help",),
    MODE_GENERAL: ("answer",),
    MODE_ANALYSIS: ("what", "contribution", "why", "actions"),
    MODE_REPORT: ("what", "why", "actions"),
    MODE_CLARIFY: ("what", "why", "actions"),
}

# ★ GO 条件 6 的机器可读形式：这三档**后端就不生成**旧 SECTION_WHAT，
#   而不是"生成了但前端藏起来"。测试直接拿这个元组去断言。
MODES_WITHOUT_SECTION_WHAT: tuple[str, ...] = (MODE_DIRECT, MODE_HELP, MODE_GENERAL)


# ════════════════════════════════════════════════════════════════════════
# ③ 关键词表（全部是**必要条件**，不是单一判据）
# ════════════════════════════════════════════════════════════════════════
# ── SYSTEM_HELP：系统怎么用（不碰销售数据）────────────────────────────
_HELP_TOPICS = (
    "导出", "下载", "导入", "上传", "登录", "退出登录", "注册", "密码", "账号", "用户名",
    "帮助", "使用说明", "说明书", "教程", "怎么用", "操作", "设置", "功能", "界面", "页面",
    "支持哪些", "支持什么", "能做什么", "能干什么", "你是谁",
)
_HELP_QUESTIONS = ("怎么", "如何", "怎样", "咋", "能不能", "可以吗", "在哪", "哪里", "去哪", "找不到", "忘了")
# 「我的文件在哪」这类位置类问题（包含"周报/报告"字样，但它们不是在要一份新报告）
_HELP_WHERE_NOUNS = ("文件", "报表", "周报", "月报", "报告", "产出", "导出", "下载", "记录", "历史", "数据")

# ── ARITHMETIC ────────────────────────────────────────────────────────
# 识别逻辑在 arithmetic.py（受限 AST 计算器），这里只是"问一句"。

# ── GENERAL_QA：概念/定义类问题（不碰销售数据）────────────────────────
_GENERAL_PATTERNS = (
    "什么是", "是什么意思", "是啥意思", "指的是什么", "指什么", "怎么理解", "如何理解",
    "解释一下", "解释下", "的定义", "什么概念", "有什么用", "干什么用的", "为什么叫",
)
# 概念问句里**不该出现**的东西：一出现就说明用户在问数据，不是问概念
_GENERAL_BLOCKERS = (
    "最", "排行", "排名", "哪些", "哪个", "多少", "几", "趋势", "对比", "比较", "占比",
    "月", "年", "周", "今天", "昨天", "最近", "本期", "上期", "报表", "数据", "导出", "下载",
)
_GENERAL_TERM_RE = re.compile(r"(?:什么是|是什么意思|指的是什么|怎么理解|如何理解|解释一下|解释下)(.{1,16})[？?。！!,，]?$")

# ── REPORT_GENERATION ────────────────────────────────────────────────
# 「这句话是不是要一份报告」的判据**复用 report.report_period()**（TASK-010 已冻结），
# 这里不另抄一份周末/月末词表 —— 两套判据早晚会漂移。

# ── DATA_LOOKUP：单值查询（三件事同时成立才算）────────────────────────
_LOOKUP_METRICS = (
    "销售额", "营业额", "营收", "销售收入", "销售金额", "销售总额", "卖了多少", "卖了多少钱",
    "金额", "订单数", "订单量", "多少单", "多少笔", "订单", "客户数", "客户数量", "客单价",
    "平均单价", "销量", "销售量", "退货额", "退款额",
)
_LOOKUP_SEMANTICS = (
    "多少", "是多少", "是几", "几单", "几笔", "一共", "总共", "合计", "总计", "总额",
    "统计", "查一下", "看一下", "报一下", "给我", "有没有", "是多少钱",
)
_LOOKUP_TIME_RE = re.compile(
    r"\d{4}\s*[-/.年]\s*\d{1,2}|\d{1,2}\s*月|本月|这个月|上月|上个月|本周|这周|上周|"
    r"今天|昨天|前天|最近|近\s*\d|季度|全年|今年|去年|同期|本期|当期|这期|上期"
)
# 分析/排行/明细类信号：出现任意一个，这条问题就**不是**单值查询。
_LOOKUP_BLOCKERS = (
    "排行", "排名", "最好卖", "最畅销", "畅销", "热销", "最高", "最多", "最大", "前几",
    "前10", "前20", "前五", "top", "TOP", "Top", "趋势", "走势", "变化", "对比", "比较",
    "相比", "环比", "同比", "增长", "下降", "上升", "减少", "贡献", "推动", "造成", "拉动",
    "为什么", "为何", "原因", "退货", "退款", "取消", "复购", "回购", "沉睡", "新客", "新增",
    "分布", "占比", "各", "哪些", "哪个", "结构", "明细", "清单", "列表", "报表",
    "产品", "商品", "货号", "编码", "SKU", "sku", "库存",
)
# 「客户」单独出现算分析类（客户排行/某客户），但「客户数」是实打实的单值指标 ——
# 用**否定前瞻**把这一种写法排除在拦截之外。
_CUSTOMER_BLOCK_RE = re.compile(r"客户(?!数|数量)")

# ── SALES_ANALYSIS：销售类里**不是**单值查询的其余问题 ─────────────────
_SALES_TERMS = (
    "销售", "卖", "营业额", "营收", "订单", "客户", "顾客", "买家", "产品", "商品", "货号",
    "编码", "库存", "销量", "退货", "退款", "取消", "复购", "回购", "沉睡", "新客", "趋势",
    "走势", "对比", "比较", "相比", "环比", "同比", "增长", "下降", "上升", "排行", "排名",
    "top", "TOP", "Top", "国家", "地区", "金额", "单价", "客单价", "毛利", "利润",
    "数据", "报表", "业绩", "占比", "分布", "结构",
)
# 注意两件事，都是"别把闲话拖进销售链路"：
#   · **时间词不算销售信号**（"今天/本月/11月"）—— 算进去的话"今天天气怎么样"就成了销售分析；
#   · **`多少` / `哪些` 也不算**（它们是疑问词，不是业务实体）—— 算进去的话
#     "天上星星有多少" 会落成销售分析。业务实体（销售额/订单/客户…）才是判据。

# ── 闲聊（"你好"）────────────────────────────────────────────────────
# 施工指令 §八B：「你好」→ mode=general，无销售工具、无销售 facts。
# 闲聊归 general_qa：它同样**不读销售数据**，走同一个 general 渲染档。
_CHITCHAT_RE = re.compile(
    r"^(你好|您好|哈喽|嗨|在吗|在不在|hi|hello|hey|早上好|中午好|下午好|晚上好|谢谢|多谢|好的|收到|ok)[!！。.？?~\s]*$",
    re.IGNORECASE,
)
# 销售类里也出现"怎么/如何"时（"销售额怎么算"），不该被 SYSTEM_HELP 抢走 ——
# 所以 SYSTEM_HELP 只在**没有**销售实体词时成立（顺序在前，但条件更严）。


# ════════════════════════════════════════════════════════════════════════
# ④ 路由结果
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Route:
    """一次分类的结论（全部可落盘、可断言，不含任何销售数据）。"""

    intent: str
    response_mode: str
    reason: str = ""
    confidence: float = 0.0
    source: str = "code"          # 分类器是纯代码；这个字段留痕用

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "response_mode": self.response_mode,
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
        }

    def to_intent_dict(self) -> dict[str, object]:
        """落进记录的 `intent` 字段时用的形状 —— **与销售解析的 to_dict() 完全一致**。

        为什么保持同一个形状：记录里只有一个 `intent` 字段，前端/历史列表/审计脚本
        都按同一个形状读它。非销售分支要是另起一种形状（比如直接放一个字符串），
        读的人就得多写一个分支去猜"这次的 intent 是哪种写法" —— 那正是"三套字段"的开端。
        """
        return {
            "intent": self.intent,
            "params": {},
            "assumptions": [],
            "confidence": self.confidence,
            "reason": self.reason,
            "report": "",
        }


def _has_sales_signal(text: str) -> bool:
    return any(term in text for term in _SALES_TERMS)


# 「某段时间**为什么变了**」——没有业务名词也是销售分析（施工指令 §八E 的原句就是
# 「为什么本周比上周高？」）。判据要**两件同时成立**：点了时间段 + 点了变化/原因。
# 只点一个不算："今天天气怎么样"有时间段但没有变化词 → 仍然走澄清（不会误抓）。
_PERIOD_WORDS = ("本周", "这周", "上周", "本月", "这个月", "上月", "上个月",
                 "本季度", "上季度", "今年", "去年", "今天", "昨天", "前天", "最近")
_CHANGE_WORDS = ("比", "高", "低", "涨", "跌", "增长", "下降", "上升", "减少",
                 "变化", "波动", "原因", "为什么", "为何", "变好", "变差")


def _is_change_over_time(text: str) -> bool:
    return any(word in text for word in _PERIOD_WORDS) and any(word in text for word in _CHANGE_WORDS)


def _is_system_help(text: str) -> bool:
    """系统怎么用 / 文件在哪 —— **不碰销售数据**的那一类。"""
    if not any(topic in text for topic in _HELP_TOPICS):
        return False
    # 有明确销售实体、又在问数的（"11月销售额怎么算"），不算系统帮助
    if _has_sales_signal(text) and any(word in text for word in ("卖了多少", "销售额是多少")):
        return False
    if any(word in text for word in _HELP_QUESTIONS):
        return True
    # 光一个"帮助/使用说明"也算
    return any(topic in text for topic in ("帮助", "使用说明", "说明书", "教程", "能做什么", "能干什么", "你是谁"))


def _is_where_question(text: str) -> bool:
    """「我放的周报在哪里」—— 位置类问题（**不是**在要一份新报告）。"""
    return any(word in text for word in ("在哪", "哪里", "去哪儿", "去哪了", "找不到")) and any(
        noun in text for noun in _HELP_WHERE_NOUNS
    )


def _is_general_qa(text: str) -> bool:
    """概念/定义类问题（"什么是毛利率"）与闲聊（"你好"）—— 都不读当前业务数据。"""
    if _CHITCHAT_RE.match(text):
        return True
    match = _GENERAL_TERM_RE.search(text)
    if not match:
        return False
    term = match.group(1).strip()
    if not term:
        return False
    # 概念问句里带"最/排行/多少/11月"这类词 → 用户在问数据，不是问概念
    if any(blocker in text for blocker in _GENERAL_BLOCKERS):
        return False
    return True


def _is_data_lookup(text: str) -> bool:
    """单值查询：单值语义 + 业务实体 + 时间/维度，且不含分析/排行类信号。"""
    if not any(metric in text for metric in _LOOKUP_METRICS):
        return False
    if not any(word in text for word in _LOOKUP_SEMANTICS):
        return False
    if not _LOOKUP_TIME_RE.search(text):
        return False
    if any(blocker in text for blocker in _LOOKUP_BLOCKERS):
        return False
    if _CUSTOMER_BLOCK_RE.search(text):
        return False
    return True


def classify(question: str) -> Route:
    """一句话 → Route（intent + response_mode）。**纯代码，不碰数据。**

    顺序是冻结的（见模块开头）：SYSTEM_HELP → ARITHMETIC → GENERAL_QA →
    REPORT_GENERATION → DATA_LOOKUP → SALES_ANALYSIS → clarify。
    """
    text = (question or "").strip()
    if not text:
        return Route(INTENT_CLARIFY, MODE_CLARIFY, reason="问题为空", confidence=1.0)

    # ── ① 系统帮助（不碰销售数据）────────────────────────────────────
    if _is_system_help(text) or _is_where_question(text):
        return Route(INTENT_SYSTEM_HELP, MODE_HELP, reason="问的是系统怎么用/文件在哪", confidence=0.9)

    # ── ② 算术（走受限 AST 计算器，不过 LLM 心算）─────────────────────
    if arithmetic.solve(text) is not None:
        return Route(INTENT_ARITHMETIC, MODE_DIRECT, reason="这是一道纯算术题", confidence=0.95)

    # ── ③ 概念问答（不碰销售数据）────────────────────────────────────
    if _is_general_qa(text):
        return Route(INTENT_GENERAL_QA, MODE_GENERAL, reason="问的是一个概念/定义", confidence=0.85)

    # ── ④ 报告（判据复用 report.report_period，不另抄一份）────────────
    if report_period(text):
        return Route(INTENT_REPORT_GENERATION, MODE_REPORT, reason="要的是一份报告", confidence=0.9)

    # ── ⑤ 单值查询 ───────────────────────────────────────────────────
    if _is_data_lookup(text):
        return Route(INTENT_DATA_LOOKUP, MODE_DIRECT, reason="要的是销售数据里的一个值", confidence=0.8)

    # ── ⑥ 销售分析（销售类里的其余问题）──────────────────────────────
    if _has_sales_signal(text) or _is_change_over_time(text):
        return Route(INTENT_SALES_ANALYSIS, MODE_ANALYSIS, reason="这是销售分析类问题", confidence=0.6)

    # ── ⑦ 兜底：澄清（**绝不默认 sales_summary**）────────────────────
    return Route(
        INTENT_CLARIFY,
        MODE_CLARIFY,
        reason="没看出这是哪一类问题（既不涉及销售数据，也不是算术/概念/系统使用类）",
        confidence=0.0,
    )


__all__ = [
    "ALL_INTENTS",
    "ALL_MODES",
    "INTENT_ARITHMETIC",
    "INTENT_CLARIFY",
    "INTENT_DATA_LOOKUP",
    "INTENT_GENERAL_QA",
    "INTENT_REPORT_GENERATION",
    "INTENT_SALES_ANALYSIS",
    "INTENT_SYSTEM_HELP",
    "MODE_ANALYSIS",
    "MODE_BY_INTENT",
    "MODE_CLARIFY",
    "MODE_DIRECT",
    "MODE_GENERAL",
    "MODE_HELP",
    "MODE_REPORT",
    "MODES_WITHOUT_SECTION_WHAT",
    "NON_SALES_INTENTS",
    "SECTIONS_BY_MODE",
    "Route",
    "classify",
]
