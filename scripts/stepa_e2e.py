#!/usr/bin/env python
"""stepa_e2e.py · STEP A 真闭环验收（**真服务 + 真数据 + 真 HTTP + 真浏览器**）。

    .venv/Scripts/python.exe scripts/stepa_e2e.py                 # 默认端口 8520
    .venv/Scripts/python.exe scripts/stepa_e2e.py --no-browser    # 跳过浏览器那一步

════════════════════════════════════════════════════════════════════════
【它验什么（A-01..A-14 的端到端证据）】
════════════════════════════════════════════════════════════════════════
① **参考值在本进程内独立算**：本文件自己手写 D16 三条掩码 + 客户/产品/销售聚合，
   一行都不 import `app.ai.tools` 的取数逻辑 —— 两条路径独立，比对才算数；
② 起真 uvicorn（`app/api:app`），走真 HTTP 打业务表端点：
   分页 / 排序 / 筛选 / 维度×指标 / 原始数据浏览器 —— 每一条都与 ① 逐项比对；
③ **导出与页面同源**：同一个查询条件再打一次导出端点，用 openpyxl 把 xlsx 读回来，
   条数、顺序、数值与**页面那次响应**逐项核对（csv 也一样读回来核对）；
④ **导入诚实边界**：真上传一个 xlsx → 检查 → 登记成数据集 → 拿到 dataset_id 与
   审计指纹；再用这个 dataset_id 查业务表必须被明确拒绝（分析未开通），
   证明系统没有拿未开通的数据源冒充结果；
⑤ **真浏览器**（本机 Edge，`--headless --virtual-time-budget … --dump-dom`）打开真页面：
   客户/产品/销售/原始数据四页的表格内容从渲染后的 DOM 里抓回来核对，
   并扫描页面上有没有技术实现信息（A-09）。

为什么不做成 pytest：pytest 里起真服务、拉真浏览器会让全量测试又慢又脆；
真链路的证据单独一个脚本跑（与 task6_e2e.py 同一个办法）。
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from app.engine import loader  # noqa: E402

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

MONTH = ("2011-11-01", "2011-11-30")
DAY = ("2011-11-07", "2011-11-07")          # 单日：原始数据浏览器与搜索都用它（行数够少）
PAGE_SIZES = (50, 100, 200)

PASSED: list[str] = []
FAILED: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"  {'✅' if condition else '❌'} {label}" + (f" —— {extra}" if extra else ""), flush=True)
    return condition


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol


# ════════════════════════════════════════════════════════════════════════
# ① 独立参考实现（与 app/ai/tools.py **不是**同一条代码路径）
# ════════════════════════════════════════════════════════════════════════
def _window(start: str, end: str) -> pd.DataFrame:
    """区间内的原始行（D16-2：含首尾全天）。"""
    frame = loader.load_raw()
    low = pd.Timestamp(start)
    high = pd.Timestamp(end) + pd.Timedelta(days=1)
    stamps = pd.to_datetime(frame["InvoiceDate"])
    return frame.loc[(stamps >= low) & (stamps < high)]


def _valid(frame: pd.DataFrame) -> pd.DataFrame:
    """有效行（D16-3/4/5 三条排除规则，命中多条只算一次）。"""
    invoice = frame["InvoiceNo"].astype("string")
    hit = (
        invoice.str.startswith("C").fillna(False)
        | (frame["Quantity"] <= 0)
        | (frame["UnitPrice"] <= 0)
    )
    valid = frame.loc[~hit].copy()
    valid["_amount"] = valid["Quantity"] * valid["UnitPrice"]
    return valid


def reference_customers(start: str, end: str) -> dict:
    valid = _valid(_window(start, end))
    scoped = valid.dropna(subset=["CustomerID"])
    rows = []
    for customer_id, block in scoped.groupby("CustomerID", sort=True):
        orders = block["InvoiceNo"].nunique()
        amount = float(block["_amount"].sum())
        rows.append({
            "customer_id": int(customer_id) if float(customer_id).is_integer() else float(customer_id),
            "sales_amount": amount,
            "order_count": int(orders),
            "purchase_count": int(orders),
            "avg_order_amount": amount / orders if orders else 0.0,
        })
    rows.sort(key=lambda item: (-item["sales_amount"], item["customer_id"]))
    # 首购/末购是**全数据集**视角（与窗口无关）：同一套有效行掩码，但不加时间限制
    all_valid = _valid(loader.load_raw())
    activity = all_valid.dropna(subset=["CustomerID"]).assign(
        _stamp=pd.to_datetime(all_valid.loc[all_valid["CustomerID"].notna(), "InvoiceDate"])
    )
    grouped = activity.groupby("CustomerID")["_stamp"]
    first = {float(key): value.date().isoformat() for key, value in grouped.min().items()}
    last = {float(key): value.date().isoformat() for key, value in grouped.max().items()}
    for item in rows:
        key = float(item["customer_id"])
        item["first_purchase"] = first.get(key)
        item["last_purchase"] = last.get(key)
    return {
        "total": len(rows),
        "rows": rows,
        "total_amount": float(sum(item["sales_amount"] for item in rows)),
        "total_purchase_count": int(sum(item["purchase_count"] for item in rows)),
    }


def reference_products(start: str, end: str) -> dict:
    window = _window(start, end)
    valid = _valid(window)
    rows = []
    grouped = valid.groupby("StockCode", dropna=False)
    for stock_code, block in grouped:
        names = block["Description"].dropna()
        counts = names.value_counts()
        description = ""
        if not counts.empty:
            description = sorted(str(name) for name in counts[counts == counts.max()].index)[0]
        rows.append({
            "stock_code": str(stock_code),
            "description": description,
            "sales_amount": float(block["_amount"].sum()),
            "quantity": float(block["Quantity"].sum()),
            "order_count": int(block["InvoiceNo"].nunique()),
        })
    # 退货列按**原始行**统计：数量为负 或 取消单
    invoice = window["InvoiceNo"].astype("string")
    blocked = window.loc[(window["Quantity"] < 0) | invoice.str.startswith("C").fillna(False)]
    blocked = blocked.assign(_stock=blocked["StockCode"].map(str))
    returns = blocked.groupby("_stock").agg(
        return_rows=("InvoiceNo", "size"), return_quantity=("Quantity", "sum")
    )
    for item in rows:
        item["return_rows"] = int(returns["return_rows"].get(item["stock_code"], 0))
        item["return_quantity"] = float(returns["return_quantity"].get(item["stock_code"], 0.0))
    rows.sort(key=lambda item: (-item["sales_amount"], item["stock_code"]))
    return {"total": len(rows), "rows": rows,
            "total_amount": float(sum(item["sales_amount"] for item in rows))}


def reference_sales(start: str, end: str, dimension: str) -> dict:
    """维度聚合。day/week 的桶起点与项目约定一致（周以**周一**为起点），且不补零。"""
    low = pd.Timestamp(start)
    high = pd.Timestamp(end) + pd.Timedelta(days=1)
    valid = _valid(_window(start, end))
    if dimension == "country":
        keys = valid["Country"].astype(str)
        buckets: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
        for key, block in valid.groupby("Country", dropna=False):
            text = str(key)
            buckets[text] = (low, high)
            del block
    else:
        stamps = pd.to_datetime(valid["InvoiceDate"])
        if dimension == "day":
            period = stamps.dt.normalize()
            span = 0
        else:
            offset = (stamps.dt.weekday - 0) % 7          # 周一为起点
            period = stamps.dt.normalize() - pd.to_timedelta(offset, unit="D")
            span = 6
        buckets = {}
        for stamp in sorted(set(period)):
            buckets[stamp.date().isoformat()] = (
                stamp, stamp + pd.Timedelta(days=span)
            )
        valid = valid.assign(_bucket=period.dt.date.astype(str))

    result = {}
    for key, (period_start, period_end) in buckets.items():
        if dimension == "country":
            block = valid.loc[valid["Country"].astype(str) == key]
            partial = False
        else:
            block = valid.loc[valid["_bucket"] == key]
            partial = (period_start.date() < _dt.date.fromisoformat(start)
                       or period_end.date() > _dt.date.fromisoformat(end))
        orders = int(block["InvoiceNo"].nunique())
        amount = float(block["_amount"].sum())
        result[key] = {
            "dimension_value": key,
            "sales_amount": amount,
            "order_count": orders,
            "customer_count": int(block["CustomerID"].nunique()),
            "avg_order_amount": amount / orders if orders else 0.0,
            "partial": bool(partial),
        }
    return result


# ════════════════════════════════════════════════════════════════════════
# ② 起真服务
# ════════════════════════════════════════════════════════════════════════
def start_server(port: int, env: dict) -> subprocess.Popen:
    log_path = Path(env["SRA_STATE_DIR"]).parent / "server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    return subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=env, cwd=str(PROJECT_ROOT),
    )


def table(client: httpx.Client, name: str, **params) -> dict:
    response = client.get(f"/api/tables/{name}", params=params)
    assert response.status_code == 200, f"{name} {params} → {response.status_code} {response.text[:300]}"
    return response.json()


def export(client: httpx.Client, name: str, fmt: str, **params):
    response = client.get(f"/api/tables/{name}/export", params={"format": fmt, **params})
    assert response.status_code == 200, response.text[:300]
    return response


# ════════════════════════════════════════════════════════════════════════
# ③ 真浏览器
# ════════════════════════════════════════════════════════════════════════
def dump_dom(edge: str, url: str, width: int = 1600) -> str | None:
    profile = tempfile.mkdtemp(prefix="sra_edge_", dir=str(PROJECT_ROOT / "outputs"))
    try:
        completed = subprocess.run(
            [
                edge, "--headless", "--disable-gpu", "--no-first-run",
                "--user-data-dir=" + profile, f"--window-size={width},1000",
                "--virtual-time-budget=25000", "--dump-dom", url,
            ],
            capture_output=True, timeout=240,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"   ⚠️ 浏览器这一步没跑成：{type(exc).__name__}: {exc}")
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def page_text(dom: str) -> str:
    """把 DOM 里的标签剥掉，只留**渲染出来的文字**（页面上真正看得到的东西）。"""
    body = re.search(r"<body[^>]*>(.*?)</body>", dom, re.S)
    inner = body.group(1) if body else dom
    inner = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", inner, flags=re.S)
    inner = re.sub(r"<!--.*?-->", " ", inner, flags=re.S)
    inner = re.sub(r"<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", inner.replace("&nbsp;", " ")).strip()


def block_of(dom: str, element_id: str) -> str:
    """抠出某个 id 元素（到它的闭合标签为止，按同类标签配平）。"""
    match = re.search(rf'<(\w+)[^>]*id="{re.escape(element_id)}"[^>]*>', dom)
    if not match:
        return ""
    tag = match.group(1)
    depth = 0
    for token in re.finditer(rf"<(/?){tag}\b[^>]*?(/?)>", dom[match.start():]):
        if token.group(2):
            continue
        depth += -1 if token.group(1) else 1
        if depth == 0:
            return dom[match.start(): match.start() + token.end()]
    return dom[match.start():]


def main() -> int:
    parser = argparse.ArgumentParser(description="STEP A 真闭环验收")
    parser.add_argument("--port", type=int, default=8520)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    sandbox = tempfile.mkdtemp(prefix="sra_stepa_e2e_", dir=str(PROJECT_ROOT / "outputs"))
    env = dict(os.environ)
    for key, name in (
        ("SRA_STATE_DIR", "state"), ("SRA_DOC_DIR", "documents"),
        ("SRA_UPLOAD_DIR", "uploads"), ("SRA_OUTPUT_DIR", "outputs"),
    ):
        env[key] = os.path.join(sandbox, name)
        os.makedirs(env[key], exist_ok=True)
    print(f"落盘沙箱（跑完可删）：{sandbox}")

    step("① 进程内独立复算参考值（不 import app/ai 一行）")
    ref_customers = reference_customers(*MONTH)
    ref_products = reference_products(*MONTH)
    print(f"   2011-11 客户 {ref_customers['total']} 个（客户号非空），产品 {ref_products['total']} 个；"
          f"客户口径销售额合计 {ref_customers['total_amount']:.2f}")
    check(ref_customers["total"] > 1000 and ref_products["total"] > 1000, "参考值算出来了（与 Agent 无关）")

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

        # ── A-01 / A-04 / A-05：客户表（真表 / 后端排序 / 后端分页）─────────
        step("② A-01 / A-04 / A-05：客户表 分页 + 排序（后端算）")
        body = table(client, "customers", start=MONTH[0], end=MONTH[1],
                     page=1, page_size=50, sort="sales_amount", order="desc")
        keys = [column["key"] for column in body["columns"]]
        check(keys == ["customer_id", "sales_amount", "order_count", "purchase_count",
                       "avg_order_amount", "first_purchase", "last_purchase"],
              "客户表七列齐（客户号/销售额/订单数/购买次数/平均每单/首购/末购）", " / ".join(keys))
        check(body["total"] == ref_customers["total"],
              "total 与独立参考一致", f"{body['total']} vs {ref_customers['total']}")
        check(body["page"] == 1 and body["page_size"] == 50 and len(body["items"]) == 50,
              "第一页 50 行（page/page_size/items 全部来自后端）")
        head = ref_customers["rows"][:50]
        mismatch = [
            (item["customer_id"], item["sales_amount"], want["customer_id"], want["sales_amount"])
            for item, want in zip(body["items"], head)
            if item["customer_id"] != want["customer_id"] or not close(item["sales_amount"], want["sales_amount"])
        ]
        check(not mismatch, "第 1 页 50 行与独立参考**逐项一致**（含金额）", str(mismatch[:2]))
        check(all(close(item["avg_order_amount"], item["sales_amount"] / item["order_count"])
                  for item in body["items"]), "平均每单 = 销售额 ÷ 订单数（逐行核对）")
        check(all(item["first_purchase"] and item["last_purchase"] for item in body["items"]),
              "首购/末购日期非空", str(body["items"][0]["first_purchase"]))

        page2 = table(client, "customers", start=MONTH[0], end=MONTH[1],
                      page=2, page_size=50, sort="sales_amount", order="desc")
        check([item["customer_id"] for item in page2["items"]]
              == [item["customer_id"] for item in ref_customers["rows"][50:100]],
              "第 2 页 50 行与参考的第 51~100 名一致")
        last_page = (ref_customers["total"] + 49) // 50
        page_last = table(client, "customers", start=MONTH[0], end=MONTH[1],
                          page=last_page, page_size=50, sort="sales_amount", order="desc")
        check(page_last["page_count"] == last_page and page_last["page"] == last_page,
              "最后一页页码与总页数一致", f"page {page_last['page']}/{page_last['page_count']}")
        check([item["customer_id"] for item in page_last["items"]]
              == [item["customer_id"] for item in ref_customers["rows"][(last_page - 1) * 50:]],
              "最后一页行与参考一致")

        asc = table(client, "customers", start=MONTH[0], end=MONTH[1],
                    page=1, page_size=50, sort="sales_amount", order="asc")
        # 升序的并列打破规则与降序一样是「客户号升序」——所以不能把降序结果直接倒过来（并列行序会错），
        # 参考值按 (销售额 asc, 客户号 asc) 独立排一次
        ref_asc = sorted(ref_customers["rows"],
                         key=lambda item: (item["sales_amount"], item["customer_id"]))
        check([item["customer_id"] for item in asc["items"]]
              == [item["customer_id"] for item in ref_asc[:50]],
              "换 order=asc → 后端返回最小的 50 个客户（排序真的在后端）")
        by_id = table(client, "customers", start=MONTH[0], end=MONTH[1],
                      page=1, page_size=50, sort="customer_id", order="asc")
        check([item["customer_id"] for item in by_id["items"]]
              == sorted(item["customer_id"] for item in ref_customers["rows"])[:50],
              "换 sort=customer_id → 按客户号升序（另一列也是后端排的）")

        search = table(client, "customers", start=MONTH[0], end=MONTH[1],
                       search=str(ref_customers["rows"][0]["customer_id"]), page=1, page_size=50)
        check(search["total"] >= 1 and all(
            str(ref_customers["rows"][0]["customer_id"]) in str(item["customer_id"])
            for item in search["items"]), "按客户号搜索命中且只命中该客户")

        summary = body.get("summary") or {}
        check(close(summary.get("sales_amount", 0), ref_customers["total_amount"]),
              "表下方合计销售额与参考一致",
              f"{summary.get('sales_amount')} vs {ref_customers['total_amount']:.2f}")
        dataset_block = body.get("dataset") or {}
        check(bool(dataset_block.get("name")) and not any(
            field in dataset_block for field in
            ("file_hash", "schema_hash", "stored_path", "sha256", "audit", "source_file_id")),
              "表响应里的数据源块**不含**任何审计字段（哈希/路径/文件编号）",
              " / ".join(sorted(dataset_block)))

        # ── A-02：产品表 ────────────────────────────────────────────────
        step("③ A-02：产品表（含退货列）")
        prod = table(client, "products", start=MONTH[0], end=MONTH[1],
                     page=1, page_size=50, sort="sales_amount", order="desc")
        prod_keys = [column["key"] for column in prod["columns"]]
        check(prod_keys == ["stock_code", "description", "sales_amount", "quantity",
                            "order_count", "return_rows", "return_quantity"],
              "产品表七列齐（商品编码/名称/销售额/数量/订单数/退货数/退货数量）", " / ".join(prod_keys))
        check(prod["total"] == ref_products["total"],
              "total 与独立参考一致", f"{prod['total']} vs {ref_products['total']}")
        bad = [
            (item["stock_code"], item["sales_amount"], want["sales_amount"])
            for item, want in zip(prod["items"], ref_products["rows"][:50])
            if item["stock_code"] != want["stock_code"]
            or not close(item["sales_amount"], want["sales_amount"])
            or not close(item["quantity"], want["quantity"])
            or item["order_count"] != want["order_count"]
            or item["return_rows"] != want["return_rows"]
        ]
        check(not bad, "TOP50 与参考逐项一致（销售额/数量/订单数/退货数）", str(bad[:2]))
        check(all(item["description"] == want["description"]
                  for item, want in zip(prod["items"], ref_products["rows"][:50])),
              "商品名称与参考一致（同码多名取最多的那个）")

        # ── A-03：销售表 维度 × 指标 ────────────────────────────────────
        step("④ A-03：销售表 维度（日/周/国家）× 指标（销售额/订单数/客户数/客单价）")
        seen: dict[str, dict] = {}
        for dimension in ("day", "week", "country"):
            ref = reference_sales(MONTH[0], MONTH[1], dimension)
            seen[dimension] = ref
            values = {item["dimension_value"]: item for item in
                      table(client, "sales", start=MONTH[0], end=MONTH[1], dimension=dimension,
                            metric="sales_amount", page=1, page_size=500, sort="dimension_value",
                            order="asc")["items"]}
            check(set(values) == set(ref.keys()),
                  f"{dimension} 维度：行（期间）与参考完全一致",
                  f"{len(values)} 行 vs 参考 {len(ref)} 行")
            wrong = [
                (key, values[key]["sales_amount"], ref[key]["sales_amount"],
                 values[key]["order_count"], ref[key]["order_count"],
                 values[key]["customer_count"], ref[key]["customer_count"])
                for key in ref
                if key in values and (
                    not close(values[key]["sales_amount"], ref[key]["sales_amount"])
                    or values[key]["order_count"] != ref[key]["order_count"]
                    or values[key]["customer_count"] != ref[key]["customer_count"]
                )
            ]
            check(not wrong, f"{dimension} 维度：销售额/订单数/客户数逐行一致", str(wrong[:2]))
            check(all(close(values[key]["avg_order_amount"],
                            ref[key]["avg_order_amount"]) for key in ref if key in values),
                  f"{dimension} 维度：客单价 = 销售额 ÷ 订单数")
            if dimension in ("day", "week"):
                check(all(values[key]["period_note"] == "" or "只统计" in values[key]["period_note"]
                          for key in values), f"{dimension} 维度：不完整期间有说明（不静默）")
                partials = {key for key in ref if ref[key]["partial"]}
                check({key for key in values if values[key]["period_note"]} == partials,
                      f"{dimension} 维度：partial 标记与参考一致", str(sorted(partials)[:3]))
        check(seen["day"] != seen["week"] and seen["week"] != seen["country"],
              "三个维度的聚合结果**互不相同**（切换维度真的换数据）")

        metric_views = {}
        for metric in ("sales_amount", "order_count", "customer_count", "avg_order_amount"):
            body_m = table(client, "sales", start=MONTH[0], end=MONTH[1], dimension="country",
                           metric=metric, page=1, page_size=500, sort="metric_value", order="desc")
            metric_views[metric] = [(item["dimension_value"], item["metric_value"])
                                    for item in body_m["items"]]
            check(body_m["summary"]["metric_label"] and body_m["summary"]["metric"] == metric,
                  f"指标 {metric}：后端回显当前指标标签", str(body_m["summary"].get("metric_label")))
        check(len({tuple(view) for view in metric_views.values()}) == 4,
              "四个指标给出的排序结果**互不相同**（换指标真的换数据）")
        top_metric = metric_views["sales_amount"][0]
        ref_country = seen["country"]
        check(close(top_metric[1], ref_country[top_metric[0]]["sales_amount"]),
              "指标排序榜首与参考一致", f"{top_metric[0]} = {top_metric[1]:.2f}")
        share = table(client, "sales", start=MONTH[0], end=MONTH[1], dimension="country",
                      metric="sales_amount", page=1, page_size=500, sort="metric_value", order="desc")
        share_sum = sum(float(item["metric_share"] or 0) for item in share["items"])
        check(close(share_sum, 100.0, 0.5), "占比列合计约 100%", f"{share_sum:.2f}%")
        avg_share = table(client, "sales", start=MONTH[0], end=MONTH[1], dimension="country",
                          metric="avg_order_amount", page=1, page_size=500)
        check(all(item["metric_share"] is None for item in avg_share["items"]),
              "客单价是平均值 → 占比列明确为空（不硬算一个数）")

        # ── A-07：原始数据浏览器 ────────────────────────────────────────
        step("⑤ A-07：原始数据浏览器（默认只给一页，不塞 54 万行）")
        raw_total_ref = int(len(_window(*DAY)))
        raw_first = table(client, "raw", start=DAY[0], end=DAY[1])
        check(raw_total_ref > 100, "这一天的原始行数（参考）", f"{raw_total_ref} 行")
        check(raw_first["total"] == raw_total_ref and raw_first["page_size"] == 50
              and len(raw_first["items"]) == 50,
              "首次加载只回 50 行（total 是真实总行数，不是 50）",
              f"total={raw_first['total']} page_size={raw_first['page_size']}")
        check([item["row_no"] for item in raw_first["items"]] == list(range(1, 51)),
              "默认按序号升序：第 1~50 行")
        for size in PAGE_SIZES:
            body_size = table(client, "raw", start=DAY[0], end=DAY[1], page=1, page_size=size)
            check(len(body_size["items"]) == min(size, raw_total_ref)
                  and body_size["page_size"] == size,
                  f"每页 {size} 行可选（三档都在）")
        raw_big = table(client, "raw", start="2010-12-01", end="2011-12-09")
        check(raw_big["total"] > 500000 and len(raw_big["items"]) == 50,
              "全区间 54 万行也**只回一页**（不吃满浏览器）",
              f"total={raw_big['total']}，首屏 {len(raw_big['items'])} 行")
        raw_sorted = table(client, "raw", start=DAY[0], end=DAY[1], page=1, page_size=50,
                           sort="Quantity", order="desc")
        quantities = [item["Quantity"] for item in raw_sorted["items"]]
        check(quantities == sorted(quantities, reverse=True),
              "按数量降序：返回的行本身有序（后端排的）", str(quantities[:5]))
        day_frame = _window(*DAY)
        check(quantities[0] == int(day_frame["Quantity"].max()),
              "降序榜首 = 独立参考的最大数量", f"{quantities[0]} vs {int(day_frame['Quantity'].max())}")
        raw_search = table(client, "raw", start=DAY[0], end=DAY[1], page=1, page_size=50,
                           search="C")
        check(raw_search["total"] < raw_total_ref and raw_search["total"] > 0
              and all("C" in json.dumps(item, ensure_ascii=False) for item in raw_search["items"]),
              "搜索「C」命中取消单且确实变少", f"{raw_search['total']} / {raw_total_ref}")

        # ── A-06：导出与页面同源 ────────────────────────────────────────
        step("⑥ A-06：导出 xlsx / csv 与页面同源（openpyxl 读回逐项核对）")
        query = {"start": MONTH[0], "end": MONTH[1], "sort": "sales_amount", "order": "desc"}
        page_view = table(client, "customers", page=1, page_size=50, **query)
        exported = export(client, "customers", "xlsx", **query)
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(exported.content))
        sheet = workbook.active
        header = [cell.value for cell in sheet[1]]
        rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
        check(header == [column["label"] for column in page_view["columns"]],
              "xlsx 表头 = 后端给的列名（同一处定义）", " / ".join(str(item) for item in header))
        check(len(rows) == ref_customers["total"],
              "xlsx 是**完整结果**（不是页面那 50 行）",
              f"{len(rows)} 行 vs 参考 {ref_customers['total']}")
        check([row[0] for row in rows[:50]] == [item["customer_id"] for item in page_view["items"]],
              "xlsx 前 50 行 = 页面第一页（同一条件同一排序）")
        check(all(close(row[1], want["sales_amount"])
                  for row, want in zip(rows, ref_customers["rows"])),
              "xlsx 每行金额与独立参考一致（全量比对）")

        csv_response = export(client, "customers", "csv", **query)
        text = csv_response.content.decode("utf-8-sig")
        csv_rows = list(csv.reader(io.StringIO(text)))
        check(csv_response.content[:3] == b"\xef\xbb\xbf",
              "csv 带 BOM（Excel 双击不乱码）")
        check(csv_rows[0] == [column["label"] for column in page_view["columns"]],
              "csv 表头与 xlsx 一致")
        check(len(csv_rows) - 1 == ref_customers["total"], "csv 行数与参考一致",
              f"{len(csv_rows) - 1} 行")
        check(close(float(csv_rows[1][1]), ref_customers["rows"][0]["sales_amount"]),
              "csv 第一行金额与参考一致", f"{csv_rows[1][1]}")
        for banned in ("sha256", "file_hash", "file_id", "dataset_id", "stored_path"):
            check(banned not in text.lower(), f"导出文件里没有审计字段：{banned}")
        sales_export = export(client, "sales", "csv", start=MONTH[0], end=MONTH[1],
                              dimension="country", metric="customer_count")
        sales_rows = list(csv.reader(io.StringIO(sales_export.content.decode("utf-8-sig"))))
        ref_country = seen["country"]
        check(len(sales_rows) - 1 == len(ref_country)
              and all(close(float(row[3]), ref_country[row[0]]["customer_count"])
                      for row in sales_rows[1:] if row[0] in ref_country),
              "销售表导出（维度×指标）与参考一致", f"{len(sales_rows) - 1} 行")
        too_big = client.get("/api/tables/raw/export", params={"format": "csv",
                                                               "start": "2010-12-01", "end": "2011-12-09"})
        check(too_big.status_code == 413 and "缩小" in too_big.json()["error"]["message"],
              "超过单次上限的导出被**明确拒绝**（不悄悄截断）",
              f"{too_big.status_code} {too_big.json()['error']['code']}")

        # ── A-10 / A-11：导入向导 + Dataset 语义 ────────────────────────
        step("⑦ A-10 / A-11：导入向导 + 数据集身份（不假装分析已开通）")
        sample = Path(sandbox) / "sales_2026.xlsx"
        frame = pd.DataFrame({
            "InvoiceNo": ["10001", "10002", "10003", "C10004"],
            "StockCode": ["85123A", "85123A", "71053", "71053"],
            "Description": ["白色爱心烛台", "白色爱心烛台", "白色金属相框", "白色金属相框"],
            "Quantity": [6, 2, -1, 4],
            "InvoiceDate": ["2026-01-05 09:30", "2026-01-06 10:00", "2026-01-07 11:00", "2026-01-09 15:20"],
            "UnitPrice": [2.55, 2.55, 2.55, 3.39],
            "CustomerID": [17850, 17850, 13047, 13047],
            "Country": ["United Kingdom"] * 4,
        })
        frame.to_excel(sample, index=False, sheet_name="订单明细")
        with open(sample, "rb") as handle:
            rejected = client.post("/api/datasets/inspect",
                                   files={"file": ("销售制度.docx", handle, "application/msword")})
        check(rejected.status_code == 400 and rejected.json()["error"]["code"] == "dataset_unsupported_type",
              "文档类文件走数据集入口被明确拒绝（不升级成万能上传）",
              rejected.json()["error"]["message"][:60])
        check("文档" in rejected.json()["error"]["message"],
              "拒绝时把用户指向正确的入口（文档资料）")

        with open(sample, "rb") as handle:
            inspect = client.post("/api/datasets/inspect",
                                  files={"file": (sample.name, handle,
                                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}).json()
        check(inspect["sheets"] == ["订单明细"], "工作表清单来自真实读取", str(inspect["sheets"]))
        check(len(inspect["preview"]) == 4 and inspect["preview"][0]["Description"] == "白色爱心烛台",
              "前 N 行预览来自真实数据（中文不乱码）", f"{len(inspect['preview'])} 行")
        check(inspect["mapping_complete"] is True, "字段映射自动猜全（8 个必需字段都在）")
        check(all(field["mapped_to"] for field in inspect["required_fields"]),
              "每个必需字段都能看到「文件里的哪一列」")

        created = client.post("/api/datasets/import", json={
            "upload_id": inspect["upload_id"], "name": "2026 年销售数据",
            "sheet": "订单明细",
        }).json()
        check(bool(created.get("dataset_id")) and created["imported"] is True,
              "登记成功并拿到 dataset_id", str(created.get("dataset_id")))
        check(created["row_count"] == 4 and created["date_range"] == {
            "start": "2026-01-05", "end": "2026-01-09"},
              "行数与时间范围是从文件里真算的", json.dumps(created.get("date_range"), ensure_ascii=False))
        check(created["analysis_enabled"] is False
              and created["status_label"] == "已登记 · 分析未开通",
              "状态如实写「分析未开通」（没有假装导入即可分析）", created["status_label"])
        check(bool(created["analysis_note"]), "给用户的说明不为空", created["analysis_note"][:40])
        check(bool(created["metric_definition"].get("name")), "口径绑定跟着数据集（不是全局规则）",
              str(created["metric_definition"].get("name")))
        audit = created.get("audit") or {}
        check(bool(audit.get("file_hash")) and audit.get("hash_algo") == "sha256"
              and bool(audit.get("schema_hash")),
              "审计层保留文件指纹与结构指纹（前端不渲染）",
              f"file_hash={str(audit.get('file_hash'))[:12]}… schema_hash={str(audit.get('schema_hash'))[:12]}…")
        listing = client.get("/api/datasets", params={"limit": 50}).json()
        business = [item for item in listing["datasets"] if item["dataset_id"] == created["dataset_id"]]
        check(len(business) == 1 and not any(
            field in json.dumps(business[0], ensure_ascii=False)
            for field in ("file_hash", "stored_path", "sha256", "audit")),
              "数据源列表接口**不含**任何审计字段", json.dumps(business[0], ensure_ascii=False)[:80])
        check(len(listing["datasets"]) == 2 and listing["total"] == 2,
              "内置数据源 + 新数据源都在列表里", f"total={listing['total']}")
        blocked = client.get("/api/tables/customers", params={"dataset_id": created["dataset_id"]})
        check(blocked.status_code == 409
              and blocked.json()["error"]["code"] == "dataset_analysis_not_ready",
              "用未开通的数据源查业务表 → **明确拒绝**（不拿内置数据冒充）",
              f"{blocked.status_code} {blocked.json()['error']['code']}")
        check(created["analysis_enabled"] is False
              and client.get("/api/tables/customers").json()["dataset"]["analysis_enabled"] is True,
              "默认业务表仍然只基于内置数据源（没被导入悄悄改掉）")

        # ── A-12：既有契约不回归 ────────────────────────────────────────
        step("⑧ A-12：既有 12 端点语义不动")
        legacy = Path(sandbox) / "legacy.xlsx"
        pd.DataFrame({"A": [1, 2], "B": ["x", "y"]}).to_excel(legacy, index=False)
        with open(legacy, "rb") as handle:
            uploaded = client.post("/api/upload", files={"file": (legacy.name, handle,
                                                                 "application/vnd.ms-excel")}).json()
        check(all(key in uploaded for key in ("file_id", "filename", "rows", "columns",
                                              "column_names", "size_bytes", "sha256",
                                              "stored_path", "created_at")),
              "冻结的 /api/upload 响应字段一个没少")
        health = client.get("/api/health").json()
        check(health["status"] == "ok" and health["task"] == "TASK-002D",
              "健康检查仍是冻结形状")
        caps = client.get("/api/chat/capabilities").json()
        check(len(caps["intents"]) == 7,
              "7 个 Intent 没变（TASK-004/005/006 不回归）",
              " / ".join(item["name"] for item in caps["intents"]))
        openapi = client.get("/openapi.json").json()
        paths = set(openapi["paths"])
        frozen = {"/api/upload", "/api/schema", "/api/execute", "/api/download/{execution_id}",
                  "/api/executions", "/api/health", "/api/tasks", "/api/tasks/{task_id}",
                  "/api/tasks/{task_id}/run", "/api/tasks/{task_id}/runs",
                  "/api/documents", "/api/documents/{doc_id}",
                  "/api/documents/{doc_id}/text", "/api/documents/{doc_id}/summary"}
        check(frozen <= paths, "冻结端点路由都还在", str(sorted(frozen - paths)))
        new_paths = {"/api/tables/{table}", "/api/tables/{table}/export",
                     "/api/datasets", "/api/datasets/{dataset_id}",
                     "/api/datasets/inspect", "/api/datasets/import"}
        check(new_paths <= paths, "STEP A 新端点都已挂上", str(sorted(new_paths - paths)))

        # ── A-01..A-09：真浏览器 ────────────────────────────────────────
        if not args.no_browser:
            edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
            if not edge:
                print("   ⚠️ 没找到 Edge，跳过浏览器那一步")
            else:
                step("⑨ A-01~A-03：真浏览器打开四个业务页，抓渲染后的表格")
                pages = {
                    "customers": "#/customers",
                    "products": "#/products",
                    "sales": "#/sales",
                    "raw": "#/raw",
                }
                doms = {name: dump_dom(edge, f"{base}/{route}") for name, route in pages.items()}
                for name, dom in doms.items():
                    if dom is None:
                        check(False, f"{name} 页面抓取成功")
                        continue
                    text = page_text(dom)
                    check("undefined" not in text and "NaN" not in text,
                          f"{name} 页面没有 undefined / NaN")
                cust_dom = doms.get("customers") or ""
                cust_text = page_text(block_of(cust_dom, "tbl-customers"))
                check(all(label in cust_text for label in
                          ("客户号", "销售额", "订单数", "购买次数", "平均每单", "首次购买", "最后购买")),
                      "A-01 客户页是**真表格**：七个表头都在页面上")
                # 页面打开时不带日期（用数据源自身的覆盖范围）——用**同样的参数**问一次接口，
                # 页面第一行必须就是接口的第一行（这就是"页面 = 接口"，不是各算各的）
                default_customers = table(client, "customers", page=1, page_size=50)
                default_products = table(client, "products", page=1, page_size=50)
                default_sales = table(client, "sales", page=1, page_size=50)
                first_customer = str(default_customers["items"][0]["customer_id"])
                check(first_customer in cust_text,
                      "A-01 客户页第一行 = 同参数接口的第一行", first_customer)
                check(block_of(cust_dom, "tbl-customers").count("<tr")
                      == 1 + len(default_customers["items"]),
                      "A-01 客户页渲染的行数 = 接口这一页的行数",
                      f"{block_of(cust_dom, 'tbl-customers').count('<tr') - 1} 行")
                check(re.search(r"\d", cust_text) is not None,
                      "A-01 客户页表格里有真实数字")
                prod_text = page_text(block_of(doms.get("products") or "", "tbl-products"))
                check(all(label in prod_text for label in
                          ("商品编码", "商品名称", "销售额", "数量", "订单数", "退货数")),
                      "A-02 产品页七个表头都在")
                first_product = str(default_products["items"][0]["stock_code"])
                check(first_product in prod_text,
                      "A-02 产品页第一行 = 同参数接口的第一行", first_product)
                sales_text = page_text(block_of(doms.get("sales") or "", "tbl-sales"))
                check("销售额" in sales_text and "客单价" in sales_text and "客户数" in sales_text,
                      "A-03 销售页表头含四个指标列")
                # 页面默认「按日 × 销售额」→ 第一行 = 接口按日聚合的第一行（第 0 天）
                first_day = default_sales["items"][0]["dimension_value"]
                check(first_day in sales_text,
                      "A-03 销售页第一行 = 同参数接口的第一行（后端聚合结果）", first_day)
                check(block_of(doms.get("sales") or "", "tbl-sales").count("<tr")
                      == 1 + len(default_sales["items"]),
                      "A-03 销售页渲染的行数 = 接口这一页的行数")
                raw_block = block_of(doms.get("raw") or "", "tbl-raw")
                raw_rows = raw_block.count("<tr")
                check(45 <= raw_rows <= 60,
                      "A-07 原始数据页只渲染**一页**行（54 万行没有塞进浏览器）",
                      f"页面里 {raw_rows} 个 <tr>（含表头）")
                check(f"{raw_big['total']:,}" in page_text(raw_block),
                      "A-07 页脚写的是**真实总条数**（541,909），而页面只渲染一页",
                      f"{raw_big['total']:,}")
                raw_customers_text = page_text(block_of(doms.get("customers") or "", "ov-customers"))
                check(str(ref_customers["rows"][0]["customer_id"]) in raw_customers_text,
                      "首页「客户 TOP5」是**真数据**（不是一句说明）")

                step("⑩ A-08 / A-09：顶栏与页面文字（宽窄两种窗口）")
                for width in (1600, 1280, 1024):
                    dom = dump_dom(edge, f"{base}/", width=width)
                    if dom is None:
                        check(False, f"{width}px 窗口抓取成功")
                        continue
                    top = re.search(r'<header class="topbar".*?</header>', dom, re.S)
                    top_html = top.group(0) if top else ""
                    check(bool(top_html), f"{width}px：顶栏存在")
                    for piece in ('id="datasource-chip"', 'id="nl-input"', 'id="nl-ask"',
                                  'id="btn-menu"', 'class="user-area"'):
                        check(piece in top_html, f"{width}px：顶栏含 {piece}")
                    check('id="nl-note"' not in top_html,
                          f"{width}px：顶栏里没有长文案（提问框吃满剩余宽度）")
                    text = page_text(dom)
                    banned = ["TASK-", "/api/", "POST ", "openapi", "白名单", "数字闸门",
                              "sha256", "file_id", "哈希", "dataset_id", "Repository",
                              "Executor", "pytest", "版本：", "0.2.0", "owner"]
                    hits = [word for word in banned if word in text]
                    check(not hits, f"{width}px：页面上检索不到技术实现信息（A-09）", str(hits))
                    check("undefined" not in text and "NaN" not in text,
                          f"{width}px：首页没有 undefined / NaN")

        step("⑪ 汇总")
        print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项", flush=True)
        if FAILED:
            print("失败清单：", flush=True)
            for item in FAILED:
                print(f"  ❌ {item}", flush=True)
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        print(f"\n沙箱保留在：{sandbox}（logs/state 可查）", flush=True)


if __name__ == "__main__":
    sys.exit(main())
