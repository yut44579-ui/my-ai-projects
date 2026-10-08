"""tests/test_fr007_arithmetic.py · FR-007 验收 A：纯算术走**受限 AST 计算器**。

════════════════════════════════════════════════════════════════════════
【这一条在防什么】
════════════════════════════════════════════════════════════════════════
施工前的真实复现：「1+1等于多少？」被当成销售汇总，去算了一张 7 行的销售表。
修法的两条硬要求（评审 GO 条件 3 + 施工指令 §四）：

  ① 数学**不经过 LLM**（不留"模型心算"这种不可追溯的路径）；
  ② **禁止 eval / exec** —— 用白名单 AST + 自己递归求值；
  ③ 资源攻击也要拦：`10 ** 100000000` 即使不执行用户代码，也会让进程算到天荒地老。

所以本文件分三段：
  A. 正常算术：答得对、答得快、且**根本不碰销售工具**
  B. 恶意输入：`__import__("os")` / `open("x")` 这类**必须安全拒绝**（并且真的没有执行）
  C. 资源与边界：长度/位数/深度/指数/除零/数量级，逐条都必须被拒
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import arithmetic, routing, service, tools  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """记录落临时盘：**绝不**写进仓库里的真实 state/。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """算术档本来就不该问模型；把模型通道关上，是为了让"没问模型"这件事可证。"""
    monkeypatch.setattr(service.llm, "available", lambda: False)
    return None


# ════════════════════════════════════════════════════════════════════════
# A. 正常算术（验收 A 的前半）
# ════════════════════════════════════════════════════════════════════════
def test_A01_一一等于多少走计算器且不碰销售工具(monkeypatch):
    """「1+1等于多少？」—— intent=arithmetic / mode=direct / 答案是 2。"""
    calls: list[str] = []

    def _spy(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"算术问题不该调用 {name}")
        return _inner

    monkeypatch.setattr(tools, "run_tool", _spy("run_tool"))
    monkeypatch.setattr(tools, "dataset_profile", _spy("dataset_profile"))

    record = service.ask("1+1等于多少？", use_llm=False)

    assert record["routing"]["intent"] == "arithmetic"
    assert record["response_mode"] == "direct"
    assert record["status"] == "ok"
    assert record["tool"] is None and record["facts"] is None
    assert record["sales_data_accessed"] is False
    assert calls == []                                     # 销售工具一次都没被碰
    sections = record["answer"]["sections"]
    assert [item["key"] for item in sections] == ["answer"]  # 不是旧的四段
    assert "2" in sections[0]["text"]
    assert record["answer"]["source"] == "deterministic"


def test_A02_乘法的结果与python自己算的逐位一致():
    """「12345×6789是多少？」—— 结果必须来自确定性计算器（拿 Python 自己算一遍对账）。"""
    record = service.ask("12345×6789是多少？", use_llm=False)
    text = record["answer"]["sections"][0]["text"]
    assert f"{12345 * 6789:,}".replace(",", "") in text.replace(",", "")
    assert str(12345 * 6789) in text                        # 83810205
    assert record["routing"]["intent"] == "arithmetic"
    assert record["facts"] is None                          # 没有"销售事实"这回事


def test_A03_半数写法与括号也认():
    for question, expected in (
        ("100/7是多少", arithmetic.format_number(100 / 7)),
        ("2**10等于多少", "1024"),
        ("(1+2)*(3+4)等于多少", "21"),
        ("1 + 1", "2"),                                     # 整句就是算式
        ("0.1+0.2等于多少", "0.3"),                          # 不留浮点尾巴
    ):
        result = arithmetic.solve(question)
        assert result is not None and result.ok, question
        assert expected in result.render(), (question, result.render())


def test_A04_销售问题不会被算术抢走():
    """识别必须**严进**：日期与业务词不能把销售问题误判成算术题。"""
    for question in (
        "2011年11月一共卖了多少？",
        "2011-11-01 到 2011-11-07 的销售趋势",              # 长得像减法，必须是销售问题
        "2011年11月和2011年10月的销售额对比",
        "销售额是多少",
    ):
        assert arithmetic.solve(question) is None, question
        assert routing.classify(question).intent != routing.INTENT_ARITHMETIC, question


def test_A05_幂指数炸弹不执行也不计算():
    """`10 ** 100000000` 必须在**求值之前**被拒（不是算到一半崩、更不是算完再比大小）。"""
    import time

    start = time.time()
    result = arithmetic.solve("10**100000000")
    elapsed = time.time() - start
    assert result is not None and not result.ok
    assert "指数太大" in result.error
    assert elapsed < 1.0, f"算得太久了（{elapsed:.2f}s）—— 说明真的去算了"


# ════════════════════════════════════════════════════════════════════════
# B. 恶意输入：安全拒绝，且**真的没有执行**
# ════════════════════════════════════════════════════════════════════════
MALICIOUS = (
    '__import__("os")',
    '__import__("os").system("whoami")',
    'open("x")',
    'open("C:/Windows/System32/drivers/etc/hosts","w")',
    "eval(\"1+1\")",
    "exec(\"import os\")",
    "(1).__class__.__bases__[0].__subclasses__()",
    "1+1; __import__('os')",
)


@pytest.mark.parametrize("question", MALICIOUS)
def test_B01_注入写法全部安全拒绝且不执行(question, monkeypatch):
    """四类恶意输入**全部安全拒绝**：既不执行代码，也不给"没看懂"含糊过去。

    这里把 `eval` / `exec` 两个"执行入口"换成炸弹：真要是走了它们，测试立刻炸。
    （不换 `__import__`：pytest 自己的 monkeypatch 就要用它，换了会误伤测试框架 ——
      "不许 import" 这条由下面的**静态检查**兜住，那比运行时打桩更彻底。）
    """
    def _never(*args, **kwargs):
        raise AssertionError(f"执行了不该执行的东西：{args!r} {kwargs!r}")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "eval", _never)
        patch.setattr(builtins, "exec", _never)
        result = arithmetic.solve(question)

    assert result is not None, f"{question} 应该被明确拒绝，而不是含糊地说「没看懂」"
    assert not result.ok, question
    assert result.error, question


def test_B01b_计算器源码里根本没有求值入口():
    """静态证据：`arithmetic.py` 里**没有** eval / exec / __import__ / compile 的调用。

    比运行时打桩更彻底：想加一行 `eval(user_input)`，这条就会红 —— 加不进去。
    """
    tree = ast.parse((PROJECT_ROOT / "app" / "ai" / "arithmetic.py").read_text(encoding="utf-8"))
    banned = {"eval", "exec", "__import__", "compile", "getattr", "globals", "locals"}
    called: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
            called.append(node.func.id)
    assert called == [], f"计算器里出现了求值入口：{called}"
    # 唯一允许的"把文本变代码"的一步是 ast.parse（它只建树、不执行）
    assert "ast.parse" in (PROJECT_ROOT / "app" / "ai" / "arithmetic.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("question", MALICIOUS)
def test_B02_恶意输入走完整链路也不产出数字(question, monkeypatch):
    """走 service 也一样：不进销售链路、不产出任何数字、不给 facts。"""
    record = service.ask(question, use_llm=False)
    # 有的写法被认成算术题（明确拒绝），有的连算式都不是（走澄清）——两种都必须**不碰数据**
    assert record["routing"]["intent"] in ("arithmetic", "clarify"), question
    assert record["facts"] is None and record["tool"] is None, question
    assert record["sales_data_accessed"] is False, question


def test_B03_被拒的算式记录里说得清为什么被拒():
    record = service.ask("1/0", use_llm=False)
    assert record["routing"]["intent"] == "arithmetic"
    assert record["response_mode"] == "direct"
    assert record["status"] == "unsupported"                # 答不了 → 明确说答不了
    assert "除数" in record["answer"]["sections"][0]["text"]
    assert "= " not in record["answer"]["sections"][0]["text"]   # 没有算出任何结果


# ════════════════════════════════════════════════════════════════════════
# C. 资源与边界（逐条都能说出被拒的原因）
# ════════════════════════════════════════════════════════════════════════
def test_C01_四道闸门逐条生效():
    cases = (
        ("1+" + "1+" * 100 + "1", "太长"),                             # 超长表达式
        ("12345678901234567890+1", "数字太长"),                        # 单个数字位数
        ("(((((((((1+1)))))))))", "嵌套太深"),                        # 括号深度
        ("9**9999", "指数太大"),                                       # 幂指数
        ("999999999999999*999999999999999", "超出我能算的范围"),        # 结果数量级
    )
    for question, expected in cases:
        result = arithmetic.solve(question)
        assert result is not None, question
        assert not result.ok, question
        assert expected in result.error, (question, result.error)


def test_C02_除零与取模零都拒():
    for question in ("1/0", "1÷0", "5%0", "1/(2-2)"):
        result = arithmetic.solve(question)
        assert result is not None and not result.ok, question
        assert "0" in result.error, (question, result.error)


def test_C03_闸门是常量而非常量之外的口子():
    """限制值都写在模块顶部，且**足够保守**（改大了会被这条看出来）。"""
    assert arithmetic.MAX_EXPRESSION_LENGTH <= 200
    assert arithmetic.MAX_LITERAL_DIGITS <= 20
    assert arithmetic.MAX_DEPTH <= 16
    assert arithmetic.MAX_POW_EXPONENT <= 10000
    assert arithmetic.MAX_ABS_RESULT <= 1e18


def test_C04_不做通用数学引擎():
    """明确不做：函数调用 / 单位换算 / 方程 / 科学计算 —— 白名单外一律拒绝。"""
    for question in ("sqrt(4)等于多少", "log(100)等于多少", "sin(0)等于多少", "x+1等于多少"):
        result = arithmetic.solve(question)
        assert result is None or not result.ok, question
