#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档输入真闭环冒烟（TASK-003）—— 起**真服务**、发**真 HTTP**、用**真 Word/PDF 文件**。

为什么要有这个脚本（而不是只跑 pytest）：
    TestClient 是进程内直调，**证明不了"真的走 HTTP"**、也证明不了"页面真的被 serve 出去了"。
    本脚本起真 uvicorn（带静态挂载）→ GET / 拿真 HTML → 之后每一步都用**上一步 HTTP 响应里的值**
    （doc_id 从上传响应里来，不硬编码）→ 真上传 .docx/.pdf → 真提取 → 真读全文 → 真生成摘要。
    这就是 TASK-003 在 http 层的证据；浏览器走查由 Hermes 用 CDP 做（本项目**不装 Playwright**）。

真文件从哪来：
    **不塞死样本文件**，而是现造 —— 直接复用 `tests/test_documents.py` 里的两个构造器
    （`make_docx` / `build_minimal_pdf`）。复用而不是各写一套，是为了保证"冒烟用的文件"
    与"单测用的文件"是同一套字节，两边不会各自漂移。
    （代价：脚本 import 了测试模块。可接受 —— 测试模块本身没有副作用，也不改任何状态。）

跑法：
    .venv/Scripts/python.exe scripts/docs_e2e.py            # 默认端口 8513
预期末行：`🎉 TASK-003 文档闭环全部通过`
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# Windows 下 stdout 被管道接走时默认是 GBK，打印 ✅/❌ 会直接 UnicodeEncodeError 挂掉脚本。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENV = dict(os.environ)
ENV["PYTHONPATH"] = ""
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

sys.path.insert(0, os.path.join(ROOT, "tests"))

import httpx  # noqa: E402

from test_documents import (  # noqa: E402
    DOCX_LINES,
    DOCX_TITLE,
    PDF_PAGES,
    build_minimal_pdf,
    make_docx,
)

failures: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> None:
    """断言但**不中止**：一条失败也跑完，最后统一汇总（排障不用反复重跑整条链路）。"""
    mark = "✅" if condition else "❌"
    print(f"   {mark} {label}{(' | ' + extra) if extra else ''}")
    if not condition:
        failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser(description="TASK-003 文档输入真闭环冒烟")
    parser.add_argument("--port", type=int, default=8513)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    # 隔离落盘目录：冒烟会故意造失败上传，不该进仓库的真实审计目录
    sandbox = tempfile.mkdtemp(prefix="sra_docs_e2e_", dir=os.path.join(ROOT, "outputs"))
    ENV["SRA_STATE_DIR"] = os.path.join(sandbox, "state")
    ENV["SRA_DOC_DIR"] = os.path.join(sandbox, "documents")
    ENV["SRA_UPLOAD_DIR"] = os.path.join(sandbox, "uploads")
    ENV["SRA_OUTPUT_DIR"] = os.path.join(sandbox, "outputs")
    for key in ("SRA_STATE_DIR", "SRA_DOC_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR"):
        os.makedirs(ENV[key], exist_ok=True)
    print(f"落盘沙箱（跑完可删）：{sandbox}")

    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(args.port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=ENV,
    )
    try:
        client = httpx.Client(base_url=base, timeout=300.0)
        for _ in range(120):                      # 等真服务起来（首次 import pandas 有点慢）
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("服务 60 秒内没起来")

        # ── 【0】页面真的把「文档资料」区 serve 出去了（AC-01 / 前端可达）──────
        print("\n【0】静态前端含「文档资料」区（真 GET /）")
        page = client.get("/")
        html = page.text
        check(page.status_code == 200 and page.headers.get("content-type", "").startswith("text/html"),
              "GET / 返回真 HTML", f"status={page.status_code}")
        for control, label in [
            ("doc-input", "文档文件选择"),
            ("btn-doc-upload", "上传并提取按钮"),
            ("docs-table-body", "文档列表表格"),
            ("docs-empty", "文档空状态"),
            ("doc-viewer", "正文查看器"),
            ("btn-doc-summary", "生成摘要按钮"),
        ]:
            check(f'id="{control}"' in html, f"页面含 {label}（id={control}）")
        # 老的报表链路控件一个没少（并存，不是替换）
        for control in ("file-input", "btn-upload", "btn-create-task", "btn-run-task", "exec-table-body"):
            check(f'id="{control}"' in html, f"老报表链路控件仍在（id={control}）")

        # ── 【1】真 .docx → 真提取 ────────────────────────────────────────
        print("\n【1】真 Word（.docx）上传 → 文本提取")
        docx_path = os.path.join(sandbox, "weekly_sales.docx")
        make_docx(pathlib.Path(docx_path))
        with open(docx_path, "rb") as handle:
            response = client.post(
                "/api/documents",
                files={"file": ("weekly_sales.docx", handle, "application/octet-stream")},
            )
        check(response.status_code == 201, "上传 .docx → 201", response.text[:160])
        docx_body = response.json()
        check(docx_body.get("chars", 0) > 0, "**提取到真实文本（chars > 0）**", f"chars={docx_body.get('chars')}")
        check(docx_body.get("sha256"), "响应带原文 SHA256", f"sha256={str(docx_body.get('sha256'))[:16]}…")
        preview = docx_body.get("text_preview", "")
        check(DOCX_TITLE in preview, "提取结果里能读到文档标题（不是编的）")
        check("316,412.16" in preview, "正文段落被完整提取（含金额那句）")
        check(bool(docx_body.get("doc_id")), "响应带 doc_id", f"doc_id={docx_body.get('doc_id')}")

        # ── 【2】真 .pdf → 真提取（两页都要出来）──────────────────────────
        print("\n【2】真 PDF 上传 → 文本提取（两页）")
        pdf_path = os.path.join(sandbox, "weekly_sales.pdf")
        with open(pdf_path, "wb") as handle:
            handle.write(build_minimal_pdf(PDF_PAGES))
        with open(pdf_path, "rb") as handle:
            response = client.post(
                "/api/documents",
                files={"file": ("weekly_sales.pdf", handle, "application/pdf")},
            )
        check(response.status_code == 201, "上传 .pdf → 201", response.text[:160])
        pdf_body = response.json()
        check(pdf_body.get("chars", 0) > 0, "**提取到真实文本（chars > 0）**", f"chars={pdf_body.get('chars')}")
        check(pdf_body.get("blocks") == len(PDF_PAGES), "块数 = 页数（两页都读了）",
              f"blocks={pdf_body.get('blocks')}")
        pdf_preview = pdf_body.get("text_preview", "")
        check("316,412.16" in pdf_preview, "第 1 页正文提取到了")
        check("United Kingdom" in pdf_preview or "Excluded rows" in pdf_preview,
              "第 2 页正文提取到了（不是只读了首页）")

        # ── 【3】列表（刷新恢复的真相来源）──────────────────────────────
        print("\n【3】GET /api/documents 列表")
        listing = client.get("/api/documents").json()
        check(listing["total"] == 2, "列表 total = 2（两次上传两条记录）", f"total={listing['total']}")
        ids = {item["doc_id"] for item in listing["documents"]}
        check(ids == {docx_body["doc_id"], pdf_body["doc_id"]}, "列表里的 doc_id 与上传响应一致")
        check(all(item["chars"] > 0 for item in listing["documents"]), "列表里每条都标着真实字符数")

        # ── 【4】全文端点（前端「看正文」按钮打的就是它）─────────────────
        print("\n【4】GET /api/documents/{doc_id}/text 全文")
        text_response = client.get(f"/api/documents/{docx_body['doc_id']}/text")
        check(text_response.status_code == 200, "全文端点 200")
        check(text_response.headers.get("content-type", "").startswith("text/plain"), "返回 text/plain")
        full_text = text_response.text
        check(len(full_text) == docx_body["chars"], "全文长度 = 记录里的 chars（两处一致）",
              f"{len(full_text)} == {docx_body['chars']}")
        check(all(line in full_text for line in DOCX_LINES), "写进去的每一段原文都能一字不差找回来")
        check(text_response.headers.get("X-Doc-Sha256") == docx_body["sha256"], "响应头带的是这份原文的 SHA256")

        # ── 【5】结构化摘要（抽取式：每句都能回原文核）───────────────────
        print("\n【5】POST /api/documents/{doc_id}/summary 结构化摘要")
        summary_response = client.post(f"/api/documents/{docx_body['doc_id']}/summary")
        check(summary_response.status_code == 200, "摘要端点 200", summary_response.text[:160])
        summary = summary_response.json()
        check(bool(summary.get("keywords")), "关键词非空（词频 ≥2）", f"{len(summary.get('keywords', []))} 个")
        check(bool(summary.get("key_facts")), "关键事实非空（正则抽取）",
              f"{len(summary.get('key_facts', []))} 条")
        check(all(sentence in full_text for sentence in summary.get("sentences", [])),
              "**摘要每一句都是原文原句（抽取式，没让任何模型改写）**")
        check(all(fact["value"] in full_text for fact in summary.get("key_facts", [])),
              "每个关键事实的值都是原文里的字面量")
        check(summary.get("input_sha256") == docx_body["sha256"], "摘要标明了它是针对哪一版原文生成")
        cached = client.post(f"/api/documents/{docx_body['doc_id']}/summary").json()
        check(cached.get("cached") is True, "再点一次 → 取回已缓存的那份（不重算，幂等）")

        # ── 【6】失败要看得见（前端红条的真实来源）───────────────────────
        print("\n【6】失败路径")
        bad = client.post("/api/documents", files={"file": ("notes.txt", b"plain text", "text/plain")})
        check(bad.status_code == 400 and bad.json().get("error", {}).get("code") == "document_unsupported",
              "上传 .txt → 400 document_unsupported",
              f"code={bad.json().get('error', {}).get('code')}")
        scanned = os.path.join(sandbox, "scanned.pdf")
        with open(scanned, "wb") as handle:
            handle.write(build_minimal_pdf(["", ""], draw_text=False))
        with open(scanned, "rb") as handle:
            no_text = client.post("/api/documents", files={"file": ("scanned.pdf", handle, "application/pdf")})
        check(no_text.status_code == 422
              and no_text.json().get("error", {}).get("code") == "document_no_text",
              "扫描件（有页面无文字）→ 422 document_no_text，**不算成功**",
              f"code={no_text.json().get('error', {}).get('code')}")
        check(client.get("/api/documents").json()["total"] == 2, "两次失败都没留下记录（不留半截数据）")
        missing = client.get("/api/documents/doc-not-exists")
        check(missing.status_code == 404 and missing.json().get("error", {}).get("code") == "document_not_found",
              "查不存在的 doc_id → 404 document_not_found")

        # ── 【7】两条管道并存（谁也没放宽）───────────────────────────────
        print("\n【7】新老两条输入管道并存")
        with open(docx_path, "rb") as handle:
            rejected = client.post("/api/upload", files={"file": ("weekly_sales.docx", handle, "application/octet-stream")})
        check(rejected.status_code == 400, "老表格端点仍拒收 .docx（没为文档能力放宽）",
              f"status={rejected.status_code}")
        xlsx = os.path.join(sandbox, "table.xlsx")
        import pandas as pd  # noqa: PLC0415 —— 只在造这份表格时用
        pd.DataFrame({"a": [1]}).to_excel(xlsx, index=False)
        with open(xlsx, "rb") as handle:
            rejected2 = client.post("/api/documents", files={"file": ("table.xlsx", handle, "application/octet-stream")})
        check(rejected2.status_code == 400, "新文档端点拒收 .xlsx（各拒各的）",
              f"status={rejected2.status_code}")

        # ── 【8】Legacy Contract：/api/health 的 state 块**逐字**没变 ─────
        print("\n【8】Legacy Contract（AC-09）：老端点语义未变")
        health = client.get("/api/health").json()
        check(set(health["state"]) == {"state_dir", "schema_version", "uploads", "executions", "tasks", "readable"},
              "**/api/health 的 state 块键集与冻结版逐字一致（文档计数没有搭车塞进来）**",
              f"keys={sorted(health['state'])}")
        check(health["state"]["uploads"] == 0, "文档上传没有污染表格上传计数（两条管道各有各的目录）")

        # ── 【9】落盘证据（刷新恢复不靠内存）────────────────────────────
        print("\n【9】落盘证据")
        docs_json = os.path.join(ENV["SRA_STATE_DIR"], "documents.json")
        check(os.path.isfile(docs_json), "state/documents.json 真的落盘了", docs_json)
        records = json.loads(open(docs_json, encoding="utf-8").read())["documents"]
        check(len(records) == 2, "两份文档都记在盘上", f"{len(records)} 条")
        check(all(record.get("chars", 0) > 0 and record.get("sha256") for record in records),
              "每条记录都带真实字符数与 SHA256（审计凭据）")
        stored = [record["stored_path"] for record in records]
        check(all(os.path.isfile(path) for path in stored), "原文文件也都在盘上（取全文要重读原件）")
        # 换一个全新的 client（不复用连接/不依赖任何内存状态）重新读一遍
        with httpx.Client(base_url=base, timeout=60.0) as fresh:
            reread = fresh.get("/api/documents").json()
        check(reread["total"] == 2, "**新连接重新 GET 能读回同样的两条（刷新恢复靠后端，不靠 JS 内存）**")

        print()
        if failures:
            print(f"❌ TASK-003 文档闭环有 {len(failures)} 项未通过：")
            for item in failures:
                print(f"   · {item}")
            return 1
        print("🎉 TASK-003 文档闭环全部通过")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
