"""tests/test_fr007_isolation.py · FR-007 安全回归：非销售分支**碰不到销售数据**。

════════════════════════════════════════════════════════════════════════
【为什么这条要用"炸弹"来测，而不是检查文本里有没有金额】
════════════════════════════════════════════════════════════════════════
评审原话（施工指令 §六）："因为现在安全链路是针对销售分析设计的，新增 non-sales 分支
很容易开出一条绕过数字闸门的新出口"，并明确要求：

    测试里用 monkeypatch 把 tools.run_tool / executor / loader 换成 fail_test，
    跑「什么是毛利率？」—— 只要触发了销售工具，测试直接失败。

这么测的道理：检查"回答里没有出现销售额"是**看结果**，而危险发生在**过程中** ——
只要有一行代码去读了当前数据，闸门就已经被绕过了，哪怕那次的输出恰好没露出数字。
所以这里把读数据的那四个入口**全部换成炸弹**，让"碰了数据"这件事当场炸出来。

（GENERAL_QA 的答案里提到"毛利率"这个词是**允许**的：概念解释与"读取当前业务数据"
是两件事。这条测的是后者。）
"""

from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import service, tools  # noqa: E402
from app.engine import executor, loader  # noqa: E402

# 非销售三类各取一句（§八B/C + §六④）
NON_SALES_QUESTIONS = (
    "你好",
    "1+1等于多少？",
    "12345×6789是多少？",
    "什么是毛利率？",
    "什么是 API",
    "怎么导出数据",
    "我放的周报在哪里",
)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(service.llm, "available", lambda: False)
    return None


class SalesDataBomb:
    """把读销售数据的每个入口换成炸弹，并记录有没有人碰过它们。"""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def _bomb(self, name, original):
        def _inner(*args, **kwargs):
            self.touched.append(name)
            raise AssertionError(f"非销售分支调用了 {name} —— 销售数据被读了")
        return _inner

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(tools, "run_tool", self._bomb("tools.run_tool", tools.run_tool))
        monkeypatch.setattr(tools, "dataset_profile",
                            self._bomb("tools.dataset_profile", tools.dataset_profile))
        monkeypatch.setattr(loader, "load_raw", self._bomb("loader.load_raw", loader.load_raw))
        monkeypatch.setattr(executor, "compute_sales_amount",
                            self._bomb("executor.compute_sales_amount", executor.compute_sales_amount))


@pytest.mark.parametrize("question", NON_SALES_QUESTIONS)
def test_非销售问题一次都不碰销售数据(question, monkeypatch):
    bomb = SalesDataBomb()
    bomb.install(monkeypatch)

    record = service.ask(question, use_llm=False)

    assert bomb.touched == [], f"{question} 碰了：{bomb.touched}"
    assert record["sales_data_accessed"] is False, question
    assert record["tool"] is None and record["facts"] is None, question
    assert record["data_profile"] is None, question
    assert record["status"] in ("ok", "unsupported"), (question, record["status"])
    assert record["answer"] is not None, question
    keys = [item["key"] for item in record["answer"]["sections"]]
    assert "what" not in keys, (question, keys)          # 旧 SECTION_WHAT 后端就不生成
    assert keys in (["answer"], ["help"]), (question, keys)


@pytest.mark.parametrize("question", NON_SALES_QUESTIONS)
def test_非销售问题的记录里没有销售事实字段(question, monkeypatch):
    """`facts` / `series` / `items` / 报告文档全都是 None —— 没有数据，就没有这些字段。"""
    bomb = SalesDataBomb()
    bomb.install(monkeypatch)
    record = service.ask(question, use_llm=False)
    for field in ("facts", "series", "items", "report_document", "params"):
        assert record[field] in (None, {}), (question, field, record[field])


def test_恶意算式也不借道销售链路(monkeypatch):
    """`__import__("os")` 这种输入既不能执行代码，也不能被丢进销售分析"当作问题理解"。"""
    bomb = SalesDataBomb()
    bomb.install(monkeypatch)
    for question in ('__import__("os")', 'open("x")', "1/0", "10**100000000"):
        record = service.ask(question, use_llm=False)
        assert bomb.touched == [], (question, bomb.touched)
        assert record["routing"]["intent"] == "arithmetic", question
        assert record["sales_data_accessed"] is False, question


def test_澄清档同样不读数据就不该给数据画像(monkeypatch):
    """「今天天气怎么样」走到"没听懂"那一条时，记录里不该凭空多出一份数据画像。

    （这条是**如实**要求：既然这一支没有读数据，写一个画像进去就是在记录里说假话。
      注意它走的是"不猜"那条既有链路，回答为空 —— status 仍是 error。）
    """
    record = service.ask("今天天气怎么样", use_llm=False)
    assert record["routing"]["intent"] == "clarify"
    assert record["response_mode"] == "clarify"
    assert record["status"] == "error"
    assert record["facts"] is None and record["tool"] is None
