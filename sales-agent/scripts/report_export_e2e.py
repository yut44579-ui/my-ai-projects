"""scripts/report_export_e2e.py · 报告导出真闭环（真服务 + 真 HTTP + 真读回）

════════════════════════════════════════════════════════════════════════
【为什么还要这一份（pytest 里已经验过一遍了）】
════════════════════════════════════════════════════════════════════════
pytest 那份用进程内 TestClient 验逻辑；这一份起**真 uvicorn**、用 httpx 打真端口，
并把下载到的文件**写到盘上**（`outputs/report_export_<时间>/`）—— 用户可以直接双击打开，
用眼睛确认"这次下到的是能打开的 Word / Excel"，而不是 .md 文本。

验的东西与 pytest 那份不重复的部分：
  ① 真 HTTP 栈下的响应头（内容类型 / Content-Disposition 的中文文件名编码）；
  ② 落盘文件的**字节数**与用 python-docx / openpyxl 读回来的结构；
  ③ 三种格式的指标数字逐位一致（同一份冻结报告 → 三种格式）。

【不做的事】不比对密码那一类与本任务无关的东西；不引新依赖（httpx / python-docx /
openpyxl 都在）。
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402
from docx import Document  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
QUESTION = "帮我根据本星期的销售数据做一份销售周报"

PASSED: list[str] = []
FAILED: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"  {'OK  ' if condition else 'FAIL'} {label}" + (f" —— {extra}" if extra else ""), flush=True)
    return condition


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def start_server(port: int, env: dict) -> tuple[subprocess.Popen, Path]:
    log_path = Path(env["SRA_STATE_DIR"]).parent / "server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=env, cwd=str(PROJECT_ROOT),
    )
    return proc, log_path


def wait_ready(client: httpx.Client, server: subprocess.Popen, deadline_seconds: int = 240) -> bool:
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        if server.poll() is not None:
            return False
        try:
            if client.get("/api/health").status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="报告导出真闭环验收")
    parser.add_argument("--port", type=int, default=8540)
    args = parser.parse_args()

    workspace = PROJECT_ROOT / "outputs" / f"report_export_{int(time.time())}"
    env = dict(os.environ)
    for key, name in (("SRA_STATE_DIR", "state"), ("SRA_DOC_DIR", "documents"),
                      ("SRA_UPLOAD_DIR", "uploads"), ("SRA_OUTPUT_DIR", "outputs")):
        env[key] = str(workspace / name)
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    server, log_path = start_server(args.port, env)
    base = f"http://127.0.0.1:{args.port}"
    try:
        with httpx.Client(base_url=base, timeout=600) as client:
            step("① 真服务 + 真问一次周报")
            if not check(wait_ready(client, server), "服务起来了（/api/health 200）"):
                print(log_path.read_text(encoding="utf-8", errors="replace")[-800:])
                return 1
            asked = client.post("/api/chat", json={"question": QUESTION, "use_llm": False})
            check(asked.status_code == 200, "POST /api/chat 200", str(asked.status_code))
            record = asked.json()
            cid = record["conversation_id"]
            export = (record.get("answer") or {}).get("export") or {}
            check(export.get("default_format") == "docx", "默认格式：Word",
                  export.get("default_format", ""))
            metrics = (record["report_document"]["sections"][1]["table"])

            step("② 三种格式各下载一次（真 HTTP，文件落盘）")
            saved = {}
            for fmt, ext in (("docx", "docx"), ("xlsx", "xlsx"), ("md", "md")):
                response = client.get(
                    f"/api/conversations/{cid}/report/export", params={"format": fmt})
                if not check(response.status_code == 200, f"下载 {fmt} 200",
                             str(response.status_code)):
                    continue
                encoded = response.headers.get("content-disposition", "").split("filename*=UTF-8''")[-1]
                filename = unquote(encoded.split(";")[0].strip()) or f"{cid}.{ext}"
                path = workspace / filename
                path.write_bytes(response.content)
                saved[fmt] = path
                check(path.stat().st_size > 2000, f"{fmt} 文件写盘（{path.stat().st_size} 字节）",
                      path.name)
                check(f".{ext}" in filename, f"{fmt} 文件名带对扩展名", filename)

            step("③ 用 python-docx / openpyxl 把文件读回来")
            if "docx" in saved:
                document = Document(saved["docx"])
                headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1"]
                check("结论与建议" in headings and len(document.tables) == 4,
                      "Word 读回来：标题层级与四张真表格都在",
                      f"{len(document.tables)} 张表 / {len(headings)} 个一级标题")
                first = [cell.text for cell in document.tables[0].rows[1].cells]
                check(first == [str(cell) for cell in metrics["rows"][0]],
                      "Word 里第一行指标 = 报告里的那一行", " | ".join(first[:3]))
            if "xlsx" in saved:
                workbook = load_workbook(saved["xlsx"])
                check(workbook.sheetnames == ["摘要与建议", "核心指标", "趋势", "结构-国家", "结构-商品"],
                      "Excel 读回来：五张工作表齐全", " / ".join(workbook.sheetnames))
                sheet = workbook["核心指标"]
                rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
                head = next(index for index, row in enumerate(rows)
                            if row and str(row[0]).strip() == metrics["headers"][0])
                value = rows[head + 1][1]
                expected = float(str(metrics["rows"][0][1]).replace("元", "").replace(",", ""))
                check(isinstance(value, (int, float)) and abs(float(value) - expected) < 0.005,
                      "Excel 里是**真数字**且与页面上的一致", f"{value} ≈ {expected}")
                check("%" in sheet.cell(row=head + 2, column=5).number_format,
                      "变化率列带百分比数字格式",
                      sheet.cell(row=head + 2, column=5).number_format)
            if "md" in saved:
                text = saved["md"].read_text(encoding="utf-8")
                check(text == export.get("markdown"), "Markdown 文件 = 页面预览的那段文本")

            step("④ 诚实边界")
            plain = client.post("/api/chat", json={"question": "2011年11月一共卖了多少？",
                                                   "use_llm": False}).json()
            no_report = client.get(f"/api/conversations/{plain['conversation_id']}"
                                   "/report/export", params={"format": "docx"})
            check(no_report.status_code == 400
                  and no_report.json()["error"]["code"] == "report_not_available",
                  "没有报告的问答：明确说没有，不产出空文件", str(no_report.status_code))
            unknown = client.get(f"/api/conversations/{cid}/report/export",
                                 params={"format": "pdf"})
            check(unknown.status_code == 400
                  and unknown.json()["error"]["code"] == "report_format_unknown",
                  "不认识的格式：明确拒绝（不偷偷换成别的格式）", str(unknown.status_code))

        print(f"\n产物目录（可以直接双击打开）：{workspace}")
        for fmt, path in saved.items():
            print(f"  · {path.name}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for label in FAILED:
        print(f"  FAIL  {label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
