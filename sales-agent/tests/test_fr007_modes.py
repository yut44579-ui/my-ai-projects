"""tests/test_fr007_modes.py · FR-007 验收 B~G：五档回答各自的形态与契约字段。

════════════════════════════════════════════════════════════════════════
【一档一条规矩，逐档钉住】
════════════════════════════════════════════════════════════════════════
  B 普通聊天   「你好」           → general：无销售工具 / 无 facts / 无指标表 / 无 SECTION_WHAT
  C 概念问答   「什么是毛利率」   → general：**不读当前业务数据**（不是"文本里不许出现销售额"）
  D 单值查询   「本期销售额是多少」→ direct：页面是**单值**，禁止指标表/贡献/为什么/建议/趋势
  E 销售分析   「11月 vs 10月」   → analysis：允许事实/归因，闸门（数字/币种/维度）继续生效
  F 报告生成   「帮我生成上周周报」→ report：六节结构齐全，不被 direct 档吞掉
  G 契约       接口响应里带着 response_mode / routing / answer.source / sales_data_accessed

★ 全程用隔离的 SRA_STATE_DIR —— 绝不把测试记录写进仓库里的真实 state/（这个项目犯过 607 条污染的错）。
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import answer, intent as intent_module, service, tools  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

NOV = (_dt.date(2011, 11, 1), _dt.date(2011, 11, 30))


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """关掉模型通道：所有"这一段是谁写的"都能落到确定的那一边。"""
    monkeypatch.setattr(service.llm, "available", lambda: False)
    return None


def _ask(question: str) -> dict:
    return service.ask(question, use_llm=False)


def _keys(record: dict) -> list[str]:
    return [item["key"] for item in record["answer"]["sections"]]


# ════════════════════════════════════════════════════════════════════════
# B. 普通聊天
# ════════════════════════════════════════════════════════════════════════
def test_B_你好走general档且没有任何销售痕迹():
    record = _ask("你好")
    assert record["routing"]["intent"] == "general_qa"
    assert record["response_mode"] == "general"
    assert record["status"] == "ok"
    assert record["tool"] is None and record["facts"] is None      # 没有销售工具、没有销售事实
    assert record["data_profile"] is None                          # 也没读数据画像
    assert record["sales_data_accessed"] is False
    assert _keys(record) == ["answer"]                             # 没有 SECTION_WHAT
    assert record["answer"]["export"] is None
    assert "你好" in record["answer"]["sections"][0]["text"]


# ════════════════════════════════════════════════════════════════════════
# C. 概念问答：**不读取当前业务数据**（而不是"文本里不能出现销售额"）
# ════════════════════════════════════════════════════════════════════════
def test_C_什么是毛利率走general档且不读业务数据():
    record = _ask("什么是毛利率？")
    assert record["routing"]["intent"] == "general_qa"
    assert record["response_mode"] == "general"
    assert record["status"] == "ok"
    assert record["tool"] is None and record["facts"] is None
    assert record["sales_data_accessed"] is False
    assert _keys(record) == ["answer"]
    # 概念解释本身可以合理提到"毛利"这个词 —— 关键是这一段**不是**从数据里算出来的
    assert "毛利率" in record["answer"]["sections"][0]["text"]


def test_C_概念问答一次都不碰销售工具(monkeypatch):
    calls = {"run_tool": 0, "profile": 0, "loader": 0, "executor": 0}

    def _count(key, original):
        def _inner(*args, **kwargs):
            calls[key] += 1
            return original(*args, **kwargs)
        return _inner

    from app.engine import executor, loader
    monkeypatch.setattr(tools, "run_tool", _count("run_tool", tools.run_tool))
    monkeypatch.setattr(tools, "dataset_profile", _count("profile", tools.dataset_profile))
    monkeypatch.setattr(loader, "load_raw", _count("loader", loader.load_raw))
    monkeypatch.setattr(executor, "compute_sales_amount", _count("executor", executor.compute_sales_amount))

    record = _ask("什么是毛利率？")
    assert record["status"] == "ok"
    assert calls == {"run_tool": 0, "profile": 0, "loader": 0, "executor": 0}, calls


# ════════════════════════════════════════════════════════════════════════
# D. 单值查询：只有一个数
# ════════════════════════════════════════════════════════════════════════
def test_D_本期销售额是单值且不带分析段():
    record = _ask("本期销售额是多少？")
    assert record["routing"]["intent"] == "data_lookup"
    assert record["response_mode"] == "direct"
    assert record["status"] == "ok"
    assert _keys(record) == ["answer"]
    for forbidden in ("what", "contribution", "why", "actions"):
        assert forbidden not in _keys(record), forbidden
    # 页面上的事实表**只有一行**（被问的那一个指标），不是整张指标表
    assert len(record["tool"]["display"]) == 1, record["tool"]["display"]
    assert record["tool"]["display"][0]["label"].startswith("销售额")


def test_D_单值的数字来自确定性结果():
    """与 executor 的冻结口径逐位对账 —— 页面上的那个数就是算出来的那个数。"""
    record = _ask("2011年11月一共卖了多少？")
    assert record["routing"]["intent"] == "data_lookup"
    amount = record["facts"]["sales_amount"]
    from app.engine import executor
    assert amount == executor.compute_sales_amount(*NOV)["amount"]
    text = record["answer"]["sections"][0]["text"]
    assert answer.format_value(amount, "money") in text
    assert "2011-11-01 ~ 2011-11-30" in text


def test_D_单值档不问模型也没有为什么建议():
    record = _ask("2011年11月一共卖了多少？")
    assert record["llm"]["used"] is False
    assert record["answer"]["source"] == "deterministic"
    assert "为什么" not in record["answer"]["text"]
    assert "建议" not in record["answer"]["text"]


def test_D_问哪个指标就给哪个指标():
    record = _ask("2011年11月有多少订单？")
    assert record["routing"]["intent"] == "data_lookup"
    display = record["tool"]["display"]
    assert len(display) == 1 and display[0]["label"].startswith("订单数")
    assert str(record["facts"]["order_count"]) in record["answer"]["text"].replace(",", "")


# ════════════════════════════════════════════════════════════════════════
# E. 销售分析：允许事实/归因，闸门继续生效
# ════════════════════════════════════════════════════════════════════════
def test_E_销售分析仍然是分析档且保留事实与归因():
    record = _ask("2011年11月和10月的销售额对比")
    assert record["routing"]["intent"] == "sales_analysis"
    assert record["response_mode"] == "analysis"
    assert _keys(record) == ["what", "why", "actions"]
    assert record["facts"]["comparison_status"] == "ok"

    attributed = _ask("2011年11月相比10月，哪些国家推动了销售额变化")
    assert _keys(attributed) == ["what", "contribution", "why", "actions"]


def test_E_三道闸门在销售路径上照旧生效():
    # ① 数字闸门：不在允许集合里的数字照样被拦
    allowed = answer.collect_allowed_numbers("销售额 1,234.56 元")
    report = answer.number_guard("这一周涨了 999999.99 元", allowed)
    assert report["passed"] is False and "999999.99" in report["violations"]
    # ② 币种闸门：写错币种照样被拦
    assert answer.currency_guard("折合 12 英镑") != []
    # ③ 维度硬闸门：数据里没有的维度照样被拒（且**不会**拿国家顶替）
    refused = _ask("华南区上个月卖了多少？")
    assert refused["status"] == "unsupported"
    assert refused["facts"] is None
    assert "区域" in refused["notice"] or "维度" in refused["notice"]
    # 闸门的词表没被本 TASK 动过
    assert any("区域" in words for words, _reason in intent_module._BANNED_DIMENSIONS)


def test_E_单值档正文里的每个数字都只可能来自确定性结果():
    """正文里的数字**只允许**来自两处：程序解析出的时间段、以及被问指标的值本身。

    为什么不直接调 number_guard：那道闸门是给**模型写的字**用的（比对"合法数字集合"），
    而单值档的正文是代码拼的 —— 用自己证明自己就成了白测。这里改成查它的**来源**：
    除时间段与格式化后的指标值以外，正文里不该再有任何数字（_DIRECT 模板只吃这两样）。
    """
    record = _ask("2011年11月21日到11月27日一共卖了多少？")
    amount = record["facts"]["sales_amount"]
    from app.engine import executor
    assert amount == executor.compute_sales_amount("2011-11-21", "2011-11-27")["amount"]  # 位级一致

    text = record["answer"]["sections"][0]["text"]
    shown = answer.format_value(amount, "money")
    assert shown in text
    allowed_digits = set(re.findall(r"\d+", "2011-11-21 2011-11-27 " + shown))
    assert set(re.findall(r"\d+", text)) <= allowed_digits, text
    assert answer.currency_guard(text) == []
    # 一致性：页面上的那个值 = 事实里的那个值（不是另算一份）
    assert record["tool"]["display"][0]["value"] == amount


# ════════════════════════════════════════════════════════════════════════
# F. 报告生成：六节结构齐全，不被 direct 档吞掉
# ════════════════════════════════════════════════════════════════════════
def test_F_周报走report档且六节结构齐全():
    record = _ask("帮我生成上周周报")
    assert record["routing"]["intent"] == "report_generation"
    assert record["response_mode"] == "report"
    assert record["status"] in ("ok", "degraded")

    document = record["report_document"]
    assert document is not None, "报告请求没有被当成报告"
    titles = [section["title"] for section in document["sections"]]
    # 摘要 / 核心指标 / 趋势 / 结构 / 口径与异常 —— 五节在文档里
    assert any("摘要" in title for title in titles)
    assert any("核心指标" in title for title in titles)
    assert any("趋势" in title for title in titles)
    assert any("结构" in title for title in titles)
    assert any("异常" in title for title in titles)
    # 第六节「建议」由回答的【建议行动】承担（导出文件里也是这两段拼起来的）
    assert "actions" in _keys(record)
    assert record["answer"]["export"] is not None


def test_F_报告请求不会被单值档吞掉():
    for question in ("帮我生成上周周报", "出一份 2011 年 11 月的月报", "做一份销售报告"):
        record = _ask(question)
        assert record["response_mode"] == "report", question
        assert record["report_document"] is not None, question


# ════════════════════════════════════════════════════════════════════════
# G. 契约：字段名与取值（接口响应 = 落盘记录，同一个形状）
# ════════════════════════════════════════════════════════════════════════
def test_G_接口响应里带着路由与档位字段():
    response = client.post("/api/chat", json={"question": "1+1等于多少？", "use_llm": False})
    assert response.status_code == 200
    body = response.json()
    assert body["response_mode"] == "direct"
    assert body["routing"]["intent"] == "arithmetic"
    assert body["routing"]["response_mode"] == body["response_mode"]   # 同一个字段名、同一个值
    assert body["sales_data_accessed"] is False
    assert body["answer"]["source"] in ("deterministic", "llm", "system")
    assert [section["key"] for section in body["answer"]["sections"]] == ["answer"]
    # 落盘的那条与接口返回的是**同一条记录**（不是两套字段）
    again = client.get(f"/api/conversations/{body['conversation_id']}").json()
    assert again["response_mode"] == body["response_mode"]
    assert again["routing"]["intent"] == body["routing"]["intent"]


def test_G_每一条记录都有档位字段不论走哪条路():
    for question in ("你好", "1+1等于多少？", "什么是毛利率", "怎么导出数据",
                     "本期销售额是多少？", "2011年11月和10月的销售额对比",
                     "帮我生成上周周报", "今天天气怎么样", "华南区上个月卖了多少"):
        record = _ask(question)
        assert record["response_mode"] in (
            "analysis", "direct", "general", "report", "help", "clarify",
        ), question
        assert record["routing"]["intent"] in (
            "sales_analysis", "data_lookup", "general_qa", "arithmetic",
            "report_generation", "system_help", "clarify",
        ), question
        assert isinstance(record["sales_data_accessed"], bool), question
        assert record["routing"]["response_mode"] == record["response_mode"] or \
            record["status"] in ("error", "unsupported"), question


def test_G_记录里不出现第二套字段名():
    """字段名只允许一套：response_mode / routing / answer.source（不许 mode / answer_mode 之类）。"""
    body = client.post("/api/chat", json={"question": "你好", "use_llm": False}).json()
    blob = json.dumps(body, ensure_ascii=False)
    for stray in ('"answer_mode"', '"mode":', '"intent_mode"', '"responseMode"'):
        assert stray not in blob, stray
