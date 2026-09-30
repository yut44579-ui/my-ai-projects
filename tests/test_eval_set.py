"""test_eval_set.py · FR-011 ② 验收：**评测集本身**与**跑分判据**也是被测对象。

════════════════════════════════════════════════════════════════════════
【为什么评测集要写测试】
════════════════════════════════════════════════════════════════════════
一把尺子如果自己都没人量，它就会慢慢变成"永远通过"的摆设。这个文件量的是尺子：

 ① 规模与字段齐全        60–100 条、每条 id/question/expect_mode/expect/must_not/source/note/category
 ② 覆盖到位              派单点名的每一类（问数/资料/联合/元信息/使用说明/概念/算术/该拒的）都有条
 ③ `source=real` 不掺水  标成"用户真实提问"的，必须能在用户语料文件里**逐字找到**
                         （语料文件不在本机时自动跳过 —— 它是 Hermes 那边的材料，不是仓库资产）
 ④ 判据能红              自检红线样例走**真服务**跑一遍：必须被判红；把期望改对之后必须变绿
                         （证明"红"是期望写错造成的，不是产品坏了、也不是判据乱杀）
 ⑤ 判据四项各自能触发    判档 / 数字出处 / 内部名词 / 反向断言，各用一份**合成记录**触发一次

④ 用的是真服务真数据（与跑分脚本同一条路），⑤ 只是给判据本身做的单元级对照 ——
两者合起来才能说"这把尺子会失败、而且失败得**是地方**"。
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts"), str(PROJECT_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_run  # noqa: E402  （被测对象：跑分脚本）
from app.ai import routing  # noqa: E402

EVAL_SET = PROJECT_ROOT / "scripts" / "eval_set.jsonl"
ITEMS = eval_run.load_items(EVAL_SET)

#: 用户语料（Hermes 那边的材料；不在本机就只按仓库里的冻结清单查）
CORPUS_MD = pathlib.Path("D:/Hermes/cache/真实用户语料-销售报表Agent.md")
CORPUS_JSONL = pathlib.Path("D:/Hermes/cache/eval_set_real_user_questions.jsonl")

#: 仓库里**明确标注为真实问法/真实原话**的冻结文件：资料问答（B）、联合分析（C）、
#: 元信息与身份（A）这三批的真实问法就记在这里（派单的原文也逐字抄在这些清单里）。
FROZEN_REAL_SOURCES = (
    PROJECT_ROOT / "tests" / "test_fr010b_documents.py",
    PROJECT_ROOT / "tests" / "test_fr010c_joint.py",
    PROJECT_ROOT / "tests" / "test_fr010a_meta.py",
)

ISOLATED_ENV = ("SRA_STATE_DIR", "SRA_DB_PATH", "SRA_ORIGINAL_DIR",
                "SRA_UPLOAD_DIR", "SRA_DOC_DIR", "SRA_OUTPUT_DIR")


# ════════════════════════════════════════════════════════════════════════
# ① 规模与字段
# ════════════════════════════════════════════════════════════════════════
def test_评测集规模在派单要求的区间里() -> None:
    assert 60 <= len(ITEMS) <= 100, f"派单要求 60–100 条，实际 {len(ITEMS)}"


@pytest.mark.parametrize("item", ITEMS, ids=[item["id"] for item in ITEMS])
def test_每条都齐了必需字段且取值合法(item: dict) -> None:
    for key in ("id", "question", "expect_mode", "expect", "must_not", "source", "note", "category"):
        assert key in item, f"{item['id']} 缺字段 {key}"
    modes = [item["expect_mode"]] if isinstance(item["expect_mode"], str) else item["expect_mode"]
    for mode in modes:
        assert mode in routing.ALL_MODES, f"{item['id']} 的档位 {mode} 不在产品的档位表里"
    assert item["source"] in ("real", "derived"), item["id"]
    assert item["note"].strip(), f"{item['id']} 没有写「这一条在验什么」"
    assert isinstance(item["expect"], dict) and isinstance(item["must_not"], list)


def test_覆盖了派单点名的每一类() -> None:
    categories = {item["category"] for item in ITEMS}
    for wanted in ("问数·单值", "问数·排行/趋势/对比/归因", "问数·周报", "资料问答", "联合分析",
                   "元信息问答", "使用说明", "概念与寒暄", "算术", "该拒的（数据里没有的维度）"):
        assert wanted in categories, f"少了这一类：{wanted}"
    counts = {name: sum(1 for item in ITEMS if item["category"] == name) for name in categories}
    assert all(count > 0 for count in counts.values()), counts


def test_反向断言出现了且不是空话() -> None:
    """每条都得有 must_not（**反向断言**是这套评测的骨头：只判"该出现的出现了"会放过胡说的回答）。"""
    for item in ITEMS:
        assert item["must_not"], f"{item['id']} 没有反向断言"
    kinds = {rule for item in ITEMS for rule in item["must_not"]}
    assert {"internal_terms", "unlisted_numbers", "sales_amount", "sales_data_accessed"} <= kinds, kinds


# ════════════════════════════════════════════════════════════════════════
# ③ source=real 不掺水（语料文件不在本机则跳过）
# ════════════════════════════════════════════════════════════════════════
def test_real_条目必须逐字来自有记录的来源() -> None:
    """标成 real 的，必须能在**有记录的地方**逐字找到 —— 不许自己编一句再贴上"真实提问"。

    两个来源，缺一不可地都要查：
      · 仓库里的冻结清单（资料问答 / 联合分析 / 元信息与身份那三批真实问法就记在测试文件里）
      · Hermes 的两个用户语料文件（不在本机时自动跳过这一半）
    """
    haystack = "\n".join(path.read_text(encoding="utf-8") for path in FROZEN_REAL_SOURCES)
    if CORPUS_MD.exists() and CORPUS_JSONL.exists():
        haystack += "\n" + CORPUS_MD.read_text(encoding="utf-8")
        haystack += "\n" + CORPUS_JSONL.read_text(encoding="utf-8")
    missing = [item["id"] + " " + item["question"] for item in ITEMS
               if item["source"] == "real" and item["question"] not in haystack]
    assert not missing, "标成 real 但任何有记录的来源里都找不到原话：\n" + "\n".join(missing)


def test_派生条目也标了来源() -> None:
    """derived 的条目必须在 note 里说清它是怎么派生的（免得日后分不清哪句是用户说的）。"""
    for item in ITEMS:
        if item["source"] == "derived":
            assert "派生" in item["note"] or "自检" in item["note"], item["id"]


# ════════════════════════════════════════════════════════════════════════
# ④ 自检红线：走真服务跑一遍
# ════════════════════════════════════════════════════════════════════════
def _run_one(item: dict, tmp_path: pathlib.Path) -> dict:
    """用跑分脚本自己的 `run()` 跑一条（真隔离、真导入语料、真提问）。"""
    saved = {key: os.environ.get(key) for key in ISOLATED_ENV}
    try:
        report = eval_run.run([item], use_llm=False, only=None, limit=None, state_root=tmp_path / "eval")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert report["count"] == 1
    return report["results"][0]


def test_自检红线样例确实被这套判据判红(tmp_path) -> None:
    """★ 存在性证明：这把尺子**会**失败。

    这一条走真实数据与真实服务：期望档位故意写成 report（实际是 direct），
    所以必须判红。它要是绿的，说明判据根本没在看结果。
    """
    red = [item for item in ITEMS if item.get("selfcheck")]
    assert len(red) == 1, "自检红线样例应当有且只有一条"
    verdict = _run_one(red[0], tmp_path)

    assert verdict["ok"] is False, "自检红线样例被判成绿的了 —— 判据失效"
    assert any("档位" in failure for failure in verdict["failures"]), verdict["failures"]
    assert verdict["actual_mode"] == "direct", "这一问的真实档位就是 direct（期望故意写成 report）"


def test_把期望改对之后同一条就变绿(tmp_path) -> None:
    """对照：不是判据乱杀 —— 把期望改对，同一条立刻通过。"""
    red = [item for item in ITEMS if item.get("selfcheck")][0]
    fixed = dict(red, expect_mode="direct", selfcheck=False, note="对照：期望改对之后应当变绿")
    verdict = _run_one(fixed, tmp_path)
    assert verdict["ok"] is True, verdict["failures"]


# ════════════════════════════════════════════════════════════════════════
# ⑤ 四项判据各自能触发（合成记录：判据本身的单元级对照）
# ════════════════════════════════════════════════════════════════════════
def _record(*, mode: str, sections: list[tuple[str, str]], facts: dict | None = None,
            status: str = "ok", sales_data_accessed: bool = True,
            section_source: str = "code") -> dict:
    return {
        "response_mode": mode,
        "status": status,
        "sales_data_accessed": sales_data_accessed,
        "notice": "",
        "data_profile": {"rows": 541909, "start": "2010-12-01", "end": "2011-12-09"},
        "facts": facts,
        "items": None,
        "series": None,
        "report_document": None,
        "tool": {"name": "sales_summary", "display": [], "notes": [], "params": {}},
        "answer": {"sections": [{"key": key, "title": "", "text": text, "source": section_source}
                               for key, text in sections]},
        "error": None,
    }


def test_判据能判出档位写错() -> None:
    item = {"id": "T-1", "question": "q", "expect_mode": "analysis", "expect": {}, "must_not": [],
            "source": "derived", "note": "n", "category": "c"}
    verdict = eval_run.judge(item, _record(mode="direct", sections=[("answer", "316,412.16 元")]), "")
    assert verdict["ok"] is False and "档位" in verdict["failures"][0]


def test_判据能判出编造的数字() -> None:
    """数字出处这一条：事实里只有 316,412.16，**模型写的**那段却冒出 999,999.99 → 必须判红。

    这里的段来源标成 `llm`：这条判据的域就是"模型写出来的文字"
    （代码写的正文本身就是确定性产物，见 `eval_run._code_text` 的说明）。
    """
    item = {"id": "T-2", "question": "q", "expect_mode": "direct", "expect": {"numbers_from": "deterministic"},
            "must_not": ["unlisted_numbers"], "source": "derived", "note": "n", "category": "c"}
    record = _record(mode="direct", sections=[("answer", "本期 316,412.16 元，比上期多 999,999.99 元")],
                     facts={"amount": 316412.16}, section_source="llm")
    verdict = eval_run.judge(item, record, "")
    assert verdict["ok"] is False, verdict
    assert "999999.99" in verdict["number_violations"], verdict["number_violations"]  # 归一化后的形式


def test_判据能判出内部名词泄漏() -> None:
    item = {"id": "T-3", "question": "q", "expect_mode": "help", "expect": {},
            "must_not": ["internal_terms"], "source": "derived", "note": "n", "category": "c"}
    record = _record(mode="help", sections=[("help", "这次检索用的是 BM25 打分")],
                     sales_data_accessed=False)
    verdict = eval_run.judge(item, record, "")
    assert verdict["ok"] is False, verdict
    assert any("BM25" in failure for failure in verdict["failures"]), verdict["failures"]


def test_判据能判出拒绝档带了销售额() -> None:
    item = {"id": "T-4", "question": "q", "expect_mode": "clarify", "expect": {"numbers_from": "none"},
            "must_not": ["sales_amount"], "source": "derived", "note": "n", "category": "c"}
    record = _record(mode="clarify", sections=[("answer", "数据里没有地区字段。本期销售额 10,666,684.54 元")],
                     status="unsupported")
    verdict = eval_run.judge(item, record, "")
    assert verdict["ok"] is False, verdict
    assert any("金额格式" in failure for failure in verdict["failures"])


def test_判据干净时放行() -> None:
    """反向对照：一条干净的回答不许被误杀（否则尺子只会一直红，等于没有刻度）。"""
    item = {"id": "T-5", "question": "q", "expect_mode": "direct",
            "expect": {"numbers_from": "deterministic", "sales_data_accessed": True},
            "must_not": ["internal_terms", "unlisted_numbers"], "source": "derived", "note": "n",
            "category": "c"}
    record = _record(mode="direct", sections=[("answer", "本期 316,412.16 元")], facts={"amount": 316412.16})
    verdict = eval_run.judge(item, record, "")
    assert verdict["ok"] is True, verdict["failures"]
