#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真前端闭环冒烟（TASK-004A）—— 起**真服务**、发**真 HTTP**、走**十步闭环**。

为什么要这个脚本（而不是只用 pytest 的 TestClient）：
    TestClient 是进程内直调，**证明不了"真的走 HTTP"**，也证明不了"页面被真的 serve 出去了"。
    本脚本起真 uvicorn（**带静态挂载**）→ GET / 拿真 HTML → 之后每一步都用**上一步 HTTP 响应里的值**
    （file_id → task_id → spec → execution_id → download_url，一个都不许硬编码，R005 #12 / AC-09），
    最后真下载 + openpyxl 读回数字。这就是 AC-09 的 http 层证据（浏览器走查由 Hermes 用 CDP 做，
    本项目**不装 Playwright**）。

跑法：
    .venv/Scripts/python.exe scripts/web_e2e.py            # 默认端口 8512
预期末行：`🎉 004A 十步闭环全部通过`
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# Windows 下 stdout 被管道接走时默认是 GBK，打印 ✅/❌ 会直接 UnicodeEncodeError 挂掉脚本。
# 这里强制 UTF-8 + 替换非法字符：脚本在任何终端/CI 管道下都能跑完（否则报错发生在第一行输出）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ENV = dict(os.environ)
ENV["PYTHONPATH"] = ""
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

import httpx  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

DATA_PATH = os.path.join(ROOT, "data", "Online Retail.xlsx")
START, END = "2011-11-21", "2011-11-27"
METRICS = ["sales_amount", "rows_in_range", "rows_valid", "rows_excluded", "excluded_amount", "valid_qty_sum"]

EXPECTED_ROWS_UPLOAD = 541909          # 上传的真实行数（002A 已验）
EXPECTED_AMOUNT = 316412.16            # 独立 Oracle 验过的金额（002A / D16）
EXPECTED_ROWS_IN_RANGE = 19950
EXPECTED_ROWS_EXCLUDED = 296

failures: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> None:
    """断言但**不中止**：一条失败也跑完，最后统一汇总（排障不用反复重跑几分钟的链路）。"""
    mark = "✅" if condition else "❌"
    print(f"   {mark} {label}{(' | ' + extra) if extra else ''}")
    if not condition:
        failures.append(label)


def _failure_paths(client: httpx.Client, file_id: str) -> None:
    """AC-08 的①②③④：失败必须**看得见**（可读错误 + 正确状态码），不是沉默失败。

    这四个正是页面上"上传失败 / 建任务失败 / 执行失败 / 下载失败"四种红条的数据来源。
    """
    bad_upload = client.post("/api/upload", files={"file": ("notes.txt", b"not a table", "text/plain")})
    check(bad_upload.status_code == 400 and "error" in bad_upload.json(),
          "① 上传非表格 → 400 + 统一错误体",
          f"code={bad_upload.json().get('error', {}).get('code')}")
    missing = client.post("/api/tasks", json={"file_id": file_id, "start": START})   # 缺 end/metrics
    check(missing.status_code == 422 and "error" in missing.json(),
          "② 建任务缺参数 → 422 + 可读错误",
          f"code={missing.json().get('error', {}).get('code')}")
    unknown_run = client.post("/api/tasks/t-not-exists/run")
    check(unknown_run.status_code == 404 and unknown_run.json().get("error", {}).get("code") == "task_not_found",
          "③ 执行不存在的任务 → 404 task_not_found")
    bad_download = client.get("/api/download/x-not-exists")
    check(bad_download.status_code in (404, 410), "④ 下载不存在的 execution_id → 404/410",
          f"status={bad_download.status_code}")


def _failed_run_evidence(client: httpx.Client, sandbox: str) -> None:
    """真失败也留痕：造一份"不是冻结快照"的数据 → 能建任务（带 warnings）→ run 必须被拒 + 落 failed 记录。

    这是五区块"执行失败"红条背后的真实来源：不是前端编的失败，是后端真拒绝了并留了痕（D10）。
    """
    print("\n【8】真失败执行也留痕（五区块失败态的真实来源）")
    import pandas as pd  # noqa: PLC0415 —— 只在造这份"别的数据"时用
    other_path = os.path.join(sandbox, "other_data.xlsx")
    pd.DataFrame({
        "InvoiceNo": ["A1"], "StockCode": ["S1"], "Description": ["d"], "Quantity": [1],
        "InvoiceDate": ["2011-11-22 10:00:00"], "UnitPrice": [10.0], "CustomerID": [1], "Country": ["UK"],
    }).to_excel(other_path, index=False)
    with open(other_path, "rb") as handle:
        other_upload = client.post(
            "/api/upload",
            files={"file": ("other_data.xlsx", handle,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        ).json()
    other_task = client.post(
        "/api/tasks",
        json={"file_id": other_upload["file_id"], "start": START, "end": END, "metrics": ["sales_amount"]},
    ).json()
    check(bool(other_task.get("warnings")), "不同源的数据 → 建任务成功但带 warnings（能建不能算）")
    rejected = client.post(f"/api/tasks/{other_task['task_id']}/run")
    check(rejected.status_code == 422, "按任务执行 → 422 被拒（data_snapshot_mismatch）", rejected.text[:160])
    failed_runs = client.get(f"/api/tasks/{other_task['task_id']}/runs").json()["runs"]
    check(len(failed_runs) == 1 and failed_runs[0]["status"] == "failed",
          "失败也落了一条 run 记录（status=failed，D10 留痕）")
    check(failed_runs[0]["download_url"] is None and failed_runs[0]["output_rel_path"] is None,
          "失败记录没有 download_url / 产出路径（不产出假文件）")
    check(client.get(f"/api/tasks/{other_task['task_id']}").json()["status"] == "created",
          "失败不改任务状态（仍 created）")


def main() -> int:
    parser = argparse.ArgumentParser(description="004A 真前端十步闭环冒烟")
    parser.add_argument("--port", type=int, default=8512)
    parser.add_argument(
        "--real-state",
        action="store_true",
        help="不建沙箱：写进仓库真实 state/ 与 outputs/（留给浏览器走查用的真任务+真执行记录）",
    )
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    if args.real_state:
        # 给浏览器走查用的真实数据：这条路径**只有顺利路径**（失败场景会往真审计记录里塞垃圾，
        # 那些用默认的沙箱模式验证）。跑完后 state/tasks.json 里就有真任务，页面刷新能读回来。
        sandbox = os.path.join(ROOT, "outputs")
        print(f"⚠️ --real-state：写入真实 state/ 与 data/uploads/（沙箱目录：{sandbox}）")
    else:
        # 隔离落盘目录：冒烟会故意造失败执行 + 临时任务，不该进仓库的 state/*.json（那是真审计凭据）
        sandbox = tempfile.mkdtemp(prefix="sra_web_e2e_", dir=os.path.join(ROOT, "outputs"))
        ENV["SRA_STATE_DIR"] = os.path.join(sandbox, "state")
        ENV["SRA_UPLOAD_DIR"] = os.path.join(sandbox, "uploads")
        ENV["SRA_OUTPUT_DIR"] = os.path.join(sandbox, "outputs")
        for key in ("SRA_STATE_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR"):
            os.makedirs(ENV[key], exist_ok=True)
        print(f"落盘沙箱（跑完可删）：{sandbox}")

    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(args.port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=ENV,
    )
    try:
        client = httpx.Client(base_url=base, timeout=600.0)
        for _ in range(120):                      # 等真服务起来（首次 import pandas 有点慢）
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("服务 60 秒内没起来")

        print("\n【0】静态前端（AC-01）")
        page = client.get("/")
        check(page.status_code == 200, "GET / 返回 200", f"status={page.status_code}")
        check(page.headers.get("content-type", "").startswith("text/html"), "响应是 HTML")
        html = page.text
        for block_id, number in [
            ("block-data", "① 数据"),
            ("block-task", "② 建任务"),
            ("block-spec", "③ Report Spec"),
            ("block-run", "④ 执行结果"),
            ("block-history", "⑤ 执行记录"),
        ]:
            check(f'id="{block_id}"' in html and number in html, f"页面含区块 {number}（id={block_id}）")
        for asset in ("/app.js", "/api.js", "/style.css"):
            asset_response = client.get(asset)
            check(asset_response.status_code == 200 and len(asset_response.content) > 0,
                  f"静态资源可访问 {asset}", f"{len(asset_response.content)} 字节")
        check("https://" not in html and "http://" not in html, "页面无外部 CDN 依赖")
        openapi = client.get("/openapi.json")
        check(openapi.status_code == 200, "GET /openapi.json 可访问（前端指标目录的来源）")

        print("\n【1】真上传（AC-02）")
        with open(DATA_PATH, "rb") as handle:
            upload = client.post(
                "/api/upload",
                files={"file": ("Online Retail.xlsx", handle,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        check(upload.status_code == 201, "POST /api/upload → 201", upload.text[:200])
        uploaded = upload.json()
        file_id = uploaded["file_id"]                                  # ← 第 1 步响应里的值
        check(uploaded["rows"] == EXPECTED_ROWS_UPLOAD, "行数 = 541909", f"rows={uploaded['rows']}")
        check(uploaded["columns"] == 8, "列数 = 8", f"columns={uploaded['columns']}")
        with open(DATA_PATH, "rb") as handle:
            real_sha = hashlib.sha256(handle.read()).hexdigest()
        check(uploaded["sha256"] == real_sha, "落盘文件的 SHA256 = 原文件 SHA256")

        print("\n【2】建任务（AC-03 / AC-04：后端返回真实 task_id 与 Spec）")
        created = client.post(
            "/api/tasks",
            json={"file_id": file_id, "name": "004A 闭环验收", "start": START, "end": END, "metrics": METRICS},
        )
        check(created.status_code == 201, "POST /api/tasks → 201", created.text[:200])
        task = created.json()
        task_id = task["task_id"]                                      # ← 第 2 步响应里的值
        spec = task["spec"]
        check(isinstance(task_id, str) and len(task_id) > 1, "返回真实 task_id", f"task_id={task_id}")
        check(task["status"] == "created", "任务状态 = created", f"status={task['status']}")
        check(task.get("warnings") in (None, []), "数据与冻结快照一致 → 无 warnings")
        check(spec["time_range"]["start"] == START and spec["time_range"]["end"] == END,
              "Spec 区间 = 提交的区间", f"{spec['time_range']['start']} ~ {spec['time_range']['end']}")
        check([m["name"] for m in spec["metrics"]] == METRICS, "Spec 指标 = 提交的 6 个")
        check(spec["output"]["cells"]["sales_amount"] == "B4", "Spec 单元格映射 sales_amount → B4")
        check(task["spec_id"] == f"task-{task_id}", "spec_id 由后端生成（task-<task_id>）", task["spec_id"])

        print("\n【3】刷新后重读 Spec（AC-04：逐字段一致）")
        re_read = client.get(f"/api/tasks/{task_id}")
        check(re_read.status_code == 200, "GET /api/tasks/{task_id} → 200")
        check(re_read.json()["spec"] == spec, "重读的 Spec 与建任务响应**逐字段一致**（前端刷新不靠内存）")
        check(re_read.json()["run_count"] == 0, "重读时还没执行过（run_count=0）")

        print("\n【4】真执行（AC-05：金额来自本响应，不是常量）")
        ran = client.post(f"/api/tasks/{task_id}/run")
        check(ran.status_code == 200, "POST /api/tasks/{task_id}/run → 200", ran.text[:300])
        run = ran.json()
        execution_id = run["execution_id"]                             # ← 第 4 步响应里的值
        download_url = run["download_url"]                             # ← 第 4 步响应里的值
        check(run["status"] == "success", "执行成功", f"status={run['status']}")
        check(run["amount"] == EXPECTED_AMOUNT, "金额 = 316412.16", f"amount={run['amount']}")
        # amount_full 是**全精度原值**（D17-2：展示 2 位、审计用全精度），
        # 它与 316412.16 的差只可能是浮点表示误差（实测 316412.16000000003），所以按容差比。
        check(abs(float(run["amount_full"]) - EXPECTED_AMOUNT) < 1e-6,
              "全精度 amount_full 与 316412.16 一致（容差 1e-6）", f"{run['amount_full']}")
        check(run["rows_in_range"] == EXPECTED_ROWS_IN_RANGE, "区间行数 = 19950", f"{run['rows_in_range']}")
        check(run["rows_excluded"] == EXPECTED_ROWS_EXCLUDED, "排除行数 = 296", f"{run['rows_excluded']}")
        check(run["resolved_range"] == {"start": START, "end": END}, "resolved_range = 2011-11-21~11-27",
              str(run["resolved_range"]))
        check(run["task_id"] == task_id, "执行记录绑定的 task_id = 上一步的 task_id")
        check(run["download_url"] == f"/api/download/{execution_id}", "download_url 是相对路径",
              run["download_url"])
        check(bool(run.get("verification")), "带读回自检 verification")
        check("excel_path" not in run, "任务端点不给服务器绝对路径（R005 #7）")

        print("\n【5】真下载 + openpyxl 读回（AC-06）")
        downloaded = client.get(download_url)
        check(downloaded.status_code == 200, f"GET {download_url} → 200")
        check(len(downloaded.content) > 4000, "产出不是空文件", f"{len(downloaded.content)} 字节")
        report_path = os.path.join(sandbox, "downloaded_report.xlsx")
        with open(report_path, "wb") as handle:
            handle.write(downloaded.content)
        workbook = load_workbook(report_path)
        sheet = workbook[run["sheet"]]
        cell_amount = sheet["B4"].value
        check(abs(float(cell_amount) - EXPECTED_AMOUNT) < 1e-6,
              "openpyxl 读回 B4 = 316412.16（与执行响应同一个数）", f"B4={cell_amount}")
        check(str(sheet["B5"].value) == str(EXPECTED_ROWS_IN_RANGE), "B5 = 19950", f"B5={sheet['B5'].value}")
        check(sheet["B4"].number_format == "#,##0.00", "B4 数字格式仍是 #,##0.00（D17-2）",
              sheet["B4"].number_format)

        print("\n【6】执行记录 + 模拟刷新（AC-07 / AC-09 第 9~10 步）")
        runs = client.get(f"/api/tasks/{task_id}/runs")
        check(runs.status_code == 200, "GET /api/tasks/{task_id}/runs → 200")
        run_list = runs.json()["runs"]
        check(any(item["execution_id"] == execution_id for item in run_list),
              "执行记录里有刚才那次执行（execution_id 来自第 4 步响应）")
        latest = run_list[0]
        check(latest["amount"] == EXPECTED_AMOUNT, "记录里的金额也是 316412.16", f"{latest['amount']}")
        check(latest["task_id"] == task_id, "记录里带 task_id", f"{latest['task_id']}")

        listed = client.get("/api/tasks").json()
        check(any(item["task_id"] == task_id for item in listed["tasks"]),
              "GET /api/tasks 里能查到这个任务（页面刷新后的恢复路径）")
        check(client.get(f"/api/tasks/{task_id}").json()["status"] == "has_run",
              "任务状态 created → has_run（刷新后从后端读到的）")
        re_runs = client.get(f"/api/tasks/{task_id}/runs").json()["runs"]
        check(re_runs == run_list, "再读一次执行记录，内容完全相同（刷新后读得到且一致）")

        print("\n【7】失败态四项（AC-08）")
        if args.real_state:
            print("   ⏭  --real-state 模式跳过失败场景（失败记录只落在沙箱里，不污染真实审计记录）")
        else:
            _failure_paths(client, file_id)
            _failed_run_evidence(client, sandbox)

        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:            # noqa: PERF203
            proc.kill()

    print("\n" + "=" * 64)
    if failures:
        print(f"❌ 004A 闭环有 {len(failures)} 项失败：")
        for item in failures:
            print(f"   - {item}")
        return 1
    print("🎉 004A 十步闭环全部通过（真服务 + 真 HTTP + 真文件；沙箱：" + sandbox + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
