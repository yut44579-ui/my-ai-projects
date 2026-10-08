#!/usr/bin/env python
"""task6_e2e.py · TASK-006 真闭环验收（**真服务 + 真数据 + 真 HTTP + 真浏览器**）。

    .venv/Scripts/python.exe scripts/task6_e2e.py            # 默认端口 8516
    .venv/Scripts/python.exe scripts/task6_e2e.py --no-browser   # 跳过浏览器那一步

════════════════════════════════════════════════════════════════════════
【它验什么（AC-01..AC-12 的端到端证据）】
════════════════════════════════════════════════════════════════════════
① **参考值在脚本进程内独立算**：本文件自己手写 D16 三条掩码 + 客户聚合，
   不 import `app.ai.tools` 的一行取数/聚合逻辑 —— 两条路径独立，比对才算数；
② 起真 uvicorn（`app.api:app`），走真 HTTP 打 `/api/chat`：
   客户 TOP / 购买次数 / 复购率 / 数据集内新客 / 沉睡客户 / 商品 TOP / 商品趋势 /
   商品退货 / VIP 拒绝 / 客户地区拒绝 —— 每一条都与 ① 的参考值逐项比对；
③ **真浏览器**（本机 Edge，`--headless --virtual-time-budget … --dump-dom`）打开真页面、
   由页面的深链 `#/overview?ask=…` 自动提问，把渲染完的 DOM 抓回来检查：
   页面上真的出现了"客户范围""退货/取消""沉睡客户"这些分区与数字，且没有 undefined/NaN。
   （不装 Playwright —— 用 Edge 自带能力，与 TASK-004 的渲染层验收同一个办法。）

为什么不做成 pytest：pytest 里起真服务、拉真浏览器会让全量测试变得又慢又脆；
真链路的证据单独一个脚本跑，测试文件只负责"确定性计算对不对"。
"""

from __future__ import annotations

import argparse

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from app.engine import executor, loader  # noqa: E402

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

MONTH = ("2011-11-01", "2011-11-30")
PASSED: list[str] = []
FAILED: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"  {'✅' if condition else '❌'} {label}" + (f" —— {extra}" if extra else ""), flush=True)
    return condition


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


# ════════════════════════════════════════════════════════════════════════
# ① 独立参考实现（与 app/ai/tools.py **不是**同一条代码路径）
# ════════════════════════════════════════════════════════════════════════
def reference() -> dict:
    frame = loader.load_raw()
    low, high = pd.Timestamp(MONTH[0]), pd.Timestamp(MONTH[1]) + pd.Timedelta(days=1)
    window = frame.loc[(frame["InvoiceDate"] >= low) & (frame["InvoiceDate"] < high)].copy()
    valid = window.loc[
        ~window["InvoiceNo"].astype("string").str.startswith("C").fillna(False)
        & (window["Quantity"] > 0)
        & (window["UnitPrice"] > 0)
    ].copy()
    valid["_amount"] = valid["Quantity"] * valid["UnitPrice"]

    # 全数据集的首购/末购（新客与沉睡要用）
    all_valid = frame.loc[
        ~frame["InvoiceNo"].astype("string").str.startswith("C").fillna(False)
        & (frame["Quantity"] > 0)
        & (frame["UnitPrice"] > 0)
    ]
    # **归一成日期**再比：口径是"日期减日期"，留着时分秒相减会向下取整，
    # 阈值正好在边界上的客户（如 last=11-09 10:31、参考日=12-09）会被漏掉一天。
    first = all_valid.groupby("CustomerID")["InvoiceDate"].min().dt.normalize()
    last = all_valid.groupby("CustomerID")["InvoiceDate"].max().dt.normalize()

    scoped = valid.dropna(subset=["CustomerID"])
    grouped = scoped.groupby("CustomerID")
    customers = pd.DataFrame({
        "amount": grouped["_amount"].sum(),
        "purchase_count": grouped["InvoiceNo"].nunique(),
    })
    top = customers.sort_values(["amount", "CustomerID"], ascending=[False, True], kind="mergesort")
    by_count = customers.sort_values(
        ["purchase_count", "CustomerID"], ascending=[False, True], kind="mergesort"
    )
    window_customers = customers.index
    first_win = first.reindex(window_customers)
    last_win = last.reindex(window_customers)
    days_idle = (pd.Timestamp(frame["InvoiceDate"].max().date()) - last_win).dt.days   # 两侧都是日期

    negative = window["Quantity"] < 0
    cancel = window["InvoiceNo"].astype("string").str.startswith("C").fillna(False)

    products = all_valid.groupby("StockCode")  # 用于商品 TOP 参考
    return {
        "total_amount": float(executor.compute_sales_amount(*MONTH)["amount"]),
        "window_rows": int(len(window)),
        "null_customer_rows": int(window["CustomerID"].isna().sum()),
        "scope_amount": float(scoped["_amount"].sum()),
        "scope_share": float(scoped["_amount"].sum() / valid["_amount"].sum()),
        "customer_count": int(len(customers)),
        "top1_customer": int(top.index[0]),
        "top1_amount": float(top["amount"].iloc[0]),
        "top10": [(int(cid), float(row["amount"])) for cid, row in top.head(10).iterrows()],
        "count_top1_customer": int(by_count.index[0]),
        "count_top1_purchase": int(by_count["purchase_count"].iloc[0]),
        "count_top10": [
            (int(cid), int(row["purchase_count"])) for cid, row in by_count.head(10).iterrows()
        ],
        "repeat_customers": int((customers["purchase_count"] >= 2).sum()),
        "repeat_rate": float((customers["purchase_count"] >= 2).sum() / len(customers)),
        "new_customers": int(((first_win >= pd.Timestamp(MONTH[0])) & (first_win <= pd.Timestamp(MONTH[1]))).sum()),
        "inactive_90": int((days_idle >= 90).sum()),
        # 全数据集口径的沉睡客户（问句没给时间范围时，目标客户 = 全数据集的客户）
        "inactive_90_full": int(((pd.Timestamp(frame["InvoiceDate"].max().date()) - last).dt.days >= 90).sum()),
        "top_product": str(
            all_valid.assign(_amount=all_valid["Quantity"] * all_valid["UnitPrice"])
            .groupby("StockCode")["_amount"].sum().sort_values(ascending=False).index[0]
        ),
        "negative_rows": int(negative.sum()),
        "cancel_rows": int(cancel.sum()),
        "overlap_rows": int((negative & cancel).sum()),
        "union_rows": int((negative | cancel).sum()),
    }


# ════════════════════════════════════════════════════════════════════════
# ② 起真服务 + 真 HTTP
# ════════════════════════════════════════════════════════════════════════
def start_server(port: int, env: dict) -> subprocess.Popen:
    log_path = Path(env["SRA_STATE_DIR"]).parent / "server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=env, cwd=str(PROJECT_ROOT),
    )
    return proc


def ask(client: httpx.Client, question: str) -> dict:
    response = client.post("/api/chat", json={"question": question, "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-006 真闭环验收")
    parser.add_argument("--port", type=int, default=8516)
    parser.add_argument("--no-browser", action="store_true", help="跳过真浏览器那一步")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    sandbox = tempfile.mkdtemp(prefix="sra_task6_e2e_", dir=str(PROJECT_ROOT / "outputs"))
    env = dict(os.environ)
    for key, name in (
        ("SRA_STATE_DIR", "state"), ("SRA_DOC_DIR", "documents"),
        ("SRA_UPLOAD_DIR", "uploads"), ("SRA_OUTPUT_DIR", "outputs"),
    ):
        env[key] = os.path.join(sandbox, name)
        os.makedirs(env[key], exist_ok=True)
    print(f"落盘沙箱（跑完可删）：{sandbox}")

    step("① 进程内独立复算参考值（不经过 app/ai 一行）")
    ref = reference()
    print(f"   2011-11 有效行金额 = {ref['total_amount']:.2f}；客户数 = {ref['customer_count']}")
    check(ref["total_amount"] > 0 and ref["customer_count"] > 0, "参考值算出来了（与 Agent 无关）")
    check(ref["union_rows"] != ref["negative_rows"] + ref["cancel_rows"], "两个退货口径**重叠**（相加会重复计数）")

    proc = start_server(args.port, env)
    try:
        client = httpx.Client(base_url=base, timeout=900.0)
        for _ in range(240):
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("服务 120 秒内没起来")

        step("② 真 HTTP：客户 / 商品七类问题 + 两条拒绝")
        caps = client.get("/api/chat/capabilities").json()
        names = [item["name"] for item in caps["intents"]]
        check(names == [
            "sales_summary", "sales_trend", "top_products", "sales_compare",
            "sales_breakdown_by_country", "customer_analysis", "product_analysis",
        ], "能力清单里恰好 7 个 Intent（只加了两个）", " / ".join(names))

        # ── 客户 TOP ──────────────────────────────────────────────────
        record = ask(client, "2011年11月销售额最高的10个客户")
        facts = record["facts"]
        items = record["items"]
        check(record["intent"]["intent"] == "customer_analysis", "客户排行走的是 customer_analysis")
        check(
            [(item["customer_id"], item["sales_amount"]) for item in items] == ref["top10"],
            "客户 TOP10 与独立参考**逐项一致**",
            f"榜首 {items[0]['customer_id']} = {items[0]['sales_amount']:.2f}",
        )
        scope = facts["customer_scope"]
        check(scope["customer_id_null_rows"] == ref["null_customer_rows"],
              "区间内无客户号行数与参考一致", f"{scope['customer_id_null_rows']}")
        check(abs(scope["customer_scope_sales_amount"] - ref["scope_amount"]) < 1e-6,
              "客户范围金额与参考一致", f"{scope['customer_scope_sales_amount']:.2f}")
        check(scope["total_sales_amount"] == ref["total_amount"],
              "总销售额仍**含**无客户号行（= executor 口径）", f"{scope['total_sales_amount']:.2f}")
        check(abs(scope["customer_scope_sales_share"] - ref["scope_share"]) < 1e-12,
              "覆盖率由代码算出且与参考一致", f"{scope['customer_scope_sales_share'] * 100:.2f}%")
        # 浮点求和**不满足结合律**：两个子集各自求和相加，与整体求和可能差 ~1e-9，
        # 所以这里用容差而不是 `==`（差的是舍入，不是口径）。
        check(abs(scope["customer_scope_sales_amount"] + scope["null_customer_sales_amount"]
                  - scope["total_sales_amount"]) < 1e-6,
              "客户范围金额 + 无客户号金额 = 总销售额（两套口径同时成立）",
              f"差 {abs(scope['customer_scope_sales_amount'] + scope['null_customer_sales_amount'] - scope['total_sales_amount']):.2e}")

        # ── 购买次数（distinct InvoiceNo）───────────────────────────────
        record = ask(client, "2011年11月购买次数最多的10个客户")
        check(
            [(item["customer_id"], item["purchase_count"]) for item in record["items"]] == ref["count_top10"],
            "购买次数 TOP10 与独立参考一致（按 distinct InvoiceNo，不是行数）",
        )
        check(record["items"][0]["rows"] > record["items"][0]["purchase_count"],
              "榜首的行数 > 购买次数（证明「次数 ≠ 行数」这条真的生效了）")

        # ── 复购率 ────────────────────────────────────────────────────
        record = ask(client, "2011年11月的复购率是多少")
        rfacts = record["facts"]
        check(rfacts["total_customers"] == ref["customer_count"], "有成交客户数与参考一致")
        check(rfacts["repeat_customers"] == ref["repeat_customers"], "复购客户数与参考一致")
        check(abs(rfacts["repeat_rate"] - rfacts["repeat_customers"] / rfacts["total_customers"]) < 1e-12,
              "复购率 = 复购客户 ÷ 客户数（三个数可互相验算）")

        # ── 数据集内新客 ──────────────────────────────────────────────
        record = ask(client, "2011年11月有多少新客")
        check(record["facts"]["new_customers"] == ref["new_customers"],
              "数据集内新客数与参考一致", f"{record['facts']['new_customers']}")
        check("数据集内新客" in record["answer"]["text"], "回答里明确写的是「数据集内新客」")

        # ── 沉睡客户 ──────────────────────────────────────────────────
        record = ask(client, "2011年11月有多少沉睡客户（90天没买）")
        check(record["facts"]["inactive_customers"] == ref["inactive_90"],
              "2011-11 目标客户里的沉睡客户数与参考一致", f"{record['facts']['inactive_customers']}")
        # 换一个**非退化**的问法：没给区间 = 全数据集客户，数字明显不为 0
        record = ask(client, "90天没买货的沉睡客户有多少")
        check(record["facts"]["inactive_customers"] == ref["inactive_90_full"] > 0,
              "全数据集口径的沉睡客户数与参考一致", f"{record['facts']['inactive_customers']}")
        check(record["facts"]["inactive_days"] == 90, "阈值天数取自问题里的「90天」")
        dumped = json.dumps(record, ensure_ascii=False).lower()
        check("churn" not in dumped and "流失" not in dumped,
              "记录里没有 churn_* 字段、也没把这件事叫「流失」")
        check("规则型" in record["answer"]["text"], "回答里写明了这是规则型判定（不是预测）")

        # ── 商品 TOP / 趋势 / 退货 ─────────────────────────────────────
        record = ask(client, "2011年11月卖得最好的5个产品")
        check(record["intent"]["intent"] == "top_products", "商品排行仍然走既有的 top_products（回归）")

        record = ask(client, "2011年11月各商品的退货情况前10名")
        ifacts = record["facts"]
        check(record["intent"]["intent"] == "product_analysis"
              and ifacts["operation"] == "return", "退货问题走 product_analysis(return)")
        check(ifacts["negative_quantity_rows"] == ref["negative_rows"]
              and ifacts["cancel_invoice_rows"] == ref["cancel_rows"],
              "两个退货口径各自与参考一致",
              f"数量为负 {ifacts['negative_quantity_rows']} 行 / C 单 {ifacts['cancel_invoice_rows']} 行")
        check(ifacts["overlap_rows"] == ref["overlap_rows"] > 0, "重叠样本被正确识别",
              f"重叠 {ifacts['overlap_rows']} 行")
        check(ifacts["return_or_cancel_rows"] == ref["union_rows"],
              "统一指标 = **去重并集**（不是 a+b）", f"{ifacts['return_or_cancel_rows']} 行")
        check(ifacts["return_or_cancel_rows"] != ifacts["negative_quantity_rows"] + ifacts["cancel_invoice_rows"],
              "相加的结果 ≠ 并集（这条专门盯住「合并成一个退货率」的错法）")

        record = ask(client, "85123A 和 10002 在2011年11月的销售趋势")
        tfacts = record["facts"]
        check(tfacts["operation"] == "trend" and tfacts["product_codes"] == ["85123A", "10002"],
              "商品趋势按商品分别聚合，编码来自问题本身")
        check(len(record["series"]["products"]) == 2, "返回两条**独立**的商品序列")
        check(tfacts["bucket_count"] > 0, "时间桶完整", f"{tfacts['bucket_count']} 个桶")

        # ── 两条必须拒绝 ──────────────────────────────────────────────
        record = ask(client, "2011年11月VIP客户销售额最高的10个")
        check(record["status"] == "unsupported" and record["items"] is None,
              "VIP 客户 → 明确拒绝，**没有**降级成销售额 TOP10")
        record = ask(client, "按客户地区看销售额")
        check(record["status"] == "unsupported", "客户地区 → 明确拒绝（客户维度没有地区字段）")
        record = ask(client, "华南区上个月卖了多少")
        check(record["status"] == "unsupported", "区域拒绝行为没有因为 TASK-006 改变（回归）")

        # ── 浏览器步骤用的深链（真页面自动提问）───────────────────────
        step("③ 真浏览器（Edge headless + 页面深链）")
        if args.no_browser:
            print("   已跳过（--no-browser）")
        else:
            edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
            if edge is None:
                print("   ⚠️ 没找到 Edge，跳过浏览器这一步（不算失败，但请手工用 CDP 走查）")
            else:
                # 每条：问题 + 页面上必须出现的关键标记 + **参考值在页面上的样子**。
                # 最后一项是关键 —— 只看到"客户范围"这四个字不算数，覆盖率那个数字
                # 必须真的由后端算出来、经 HTTP 传到页面、渲染成文案（前端不做算术）。
                for question, must_contain, expected_value in (
                    ("2011年11月销售额最高的10个客户", "客户范围",
                     f"{ref['scope_share'] * 100:.2f}"),
                    ("2011年11月各商品的退货情况前10名", "退货或取消的行占比", None),
                    ("2011年11月有多少沉睡客户（90天没买）", "沉睡客户", None),
                ):
                    dom = dump_dom(edge, f"{base}/#/overview?ask={urllib.parse.quote(question)}")
                    ok = (
                        dom is not None
                        and must_contain in dom
                        and "undefined" not in dom
                        and "NaN" not in dom
                        and (expected_value is None or expected_value in dom)
                    )
                    check(ok, f"真页面上渲染出了「{question}」的结果",
                          f"找 {must_contain!r}"
                          + (f" 与覆盖率 {expected_value}%" if expected_value else "")
                          + f"（DOM {len(dom or '')} 字符）")

        step("④ 结果")
        print(f"   通过 {len(PASSED)} 项 / 失败 {len(FAILED)} 项")
        for item in FAILED:
            print(f"   ❌ {item}")
        return 0 if not FAILED else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"\n沙箱：{sandbox}（可删）")


def dump_dom(edge: str, url: str) -> str | None:
    """用 Edge 打开页面、跑完 JS（含深链自动提问），把渲染后的 DOM 抓回来。

    `--virtual-time-budget` 让浏览器"快进"到给定时间之后再 dump —— 否则
    `--dump-dom` 在 load 事件就抓，异步的 fetch 还没回来，抓到的是空壳。
    """
    profile = tempfile.mkdtemp(prefix="sra_edge_", dir=str(PROJECT_ROOT / "outputs"))
    try:
        completed = subprocess.run(
            [
                edge, "--headless", "--disable-gpu", "--no-first-run",
                "--user-data-dir=" + profile,
                "--virtual-time-budget=25000", "--dump-dom", url,
            ],
            capture_output=True, timeout=180,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"   ⚠️ 浏览器这一步没跑成：{type(exc).__name__}: {exc}")
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
