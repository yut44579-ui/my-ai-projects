"""tests/test_fr007_routing.py · FR-007 验收：Intent → mode 映射**冻结**、分类顺序、正反矩阵。

════════════════════════════════════════════════════════════════════════
【这一条钉的是"路由本身"，不是"某句话答得对不对"】
════════════════════════════════════════════════════════════════════════
  ① 6 Intent × 4 mode 的映射表冻结（施工指令 §三，**不许加**）；
  ② 分类顺序冻结：SYSTEM_HELP → ARITHMETIC → GENERAL_QA → REPORT_GENERATION
     → DATA_LOOKUP → SALES_ANALYSIS → clarify（顺序反了，"1+1等于多少"就会被销售吞掉）；
  ③ 反向矩阵：正常销售问题**不得**被判成 general / clarify；
  ④ 非销售问题**不得**进销售分析；
  ⑤ 分类失败默认**澄清**，绝不默认 sales_summary；
  ⑥ 路由层**不碰数据**：它连读数据的入口都不 import（结构性隔离，不靠自觉）。
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import routing  # noqa: E402

AI_DIR = PROJECT_ROOT / "app" / "ai"


# ════════════════════════════════════════════════════════════════════════
# ① 映射表冻结
# ════════════════════════════════════════════════════════════════════════
def test_01_六意图到四档的映射逐条冻结():
    assert routing.MODE_BY_INTENT == {
        "sales_analysis": "analysis",
        "data_lookup": "direct",
        "general_qa": "general",
        "arithmetic": "direct",
        "report_generation": "report",
        "system_help": "help",
        "clarify": "clarify",
    }
    # 多一个 intent 就多一条要维护的分支 —— 这里钉住"只许这七个名字"
    assert routing.ALL_INTENTS == (
        "sales_analysis", "data_lookup", "general_qa",
        "arithmetic", "report_generation", "system_help", "clarify",
    )
    assert set(routing.MODE_BY_INTENT) == set(routing.ALL_INTENTS)


def test_02_三档必须不生成旧SECTION_WHAT():
    assert set(routing.MODES_WITHOUT_SECTION_WHAT) == {"direct", "help", "general"}
    for mode in routing.MODES_WITHOUT_SECTION_WHAT:
        assert "what" not in routing.SECTIONS_BY_MODE[mode], mode
        assert routing.SECTIONS_BY_MODE[mode], mode


def test_03_分段约束表覆盖每一个mode():
    for mode in routing.ALL_MODES:
        assert mode in routing.SECTIONS_BY_MODE, mode
    # 三档非销售：一段就够（不许偷偷加"为什么/建议行动"）
    assert routing.SECTIONS_BY_MODE["direct"] == ("answer",)
    assert routing.SECTIONS_BY_MODE["general"] == ("answer",)
    assert routing.SECTIONS_BY_MODE["help"] == ("help",)


def test_04_非销售三类在销售工具之前结束分支():
    assert set(routing.NON_SALES_INTENTS) == {"system_help", "arithmetic", "general_qa"}
    for intent in routing.NON_SALES_INTENTS:
        assert routing.MODE_BY_INTENT[intent] != "analysis", intent


# ════════════════════════════════════════════════════════════════════════
# ② 分类顺序 + ③ 反向矩阵 + ④ 非销售不进销售
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "question,intent,mode",
    (
        # §八G 的浏览器矩阵 + §一 的三条真实事故
        ("你好", "general_qa", "general"),
        ("1+1等于多少？", "arithmetic", "direct"),
        ("12345×6789是多少？", "arithmetic", "direct"),
        ("什么是毛利率？", "general_qa", "general"),
        ("什么是 API", "general_qa", "general"),
        ("系统怎么导出", "system_help", "help"),
        ("怎么导出数据", "system_help", "help"),
        ("我放的周报在哪里", "system_help", "help"),
        ("本期销售额是多少？", "data_lookup", "direct"),
        ("2011年11月21日到11月27日一共卖了多少？", "data_lookup", "direct"),
        ("为什么本周比上周高？", "sales_analysis", "analysis"),
        ("帮我生成上周周报", "report_generation", "report"),
    ),
)
def test_05_关键问句的分类与档位(question, intent, mode):
    route = routing.classify(question)
    assert route.intent == intent, question
    assert route.response_mode == mode, question
    assert route.response_mode == routing.MODE_BY_INTENT[route.intent]


@pytest.mark.parametrize(
    "question",
    (
        "本月销售额", "本周销售额", "某客户销售额", "某产品销售额",
        "11月卖了多少", "英国卖了多少", "2011年11月的销售趋势",
        "2011年11月各国家销售额TOP5", "2011年11月卖得最好的5个产品",
        "2011年11月和10月的销售额对比", "比较 9月、10月、11月的销售额",
    ),
)
def test_06_反向矩阵_正常销售问题不得被误判(question):
    """正常销售问题**不许**被判成 general / clarify / arithmetic / help。"""
    route = routing.classify(question)
    assert route.intent in ("sales_analysis", "data_lookup"), (question, route.intent)
    assert route.response_mode in ("analysis", "direct"), (question, route.response_mode)


@pytest.mark.parametrize(
    "question",
    (
        "1+1等于多少？", "你好", "什么是毛利率？", "什么是 API",
        "系统怎么导出", "我放的周报在哪里", "怎么导出数据",
    ),
)
def test_07_非销售问题不得进入销售链路(question):
    route = routing.classify(question)
    assert route.intent not in ("sales_analysis", "data_lookup"), (question, route.intent)


def test_08_分类失败默认澄清而不是销售汇总():
    for question in ("今天天气怎么样", "帮我订一张去上海的机票", "讲个笑话"):
        route = routing.classify(question)
        assert route.intent == routing.INTENT_CLARIFY, question
        assert route.response_mode == routing.MODE_CLARIFY


def test_09_单关键词多少不再决定销售意图():
    """`多少` 只是**必要条件**之一：没有业务实体/时间维度，它什么也决定不了。"""
    assert routing.classify("1+1等于多少？").intent == "arithmetic"
    assert routing.classify("天上星星有多少").intent == "clarify"
    # 有了业务实体 + 时间，才算单值查询
    assert routing.classify("2011年11月销售额是多少").intent == "data_lookup"


def test_10_分析信号把单值查询挡回去():
    """带"最/排行/趋势/对比"的问题**不是**单值查询（否则会把一张表塞进单值档）。"""
    for question in (
        "2011年11月销售额最高的10个客户",
        "2011年11月各国家销售额TOP5",
        "2011年11月的销售趋势是多少",
        "2011年11月销售额最多的是哪个产品",
    ):
        assert routing.classify(question).intent == "sales_analysis", question


def test_11_位置类问题不是在要一份新报告():
    """「我放的周报在哪里」含"周报"二字，但它问的是位置 —— 不能被报告分支吞掉。"""
    assert routing.report_period("我放的周报在哪里") == "weekly"      # 既有的判据确实会命中
    assert routing.classify("我放的周报在哪里").intent == "system_help"   # 但顺序把它拦在前面
    assert routing.classify("帮我生成上周周报").intent == "report_generation"


# ════════════════════════════════════════════════════════════════════════
# ⑥ 路由层不碰数据（结构性）
# ════════════════════════════════════════════════════════════════════════
def _imported_modules(path: pathlib.Path) -> set[str]:
    """静态读出一个 .py 文件 import 了哪些模块（只看顶层 import 语句）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
            found.add(node.module)
    return found


@pytest.mark.parametrize("filename", ("routing.py", "arithmetic.py", "general.py"))
def test_12_非销售模块的调用图里没有销售工具(filename):
    """GO 条件 4：隔离靠**代码调用图**，不靠提示词。

    这三个文件里任何一处 import 到读数据的模块（tools / executor / loader / engine），
    这条就会红 —— 想加就得先想清楚"为什么非销售分支要读数据"。
    """
    banned = ("app.ai.tools", "app.engine", "app.ai.executor")
    imported = _imported_modules(AI_DIR / filename)
    for module in imported:
        assert not any(module == b or module.startswith(b + ".") for b in banned), \
            f"{filename} 里 import 了 {module} —— 非销售分支不该有能力读销售数据"


def test_13_路由是纯代码_同一句话永远是同一个结论():
    for question in ("1+1等于多少？", "本期销售额是多少？", "你好", "今天天气怎么样"):
        first = routing.classify(question)
        second = routing.classify(question)
        assert first == second, question
