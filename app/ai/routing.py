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
intent 之后：先由本模块判出 intent 之一，再由 service 走对应的处理分支。
（FR-007 时是 6 个 + 澄清兜底；FR-010-C 追加了第 8 个 `joint_analysis`，理由见下面那一节。）

════════════════════════════════════════════════════════════════════════
【分类顺序冻结（顺序本身就是规格）】
════════════════════════════════════════════════════════════════════════
    JOINT(DOC+DATA) → SYSTEM_HELP → CAPABILITY → ARITHMETIC → GENERAL_QA
                    → REPORT_GENERATION → DATA_LOOKUP → SALES_ANALYSIS
                    → ★资料兜底(FR-010-D) → clarify

★ FR-010-C 把 **JOINT（资料+销售数据联合分析）** 加在**最前面**，理由是它"两边都要读"：
  它既可能带着"资料"（会被 SYSTEM_HELP/DOC 抢走），也可能带着"分析/趋势"（会被
  SALES_ANALYSIS 抢走）—— 排在任何一个后面，它都会被那一档吃掉，而吃掉的结果是
  **答了另一个问题**（拿使用说明或一份销售表去回答"资料怎么说"）。它的判据自己是
  严的（必须点了一份资料，且要一个"跟数据放一起看"的产出），所以排最前不会误伤。

为什么系统类/数学/概念要**排在销售前面**：业务关键词一旦先匹配，"1+1等于多少"这种句子
里的 `多少` 就会把问题吞进销售链路 —— 这正是 FR-007 要修的那个洞。

★ FR-010-A4 追加的 CAPABILITY（「你能查天气吗 / 帮我预测下个月能卖多少」）**必须**排在
  销售之前，且理由与上面同源、但后果更严重：关键词规则会把"预测下个月能卖多少"当成
  "卖了多少"，直接拿一个**已经发生的历史数字**冒充预测结果 —— 那不是"答得不好"，
  是答了一个用户会当真的假结论。所以这类问题在计算之前就结束（见 `app.ai.capability`）。

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
【本模块是纯代码，不碰**销售**数据、不调模型】
════════════════════════════════════════════════════════════════════════
`classify()` 只做字符串判断：不调 LLM、**不读一行销售数据**、不建 DataFrame、不算任何指标。
这样它才能被"非销售分支不许碰销售数据"的测试直接钉住（见 tests/test_fr007_isolation.py：
那份测试把读数据的入口全换成炸弹，本模块一旦碰了就会当场炸出来）。

★ 唯一的例外写在明处（FR-010-D）：最后那一档兜底要问一句"**库里有没有资料可检索**"
  （`doc_qa.has_documents()`，读的是资料语料，与销售数据无关）。它**只在前面所有档都没接住**
  时才走到 —— 也就是本来就要落进 clarify 的那些问题；换句话说，**销售问题的判路上没有多这一步**。
  这一条是"资料兜底没有意义就不做"的必要条件，不是新判据：判据本身（`doc_qa.is_document_fallback`）
  仍然是纯字符串判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai import arithmetic, doc_qa, joint, system_info
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
# ★ FR-010-C 追加的第 8 个意图：**资料 + 销售数据一起看**（联合分析）。
#   为什么它必须是一个新 intent、而不是塞进 system_help（B 的资料问答就是塞在那儿的）：
#   它**要读销售数据**（第二段就是确定性算出来的数字），而 system_help 那一档
#   在代码调用图上被钉死为"一次都不碰销售数据"（见 NON_SALES_INTENTS 与
#   tests/test_fr007_isolation.py）。把一个会读数据的档塞进"不读数据的档"里，
#   那个档的分类与隔离就变成了一句假话 —— 宁可多一个意图，也不撒这个谎。
INTENT_JOINT_ANALYSIS = "joint_analysis"
# 「无法确定」不是第 7 个意图，而是分类失败的兜底档（施工指令 §三）
INTENT_CLARIFY = "clarify"

ALL_INTENTS: tuple[str, ...] = (
    INTENT_SALES_ANALYSIS,
    INTENT_DATA_LOOKUP,
    INTENT_GENERAL_QA,
    INTENT_ARITHMETIC,
    INTENT_REPORT_GENERATION,
    INTENT_SYSTEM_HELP,
    # ★ FR-010-C 追加（**只能往后追加**：前七个的顺序是既有的，动它就是动别人的合同）
    INTENT_JOINT_ANALYSIS,
    INTENT_CLARIFY,
)

MODE_ANALYSIS = "analysis"
MODE_DIRECT = "direct"
MODE_GENERAL = "general"
MODE_REPORT = "report"
MODE_HELP = "help"
# ★ FR-010-C 追加的第 6 档：**三段式**（资料事实 / 数据事实 / 推断与建议）。
#   为什么不复用 analysis 档：analysis 的四段是"事实由代码写、推断由模型写"的**销售分析**
#   形态（【发生了什么】【主要贡献】【为什么】【建议行动】），它的第一段永远是销售事实 ——
#   联合分析的第一段是**资料原文摘录**，第二段才是数据。硬套进 analysis 档，
#   用户看到的第一个标题就会是"发生了什么"，而底下写的是一份资料的引用（名不副实）。
MODE_JOINT = "joint"
MODE_CLARIFY = "clarify"

ALL_MODES: tuple[str, ...] = (
    MODE_ANALYSIS, MODE_DIRECT, MODE_GENERAL, MODE_REPORT, MODE_HELP,
    MODE_JOINT,                       # FR-010-C 追加（同样**只能往后追加**）
    MODE_CLARIFY,
)

# ★ 冻结映射（施工指令 §三）。**不许加**：多一个 intent 就多一条要维护的分支。
#   FR-010-C 是**唯一一次**追加（理由见上面的 INTENT_JOINT_ANALYSIS），且只往后加一项。
MODE_BY_INTENT: dict[str, str] = {
    INTENT_SALES_ANALYSIS: MODE_ANALYSIS,
    INTENT_DATA_LOOKUP: MODE_DIRECT,
    INTENT_GENERAL_QA: MODE_GENERAL,
    INTENT_ARITHMETIC: MODE_DIRECT,
    INTENT_REPORT_GENERATION: MODE_REPORT,
    INTENT_SYSTEM_HELP: MODE_HELP,
    INTENT_JOINT_ANALYSIS: MODE_JOINT,
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
    # FR-010-C：三段**顺序即规格**（资料事实 → 数据事实 → 推断与建议）。
    # 段名与 `app.ai.joint.SECTION_ORDER` **逐字一致**（那边是正文拼装处，这里是路由契约）。
    MODE_JOINT: ("doc_facts", "data_facts", "analysis"),
    MODE_ANALYSIS: ("what", "contribution", "why", "actions"),
    MODE_REPORT: ("what", "why", "actions"),
    MODE_CLARIFY: ("what", "why", "actions"),
}

# ★ GO 条件 6 的机器可读形式：这几档**后端就不生成**旧 SECTION_WHAT，
#   而不是"生成了但前端藏起来"。测试直接拿这个元组去断言。
#   FR-010-C 的 joint 也在这里：它生成的是三段资料/数据/推断，没有【发生了什么】
#   （联合分析的"发生了什么"是**两段**，各有自己的标题）。
MODES_WITHOUT_SECTION_WHAT: tuple[str, ...] = (MODE_DIRECT, MODE_HELP, MODE_GENERAL, MODE_JOINT)


# ════════════════════════════════════════════════════════════════════════
# ③ 关键词表（全部是**必要条件**，不是单一判据）
# ════════════════════════════════════════════════════════════════════════
# ── SYSTEM_HELP：系统怎么用（不碰销售数据）────────────────────────────
_HELP_TOPICS = (
    "导出", "下载", "导入", "上传", "登录", "退出登录", "注册", "密码", "账号", "用户名",
    "帮助", "使用说明", "说明书", "教程", "怎么用", "操作", "设置", "功能", "界面", "页面",
    "支持哪些", "支持什么", "能做什么", "能干什么", "你是谁", "你是什么",
)
_HELP_QUESTIONS = ("怎么", "如何", "怎样", "咋", "能不能", "可以吗", "在哪", "哪里", "去哪", "找不到", "忘了")
# 「我的文件在哪」这类位置类问题（包含"周报/报告"字样，但它们不是在要一份新报告）
_HELP_WHERE_NOUNS = ("文件", "报表", "周报", "月报", "报告", "产出", "导出", "下载", "记录", "历史", "数据")

# FR-010-A3 的**系统元信息**判据（我导入的文件在哪 / 我导入了几份）不住在这里：
# 它与"回答那一侧"必须同一处（`app.ai.system_info.is_meta_question`），
# 否则早晚漂移成"判得出来却答不上来"。这里只 import 来用。

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
    #: ★ FR-010-D：这条路是不是**"点名了资料就兜底检索"**那一档判出来的。
    #: 为什么必须把它带下去：兜底档与"使用说明 / 元信息"**共用 help 档**
    #: （intent 都是 system_help、mode 都是 help）—— 光看 intent，回答侧分不出
    #: 「怎么导入资料？」（使用说明，①那一档早就判走了）与「供应商与物流.md 里说成本会怎样？」
    #: （兜底）。回答侧若只看 intent 就去检索，**使用说明会被抢答成资料内容**
    #: （施工中实测到过：那条问句被答成"资料里没有找到关于「怎么导入」的内容"）。
    #: 所以"这次是从哪一档来的"必须跟着路由结论一起传下去，不能让回答侧再猜一遍。
    fallback: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "response_mode": self.response_mode,
            "reason": self.reason,
            "confidence": self.confidence,
            "source": self.source,
            # FR-010-D 追加（机器可读）：这次的 help 档是"静态使用说明"还是"资料检索兜底"。
            "fallback": self.fallback,
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
    return any(topic in text for topic in ("帮助", "使用说明", "说明书", "教程", "能做什么", "能干什么", "你是谁", "你是什么"))


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

    顺序是冻结的（见模块开头）：SYSTEM_HELP → CAPABILITY → ARITHMETIC → GENERAL_QA →
    REPORT_GENERATION → DATA_LOOKUP → SALES_ANALYSIS → clarify。
    """
    text = (question or "").strip()
    if not text:
        return Route(INTENT_CLARIFY, MODE_CLARIFY, reason="问题为空", confidence=1.0)

    # ── ①″ ★ FR-010-C 联合分析（资料 + 销售数据）—— **必须排在下面全部之前** ────
    # 为什么排最前：这一档要**同时**读资料与销售数据，而它的问法几乎都会先撞上别的分支：
    #   「根据我导入的资料，我们下一步该怎么做」→ 有"导入"+"怎么" → 会被 SYSTEM_HELP 抢走；
    #   「结合资料和销售数据，分析下后面该怎么发展」→ 有"分析" → 会被 SALES_ANALYSIS 抢走。
    # 抢走的后果不是"答得不好"，而是**答了另一个问题**（拿静态使用说明、或拿一份销售表
    # 去回答一个"资料怎么说"的问题）。判据本身在 `app.ai.joint.is_joint_question`
    # （纯代码、不读数据、不调模型），它内部**先把"只问资料内容"的问题让回给 B**。
    if joint.is_joint_question(text):
        return Route(
            INTENT_JOINT_ANALYSIS, MODE_JOINT,
            reason="要把你导入的资料和销售数据放在一起看", confidence=0.85,
        )

    # ── ① 系统帮助 / 系统元信息 / 资料内容（都不碰销售数据）────────────
    # 元信息（FR-010-A3）与使用说明走**同一档**（help）—— 它们都是"关于系统自己"的问题，
    # 只是前者要查真实记录、后者是静态文案（由 general.system_answer 分流）。
    #
    # ★ FR-010-B 的**资料问答**也排在销售之前，理由与 FR-010-A4 的 CAPABILITY 同源、后果同样严重：
    #   问句里一旦出现业务词（「资料里预计明年增长多少」里的"增长"、"销售额"），
    #   往下走就会被判成销售分析 —— 于是系统拿一份**销售数据**去回答一个**资料**问题。
    #   那不是"答得不好"，是答了另一个东西。判据本身在 `doc_qa.is_document_question`
    #   （与回答那一侧同一处，不许在这里再抄一份词表）。
    if _is_system_help(text) or _is_where_question(text) or system_info.is_meta_question(text) \
            or doc_qa.is_document_question(text):
        return Route(INTENT_SYSTEM_HELP, MODE_HELP, reason="问的是系统怎么用/文件在哪/资料里怎么写",
                     confidence=0.9)

    # ── ①′ 不具备的能力（FR-010-A4，**必须排在销售之前**：见模块开头）──────
    if system_info.looks_like_capability_question(text):
        return Route(
            INTENT_SYSTEM_HELP, MODE_HELP,
            reason="问的是我有没有某项能力（这项能力我没有）", confidence=0.9,
        )

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

    # ── ⑦ ★ FR-010-D 兜底：**点名了资料**就按资料检索回答（排在 clarify 之前）──
    # 由来：Hermes 复验 FR-010-B 时用自造素材抓到 —— 「供应商与物流.md 里说成本会怎样？」
    # 这类问法确实在问那份资料，但措辞不在 B 的 `_CONTENT_ASKS` 里，于是①∧②判不成，
    # 一路掉到"没听懂这个问题"的澄清档：**用户问资料，却被回了"我没听懂"**。
    # 这里补的就是这一段路的最后一道出口（判据见 `doc_qa.is_document_fallback`）：
    #   · 用户点名/提到了资料（「资料 / 文档 / 报告…」或书名号、带后缀的文件名）；
    #   · 并且库里**确实有可检索的资料** —— 没有资料可检索时兜底无意义，保持既有澄清；
    #   · 命中 → 与既有资料问答**完全同样**的带出处回答；没命中 → "没有找到" + 列库。
    # ★ 为什么不在这里放宽 `_CONTENT_ASKS`：那张表同时挡着「怎么导入资料」这类使用说明
    #   （它们也点着"资料"）—— 放宽就是把使用说明抢答成资料内容，那是当初加判据②的原因。
    #   这里的取舍是"允许进、但答不出要如实说没找到"。
    # ★ 为什么排在销售各档**之后**：前面每一档都比它更具体（销售/报告/概念/算术/使用说明
    #   都已经各归各位），只有**所有档都没接住**、又确实点着资料的问题才轮到它。
    if doc_qa.is_document_fallback(text) and doc_qa.has_documents():
        return Route(INTENT_SYSTEM_HELP, MODE_HELP,
                     reason="点名了资料 —— 按资料检索回答（没命中就如实说资料里没有）",
                     confidence=0.7, fallback=True)

    # ── ⑧ 兜底：澄清（**绝不默认 sales_summary**）────────────────────
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
    "INTENT_JOINT_ANALYSIS",
    "INTENT_REPORT_GENERATION",
    "INTENT_SALES_ANALYSIS",
    "INTENT_SYSTEM_HELP",
    "MODE_ANALYSIS",
    "MODE_BY_INTENT",
    "MODE_CLARIFY",
    "MODE_DIRECT",
    "MODE_GENERAL",
    "MODE_HELP",
    "MODE_JOINT",
    "MODE_REPORT",
    "MODES_WITHOUT_SECTION_WHAT",
    "NON_SALES_INTENTS",
    "SECTIONS_BY_MODE",
    "Route",
    "classify",
]
