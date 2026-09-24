#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自然语言问答真闭环冒烟（TASK-004）—— 起**真服务**、发**真 HTTP**、问**真 DeepSeek**。

为什么要有这个脚本（而不是只跑 pytest）：
    `tests/test_chat.py` 把 `app.ai.llm.chat` 这个**网络出口**换掉了（单测不能依赖外网），
    所以它证明不了"真接上 DeepSeek 之后整条链路还成立"。本脚本补的正是这一段：
    **真 key → 真模型 → 真 Intent JSON → 真白名单工具 → 真确定性计算 → 真三段回答**，
    而且每一步的数字都拿去和**直接调用冻结资产**（`app.engine.executor`）的结果逐位比对。

数字对账怎么做的（这是本脚本的核心，不是"看一眼像不像"）：
    ① 脚本**自己**在进程内直接调 `executor.compute_sales_amount()` 拿一份参考值
       —— 这条路径完全不经过 `app/ai/`，是"直接调 metrics"的字面含义；
    ② HTTP 回来的 `facts.sales_amount` 与它比 **位级相等**（`==`，不是 approx）；
    ③ 三个 Intent 互相对账：趋势各桶之和 == 汇总 == 产品排行合计 == 参考值。
    ② ③ 合起来才叫"回答里的数字能追溯到 metrics 的输出"。

跑法：
    .venv/Scripts/python.exe scripts/chat_e2e.py              # 默认端口 8514，跑全部三个 Intent
    .venv/Scripts/python.exe scripts/chat_e2e.py --quick      # 只问 sales_summary（省几次模型往返）
预期末行：`🎉 TASK-004 自然语言闭环全部通过`

耗时说明（别以为是卡住了）：
    进程内参考值要读一次 22MB Excel（约 100 秒）；服务进程**启动时会在后台自己读一次**
    （见 app/prewarm.py —— 这一步现在与脚本的参考值计算、与服务接受请求**并行**发生，
    所以它不再是"用户提问时干等"的那 100 秒）。本机实测整条约 4 分钟，绝大部分是读表。

本脚本额外验两件事（不在 TASK-004 原始范围里，是后续两个小修）：
    · 币种：事实段/display/能力端点说的是**人民币「元」**（用户 2026-09-24 定的口径，数值不换算），
      不是「英镑」——声明在 tools.DATASET_CURRENCY 一处，闸门方向随之反转；
    · 冷启动：服务**启动就预热**（日志里有那一行）、预热期间 /api/health 一直 200、
      预热之后首次碰数据只要几秒（修复前这一刻要现读 Excel ≈ 100 秒）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# Windows 下 stdout 被管道接走时默认是 GBK，打印 ✅/❌/中文会直接 UnicodeEncodeError 挂掉脚本
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV = dict(os.environ)
ENV["PYTHONPATH"] = ""
ENV["SRA_LLM_TIMEOUT"] = "300"        # 真模型 + 冷启动读表，180 秒不够宽裕
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

import httpx  # noqa: E402

from app.ai import llm  # noqa: E402
from app.engine import executor, loader  # noqa: E402

failures: list[str] = []
T0 = time.time()


def check(condition: bool, label: str, extra: str = "") -> bool:
    """断言但**不中止**：一条失败也跑完，最后统一汇总（排障不用反复重跑整条链路）。"""
    mark = "✅" if condition else "❌"
    print(f"   {mark} {label}{(' | ' + extra) if extra else ''}")
    if not condition:
        failures.append(label)
    return bool(condition)


def step(title: str) -> None:
    print(f"\n【{title}】(+{time.time() - T0:.0f}s)")


def number_tokens(text: str) -> list[str]:
    """扫一段文本里的数字 token（与 app/ai/answer.py 同一套正则，独立复核落盘的回答）。"""
    return re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", text or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-004 自然语言闭环真冒烟")
    parser.add_argument("--port", type=int, default=8514)
    parser.add_argument("--quick", action="store_true", help="只问 sales_summary，跳过另外两个 Intent")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    sandbox = tempfile.mkdtemp(prefix="sra_chat_e2e_", dir=os.path.join(ROOT, "outputs"))
    ENV["SRA_STATE_DIR"] = os.path.join(sandbox, "state")
    ENV["SRA_DOC_DIR"] = os.path.join(sandbox, "documents")
    ENV["SRA_UPLOAD_DIR"] = os.path.join(sandbox, "uploads")
    ENV["SRA_OUTPUT_DIR"] = os.path.join(sandbox, "outputs")
    for key in ("SRA_STATE_DIR", "SRA_DOC_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR"):
        os.makedirs(ENV[key], exist_ok=True)
    print(f"落盘沙箱（跑完可删）：{sandbox}")

    # ── 【0】参考值：脚本**自己在进程内**直接调冻结资产（不经过 app/ai/ 一行）────
    step("0 进程内直接调冻结资产拿参考值（这条路径与 Agent 无关）")
    print("   读 22MB Excel 中…（约 100 秒，冷启动必付）")
    month_start, month_end = "2011-11-01", "2011-11-30"
    week_start, week_end = "2011-11-21", "2011-11-27"
    ref_month = float(executor.compute_sales_amount(month_start, month_end)["amount"])
    ref_week = float(executor.compute_sales_amount(week_start, week_end)["amount"])
    frame = loader.load_raw()
    ref_rows = len(frame)
    ref_columns = list(frame.columns)
    check(ref_month > 0 and ref_week > 0, "参考金额算出来了（直接调 executor，与 Agent 无关）",
          f"11月={ref_month:.2f} / 11-21~27周={ref_week:.2f}")
    check(ref_month > ref_week, "整月金额 > 单周金额（参考值自身先自洽）")
    print(f"   数据集：{ref_rows} 行 / {len(ref_columns)} 列：{' / '.join(ref_columns)}")

    # 服务日志落文件（不用 PIPE）：① 管道没人读会写满卡住服务；② 要验"启动预热真的跑了"
    # 就必须能读到服务进程的启动日志。
    log_path = os.path.join(sandbox, "server.log")
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(args.port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=ENV,
    )
    try:
        client = httpx.Client(base_url=base, timeout=600.0)
        for _ in range(240):
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("服务 120 秒内没起来")

        # ── 【1】静态前端真的把「自然语言入口 + 链路面板」serve 出去了 ──────────
        step("1 静态前端（真 GET /）")
        page = client.get("/")
        html = page.text
        check(page.status_code == 200 and page.headers.get("content-type", "").startswith("text/html"),
              "GET / 返回真 HTML", f"status={page.status_code}")
        for control, label in [
            ("nl-input", "顶栏自然语言输入框"),
            ("nl-ask", "顶栏分析按钮"),
            ("hero-nl-input", "首屏自然语言输入框"),
            ("chat-panel", "链路面板"),
            ("chat-step-question", "① 用户问题"),
            ("chat-step-facts", "② 算出来的事实"),
            ("chat-step-answer", "③ 分区回答"),
            ("chat-history", "历史提问列表"),
            ("ai-conclusion-body", "右面板 AI 结论"),
        ]:
            check(f'id="{control}"' in html, f"页面含 {label}（id={control}）")
        # 用户 2026-09-24：技术实现细节不许上界面（后端字段照旧，只是前端不画）。
        # 这里只看**壳还在不在**；"文字真的没画出来"由 Edge 渲染后的 DOM 检查兜
        # （见 outputs/_t005_e2e.py 【7】：注释里的开发说明用户看不见，不算数）。
        for gone in ("chat-step-intent", "chat-step-tool", "chat-intent", "chat-tool", "chat-source"):
            check(f'id="{gone}"' not in html, f"**技术细节区块 {gone} 已从页面撤掉（连壳一起撤）**")
        # TASK-004 的交付物之一就是**把这个入口启用**（原来带 disabled）
        nl_tag = re.search(r'<[^>]*id="nl-input"[^>]*>', html)
        check(nl_tag is not None and "disabled" not in nl_tag.group(0),
              "**顶栏自然语言入口已启用（不再 disabled）**")
        app_js = client.get("/app.js")
        check(app_js.status_code == 200 and "API.chat(" in app_js.text,
              "app.js 真的调了 /api/chat（不是只有后端能跑）")
        check(client.get("/style.css").status_code == 200, "style.css 正常 serve")

        # ── 【1b】冷启动预热：真进程里"启动就后台读表、不挡就绪" ────────────────
        step("1b 冷启动预热（真进程）")
        print("   等启动日志里的预流行…（进程首次读 22MB Excel，约 100 秒）")
        prewarm_line = ""
        health_never_failed = True
        deadline = time.time() + 300
        while time.time() < deadline and not prewarm_line:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    for row in handle:
                        if "[预热] 数据集已读入缓存" in row:
                            prewarm_line = row.strip()
                            break
            except OSError:
                pass
            if client.get("/api/health").status_code != 200:
                health_never_failed = False
            time.sleep(1.0)
        check(bool(prewarm_line), "**启动时真的预热了（日志里有那一行）**", prewarm_line)
        check(health_never_failed, "**预热期间 /api/health 一直是 200（预热不挡服务就绪）**")
        cold_started = time.time()
        caps = client.get("/api/chat/capabilities")
        cold_seconds = time.time() - cold_started
        check(caps.status_code == 200, "能力端点 200")
        # 修复前：冷启动后第一次碰数据要现读 22MB Excel ≈ 100 秒（这一问就是用户干等的那一下）
        check(cold_seconds < 20,
              "**预热后首次碰数据只花几秒（修复前这一刻要 ~100 秒现读 Excel）**",
              f"{cold_seconds:.1f}s")
        caps = caps.json()
        names = [item["name"] for item in caps["intents"]]
        check(names == ["sales_summary", "sales_trend", "top_products"],
              "**白名单恰好三类，一个不多**（第一版范围）", f"{names}")
        profile = caps["data_profile"]
        check(profile["rows"] == ref_rows, "画像行数与直接读表一致", f"{profile['rows']} == {ref_rows}")
        check(profile["columns"] == ref_columns, "画像列名与真实数据一致")
        check(profile.get("has_region_field") is False, "**画像如实标注：没有区域字段**")
        # 币种**显式声明**：数据集 8 列里没有货币字段，单位只能声明不能猜
        # （用户 2026-09-24 把口径定成人民币「元」，数值照旧不换算）
        currency = caps.get("currency") or {}
        check(currency.get("code") == "CNY" and currency.get("symbol") == "¥",
              "**能力端点显式声明币种 CNY / ¥（人民币元）**", str(currency.get("code")))
        check(currency.get("source") == "declared",
              "声明里写明出处：这是声明的口径，不是从数据里读出来的")
        check("英国" not in json.dumps(currency, ensure_ascii=False) and
              "英镑" not in json.dumps(currency, ensure_ascii=False),
              "**旧口径话术（英国零售商/英镑）已经撤掉**")
        check(profile.get("currency") == currency, "画像与顶层声明**同源**（一处定义，两处引用）")
        check("不会用 Country 代替" in caps["unsupported"]["reason"] or
              "Country" in caps["unsupported"]["reason"],
              "能力端点明说不会拿 Country 顶替区域")
        configured = bool(caps["llm"].get("configured"))
        check(configured, "LLM 已配置（接着问真问题才有意义）", f"model={caps['llm'].get('model')}")
        key = llm.api_key() or ""
        check(bool(key), "脚本进程也能从 .env 读到 key（下面用它查泄漏，不回显）")
        check(not key or key not in json.dumps(caps, ensure_ascii=False),
              "**key 没有出现在响应里（只说有没有，不回声）**")

        asked = 0        # 每次 200 的 POST /api/chat 都会落一条记录，用它当"应该有几条"的准绳

        # ── 【3】AC-01 真问题 → 真模型 → 真计算 → 与参考值逐位一致 ────────────
        step("3 AC-01 真提问「2011年11月一共卖了多少？」")
        print("   真调 DeepSeek + 服务进程冷启动读表…（首次约 2 分钟）")
        asked_at = time.time()
        response = client.post("/api/chat", json={"question": "2011年11月一共卖了多少？"})
        check(response.status_code == 200, "POST /api/chat → 200", response.text[:200])
        record = response.json()
        asked += 1
        print(f"   （这一问耗时 {time.time() - asked_at:.0f}s）")

        check(record.get("status") == "ok", "status = ok", f"status={record.get('status')}")
        check((record.get("intent") or {}).get("intent") == "sales_summary",
              "意图解析成了 sales_summary", f"{record.get('intent')}")
        params = record.get("params") or {}
        check(params.get("start") == month_start and params.get("end") == month_end,
              "参数落成整个 11 月（不是瞎猜的区间）", f"{params}")
        tool = record.get("tool") or {}
        check(tool.get("name") == "sales_summary", "真的调了白名单工具 sales_summary")
        facts = record.get("facts") or {}
        check(facts.get("sales_amount") == ref_month,
              "**AC-01：销售额与直接调 executor 逐位相等**",
              f"{facts.get('sales_amount')!r} == {ref_month!r}")
        selfcheck = tool.get("selfcheck") or {}
        check(selfcheck.get("bit_identical") is True,
              "工具自检：同一套 D16 掩码独立复算 → 与 executor 逐位一致")
        check(selfcheck.get("executor_checks_all_passed") is True, "executor 内置口径核对全部通过")

        llm_info = record.get("llm") or {}
        check(llm_info.get("used") is True, "**LLM 真的参与了**（不是关键词降级路径）",
              f"model={llm_info.get('model')} source={llm_info.get('intent_source')}")
        sections = (record.get("answer") or {}).get("sections") or []
        by_key = {item.get("key"): item for item in sections}
        check(set(by_key) == {"what", "why", "actions"}, "回答是完整三段", f"{sorted(by_key)}")
        check(by_key.get("what", {}).get("source") == "code",
              "**第一段【发生了什么】由代码写（数字不经过模型的手）**")
        check(f"{ref_month:,.2f}" in by_key.get("what", {}).get("text", ""),
              "代码段里写着那个金额（人能看到出处）", f"{ref_month:,.2f}")
        what_text = by_key.get("what", {}).get("text", "")
        check("元" in what_text and "英镑" not in what_text,
              "**事实段用的是人民币「元」，全段没有「英镑」**")
        display_units = {item.get("unit") for item in (tool.get("display") or [])
                         if item.get("format") == "money"}
        check(display_units == {"元"},
              "事实表（前端直接渲染的那份 display）金额单位也是声明的那一个", str(display_units))

        guard = (record.get("answer") or {}).get("guard") or {}
        check(guard.get("checked") is True, "数字闸门这次确实开了（LLM 参与时才开）")
        check(guard.get("passed") is True, "**闸门通过：模型文字里没有越界数字**",
              f"violations={guard.get('violations')}")
        llm_digits = []
        for item in sections:
            if item.get("source") == "llm":
                llm_digits.extend(number_tokens(item.get("text", "")))
        check(not llm_digits,
              "**模型写的那两段里一个阿拉伯数字都没有（提示词真的管住了）**",
              f"found={llm_digits[:8]}")
        check(bool(record.get("notice")), "响应带数据边界说明（时间范围/口径如实讲）",
              str(record.get("notice"))[:90])

        # ── 【4】AC-01 交叉对账：三个 Intent 互相对得上 ──────────────────────
        checks_ok = True
        if not args.quick:
            step("4 AC-01 另两个 Intent 与参考值对账（真模型，每次约 10~20 秒）")
            trend = client.post("/api/chat", json={
                "question": "2011-11-01 到 2011-11-30 的销售趋势，按周看",
            }).json()
            check(trend.get("status") == "ok", "趋势问题 status = ok", f"{trend.get('status')}")
            tparams = trend.get("params") or {}
            check(tparams.get("granularity") == "week", "解析成按周聚合", f"{tparams}")
            points = ((trend.get("series") or {}).get("points")) or []
            total = sum(point["amount"] for point in points)
            check(abs(total - ref_month) < 1e-6,
                  "**趋势各桶之和 == 汇总（同一个月，两条代码路径对得上）**",
                  f"{total!r} ≈ {ref_month!r}")
            week_point = next((p for p in points if p.get("period_start") == week_start
                               and p.get("period_end") == week_end), None)
            check(week_point is not None and abs(week_point["amount"] - ref_week) < 1e-6,
                  "**11-21~11-27 那一周 == 直接调 executor 算的那一周**",
                  f"{None if week_point is None else week_point['amount']!r} ≈ {ref_week!r}")
            # 首尾桶跨在问句区间外：制度上的做法是**保留真实周标签、明确标不完整**，
            # 而不是把标签裁到区间里（那会让人以为是两个短周）。所以这里验的是"披露到位"。
            partial = [p for p in points if p.get("partial")]
            check(len(partial) == 2 and partial[0] is points[0] and partial[-1] is points[-1],
                  "首尾两个桶被标成不完整（正好两个，不多不少）",
                  f"{[p['period_start'] for p in partial]}")
            check(all(p["covered_start"] >= month_start and p["covered_end"] <= month_end
                      for p in points),
                  "**每个桶实际统计的天数都落在问句区间内（标签可以跨周，算的天数没跨）**",
                  f"首桶 covered={points[0]['covered_start']}~{points[0]['covered_end']}（{points[0]['covered_days']} 天）")
            check(points[0]["covered_days"] == 6 and points[-1]["covered_days"] == 3,
                  "首尾桶的天数 = 区间与那一周的实际交集（11-01~11-06 / 11-28~11-30）",
                  f"{points[0]['covered_days']} / {points[-1]['covered_days']}")
            check(all(p["covered_days"] == 7 for p in points[1:-1]),
                  "中间三个桶是完整周（7 天）")
            check("不完整" in ((trend.get("answer") or {}).get("text") or ""),
                  "**回答的代码段里写明了首尾桶「不完整」**（用户不会把它当成完整一周）")

            top = client.post("/api/chat", json={
                "question": "2011年11月卖得最好的5个产品",
            }).json()
            asked += 2
            check(top.get("status") == "ok", "产品排行 status = ok", f"{top.get('status')}")
            tfacts = top.get("facts") or {}
            check(tfacts.get("total_amount") == ref_month,
                  "**排行口径的总额 == 直接调 executor（同一区间）**",
                  f"{tfacts.get('total_amount')!r} == {ref_month!r}")
            items = top.get("items") or []
            check(len(items) == 5, "正好 5 条明细", f"{len(items)}")
            check([i["rank"] for i in items] == [1, 2, 3, 4, 5], "名次连续")
            check(all(i["amount"] >= j["amount"] for i, j in zip(items, items[1:])),
                  "按销售额降序（真的排过序）")
            # 前 5 名当然不是区间总额（4000 多个编码里的 5 个）—— 该验的是"占比能回溯"
            top5 = sum(i["amount"] for i in items)
            check(0 < top5 < ref_month,
                  "前 5 名金额是区间总额的一小部分（不超过总额，这是排行不是全量）",
                  f"{top5:,.2f} / {ref_month:,.2f}（{top5 / ref_month * 100:.2f}%）")
            check(all(i["amount"] <= ref_month for i in items), "任一条金额都不超过区间总额")
            check(all(abs(i["share"] - i["amount"] / ref_month) < 1e-9 for i in items),
                  "**每条占比 == 该条金额 / 区间总额（页面上的百分比能回算出来）**")
            check(sum(i["share"] for i in items) <= 1.0 + 1e-9, "占比之和 ≤ 1（没有重复计数）")
            check(all(0 < i["share"] < 1 for i in items), "每条占比都在 (0,1) 之间")
        else:
            print("\n【4】--quick：跳过另外两个 Intent")

        # ── 【5】AC-03 数据里没有的维度：明确拒绝，且不许拿 Country 顶替 ─────
        step("5 AC-03 问「华南区」→ 必须明确说不支持")
        unsupported = client.post("/api/chat", json={"question": "华南区2011年11月卖了多少？"}).json()
        asked += 1
        check(unsupported.get("status") == "unsupported",
              "**status = unsupported（没硬算一个数糊弄过去）**", f"{unsupported.get('status')}")
        check((unsupported.get("tool") or {}).get("name") in (None, ""), "没有调用任何工具")
        check(unsupported.get("facts") in (None, {}), "**没有产出任何事实数字**")
        u_text = (unsupported.get("answer") or {}).get("text", "")
        check("没有区域字段" in u_text, "回答里如实说明数据集没有区域字段")
        check("Country" in u_text, "并且点名「Country 不能当区域用」")
        u_json = json.dumps(unsupported, ensure_ascii=False)
        check(f"{ref_month:,.2f}" not in u_json and repr(ref_month) not in u_json,
              "拒绝的回答里**没有夹带任何金额**")

        # ── 【5b】AC-02 跟数据无关的问题：真模型也得说"答不了"，不许硬套一个 Intent ──
        step("5b AC-02 问「今天天气怎么样」→ 真模型不许瞎猜一个 Intent 硬算")
        weather = client.post("/api/chat", json={"question": "今天天气怎么样？"}).json()
        asked += 1
        check(weather.get("status") in ("unsupported", "error"),
              "**状态不是 ok（没有硬套一个 Intent 去算一个数出来）**",
              f"status={weather.get('status')} code={(weather.get('error') or {}).get('code')}")
        check((weather.get("tool") or {}).get("name") in (None, ""), "没有调用任何工具")
        check(weather.get("facts") in (None, {}), "没有产出任何事实数字")
        w_text = (weather.get("answer") or {}).get("text") or (weather.get("error") or {}).get("message", "")
        check("三类问题" in w_text or "没听懂" in w_text or "答不了" in w_text or "回答不了" in w_text,
              "明确告诉用户答不了（并给出能问什么，不是死胡同）", w_text[:80].replace("\n", " "))
        check(f"{ref_month:,.2f}" not in json.dumps(weather, ensure_ascii=False),
              "这条回答里没有夹带任何金额")

        # ── 【6】AC-06 落盘 + 刷新恢复（不靠内存）──────────────────────────
        step("6 AC-06 落盘与刷新恢复")
        listing = client.get("/api/conversations").json()
        check(listing["total"] == asked, "历史列表计数 = 问过的次数",
              f"total={listing['total']} / 问过 {asked} 次")
        check(listing["conversations"][0]["conversation_id"] == weather["conversation_id"],
              "列表新的在前（第一条就是刚问的那次）")
        check(all(item.get("question") and item.get("status") for item in listing["conversations"]),
              "每条摘要都带原问题与状态（不是空壳行）")
        detail = client.get(f"/api/conversations/{record['conversation_id']}").json()
        check(detail == record,
              "**GET /api/conversations/{id} 与提问时的响应逐字段一致（刷新恢复的真相来源）**")
        with httpx.Client(base_url=base, timeout=60.0) as fresh:
            reread = fresh.get("/api/conversations").json()
        check(reread["total"] == listing["total"], "**换一条新连接重新 GET 能读回同样的记录**")

        conv_json = os.path.join(ENV["SRA_STATE_DIR"], "conversations.json")
        check(os.path.isfile(conv_json), "state/conversations.json 真的落盘了", conv_json)
        stored = json.loads(open(conv_json, encoding="utf-8").read())
        key_name = "conversations" if "conversations" in stored else list(stored)[0]
        records = stored[key_name]
        check(len(records) == listing["total"], "盘上条数 == 接口条数", f"{len(records)}")
        first = records[0]
        check({"question", "intent", "params", "tool", "facts", "answer", "status"} <= set(first),
              "**盘上那条记录带着完整链路（问题/意图/参数/工具/事实/回答）**",
              f"keys={sorted(first)}")
        check(not key or key not in json.dumps(records, ensure_ascii=False),
              "**落盘的记录里没有 API key**")

        # ── 【7】降级路径：不接 LLM 时数字照样对 ───────────────────────────
        step("7 use_llm=false（强制降级）时数字照样对")
        degraded = client.post("/api/chat", json={
            "question": "2011年11月一共卖了多少？", "use_llm": False,
        }).json()
        check(degraded.get("status") in ("ok", "degraded"), "降级仍然是有效回答",
              f"status={degraded.get('status')}")
        asked += 1
        check((degraded.get("llm") or {}).get("used") is False, "llm.used = false（真的没走模型）")
        check((degraded.get("facts") or {}).get("sales_amount") == ref_month,
              "**不用 LLM 时金额照样逐位一致（数字本来就不是模型算的）**")
        d_sections = (degraded.get("answer") or {}).get("sections") or []
        check(all(item.get("source") == "code" for item in d_sections),
              "三段全部来自代码（没有模型文字就老老实实说）")
        check((degraded.get("answer") or {}).get("guard", {}).get("checked") is False,
              "没走模型 → 闸门没开（如实标注 checked=false，不假装核过）")

        # ── 【8】失败路径：坏请求要看得见 ──────────────────────────────────
        step("8 失败路径")
        blank = client.post("/api/chat", json={"question": "   "})
        check(blank.status_code == 400
              and blank.json().get("error", {}).get("code") == "chat_empty_question",
              "空问题 → 400 chat_empty_question", f"{blank.status_code}")
        extra = client.post("/api/chat", json={"question": "卖了多少", "nonsense": 1})
        check(extra.status_code == 422, "多传字段 → 422（不静默忽略）", f"{extra.status_code}")
        missing = client.get("/api/conversations/conv-not-exists")
        check(missing.status_code == 404
              and missing.json().get("error", {}).get("code") == "conversation_not_found",
              "查不存在的会话 → 404 conversation_not_found")
        final_total = client.get("/api/conversations").json()["total"]
        check(final_total == asked,
              "被拒的请求没进历史（不留半截记录）",
              f"total={final_total} / 有效提问 {asked} 次")

        # ── 【9】Legacy Contract：老端点语义未变 ──────────────────────────
        step("9 Legacy Contract（老端点一个字没动）")
        health = client.get("/api/health").json()
        check(set(health["state"]) == {"state_dir", "schema_version", "uploads", "executions",
                                       "tasks", "readable"},
              "**/api/health 的 state 块键集与冻结版逐字一致**（对话计数没搭车塞进来）",
              f"keys={sorted(health['state'])}")
        check(health["state"]["executions"] == 0 and health["state"]["tasks"] == 0,
              "问答没有污染任务/执行计数（各是各的目录）")
        paths = set(client.get("/openapi.json").json()["paths"])
        for new_path in ("/api/chat/capabilities", "/api/chat", "/api/conversations",
                         "/api/conversations/{conversation_id}"):
            check(new_path in paths, f"新路径已注册：{new_path}")
        for old_path in ("/api/health", "/api/upload", "/api/schema", "/api/execute",
                         "/api/tasks", "/api/tasks/{task_id}", "/api/tasks/{task_id}/run",
                         "/api/tasks/{task_id}/runs", "/api/executions", "/api/download/{execution_id}",
                         "/api/documents", "/api/documents/{doc_id}",
                         "/api/documents/{doc_id}/text", "/api/documents/{doc_id}/summary"):
            check(old_path in paths, f"老路径仍在：{old_path}")
        check(len(paths) == 18, "路径总数 = 14 个冻结 + 4 个新增（没有别的搭车）", f"{len(paths)}")

        print()
        if failures:
            print(f"❌ TASK-004 自然语言闭环有 {len(failures)} 项未通过（总耗时 {time.time() - T0:.0f}s）：")
            for item in failures:
                print(f"   · {item}")
            return 1
        print(f"🎉 TASK-004 自然语言闭环全部通过（总耗时 {time.time() - T0:.0f}s）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
