"""intent.py · 把大白话解析成 **Intent JSON**（TASK-004 链路的第一步）。

════════════════════════════════════════════════════════════════════════
【它做两件事，但只有一件允许 LLM 参与】
════════════════════════════════════════════════════════════════════════
    ① 自然语言 → Intent JSON          ← LLM（**只输出结构，不输出数字**）
    ② Intent JSON → 校验过的参数对象   ← **代码**（pydantic，extra="forbid"）

第 ② 步刻意不交给 LLM 自查："让写答案的人自己批改"不算校验。
LLM 给什么形状，这里就用 schema 卡死：多一个字段、日期格式不对、top_n 越界 —— 一律拒绝。

════════════════════════════════════════════════════════════════════════
【五个 Intent（TASK-004 三个 + TASK-005 两个）】
════════════════════════════════════════════════════════════════════════
    sales_summary   某时间段 → 销售额 / 订单数 / 客户数（调既有 metrics + executor）
    sales_trend     某时间段 → 按日或按周的销售额序列（确定性 groupby）
    top_products    某时间段 → 产品 TOP N（确定性分组排序）
    sales_compare   两个时间段比大小 + 可选归因（TASK-005）——
                    **归因不是第三个 Intent，而是它的一个受控参数** `attribution_dimension`。
                    评审明确要求别开 `sales_attribution`：那会一路裂成
                    sales_country_attribution / sales_product_attribution…（Intent 爆炸）。
    sales_breakdown_by_country  某时间段各国家分布 TOP N（TASK-005）——
                    只回答"这段时间谁卖得多"，**不承担变化归因**（那是 sales_compare 的事）。

外加两个**非计算**分支：
    unsupported     问的是数据里根本没有的维度（区域/省份/门店/毛利…）→ 明确告知不支持
    （解析不出来则是异常路径，由 service.py 落成 status="error"，不瞎猜）

════════════════════════════════════════════════════════════════════════
【降级：没有 key 时怎么解析】
════════════════════════════════════════════════════════════════════════
`parse()` 优先走 LLM；LLM 不可用（无 key/SDK 缺失/调用失败）时**不编造**，
改走 `parse_by_keywords()`（纯规则：挑日期、挑"趋势/排行"这类关键词），
并且 service.py 会在回答里**明确标注"未接 LLM"**（AC-05）。
关键词版能覆盖的grossly有限 —— 这是**故意**的：降级本来就该能力变弱，但必须诚实。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai import llm
# 报告形态（TASK-010）的常量**只在 report.py 声明一处**，这里只是引用 ——
# 周期、比较类型、趋势粒度的对应关系不许在解析层再抄一份。
from app.ai.report import PERIOD_COMPARISON, PERIOD_MONTHLY, PERIOD_WEEKLY, report_period
from app.ai.tools import dataset_bounds

# ════════════════════════════════════════════════════════════════════════
# Intent 的名字（**白名单**：不在这个元组里的 intent 一律拒绝）
# ════════════════════════════════════════════════════════════════════════
INTENT_SALES_SUMMARY = "sales_summary"
INTENT_SALES_TREND = "sales_trend"
INTENT_TOP_PRODUCTS = "top_products"
INTENT_SALES_COMPARE = "sales_compare"
INTENT_SALES_BREAKDOWN_BY_COUNTRY = "sales_breakdown_by_country"
INTENT_UNSUPPORTED = "unsupported"

COMPUTE_INTENTS: tuple[str, ...] = (
    INTENT_SALES_SUMMARY,
    INTENT_SALES_TREND,
    INTENT_TOP_PRODUCTS,
    INTENT_SALES_COMPARE,
    INTENT_SALES_BREAKDOWN_BY_COUNTRY,
)
ALL_INTENTS: tuple[str, ...] = COMPUTE_INTENTS + (INTENT_UNSUPPORTED,)

# 周口径（与 DECISIONS.md:127 的"那一周 2011-11-21~11-27"一致：周一起算）
WEEK_START_WEEKDAY = 0                      # Monday

# ════════════════════════════════════════════════════════════════════════
# 数据里**根本没有**的维度 —— 代码级硬拦（不靠 LLM 自觉）
#
# 为什么要有这道代码闸门：施工指令写的是"绝对不许用 Country 偷偷代替"。
# LLM 完全可能"善解人意"地把「华南区」翻译成某个 Country —— 那是最糟的结果：
# 用户拿到一个看着像答案的数字，而它回答的是另一个问题。所以这里用**关键词硬拦**，
# 命中就直接回 unsupported，LLM 说什么都不作数（铁律1：理解需求可以靠 LLM，守边界必须靠代码）。
# ════════════════════════════════════════════════════════════════════════
_BANNED_DIMENSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("区域", "大区", "片区", "地区", "华南", "华东", "华北", "华中",
         "西南", "西北", "东北", "东南", "长三角", "珠三角",
         "北美", "南美", "欧洲", "亚洲", "亚太", "美洲", "大洲"),
        "数据集只有 8 列（InvoiceNo / StockCode / Description / Quantity / InvoiceDate / "
        "UnitPrice / CustomerID / Country），**没有「区域」字段**，也没有任何能推出区域的列。",
    ),
    (
        ("省份", "省", "城市", "门店", "店铺", "网点", "仓库", "渠道", "销售员", "业务员",
         "客户经理", "部门", "团队", "负责人",
         "上海", "北京", "广州", "深圳", "杭州", "成都", "天津", "重庆"),
        "数据集只有 8 列，**没有省份/城市/门店/渠道/销售员这类字段**，无法按它们拆分。",
    ),
    (
        ("毛利", "利润", "成本", "折扣", "税额", "税率", "运费"),
        "数据集只有 Quantity 与 UnitPrice 两列金额信息，**没有成本/毛利/折扣字段**，算不出这些。",
    ),
)


_SALESPERSON_REASON = (
    "数据集只有 8 列，**没有销售员/业务员/负责人字段**，无法按人拆分。"
)


class IntentError(ValueError):
    """Intent 解析/校验失败（带机器可读 code）。**不猜、不降级成随便一个 Intent**。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ════════════════════════════════════════════════════════════════════════
# 参数 schema（**校验在这里，不在 LLM 那边**）
# ════════════════════════════════════════════════════════════════════════
class _DayRangeParams(BaseModel):
    """三个计算型 Intent 共用的时间参数（含首尾全天，口径同 D16-2）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: _dt.date
    end: _dt.date

    def resolved(self) -> tuple[_dt.date, _dt.date]:
        if self.start > self.end:
            raise IntentError(
                "intent_invalid_params",
                f"起始日期晚于结束日期：{self.start} > {self.end}",
            )
        return self.start, self.end


class SalesSummaryParams(_DayRangeParams):
    pass


class SalesTrendParams(_DayRangeParams):
    granularity: Literal["day", "week"] = "day"


class TopProductsParams(_DayRangeParams):
    top_n: int = Field(default=5, ge=1, le=20)


class CountryBreakdownParams(_DayRangeParams):
    top_n: int = Field(default=5, ge=1, le=20)


class SalesCompareParams(BaseModel):
    """两区间比较的受控参数（**归因是这个参数的一个取值，不是新 Intent**）。

    `attribution_dimension` 用 Literal 卡死：LLM 想按"区域/省份/门店"归因，
    在这里就被拒（连 `region` 这个词都进不来）—— 从参数层杜绝"拿国家顶替区域"。

    区间一律用**绝对日期**：LLM 只负责把"上个月"翻译成日期，翻译完就由代码接管，
    `resolve_compare_windows()` 不会再让 LLM 解释任何日期（AC-02）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_type: Literal["custom", "wow", "mom", "yoy"] = "custom"
    current_start: _dt.date | None = None
    current_end: _dt.date | None = None
    previous_start: _dt.date | None = None
    previous_end: _dt.date | None = None
    attribution_dimension: Literal["country", "stock_code"] | None = None

    def resolved(self) -> None:
        """语义校验：**要么成对给，要么都不给**（半截日期是不合法的输入）。"""
        for label, start, end in (
            ("本期", self.current_start, self.current_end),
            ("上一期", self.previous_start, self.previous_end),
        ):
            if (start is None) != (end is None):
                raise IntentError(
                    "intent_invalid_params",
                    f"{label}区间只给了一半（start={start} / end={end}）—— 必须成对给。",
                )
            if start is not None and end is not None and start > end:
                raise IntentError(
                    "intent_invalid_params",
                    f"{label}区间的起始日期晚于结束日期：{start} > {end}",
                )
        if self.comparison_type == "custom" and self.current_start is None:
            raise IntentError(
                "intent_invalid_params",
                "比较类型是 custom，但没给出两个区间（至少要给本期区间的起止日期）。",
            )
        if self.previous_start is not None and self.current_start is None:
            raise IntentError(
                "intent_invalid_params",
                "给了上一期区间却没给本期区间 —— 比较必须有个基准。",
            )


PARAM_MODELS: dict[str, type[BaseModel]] = {
    INTENT_SALES_SUMMARY: SalesSummaryParams,
    INTENT_SALES_TREND: SalesTrendParams,
    INTENT_TOP_PRODUCTS: TopProductsParams,
    INTENT_SALES_COMPARE: SalesCompareParams,
    INTENT_SALES_BREAKDOWN_BY_COUNTRY: CountryBreakdownParams,
}


class ParsedIntent(BaseModel):
    """LLM 吐出来的那坨 JSON 的**受控**形态。

    `params` 在这里仍是"原始 dict"（还没过类型校验）—— 它由 `validated_params()` 专门校验。
    为什么分两步：解析失败与参数非法要给出**不同的**错误码，混在一起前端没法分辨。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str
    params: dict[str, Any] = Field(default_factory=dict)
    # LLM 自己声明的"我做了哪些假设"（比如没给时间就用全区间）—— 原样透传给用户看，不藏着
    assumptions: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    # TASK-010：**输出形态**标记（""=普通问答，weekly/monthly=要一份报告）。
    # 注意它**不是**第 6 个 intent —— 报告走的仍是 sales_compare 的计算，
    # 只是把输出从"几张指标卡"变成"一份结构化报告"（见 report.py 开头的理由）。
    report: Literal["", "weekly", "monthly"] = ""

    def validated_params(self) -> BaseModel:
        """按 intent 取出对应 schema 校验参数。**多字段/少字段/类型不对 → 明确报错**。"""
        if self.intent not in PARAM_MODELS:
            raise IntentError(
                "intent_unknown",
                f"不认识的 intent：{self.intent!r}（只支持 {list(COMPUTE_INTENTS)} 和 {INTENT_UNSUPPORTED}）",
            )
        model = PARAM_MODELS[self.intent]
        try:
            validated = model.model_validate(self.params)
            validated.resolved()          # 顺带查"起始日期是否晚于结束日期"，别留到工具里去炸
            return validated
        except ValidationError as exc:
            raise IntentError(
                "intent_invalid_params",
                f"{self.intent} 的参数不合法：{_format_validation_error(exc)}",
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "params": _jsonable(self.params),
            "assumptions": list(self.assumptions),
            "confidence": self.confidence,
            "reason": self.reason,
            # 留痕：这次问答走的是不是"报告形态"（前端不渲染，但记录里必须有）
            "report": self.report,
        }


def _jsonable(value: Any) -> Any:
    """把 date 之类转成 JSON 能落盘的形态（落 state/conversations.json 用）。"""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc", ())) or "(根)"
        parts.append(f"{loc}: {item.get('msg')}")
    return "；".join(parts) or str(exc)


# ════════════════════════════════════════════════════════════════════════
# 系统提示词
# ════════════════════════════════════════════════════════════════════════
def system_prompt() -> str:
    """把"数据现实"如实写进提示词 —— 模型知道边界，才可能正确回答"不支持"。"""
    first, last = dataset_bounds()
    return f"""你是销售数据问答的**意图解析器**。你的唯一输出是一段 JSON，不要解释、不要 markdown 代码块、不要多余文字。
**你不做任何计算、不做任何排序、不判断"主要贡献者"** —— 那些由程序做。你只把问题翻译成结构化参数。

可用 intent 只有下面这 6 个，多一个都不许编（**没有 sales_attribution 这个 intent** ——
"归因"是 sales_compare 的一个参数）：
1. sales_summary —— 问某时间段的销售额/订单数/客户数。params: {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}}
2. sales_trend   —— 问某时间段按日或按周的趋势走势。params: {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD","granularity":"day"|"week"}}
3. top_products  —— 问某时间段卖得最好的产品排行。params: {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD","top_n":整数(1-20)}}
4. sales_compare —— 问**两个时间段之间**的差别/变化/增减/对比，或问"变化主要是谁造成的"。
   params: {{
     "comparison_type": "custom"|"wow"|"mom"|"yoy",
     "current_start":"YYYY-MM-DD","current_end":"YYYY-MM-DD",       ← 本期（可省略，见下）
     "previous_start":"YYYY-MM-DD","previous_end":"YYYY-MM-DD",      ← 上一期（可省略）
     "attribution_dimension": null|"country"|"stock_code"            ← 只有问"谁造成的/哪个国家/哪个商品"时才填
   }}
5. sales_breakdown_by_country —— 问**某一个时间段内**各个国家的销售额分布/占比/排名。
   params: {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD","top_n":整数(1-20)}}
6. unsupported   —— 问题涉及数据里不存在的维度/指标时用它。params: {{}}，并在 reason 里说明缺什么。

**最容易犯的错，先看这一条（比下面的规则都重要）**：
- 「比较一下2011年11月和2011年10月的销售额」「2011年11月比2011年10月销售额增长了多少」
  「2011年11月和2011年10月的销售额哪个高」「11月对比10月的销售额」
  「2011年11月和10月销售额对比」—— **这些全部是 sales_compare**。
- **绝对不许**把两个时间段合并成一个区间交给 sales_summary。
  `sales_summary(start=2011-10-01, end=2011-11-30)` 会算成两个月**之和**，
  那是答非所问的数字，比拒绝回答糟糕得多。
- 判断口径：句子里出现「和 / 与 / 比 / 对比 / 相比 / 哪个高 / 增长了多少 / 差多少 / 去年同期」
  并且指向**两个不同时间段** → 一律 sales_compare。
- 只有**一个**时间段、且没有比较语义（"11 月一共卖了多少"）才用 sales_summary。

sales_compare 的 comparison_type 怎么选（**这一条最容易错，请严格照做**）：
- 用户明确给了两个区间（"比较 2011-11-01 到 11-15 与 2011-10-01 到 10-15"）→ "custom"，并把两个区间都填进 params。
- 说"本周/这周 vs 上周"或只给了一个区间但要跟上一周比 → "wow"。
- 说"本月/这个月 vs 上月"或只给了一个区间但要跟上一月比 → "mom"。
- 说"同比/去年同期/和去年比" → "yoy"。
- **只要 comparison_type 不是 custom，就不要自己算上一期的日期**：上一期由程序推导
  （用户给了哪一期就填哪一期，没给就留空）。你填错日期比留空更糟。
- "哪些国家推动了变化/变化主要来自哪个国家" → sales_compare + attribution_dimension="country"。
- "哪些商品造成了变化" → sales_compare + attribution_dimension="stock_code"。
- 问"某个时间段内各国卖了多少"（只有一个时间段、没有"变化/对比"）→ sales_breakdown_by_country。

报告类问题（"做一份周报/月报/出一份报告/汇报"）：
- **不要**返回 unsupported —— "报告"是一种**输出形态**，不是数据里没有的维度。
- 按上面的规则给一条最接近的解析就行：说"周报"的按每周（wow）、说"月报"的按每月（mom）。
- **报告区间一律由程序重新解析**（周报取最近一个完整自然周、月报取最近一个完整自然月），
  你填的日期不会被采用 —— 所以不确定就留空，别硬编一个"半截周"的区间出来。

数据集事实（**必须严格按这个来，不许假装知道更多**）：
- 只有 8 列：InvoiceNo / StockCode / Description / Quantity / InvoiceDate / UnitPrice / CustomerID / Country
- 时间范围：{first} ~ {last}（注意：最后一天不是月末，**最后一个月/周是不完整的**）
- **没有「区域」「省份」「城市」「门店」「渠道」「销售员」「毛利」「成本」这些字段**
- 用户问「华南/华东/大区/某省/某门店/毛利」这类 → 必须返回 unsupported，
  **绝对不许**用 Country（国家）或其它字段"代替"回答 —— 那是答非所问。
  「国家」是合法维度（数据里有 Country），「区域」不是 —— 两者绝不可混。
- 「今天」以数据集最后一天 {last} 为基准（数据是历史数据，不是实时数据）。

参数规则：
- 时间一律解析成**绝对日期** YYYY-MM-DD。相对说法（"上个月""第三周"）也换算成绝对日期。
- 「X月」= 该月 1 日 ~ 该月最后一天。「X月第N周」= 该月内第 N 个完整周，**一周从周一开始**。
- 用户**没给**时间范围时：start={first}、end={last}，并在 assumptions 里写明"未指定时间范围，已用数据集全区间"。
  （**sales_compare 例外**：没给区间就按上面的规则留空，让程序去推。）
- granularity 没说就 "day"；top_n 没说就 5。
- assumptions：数组，写你为理解问题做的每一个**自行判断**（如"上个月"按数据集最后一天倒推）。
  没做判断就空数组。**不要**把用户已经说清楚的东西再复述一遍。

输出 JSON 的字段：intent(字符串)、params(对象)、assumptions(数组)、confidence(0~1 的小数)、reason(字符串，仅 unsupported 时填)。
"""


# ════════════════════════════════════════════════════════════════════════
# 解析入口
# ════════════════════════════════════════════════════════════════════════
def parse(question: str, *, allow_llm: bool = True) -> tuple[ParsedIntent, dict[str, Any]]:
    """解析问题 → (Intent, 解析过程信息)。

    返回的第二个值是**留痕**用的：走了哪条路（llm / keyword）、失败原因。
    这个信息会跟着回答一起落进 `state/conversations.json` —— 用户要能看出
    "这次回答是不是 LLM 在场时给的"（AC-05）。
    """
    question = (question or "").strip()
    if not question:
        raise IntentError("intent_empty_question", "问题不能为空")

    # ① 代码硬闸门：数据里没有的维度，LLM 说得再圆也不作数
    blocked = guard_unsupported(question)
    if blocked is not None:
        return blocked, {"source": "guard", "fallback": None, "llm_error": None}

    # ② LLM 解析
    if allow_llm and llm.available():
        try:
            raw = llm.chat(system_prompt(), question)
            parsed = _intent_from_json(raw)
            # **收口闸门①（TASK-010）**：问题要的是"一份报告"却被答成零散指标卡
            # —— 一律改成报告形态（真实事故见 enforce_report_semantics 的注释）。
            after_report = enforce_report_semantics(parsed, question)
            # **收口闸门②（TASK-005）**：LLM 把"两个区间比大小"答成单区间（合并求和）时，
            # 在这里被代码拉回来 —— 见 enforce_comparison_semantics 的注释。
            # 顺序：报告闸门在前（它不是比较类问题就不受影响；是报告就轮不到比较闸门出手）。
            enforced = enforce_comparison_semantics(after_report, question)
            return enforced, {
                "source": "llm",
                "model": llm.model_name(),
                "raw": raw[:4000],
                "fallback": None,
                "llm_error": None,
                "comparison_override": enforced is not after_report,
                "report_override": after_report is not parsed,
            }
        except (llm.LLMError, IntentError) as exc:
            # 不在这里静默吞掉：先记下来，再尝试关键词降级，并把失败原因一路带给前端
            llm_error = {"code": getattr(exc, "code", "llm_error"), "message": str(exc)}
            if isinstance(exc, IntentError) and exc.code == "intent_unknown":
                llm_error = {"code": exc.code, "message": exc.message}
    elif not allow_llm:
        # 调用方**显式关掉**了模型（use_llm=false：测试 / 强制降级演示），
        # 这与"环境里没有模型"是两回事：SDK 可能装得好好的、key 也配着，
        # 只是这次没让它上场。说成"组件未就绪"就是在讲一句关于系统状态的假话。
        llm_error = {
            "code": "llm_disabled",
            "message": "本次调用显式关闭了模型（allow_llm=false）—— 只做确定性计算，不编造推断",
        }
    else:
        llm_error = {
            "code": "llm_not_configured" if not llm.api_key() else "llm_sdk_missing",
            "message": "本次没有可用的模型服务（未配置）—— 只做确定性计算，不编造推断",
        }

    # ③ 降级：关键词匹配（能力弱，但绝不编）
    parsed = parse_by_keywords(question)
    if parsed is None:
        # 报告请求：区间由程序推，不需要关键词认出日期 —— 所以这里能直接给出一条完整的解析
        if report_period(question):
            parsed = build_report_intent(question)
        # 比较类问题（比如"比较 9 月、10 月、11 月"，本版只做两区间）走专门的话术：
        # 明确告诉用户"不合并"，而不是笼统的"没听懂"。
        elif looks_like_comparison(question):
            raise _comparison_unparseable()
        else:
            raise IntentError(
                "intent_unparseable",
                f"没听懂这个问题，也不知道该调哪个工具。当前支持："
                f"①某时间段卖了多少 ②某时间段卖得怎么样（趋势） ③某时间段卖得最好的产品 "
                f"④两个时间段比大小（含按国家/商品归因） ⑤某时间段各国销售额分布 "
                f"⑥做一份周报/月报。"
                f"（LLM 未能参与解析：{llm_error['message']}）",
            )
    # 同一两道收口闸门（降级路径也要过，顺序与 LLM 路径一致）
    parsed = enforce_report_semantics(parsed, question)
    parsed = enforce_comparison_semantics(parsed, question)
    parsed = parsed.model_copy(update={"assumptions": tuple(parsed.assumptions) + ("由关键词匹配降级解析（LLM 未参与）",)})
    return parsed, {"source": "keyword", "model": None, "raw": None, "fallback": True, "llm_error": llm_error}


def _intent_from_json(raw: str) -> ParsedIntent:
    """从 LLM 输出里抠出 JSON → ParsedIntent。抠不出来就是解析失败，**不猜**。"""
    payload = _extract_json(raw)
    if payload is None:
        raise IntentError("intent_unparseable", f"LLM 没返回可识别的 JSON（前 200 字：{raw[:200]!r}）")
    intent = str(payload.get("intent") or "").strip()
    if intent not in ALL_INTENTS:
        raise IntentError(
            "intent_unknown",
            f"LLM 给了不支持的 intent：{intent!r}（白名单：{list(ALL_INTENTS)}）",
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise IntentError("intent_invalid_params", f"params 必须是对象，收到 {type(params).__name__}")
    assumptions = payload.get("assumptions") or []
    if not isinstance(assumptions, (list, tuple)):
        assumptions = [str(assumptions)]
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ParsedIntent(
        intent=intent,
        params=params,
        assumptions=tuple(str(item) for item in assumptions),
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(payload.get("reason") or ""),
    )


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    """容忍三种常见形态：纯 JSON、```json 围栏、前后带闲话。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError):
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


# ════════════════════════════════════════════════════════════════════════
# 代码硬闸门 + 关键词降级
# ════════════════════════════════════════════════════════════════════════
def guard_unsupported(question: str) -> ParsedIntent | None:
    """命中"数据里没有的维度"关键词 → 直接判 unsupported；否则 None。

    这是**代码**的判断，不是 LLM 的 —— 见文件顶部 _BANNED_DIMENSIONS 的说明。
    """
    for keywords, reason in _BANNED_DIMENSIONS:
        hits = [word for word in keywords if word in question]
        if hits:
            return ParsedIntent(
                intent=INTENT_UNSUPPORTED,
                params={},
                assumptions=(),
                confidence=1.0,
                reason=f"你问到了「{'/'.join(hits)}」。{reason}",
            )
    match = _SALESPERSON_RE.search(question)
    if match:
        return ParsedIntent(
            intent=INTENT_UNSUPPORTED,
            params={},
            assumptions=(),
            confidence=1.0,
            reason=f"你问到了「{match.group(0)}」。{_SALESPERSON_REASON}",
        )
    return None


# 月粒度：`2011年11月` / `2011-11` / `2011/11月`
_MONTH_RE = re.compile(r"(20\d{2})\s*[年\-/]\s*(\d{1,2})\s*月?")
# 完整日期两种写法：`2011-11-21` 与 `2011年11月21日`（后者年份可省：`11月21日`）
_ISO_DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
_CN_DATE_RE = re.compile(r"(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")

_TREND_WORDS = ("趋势", "走势", "变化", "曲线", "逐日", "逐周", "按天", "按日", "按周", "每天", "每周")
_RANK_WORDS = ("排行", "排名", "top", "TOP", "Top", "卖得最好", "最好卖", "畅销", "热销", "销量最高", "前几", "最畅销")
_SUMMARY_WORDS = ("多少", "总额", "一共", "总共", "合计", "销售额", "营业额", "卖了多少", "卖了多少钱", "订单", "客户数")

# ── TASK-005 的关键词（降级路径用）─────────────────────────────────────────
# 「对比」类词表：命中就**必须**走 sales_compare，绝不能掉进 summary ——
# 否则"比较 11 月和 10 月"会被当成"11 月~10 月整段"汇总，给出一个**答另一个问题**的数字，
# 那比"没听懂"糟糕得多。
_COMPARE_WORDS = (
    "对比", "相比", "环比", "同比", "比较", "增长", "下降", "上升", "减少",
    "涨幅", "跌幅", "同期", "去年同期", "多了多少", "少了多少",
    # 归因类词（"贡献/推动/造成"）本质上也是"两个时段之间"的问题，一并算比较类
    "贡献", "推动", "造成", "拉动",
)
_COUNTRY_WORDS = ("国家", "各国", "国别", "按国家")
_ATTRIBUTION_WORDS = ("贡献", "推动", "造成", "拉动", "主要来自", "主要是谁", "哪些国家", "哪个国家")
_PRODUCT_WORDS = ("产品", "商品", "货号", "编码", "SKU", "sku")
_JOINER_RE = re.compile(r"[到至~～—－]")
# 「张三销售」这种"按人"的问法。前后文限定得很严，**不能误伤**「销售额/销售量/销售趋势/
# 销售占比/销售金额」这些正常指标词 —— 所以要求"销售/业务"前面有 2~3 个汉字（人名），
# 后面不是 额/量/收/单/趋/占… 这类指标后缀。
# ⚠️ TASK-010 补课：后面跟「数据 / 周报 / 月报 / 报告 / 汇总」时也**不是**人名 ——
#    否则「帮我根据本星期的销售数据做一份销售周报」会被判成"按销售员拆分"而被拒答（真实误伤）。
_SALESPERSON_SUFFIX_BAN = "额量收单增环同趋情部变现占金排人数据周月报汇表总明日年季"
_SALESPERSON_RE = re.compile(
    rf"[一-龥]{{2,3}}(?:销售|业务)(?![{_SALESPERSON_SUFFIX_BAN}])"
)
# 只写了「X月」（没写年份）→ 年份沿用前一个日期（与 `_dates_from_text` 同一套规则）
_BARE_MONTH_RE = re.compile(r"(?<![\d年\-/])(\d{1,2})\s*月")


def _is_comparison(text: str) -> bool:
    """是不是"两个时间段比大小"的问题？

    「占比」里的"比"不算（那是分布问题）—— 这个豁免必须留着，
    否则「各国销售额占比」会被误判成比较。
    """
    if any(word in text for word in _COMPARE_WORDS):
        return True
    return "比" in text and "占比" not in text


# 「两个时间段放在一起说」的信号词 —— 比 _COMPARE_WORDS 更宽。
# 只用于**闸门**（判断"解析成单区间是不是搞错了"），不用来决定走哪个工具
# —— 走哪个工具仍是 _compare_by_keywords 的事。
_COMPARISON_HINTS = (
    *_COMPARE_WORDS,
    "哪个高", "哪个多", "哪个大", "哪个低", "哪个好", "谁高", "谁多", "谁卖得多",
    "高多少", "多多少", "少多少", "差多少", "相差", "多卖", "少卖",
    "涨了", "跌了", "更高", "更低", "更多", "更少",
)
# 只吃**一个**时间窗口的 Intent —— 天生不该接"两个区间比大小"的问题
_SINGLE_WINDOW_INTENTS = (
    INTENT_SALES_SUMMARY, INTENT_SALES_TREND, INTENT_TOP_PRODUCTS, INTENT_SALES_BREAKDOWN_BY_COUNTRY,
)


def looks_like_comparison(question: str) -> bool:
    """问题是不是"把两个时间段放在一起比"？—— **代码判**，不看 LLM 的脸色。"""
    text = question or ""
    if any(word in text for word in _COMPARISON_HINTS):
        return True
    return "比" in text and "占比" not in text and "比如" not in text


def enforce_comparison_semantics(parsed: ParsedIntent, question: str) -> ParsedIntent:
    """**收口闸门**：问题里有比较语义，解析结果却是"单区间"的 Intent → 一律纠正。

    为什么必须有这道闸门（真实事故）：真 DeepSeek 面对
    「比较一下2011年11月和2011年10月的销售额」时，会给出
    `sales_summary(start=2011-10-01, end=2011-11-30)` —— 把两个区间**合并求和**，
    于是答案变成 2,664,475.63（两个月之和）。这比"拒绝回答"危险得多：
    用户拿到一个看着像答案、其实答非所问的数字，且没有任何提示。

    纠正策略（不猜）：
      ① 能用关键词规则拆成两个区间 → 改走 sales_compare（并在 assumptions 里写明改过）
      ② 拆不出来（比如"比较 9 月、10 月、11 月"三个区间）→ **报错**，告诉用户该怎么问
    两条路都不会把两个区间悄悄加起来。

    第三道（同一个洞的另一半）：问句里**明摆着三个及以上**区间时，即使 LLM 给了
    `sales_compare` 也不算数 —— 实测它会默默只挑最近的两个月，把第三个月无声吃掉
    （问"比较 9 月、10 月、11 月"→ 只比 11 月 vs 10 月）。本版只做两区间，那就明说。
    """
    if not looks_like_comparison(question):
        return parsed
    first, last = dataset_bounds()

    # ③ 三个及以上区间：本版只做两区间 → 明说，别让"只比最近两个月"混过去
    if parsed.intent == INTENT_SALES_COMPARE and len(_date_ranges_from_text(question, first, last)) > 2:
        raise _comparison_unparseable()

    if parsed.intent not in _SINGLE_WINDOW_INTENTS:
        return parsed
    rescued = _compare_by_keywords(question, first, last)
    if rescued is not None:
        return rescued.model_copy(update={"assumptions": tuple(rescued.assumptions) + (
            f"（解析器原本给的是 {parsed.intent}，但问题里有「两个时间段比大小」的语义，"
            f"代码已改走 sales_compare —— 把两个区间合并成一个区间求和会给出答非所问的数字）",
        )})
    raise _comparison_unparseable()


def _comparison_unparseable() -> IntentError:
    """比较类问题没能确定两个区间时的**统一话术**（不猜、不合并）。"""
    return IntentError(
        "intent_unparseable",
        "这看起来是「两个时间段比大小」的问题，但没能确定到底比哪两个区间 —— "
        "不猜、也不把两个区间合并求和。请写成「比较 2011-11 和 2011-10 的销售额」"
        "或「2011-11-01 到 11-15 与 2011-10-01 到 10-15 的销售额对比」。",
    )


def _report_unparseable(count: int) -> IntentError:
    """报告请求里出现了多个时间段时的**统一话术**（不挑一个凑合）。"""
    return IntentError(
        "intent_unparseable",
        f"一份报告只能有**一个**本期区间，但问题里出现了 {count} 个时间段 —— 不猜用哪一个。"
        "请写明要报告的范围，例如「把 2011-11-01 到 2011-11-30 做成月报」"
        "或「帮我根据本星期的销售数据做一份销售周报」。",
    )


def build_report_intent(question: str) -> ParsedIntent:
    """「做一份周报/月报」→ `sales_compare`（周报 wow / 月报 mom）+ `report` 标记。

    区间**由程序解析**（`tools.resolve_compare_windows`），LLM 连日期都不碰：
      · 问题里写了明确区间 → 用用户的（程序不动它）；
      · 没写 → 最近一个完整自然周/月（被数据边界切断时退到最近完整周期，由工具写明原因）。
    「本星期」这种相对说法**不交给 LLM 翻译**：它多半会翻成一个被数据边界切断的半截周
    （数据集最后一天是周五），拿 5 天去比 7 天的"周环比"是假数字。
    """
    period = report_period(question)
    if not period:
        raise IntentError("intent_unparseable", "这个问题不是一个报告请求。")

    first, last = dataset_bounds()
    ranges = _date_ranges_from_text(question, first, last)
    if len(ranges) > 1:
        raise _report_unparseable(len(ranges))

    params: dict[str, Any] = {
        "comparison_type": PERIOD_COMPARISON[period],
        "attribution_dimension": None,
    }
    if ranges:
        start, end = ranges[0]
        params["current_start"] = start.isoformat()
        params["current_end"] = end.isoformat()
        window_note = f"报告区间取自问题里写明的 `{start} ~ {end}`（程序没有改它）。"
    else:
        kind = "周报" if period == PERIOD_WEEKLY else "月报"
        window_note = (f"问题里没有写明报告区间，按{kind}口径取**最近一个完整周期**"
                       f"（由程序解析，日期不由模型解释）。")

    return ParsedIntent(
        intent=INTENT_SALES_COMPARE,
        params=params,
        assumptions=(window_note,),
        confidence=0.9,
        report=period,
    )


def enforce_report_semantics(parsed: ParsedIntent, question: str) -> ParsedIntent:
    """**收口闸门**：问题要的是"一份报告"，解析结果却是零散的单点问题 → 一律改成报告形态。

    为什么必须有这道闸门（真实事故）：用户问
    「帮我根据本星期的销售数据做一份销售周报」，LLM 给的是 `sales_summary` 之类的单点意图，
    于是页面上只有几张指标卡 —— 用户要的**一份周报**根本没有出现（"我要求的是周报，
    就该给我一份周报"）。报告请求由代码收口：只要是报告请求，一律走 `sales_compare`
    （周报 = wow / 月报 = mom）+ `report` 标记，区间由程序推。

    与「报告」无关的问题**原样返回**（这道闸门对既有 5 个 intent 零影响）。
    """
    period = report_period(question)
    if not period:
        return parsed
    if parsed.report == period:
        return parsed          # 已经是报告形态 → 原样返回（这道闸门是幂等的）
    # 一律按报告口径**重建**（不是"在 LLM 的结果上打补丁"）：
    # 即使 LLM 恰好也给了 sales_compare，它带的多半是自己翻译的半截区间（如"本星期"→ 5 天），
    # 报告区间一律重新由程序解析。
    report_intent = build_report_intent(question)
    note = (
        f"（解析器原本给的是 {parsed.intent}；问题要的是**一份报告**，代码已改走报告形态 —— "
        f"报告 = 按{'周' if period == PERIOD_WEEKLY else '月'}口径的两区间比较 + 多段组合输出，"
        f"不做第二套计算口径）"
    )
    return report_intent.model_copy(
        update={"assumptions": tuple(report_intent.assumptions) + (note,)}
    )


def _comparison_type_from_text(text: str) -> str:
    if "同比" in text or "同期" in text or "去年" in text:
        return "yoy"
    if "周环比" in text or "本周" in text or "这周" in text or "上周" in text or "每周" in text:
        return "wow"
    if "环比" in text or "本月" in text or "这个月" in text or "上月" in text or "上个月" in text:
        return "mom"
    return "custom"


def _attribution_dimension_from_text(text: str) -> str | None:
    """只有"哪些国家/谁造成了变化"这种问法才带归因 —— 单纯比较不带。"""
    if not any(word in text for word in _ATTRIBUTION_WORDS):
        return None
    if any(word in text for word in _COUNTRY_WORDS):
        return "country"
    if any(word in text for word in _PRODUCT_WORDS):
        return "stock_code"
    return None


def _top_n_from_text(text: str, default: int = 5) -> int:
    match = re.search(r"(?:top|TOP|Top|前)\s*(\d{1,2})", text)
    if match:
        return max(1, min(20, int(match.group(1))))
    return default


def parse_by_keywords(question: str) -> ParsedIntent | None:
    """无 LLM 时的降级解析：认日期 + 认几个关键词。

    能识别的很有限，**这是刻意的**：降级就该能力变弱，但绝不能靠编。
    识别不出来返回 None，由 `parse()` 抛 unparseable（AC-02：不瞎猜）。
    """
    text = (question or "").strip()
    if not text:
        return None

    first, last = dataset_bounds()

    # ── ① 比较类问题优先判（"比较 11 月和 10 月"里也有"销售额"这种 summary 词）──
    if looks_like_comparison(text):
        return _compare_by_keywords(text, first, last)

    start, end, notes = _dates_from_text(text, first, last)

    # ── ② 某一时段的国家分布（**不承担变化归因**）───────────────────────
    if any(word in text for word in _COUNTRY_WORDS):
        return ParsedIntent(
            intent=INTENT_SALES_BREAKDOWN_BY_COUNTRY,
            params={"start": start.isoformat(), "end": end.isoformat(),
                    "top_n": _top_n_from_text(text)},
            assumptions=tuple(notes),
            confidence=0.5,
        )

    if any(word in text for word in _RANK_WORDS):
        return ParsedIntent(
            intent=INTENT_TOP_PRODUCTS,
            params={"start": start.isoformat(), "end": end.isoformat(), "top_n": 5},
            assumptions=tuple(notes),
            confidence=0.5,
        )
    if any(word in text for word in _TREND_WORDS):
        granularity = "week" if ("周" in text or "每周" in text) else "day"
        return ParsedIntent(
            intent=INTENT_SALES_TREND,
            params={"start": start.isoformat(), "end": end.isoformat(), "granularity": granularity},
            assumptions=tuple(notes),
            confidence=0.5,
        )
    if any(word in text for word in _SUMMARY_WORDS):
        return ParsedIntent(
            intent=INTENT_SALES_SUMMARY,
            params={"start": start.isoformat(), "end": end.isoformat()},
            assumptions=tuple(notes),
            confidence=0.5,
        )
    return None


def _compare_by_keywords(text: str, first: _dt.date, last: _dt.date) -> ParsedIntent | None:
    """降级路径下的两区间比较。**宁可返回 None（没听懂），也不猜一个区间去汇总。**

    能认的形状：
        「比较 2011-11 和 2011-10」                     → 两个区间 → custom
        「2011-11-01 到 11-15 与 2011-10-01 到 10-15」   → 两个区间（靠"到/至/~"合对）
        「2011年11月的环比」/「本月比上月」              → 一个或零个区间 → 由程序推上一期
        三个及以上区间（"比较 9 月、10 月、11 月"）      → **认输**（本版只做两区间）
    """
    comparison_type = _comparison_type_from_text(text)
    attribution = _attribution_dimension_from_text(text)
    ranges = _date_ranges_from_text(text, first, last)

    if len(ranges) > 2:
        return None                       # 三个区间比不了 —— 不挑两个凑合
    if len(ranges) == 2:
        current, previous = ranges[0], ranges[1]
        if comparison_type == "custom":
            comparison_type = "custom"    # 两个都给了 → 就用给的
    elif len(ranges) == 1:
        if comparison_type == "custom":
            return None                   # 只给一个区间、又没说要跟哪一期比 → 认输
        current, previous = ranges[0], None
    else:
        if comparison_type == "custom":
            return None
        current = previous = None

    params: dict[str, Any] = {"comparison_type": comparison_type, "attribution_dimension": attribution}
    if current is not None:
        params["current_start"], params["current_end"] = current[0].isoformat(), current[1].isoformat()
    if previous is not None:
        params["previous_start"], params["previous_end"] = previous[0].isoformat(), previous[1].isoformat()

    notes = [f"降级解析：识别为「{comparison_type}」两区间比较（比较类型与日期均由关键词规则得出）。"]
    return ParsedIntent(intent=INTENT_SALES_COMPARE, params=params, assumptions=tuple(notes), confidence=0.4)


def _date_ranges_from_text(
    text: str, first: _dt.date, last: _dt.date
) -> list[tuple[_dt.date, _dt.date]]:
    """把问句里出现的**每一个**时间段收成列表（比较类问题需要"两个区间"）。

    相邻两个时间点之间若夹着"到/至/~/—"就当**一个**区间（`11-01 到 11-15`），
    夹着"和/与/、/比"就当**两个**（`11 月和 10 月`）。
    """
    tokens: list[tuple[int, int, tuple[_dt.date, _dt.date]]] = []   # (起点, 终点, 窗口)
    taken: list[tuple[int, int]] = []

    for match in _ISO_DATE_RE.finditer(text):
        parsed = _safe_date(*(int(part) for part in match.groups()))
        if parsed:
            tokens.append((match.start(), match.end(), (parsed, parsed)))
            taken.append((match.start(), match.end()))

    for match in _CN_DATE_RE.finditer(text):
        year_text, month_text, day_text = match.groups()
        if any(start <= match.start() < end for start, end in taken):
            continue
        if year_text:
            year = int(year_text)
        else:
            previous = [item for item in tokens if item[0] < match.start()]
            year = previous[-1][2][0].year if previous else last.year
        parsed = _safe_date(year, int(month_text), int(day_text))
        if parsed:
            tokens.append((match.start(), match.end(), (parsed, parsed)))
            taken.append((match.start(), match.end()))

    for match in _MONTH_RE.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue                 # `2011-11-21` 里的 `2011-11` 不算一个整月
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            start = _dt.date(year, month, 1)
            tokens.append((match.start(), match.end(), (start, _next_month_start(year, month) - _dt.timedelta(days=1))))
            # 记进 taken：否则「2011 年 11 月」（年与月之间有空格）会被下面那条
            # "光写月份"的规则**再算一遍**，同一个月份变成两个区间
            taken.append((match.start(), match.end()))

    for match in _BARE_MONTH_RE.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue                 # `2011年11月` 里的 `11月` 已经算过了
        month = int(match.group(1))
        if not 1 <= month <= 12:
            continue
        earlier = [item for item in tokens if item[0] < match.start()]
        year = earlier[-1][2][0].year if earlier else last.year
        start = _dt.date(year, month, 1)
        tokens.append((match.start(), match.end(), (start, _next_month_start(year, month) - _dt.timedelta(days=1))))
        taken.append((match.start(), match.end()))

    tokens.sort(key=lambda item: item[0])
    ranges: list[tuple[_dt.date, _dt.date]] = []
    index = 0
    while index < len(tokens):
        if index + 1 < len(tokens) and _JOINER_RE.search(text[tokens[index][1]:tokens[index + 1][0]]):
            ranges.append((tokens[index][2][0], tokens[index + 1][2][1]))
            index += 2
        else:
            ranges.append(tokens[index][2])
            index += 1
    return ranges


def _dates_from_text(text: str, first: _dt.date, last: _dt.date) -> tuple[_dt.date, _dt.date, list[str]]:
    """从问句里抠一个日期区间；抠不到就用数据集全区间（并在 notes 里写明）。

    支持的写法（按优先级）：
        `2011-11-21 到 2011-11-27` / `2011年11月21日到11月27日`  → 区间
        `2011年11月21日` / `11月21日`                            → 单日
        `2011年11月` / `2011-11`                                → 整月
        什么都没有                                                → 数据集全区间（写明）
    """
    notes: list[str] = []
    found: list[tuple[int, _dt.date]] = []          # (在问句里的位置, 日期)

    for match in _ISO_DATE_RE.finditer(text):
        parsed = _safe_date(*(int(part) for part in match.groups()))
        if parsed:
            found.append((match.start(), parsed))

    for match in _CN_DATE_RE.finditer(text):
        year_text, month_text, day_text = match.groups()
        if year_text:
            year = int(year_text)
        elif found:
            year = max(found)[1].year             # 省了年份 → 继承前一个日期的年份
        else:
            year = last.year                      # 一个年份都没写 → 按数据集所在年份
        parsed = _safe_date(year, int(month_text), int(day_text))
        if parsed:
            found.append((match.start(), parsed))

    if found:
        ordered = [date for _, date in sorted(found, key=lambda pair: pair[0])]
        start, end = ordered[0], ordered[-1]
        if start == end:
            notes.append(f"问句里是一个单日，按 {start} 一天计算")
        else:
            notes.append(f"问句里是 {start} ~ {end} 这个区间")
        clamped, clamp_notes = _clamp_to_data(start, end, first, last)
        return *clamped, notes + clamp_notes

    month_match = _MONTH_RE.search(text)
    if month_match:
        year, month = int(month_match.group(1)), int(month_match.group(2))
        if 1 <= month <= 12:
            start = _dt.date(year, month, 1)
            end = _next_month_start(year, month) - _dt.timedelta(days=1)
            notes.append(f"问句只给了月份，按 {year} 年 {month} 月整月计算")
            clamped, clamp_notes = _clamp_to_data(start, end, first, last)
            return *clamped, notes + clamp_notes

    notes.append(f"问句里没有时间信息，已用数据集全区间 {first} ~ {last}")
    return first, last, notes


def _safe_date(year: int, month: int, day: int) -> _dt.date | None:
    try:
        return _dt.date(year, month, day)
    except ValueError:
        return None


def _clamp_to_data(
    start: _dt.date, end: _dt.date, first: _dt.date, last: _dt.date
) -> tuple[tuple[_dt.date, _dt.date], list[str]]:
    """把区间截到数据集边界内，并**如实写明截断过**（不悄悄改用户问的范围）。

    区间整个落在数据之外时**不缩成数据边界**（那答的是另一个问题）：原样返回，
    让计算结果里的 0 行配上"区间内没有任何交易"的说明 —— 那才是诚实的答法。
    """
    if end < first or start > last:
        return (start, end), [
            f"注意：所问区间（{start} ~ {end}）**完全在数据集范围（{first} ~ {last}）之外**，"
            f"因此不会有任何交易记录。"
        ]
    notes: list[str] = []
    if start < first:
        start = first
        notes.append(f"区间起点早于数据集起点，已截断到 {first}")
    if end > last:
        end = last
        notes.append(f"区间终点晚于数据集终点，已截断到 {last}")
    return (start, end), notes


def _next_month_start(year: int, month: int) -> _dt.date:
    return _dt.date(year + 1, 1, 1) if month == 12 else _dt.date(year, month + 1, 1)


__all__ = [
    "ALL_INTENTS",
    "COMPUTE_INTENTS",
    "INTENT_SALES_BREAKDOWN_BY_COUNTRY",
    "INTENT_SALES_COMPARE",
    "INTENT_SALES_SUMMARY",
    "INTENT_SALES_TREND",
    "INTENT_TOP_PRODUCTS",
    "INTENT_UNSUPPORTED",
    "IntentError",
    "PARAM_MODELS",
    "ParsedIntent",
    "CountryBreakdownParams",
    "SalesCompareParams",
    "SalesSummaryParams",
    "SalesTrendParams",
    "TopProductsParams",
    "WEEK_START_WEEKDAY",
    "build_report_intent",
    "enforce_comparison_semantics",
    "enforce_report_semantics",
    "guard_unsupported",
    "looks_like_comparison",
    "parse",
    "parse_by_keywords",
    "system_prompt",
]
