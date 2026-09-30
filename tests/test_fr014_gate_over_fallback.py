"""tests/test_fr014_gate_over_fallback.py · FR-014：**代码闸门永远压过兜底**。

════════════════════════════════════════════════════════════════════════
【钉住的回归是什么】
════════════════════════════════════════════════════════════════════════
FR-012 组 4 为了治「2011年11月的客单价是多少」（模型说答不了、其实 sales_summary 能答）
在 `app/ai/service.py` 加了一条兜底，条件是：

    解析给了 unsupported  且  路由判它是"单值直答"（response_mode == direct）

少了第三个条件 —— **这个 unsupported 是谁给的**。于是「华南区上个月卖了多少？」
（`guard_unsupported` 用代码硬拦下来的"数据里没有这个维度"）也满足前两条，
被这条兜底救成了 `sales_summary`：用户拿到一个用 Country 顶替"华南"算出来的数字，
**看着像答案、其实答的是另一个问题** —— 这正是那道闸门存在的唯一理由。

修法：兜底只在 `parse_info["source"] == "llm"`（**模型**说的答不了）时才出手。

本文件两条，各钉一个方向：
    A 闸门那半：路由判 direct + 代码闸门判 unsupported → **仍然是 unsupported**
    B 兜底那半：只有模型说答不了时才救（证明 A 的收紧没有把 FR-012 治好的东西碰坏）
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai import intent as intent_module, llm, routing  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

#: 这几条都是 `guard_unsupported` 用代码拦下的"数据里没有的维度"，且都带时间 → 路由判 direct
GUARDED_DIRECT_QUESTIONS = (
    "华南区上个月卖了多少？",
    "华南区2011年11月卖了多少",
    "2011年11月上海门店卖了多少",
)


def ask(question: str, *, use_llm: bool) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": use_llm})
    assert response.status_code < 500, response.text
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# A · 闸门赢：路由说 direct，闸门说 unsupported → 还是 unsupported
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("question", GUARDED_DIRECT_QUESTIONS)
def test_路由判direct但代码闸门判unsupported时闸门赢(question):
    record = ask(question, use_llm=False)

    # 前提：这条问题确实落在那个边界上 —— 路由**真的**判了"单值直答"（兜底的第一、二条成立）。
    # 为什么要单独问一次路由：回答记录里的 response_mode 是**拒绝之后**改写成 clarify 的值
    # （见 service.ask 的 unsupported 分支），从记录里看不出拒绝前的档位 ——
    # 这里直接问路由，才能证明这条用例真的踩在 FR-014 修的那一处上，而不是"假绿"。
    assert routing.classify(question).response_mode == routing.MODE_DIRECT
    # 结论：闸门说了算
    assert record["status"] == "unsupported"
    assert record["intent"]["intent"] == "unsupported"
    assert record["tool"] is None and record["facts"] is None


@pytest.mark.parametrize("question", GUARDED_DIRECT_QUESTIONS)
def test_闸门拦下时一个金额都不许出现在回答里(question):
    """硬证据：被拒的回答里不许有任何"用 Country 算出来"的数字。"""
    import json

    blob = json.dumps(ask(question, use_llm=False), ensure_ascii=False, default=str)
    assert "销售额：" not in blob
    for leaked in ("10,666,684.54", "1,509,496.33", "10666684.544", "1509496.33"):
        assert leaked not in blob, f"拒绝的回答里漏出了金额 {leaked}"


def test_闸门拦下的问题解析来源就是guard(monkeypatch):
    """`source=="guard"` 是修法的判据本身，钉住它 —— 它一变这条兜底的前提就失效了。"""
    record = ask("华南区上个月卖了多少？", use_llm=False)
    assert record["parse"]["source"] == "guard"


# ════════════════════════════════════════════════════════════════════════
# B · 兜底还在：只有**模型**说答不了时才救（FR-012 治好的那条不许碰坏）
# ════════════════════════════════════════════════════════════════════════
def _llm_says(monkeypatch, payload: str) -> None:
    """让模型上场、并且**只**回这一句 JSON（不连网、不 mock 解析器）。"""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: "stub-model")
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: payload)


def test_模型对客单价说答不了时代码兜回sales_summary(monkeypatch):
    """FR-012 组 4 治的那条：模型不认识"客单价"→ 兜底按既有口径答，数字仍由代码算。"""
    _llm_says(monkeypatch, '{"intent":"unsupported","reason":"能力清单里没有客单价"}')

    record = ask("2011年11月的客单价是多少？", use_llm=True)

    assert record["status"] == "ok"
    assert record["tool"]["name"] == "sales_summary"
    assert record["facts"] is not None
    labels = [item["label"] for item in record["tool"]["display"]]
    assert any("客单价" in label for label in labels), labels


def test_模型对区域问题说答不了时闸门仍然先拦(monkeypatch):
    """模型**没机会**救闸门拦下的问题：guard 在 LLM 之前就把问题结束了，一次都不问模型。"""
    called = {"n": 0}

    def _counting(*args, **kwargs):
        called["n"] += 1
        return '{"intent":"sales_summary","params":{"start":"2011-11-01","end":"2011-11-30"}}'

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: "stub-model")
    monkeypatch.setattr(llm, "chat", _counting)

    record = ask("华南区上个月卖了多少？", use_llm=True)

    assert record["status"] == "unsupported"
    assert record["parse"]["source"] == "guard"
    assert called["n"] == 0, "闸门拦下之后不该再让 LLM 插手"
