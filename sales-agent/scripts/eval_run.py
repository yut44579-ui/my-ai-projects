#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_run.py · FR-011 ② 真实问题评测集跑分（"我的 Agent 到底好不好用"的那把尺子）

════════════════════════════════════════════════════════════════════════
【它和 tests/ 里的测试有什么不同】
════════════════════════════════════════════════════════════════════════
    tests/   = 验**代码对不对**（一个断言一个功能点，改坏了会红）
    本脚本   = 验**答得好不好**（一整轮真实提问跑下来，四项打分 + 逐条失败清单）

它存在的意义只有一个：回答"我改了提示词/加了能力，是**变好**还是**变坏**了"。
没有它，任何一次改动都只能靠"我感觉这次回答更顺"来验收。

════════════════════════════════════════════════════════════════════════
【四项判据（全部复用产品自己的判定，**不另造一套口径**）】
════════════════════════════════════════════════════════════════════════
① 判档准确率   期望档位 vs 实际 response_mode（档位由 routing 给出，评测只比对）
② 反向断言通过率 must_not 全过才算过（"不许出现编造数字/内部名词/币种错误/拒绝时带销售额"）
③ 数字可信度   回答里的每个数字都要能在本条的确定性产物里找到出处 ——
               **直接复用 `answer.number_guard`**（产品拦住 LLM 编数字用的就是它），
               不自己写一套"数字像不像真的"的启发式。
               ★ 读数要说清：**确定性跑分（默认）下这条基本不会红** —— 没有模型写句子，
                 正文本身就是确定性渲染，数字自然都在出处里；它真正的用处在 `--llm`：
                 模型写的那两段（【为什么】/【建议行动】）里出现追溯不到的数字就会被抓。
④ 内部名词泄漏 复用产品自己的禁用词表（资料类 `doc_qa.BANNED_WORDS`、
               联合分析 `joint.BANNED_WORDS`、其余档位与 `tests/test_fr010a_meta.py`
               同表的小清单 + 工具名 + 接口路径）。

════════════════════════════════════════════════════════════════════════
【评测集格式（scripts/eval_set.jsonl，每行一条）】
════════════════════════════════════════════════════════════════════════
    id            唯一编号（Q-001… / SELFCHECK-RED-001）
    question      用户原话（一字不改；"real" 的来自用户真实提问）
    expect_mode   期望档位：direct/analysis/report/help/general/joint/clarify（或它们的列表）
    expect        期望行为要点：
        status               期望 status（ok/degraded/unsupported），可省
        sales_data_accessed  期望这条有没有读销售数据（true/false），可省
        text_any / text_all / text_none   正文里至少要/必须/不许出现的子串
        numbers_from         数字的出处类型：deterministic / joint / document / none
                             （none = 这一档不该出现需要核的数字，本项不计入分母）
        sections             分段的 key 顺序（按前缀比对），可省
        sources              资料事实是否必须带出处（出现《文件名》），可省
    must_not      反向断言（全过才算过）：
        internal_terms       按本条 internal_terms 指定的词表扫（默认 meta）
        unlisted_numbers     回答里的数字必须都能追溯（复用 number_guard）
        sales_amount         不许出现**金额格式**的数字（拒绝/澄清档用）
        digits               分段正文里**一个阿拉伯数字都不许有**（"给不了"那一档用）
        sales_data_accessed  不许读销售数据
        <其它字符串>          正文里不许出现这个子串（如 "英镑"/"GBP"/"资料错"）
    internal_terms 词表选择：meta（默认）/ doc / joint
    source        real（用户真实提问）/ derived（据此派生的变体）
    note          这一条在验什么（人读）
    selfcheck     仅自检红线样例为 true（**故意写错的期望**，跑分时必须显示为红）

════════════════════════════════════════════════════════════════════════
【语料：与冻结验收**同一份**，不抄一份】
════════════════════════════════════════════════════════════════════════
资料问答 / 联合分析这两类必须挂在一份真实资料上。这里用 `import` 直接取
FR-010-B / FR-010-C 验收夹具里的那几份（`GAME_DOC` / `STRATEGY_DOC` / `DECLINE_DOC` /
`INJECTION_DOC`）与图片型 PDF 的造法（`test_documents.build_minimal_pdf`）——
**拷一份到自己文件里就会漂移**，届时"评的到底是哪份资料"就说不清了。

════════════════════════════════════════════════════════════════════════
【退出码】
════════════════════════════════════════════════════════════════════════
    0  全过
    2  **只有**自检红线样例是红的（预期内：它证明这把尺子会失败）
    1  有真失败（需要人看失败清单：是产品问题还是评测集写错了）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import fr003_helpers as helpers  # noqa: E402  （隔离 + 建表：与 FR-003 各测试同一套）

from app.ai import answer, doc_qa, joint, llm, service  # noqa: E402
from app.importer import models, pipeline  # noqa: E402

DEFAULT_SET = ROOT / "scripts" / "eval_set.jsonl"

#: 非资料类回答的内部名词表（与 tests/test_fr010a_meta.py 同一张；那里是逐字扫描的验收）
_META_BANNED: tuple[str, ...] = (
    "BM25", "bm25", "intent", "Intent", "INTENT", "document_qa", "vector", "embedding",
    "response_mode", "置信度", "耗时", "接口路径", "分段 key", "KnowledgeBase",
)
_META_TOOL_NAMES: tuple[str, ...] = (
    "sales_summary", "sales_trend", "top_products", "sales_compare", "sales_breakdown_by_country",
)
_API_PATH_RE = re.compile(r"/api/[A-Za-z0-9_{}/-]+")
#: "金额格式"的数字（拒绝/澄清档不许出现的东西）：
#:   1,234.56 / 316412.16 / 10,666,684.54 都算；541,909（行数）与 2011-12-09（日期）**不算**。
_MONEY_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\.\d{2}\b")
_DIGITS_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _display_path(path: Path) -> str:
    """给人看的路径（在仓库里就显示相对路径；在仓库外 —— 比如 pytest 的临时目录 —— 就显示全路径）。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _visible_text(record: dict) -> str:
    """用户**看得见**的那部分（提示语 + 分段标题 + 分段正文）。"""
    payload = record.get("answer") or {}
    parts = [str(record.get("notice") or "")]
    for section in payload.get("sections") or []:
        parts.append(str(section.get("title") or ""))
        parts.append(str(section.get("text") or ""))
    error = record.get("error") or {}
    parts.append(str(error.get("message") or ""))
    return "\n".join(part for part in parts if part)


def _body_text(record: dict) -> str:
    """只算**分段正文**（不含提示语/错误信息）—— "正文里零数字"这类判据用的就是它。"""
    payload = record.get("answer") or {}
    return "\n".join(str(section.get("text") or "") for section in payload.get("sections") or [])


def _content_lines(text: str) -> str:
    """回答里**正文部分**（去掉出处行）—— 与 FR-010-B 验收 4 的 `content_lines` 同义。

    为什么"不许出现某个词"要在这上面判：出处里本来就会出现《文件名》，
    而文件名可能带着那个词（《某游戏2026海外战略.md》里的"海外"）——
    那一处**必须**出现（用户要靠它找文件）。所以这条判据只扫正文行。
    """
    return "\n".join(
        line for line in (text or "").splitlines()
        if "《" not in line and not line.strip().startswith("出处")
    )


def _section_keys(record: dict) -> list[str]:
    return [str(item.get("key")) for item in ((record.get("answer") or {}).get("sections") or [])]


def _numbers_in(text: str) -> set[str]:
    return {answer._normalize_number(token) for token in _DIGITS_RE.findall(text or "")}


def _money_numbers(text: str) -> list[str]:
    return [token for token in _DIGITS_RE.findall(text or "") if _MONEY_RE.fullmatch(token)]


# ════════════════════════════════════════════════════════════════════════
# 语料（与冻结验收夹具同一份）
# ════════════════════════════════════════════════════════════════════════
def _corpus() -> dict[str, str]:
    from test_fr010b_documents import GAME_DOC, STRATEGY_DOC
    from test_fr010c_joint import DECLINE_DOC, INJECTION_DOC

    return {
        "游戏资料.md": GAME_DOC,
        "某游戏2026海外战略.md": STRATEGY_DOC,
        "市场判断.md": DECLINE_DOC,
        "内部说明.md": INJECTION_DOC,
    }


def _scan_pdf_bytes() -> bytes:
    """图片型 PDF（没有文本层）—— 与 FR-010-B 验收 7 用的是同一个造法。"""
    from test_documents import build_minimal_pdf

    return build_minimal_pdf(["\n", "\n"], draw_text=False)


def _setup(state_root: Path) -> dict[str, Any]:
    """隔离目录 + 真导入语料（与 HTTP 上传同一条管道）。"""
    paths = helpers.isolate(state_root)
    helpers.reset()
    corpus = _corpus()
    for name, text in corpus.items():
        pipeline.import_bytes(text.encode("utf-8"), name)
    scan_error = None
    try:
        pipeline.import_bytes(_scan_pdf_bytes(), "扫描件.pdf")
    except models.ImporterError as exc:      # 提不出文字的扫描件本就不该进库（产品既有行为）
        scan_error = str(exc)
    return {"paths": paths, "corpus": corpus, "scan_error": scan_error}


# ════════════════════════════════════════════════════════════════════════
# 判据
# ════════════════════════════════════════════════════════════════════════
def _code_text(record: dict) -> str:
    """本条里**由代码写出**的正文（来源不是模型）。

    它是确定性产物的一部分（节编号「第 2 节」、导入时间「2026-09-30 05:02」这类内联数字
    都只存在于正文里，结果载荷里没有）—— 所以它算"合法出处"。
    ★ 代价要说清楚：这样一来这条判据的**区分度只作用在模型写的那部分文字**上
      （确定性跑分下它必然接近 100%）。这不是判据没用，而是**跑法**决定的：
      产品装 number_guard 本来就是为了拦模型编数字（见 answer.compose 的注释）。
      报告里给了 `--llm` 跑法的对照读数。
    """
    payload = record.get("answer") or {}
    return "\n".join(
        str(section.get("text") or "") for section in payload.get("sections") or []
        if section.get("source") != answer.SOURCE_LLM
    )


def _allowed_numbers(record: dict, kind: str, corpus_text: str) -> set[str] | None:
    """本条回答里"允许出现的数字"集合（None = 这一档不判数字）。

    收集方式**与产品 `answer.compose()` 完全同源**：确定性的**渲染文本**
    （`render_facts_text` / `render_contribution_text` / 报告文档 Markdown）+ **原始结果 dict**
    + 数据画像 + 提示条模板。
    ★ 出处里**包含代码写出的正文**（理由与代价见 `_code_text`）：于是这条判据真正在抓的是
      "**模型写出来的**文字里出现了算不出来的数字" —— 与产品的 number_guard 同域。
    为什么必须带上渲染文本：答案里的金额是**按产品自己的格式化器**显示出来的
    （原始值 10666684.544 → 显示「10,666,684.54」），只喂原始 dict 会把正常的显示舍入
    误判成"编造的数字"—— 第一版就是栽在这儿（62 条里 37 条被误杀）。
    """
    if kind == "none":
        return None

    tool = record.get("tool") or {}
    sources: list[Any] = [
        record.get("facts"), record.get("items"), record.get("series"),
        record.get("report_document"), record.get("data_profile"), record.get("params"),
        tool.get("display"), tool.get("notes"), tool.get("params"),
        # 提示条是**代码写死的模板文案**（比如"「上周」这次没有被当成一个时间范围…例如 2011-11-28 到 2011-12-04"），
        # 它里面的数字同样是确定性产物 —— 不带上它会把正常的提示文案误判成"编造的数字"。
        record.get("notice"),
        # 代码写出的正文（节编号/时间戳这类内联数字只存在于正文里）
        _code_text(record),
    ]
    if record.get("facts") or record.get("items") or record.get("series"):
        result_like = {
            "tool": tool.get("name"), "title": tool.get("title"), "params": tool.get("params"),
            "notes": tool.get("notes"), "display": tool.get("display"), "selfcheck": tool.get("selfcheck"),
            "facts": record.get("facts"), "items": record.get("items"), "series": record.get("series"),
            "report": record.get("report_document"),
        }
        sources.append(answer.render_facts_text(result_like, question=""))
        sources.append(answer.render_contribution_text(result_like))
    if record.get("report_document"):
        sources.append(answer.render_report_document(record["report_document"]))
    if kind in ("document", "joint"):
        # 资料侧的数字（引原文里的「预计增长 50%」之类）出处就是**资料原文**
        sources.append(corpus_text)
    return answer.collect_allowed_numbers(*sources)


def _internal_words(item: dict) -> tuple[str, ...]:
    which = item.get("internal_terms") or "meta"
    if which == "doc":
        return doc_qa.BANNED_WORDS
    if which == "joint":
        return joint.BANNED_WORDS
    return _META_BANNED + _META_TOOL_NAMES


def judge(item: dict, record: dict, corpus_text: str) -> dict[str, Any]:
    """跑一条：返回 {ok, failures:[...], 各分项}。"""
    text = _visible_text(record)
    expect = item.get("expect") or {}
    failures: list[str] = []

    # ① 判档
    wanted = item.get("expect_mode")
    wanted_list = [wanted] if isinstance(wanted, str) else list(wanted or [])
    mode = record.get("response_mode")
    mode_ok = (not wanted_list) or (mode in wanted_list)
    if not mode_ok:
        failures.append(f"档位：期望 {'/'.join(map(str, wanted_list))}，实际 {mode}")

    # ② 期望行为要点
    for needle in expect.get("text_all") or []:
        if needle not in text:
            failures.append(f"正文缺少必须出现的内容：{needle!r}")
    if expect.get("text_any"):
        if not any(needle in text for needle in expect["text_any"]):
            failures.append(f"正文里这几条一条都没出现：{expect['text_any']}")
    content = _content_lines(text)
    for needle in expect.get("text_none") or []:
        if needle in content:
            failures.append(f"正文里出现了不该出现的内容：{needle!r}")
    if expect.get("status") is not None:
        allowed_status = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
        if record.get("status") not in allowed_status:
            failures.append(f"状态：期望 {'/'.join(allowed_status)}，实际 {record.get('status')}")
    if expect.get("sales_data_accessed") is not None:
        if bool(record.get("sales_data_accessed")) != bool(expect["sales_data_accessed"]):
            failures.append(f"是否读过销售数据：期望 {expect['sales_data_accessed']}，"
                            f"实际 {record.get('sales_data_accessed')}")
    if expect.get("sections"):
        keys = _section_keys(record)
        if keys[: len(expect["sections"])] != list(expect["sections"]):
            failures.append(f"分段：期望按 {expect['sections']} 开头，实际 {keys}")
    if expect.get("sources"):
        # 资料事实永远是**第一段**（help 档唯一一段；joint 档是 doc_facts）
        sections = (record.get("answer") or {}).get("sections") or []
        body = str(sections[0].get("text") or "") if sections else ""
        if "《" not in body:
            failures.append("资料事实没有带出处（正文里没有《文件名》）")

    # ③ 数字可信度：**复用产品自己的 number_guard**
    numbers_kind = expect.get("numbers_from", "deterministic")
    allowed = _allowed_numbers(record, numbers_kind, corpus_text)
    number_report = None
    if allowed is not None:
        number_report = answer.number_guard(text, allowed)
        if not number_report["passed"]:
            failures.append("数字无出处：" + ", ".join(number_report["violations"][:6])
                            + (f"（另有 {len(number_report['violations']) - 6} 个）"
                               if len(number_report["violations"]) > 6 else ""))

    # ④ 内部名词
    leaked = [word for word in _internal_words(item) if word in text]
    if _API_PATH_RE.search(text):
        leaked.extend(_API_PATH_RE.findall(text))
    if leaked:
        failures.append("内部名词泄漏：" + ", ".join(sorted(set(leaked))))

    # ⑤ 反向断言（must_not）
    for rule in item.get("must_not") or []:
        if rule == "internal_terms" and leaked:
            continue                                   # 已在 ④ 记过，避免同一条重复计数
        if rule == "unlisted_numbers" and number_report is not None and not number_report["passed"]:
            continue                                   # 已在 ③ 记过
        if rule == "unlisted_numbers" and allowed is None:
            continue
        if rule == "sales_amount":
            hits = _money_numbers(text)
            if hits:
                failures.append(f"出现了金额格式的数字（这一档不该有）：{hits[:5]}")
        elif rule == "digits":
            # "给不了"的回答里正文一个阿拉伯数字都不许有（与 FR-010-C 验收 5 同一条判据）
            hits = _DIGITS_RE.findall(_body_text(record))
            if hits:
                failures.append(f"这一档的正文里出现了数字（应当零数字）：{hits[:5]}")
        elif rule == "sales_data_accessed":
            if record.get("sales_data_accessed"):
                failures.append("这条不该读销售数据，但 sales_data_accessed=True")
        elif rule == "internal_terms":
            pass                                       # 干净：已经在 ④ 判过
        elif rule == "unlisted_numbers":
            pass
        elif rule in text:
            failures.append(f"正文里出现了禁止内容：{rule!r}")

    return {
        "ok": not failures,
        "mode_ok": mode_ok,
        "failures": failures,
        "actual_mode": mode,
        "actual_status": record.get("status"),
        "numbers_judged": allowed is not None,          # 这一条的数字到底判没判（分母）
        "number_violations": (number_report or {}).get("violations", []),
        "leaked_words": sorted(set(leaked)),
        "answer_excerpt": text.strip().replace("\n", " ⏎ ")[:280],
    }


# ════════════════════════════════════════════════════════════════════════
# 跑分
# ════════════════════════════════════════════════════════════════════════
def load_items(path: Path) -> list[dict]:
    items = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        for key in ("id", "question", "expect_mode", "expect", "must_not", "source", "note", "category"):
            if key not in item:
                raise SystemExit(f"{path}:{line_no} 缺少字段 {key}")
        items.append(item)
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise SystemExit("评测集里有重复 id")
    return items


def run(items: list[dict], *, use_llm: bool, only: str | None, limit: int | None,
        state_root: Path | None = None) -> dict[str, Any]:
    selected = [item for item in items if not only or item["id"].startswith(only)]
    if limit:
        selected = selected[:limit]

    state_root = state_root or (ROOT / "outputs" / "_eval_state")
    # 每次跑分都把隔离目录清空重建（导入记录/资料库/执行记录必须是干净的），
    # **只留 Excel 解析缓存**（FR-011 ①）：它是加速件、不影响任何答案 ——
    # 留着就不必每跑一次都重付 250 秒解析（数据文件本身有 SHA256 把关）。
    keep = state_root / "state" / "cache"
    stash = state_root.with_name(state_root.name + "_cache_stash")
    shutil.rmtree(stash, ignore_errors=True)
    if keep.is_dir():
        shutil.move(str(keep), str(stash))
    shutil.rmtree(state_root, ignore_errors=True)
    setup = _setup(state_root)
    if stash.is_dir():
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stash), str(keep))
    corpus_text = "\n".join(setup["corpus"].values())

    results = []
    started = time.perf_counter()
    for index, item in enumerate(selected, start=1):
        question = item["question"]
        print(f"[{index}/{len(selected)}] {item['id']} {question}", flush=True)
        t0 = time.perf_counter()
        record = service.ask(question, use_llm=use_llm)
        verdict = judge(item, record, corpus_text)
        verdict.update({
            "id": item["id"], "question": question, "note": item["note"],
            "expect_mode": item["expect_mode"], "source": item["source"],
            "category": item["category"],
            "selfcheck": bool(item.get("selfcheck")),
            "seconds": round(time.perf_counter() - t0, 2),
        })
        results.append(verdict)
        flag = "✅" if verdict["ok"] else ("🔴(自检)" if verdict["selfcheck"] else "❌")
        print(f"    {flag} mode={verdict['actual_mode']} status={verdict['actual_status']} "
              f"{verdict['seconds']}s" + ("" if verdict["ok"] else f"  {verdict['failures']}"), flush=True)

    return {
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": str(DEFAULT_SET.relative_to(ROOT)),
        "model": llm.model_name() if use_llm else None,
        "used_llm": bool(use_llm),
        "setup": {"state_root": _display_path(state_root), "scan_import_error": setup["scan_error"],
                  "docs": sorted(setup["corpus"]) + ["扫描件.pdf"]},
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "count": len(results),
        "results": results,
    }


def metrics(report: dict) -> dict[str, Any]:
    results = report["results"]
    total = len(results) or 1
    mode_ok = sum(1 for item in results if item["mode_ok"])
    reverse_ok = sum(1 for item in results if not item["failures"])
    # 数字可信度：只统计"本条真的判了数字"的那些（numbers_from=none 的不进分母）
    number_items = [item for item in results if item.get("numbers_judged")]
    number_clean = [item for item in number_items if not item["number_violations"]]
    leaks = sum(len(item["leaked_words"]) for item in results)
    failures = [item for item in results if item["failures"]]
    coverage: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = coverage.setdefault(item["category"], {"count": 0, "passed": 0})
        bucket["count"] += 1
        bucket["passed"] += 0 if item["failures"] else 1
    return {
        "coverage": coverage,
        "mode_accuracy": {"passed": mode_ok, "total": total, "rate": round(mode_ok / total, 4)},
        "must_not_pass_rate": {"passed": reverse_ok, "total": total, "rate": round(reverse_ok / total, 4)},
        "number_trust": {"passed": len(number_clean), "total": len(number_items),
                         "rate": round(len(number_clean) / len(number_items), 4) if number_items else None},
        "internal_term_leaks": {"count": leaks,
                                "items": sorted({item["id"] for item in results if item["leaked_words"]})},
        "failures": {"count": len(failures),
                     "selfcheck_red": sum(1 for item in failures if item["selfcheck"]),
                     "product_or_set": sum(1 for item in failures if not item["selfcheck"])},
    }


def write_outputs(report: dict) -> tuple[Path, Path]:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"eval_result_{stamp}.json"
    md_path = out_dir / f"eval_summary_{stamp}.md"
    summary = metrics(report)
    report["metrics"] = summary
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 评测跑分 · {report['generated_at']}",
        "",
        f"- 评测集：`{report['eval_set']}`（{report['count']} 条）",
        f"- 是否用模型：{'是：' + str(report['model']) if report['used_llm'] else '否（确定性跑分，可复现）'}",
        f"- 耗时：{report['elapsed_seconds']}s ｜ 语料：{', '.join(report['setup']['docs'])}",
        "",
        "## 一、总分",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 判档准确率 | {summary['mode_accuracy']['passed']}/{summary['mode_accuracy']['total']}"
        f" = {summary['mode_accuracy']['rate']:.1%} |",
        f"| 反向断言通过率 | {summary['must_not_pass_rate']['passed']}/{summary['must_not_pass_rate']['total']}"
        f" = {summary['must_not_pass_rate']['rate']:.1%} |",
        f"| 数字可信度（有出处的数字 / 判过的条数） | {summary['number_trust']['passed']}"
        f"/{summary['number_trust']['total']}"
        + (f" = {summary['number_trust']['rate']:.1%}" if summary['number_trust']['rate'] is not None else "")
        + " |",
        f"| 内部名词泄漏计数 | {summary['internal_term_leaks']['count']} |",
        f"| 失败条数 | {summary['failures']['count']}（其中自检红线 {summary['failures']['selfcheck_red']} 条）|",
        "",
        "## 二、覆盖分布（按类目）",
        "",
        "| 类目 | 条数 | 通过 |",
        "|---|---|---|",
        *[f"| {name} | {bucket['count']} | {bucket['passed']} |"
          for name, bucket in sorted(summary["coverage"].items(), key=lambda pair: -pair[1]["count"])],
        "",
        "## 三、逐条失败清单",
        "",
    ]
    failures = [item for item in report["results"] if item["failures"]]
    if not failures:
        lines.append("（无）")
    for item in failures:
        tag = "🔴 自检红线（故意红的）" if item["selfcheck"] else "❌"
        lines += [
            f"### {tag} {item['id']} · {item['question']}",
            "",
            f"- 期望档位：`{item['expect_mode']}` ｜ 实际档位：`{item['actual_mode']}` ｜ "
            f"状态：`{item['actual_status']}` ｜ 来源：{item['source']}",
            f"- 这一条在验什么：{item['note']}",
            "- 失败项：",
            *[f"    - {failure}" for failure in item["failures"]],
            f"- 实际回答摘录：{item['answer_excerpt']}",
            "",
        ]
    lines += ["", "## 四、全部条目", "",
              "| id | 类目 | 档位(期望/实际) | 结果 | 问题 |", "|---|---|---|---|---|"]
    for item in report["results"]:
        mark = "✅" if not item["failures"] else ("🔴" if item["selfcheck"] else "❌")
        lines.append(f"| {item['id']} | {item['category']} | {item['expect_mode']}/{item['actual_mode']} "
                     f"| {mark} | {item['question']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="FR-011 ② 真实问题评测集跑分")
    parser.add_argument("--set", default=str(DEFAULT_SET), help="评测集 jsonl 路径")
    parser.add_argument("--only", default=None, help="只跑某个 id 前缀")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--llm", action="store_true", help="用真模型跑（默认确定性跑分）")
    parser.add_argument("--state-root", default=None, help="隔离目录（默认 outputs/_eval_state）")
    args = parser.parse_args()

    items = load_items(Path(args.set))
    report = run(items, use_llm=args.llm, only=args.only, limit=args.limit,
                 state_root=Path(args.state_root) if args.state_root else None)
    json_path, md_path = write_outputs(report)
    summary = report["metrics"]

    print("\n════════ 跑分结果 ════════")
    print(f"条数            : {report['count']}")
    print(f"判档准确率      : {summary['mode_accuracy']['passed']}/{summary['mode_accuracy']['total']}"
          f" = {summary['mode_accuracy']['rate']:.1%}")
    print(f"反向断言通过率  : {summary['must_not_pass_rate']['passed']}/{summary['must_not_pass_rate']['total']}"
          f" = {summary['must_not_pass_rate']['rate']:.1%}")
    print(f"数字可信度      : {summary['number_trust']['passed']}/{summary['number_trust']['total']}")
    print(f"内部名词泄漏    : {summary['internal_term_leaks']['count']}")
    print(f"失败            : {summary['failures']['count']} "
          f"（自检红线 {summary['failures']['selfcheck_red']}）")
    print(f"结果文件        : {_display_path(json_path)}")
    print(f"人读摘要        : {_display_path(md_path)}")

    if summary["failures"]["count"] == 0:
        return 0
    if summary["failures"]["product_or_set"] == 0:
        print("\n（只有自检红线样例是红的 —— 这正是它存在的意义：这把尺子**会**失败。）")
        return 2
    print("\n有真失败：见失败清单（要人判断是产品问题还是评测集写错了）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
