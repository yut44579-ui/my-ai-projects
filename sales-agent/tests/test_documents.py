"""tests/test_documents.py · TASK-003 验收测试（Word/PDF 输入：提取 + 结构化摘要）。

════════════════════════════════════════════════════════════════════════
【本文件测什么】
════════════════════════════════════════════════════════════════════════
真实文件、真实提取、真实 HTTP（TestClient 进程内）——**没有一份 mock**：

  ① 真 .docx（python-docx 现写）→ 提取到的文本**必须是刚写进去的那些字**；
  ② 真 .pdf（本文件手搓的最小合法 PDF，见 `build_minimal_pdf`）→ 两页都要提出文字；
  ③ 扫描件（无文字流的 PDF）→ 必须 422 `document_no_text`，**不算成功**，且不留盘；
  ④ 摘要必须是**抽取式**：摘要里的每一句都能在原文里一字不差找到，关键事实的每个值
     都是原文里的字面量 —— 这是"数字不许编"这条铁律在文档能力上的落点；
  ⑤ 摘要幂等（不 force 不重算）/ force 可重算；
  ⑥ 新端点与老端点**并存**：/api/documents 只收 docx/pdf、/api/upload 只收表格，各拒各的；
  ⑦ 记录落盘后可重新读出（刷新恢复不靠内存）、原文没了要 410 而不是假装能读。

**不测**：不测 LLM 摘要（第一阶段 Gate 明确定"不上 LLM/RAG/向量库"）；
        不测 OCR（本阶段不做，扫描件如实报 document_no_text）。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import state  # noqa: E402
from app.api import app  # noqa: E402
from app.engine import docs  # noqa: E402

client = TestClient(app)

# 文档目录（新管道）与表格上传目录（老管道）在测试里都要隔离
ISOLATED_ENV = ("SRA_STATE_DIR", "SRA_DOC_DIR", "SRA_UPLOAD_DIR", "SRA_OUTPUT_DIR")


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """四个目录都指到临时目录：绝不碰仓库里的真实 state/ 与 data/。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


# ════════════════════════════════════════════════════════════════════════
# 测试夹具：真造文件（不是从别处拷来的死文件，也不是 mock）
# ════════════════════════════════════════════════════════════════════════
DOCX_TITLE = "2011年11月销售周报"
DOCX_LINES = [
    "本报告统计区间为 2011-11-21 至 2011-11-27，数据来自线上零售交易明细。",
    "销售额合计 316,412.16 元，有效订单 19950 行，占比 12.5%。",
    "其中取消单 296 行已按口径排除，排除金额 12,345.67 元。",
    "销售分析显示，United Kingdom 是本期贡献最大的市场，客户复购率明显提升。",
]


def make_docx(path: pathlib.Path, *, with_table: bool = True) -> None:
    """用 python-docx 现写一份有标题层级、正文和表格的 .docx。"""
    import docx

    document = docx.Document()
    document.add_heading(DOCX_TITLE, level=1)
    for line in DOCX_LINES:
        document.add_paragraph(line)
    document.add_heading("二、口径说明", level=2)
    document.add_paragraph("取消单（InvoiceNo 以 C 开头）、数量非正、单价非正一律排除。")
    if with_table:
        table = document.add_table(rows=2, cols=3)
        for column, header in enumerate(("指标", "值", "单位")):
            table.cell(0, column).text = header
        for column, value in enumerate(("销售额", "316,412.16", "元")):
            table.cell(1, column).text = value
    document.save(str(path))


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_minimal_pdf(pages: list[str], *, draw_text: bool = True) -> bytes:
    """手搓一份**最小合法 PDF**（pypdf 只能读不能写，这里不引新依赖来造测试文件）。

    每页一个内容流：`BT /F1 14 Tf 72 y Td (一行字) Tj ET`。
    `draw_text=False` 时内容流为空 —— 用来模拟**扫描件**（有页面、没有文字对象）。
    xref 偏移是真算出来的（不是糊一个 0 骗过解析器）。
    """
    objects: dict[int, bytes] = {}
    page_numbers = [4 + 2 * index for index in range(len(pages))]
    content_numbers = [5 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{number} 0 R" for number in page_numbers)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for index, page_text in enumerate(pages):
        if draw_text:
            operators = []
            y = 720
            for line in page_text.split("\n"):
                operators.append(f"BT /F1 14 Tf 72 {y} Td ({_pdf_escape(line)}) Tj ET")
                y -= 20
            content = "\n".join(operators).encode("latin-1")
        else:
            content = b""                                  # 空白页：没有文字对象
        objects[content_numbers[index]] = (
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
            + content + b"\nendstream"
        )
        objects[page_numbers[index]] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_numbers[index]} 0 R >>"
        ).encode("ascii")

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode("ascii") + objects[number] + b"\nendobj\n"

    start_xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii") + b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


PDF_PAGES = [
    "Weekly Sales Report 2011-11-21 to 2011-11-27\n"
    "Total sales amount is 316,412.16 CNY.\n"
    "Valid rows are 19950 and the share is 12.5%.",
    "Excluded rows are 296 because the invoice number starts with C.\n"
    "The largest market is United Kingdom.",
]


def upload_docx(tmp_path: pathlib.Path, name: str = "weekly.docx") -> dict:
    path = tmp_path / name
    make_docx(path)
    with open(path, "rb") as handle:
        response = client.post("/api/documents", files={"file": (name, handle, "application/octet-stream")})
    assert response.status_code == 201, response.text
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# ① docx：提取到的必须是刚写进去的字
# ════════════════════════════════════════════════════════════════════════
def test_docx_upload_extracts_the_real_text(tmp_path) -> None:
    body = upload_docx(tmp_path)

    assert body["suffix"] == ".docx"
    assert body["chars"] > 0, "文本长度为 0 就不算成功（指令原文）"
    assert body["block_unit"] == "paragraph"
    assert body["title"] == DOCX_TITLE, body["title"]
    assert DOCX_TITLE in body["outline"], "一级标题应该进大纲"
    assert "二、口径说明" in body["outline"], "二级标题也应该进大纲"
    assert body["warnings"] == [], "正常的 docx 不该有 warning"

    # 关键：逐句核对**刚写进去的内容**确实被提取出来了
    text = client.get(f"/api/documents/{body['doc_id']}/text").text
    for line in DOCX_LINES:
        assert line in text, f"提取结果里缺这一句：{line}"
    assert "取消单（InvoiceNo 以 C 开头）" in text, "正文最后一段没提到"
    assert "销售额 | 316,412.16 | 元" in text, "表格内容没被拍平成文本"
    assert body["text_preview"] and body["text_preview"] in text

    # chars 是真实字符数（与全文一致，不是估算）
    assert body["chars"] == len(text.strip())
    # 段落数 = 正文段数 + 表格行数（7 段正文 + 2 行表格）
    assert body["blocks"] == 9, body["blocks"]


def test_docx_without_table_still_extracts(tmp_path) -> None:
    path = tmp_path / "no_table.docx"
    make_docx(path, with_table=False)
    with open(path, "rb") as handle:
        body = client.post(
            "/api/documents", files={"file": ("no_table.docx", handle, "application/octet-stream")}
        ).json()
    assert body["chars"] > 0
    assert body["extra"]["tables"] == 0
    assert body["blocks"] == 7, body["blocks"]


# ════════════════════════════════════════════════════════════════════════
# ② pdf：手搓的真 PDF，两页都要提出字
# ════════════════════════════════════════════════════════════════════════
def test_pdf_upload_extracts_every_page(tmp_path) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(build_minimal_pdf(PDF_PAGES))
    with open(path, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": ("report.pdf", handle, "application/pdf")}
        )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["suffix"] == ".pdf"
    assert body["chars"] > 0
    assert body["blocks"] == 2 and body["block_unit"] == "page"
    assert body["warnings"] == [], "两页都有字，不该报「扫描页」的 warning"

    text = client.get(f"/api/documents/{body['doc_id']}/text").text
    assert "316,412.16 CNY" in text, text[:400]
    assert "Excluded rows are 296" in text, "第二页的内容没被提取出来"
    assert "United Kingdom" in text


# ════════════════════════════════════════════════════════════════════════
# ③ 提不出文本 → 不算成功（扫描件）
# ════════════════════════════════════════════════════════════════════════
def test_pdf_without_text_is_rejected_and_not_kept(tmp_path) -> None:
    path = tmp_path / "scanned.pdf"
    path.write_bytes(build_minimal_pdf(["", ""], draw_text=False))
    doc_dir = state.doc_dir()
    before = sorted(doc_dir.glob("*"))

    with open(path, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": ("scanned.pdf", handle, "application/pdf")}
        )

    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error"]["code"] == "document_no_text"
    assert "不算提取成功" in payload["error"]["message"]
    assert sorted(doc_dir.glob("*")) == before, "提不出文本的文件不该留在盘上"


def test_empty_docx_is_rejected(tmp_path) -> None:
    import docx

    path = tmp_path / "empty.docx"
    docx.Document().save(str(path))                     # 一个字都没有的 docx
    with open(path, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": ("empty.docx", handle, "application/octet-stream")}
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "document_no_text"


def test_broken_and_unsupported_files_fail_loudly(tmp_path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is definitely not a pdf")
    with open(broken, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": ("broken.pdf", handle, "application/pdf")}
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "document_broken"

    # 表格文件走**老端点**，不该被文档端点收下（两条管道各管一种输入）
    table = tmp_path / "rows.csv"
    table.write_text("a,b\n1,2\n", encoding="utf-8")
    with open(table, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": ("rows.csv", handle, "text/csv")}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "document_unsupported"
    assert "/api/upload" in response.json()["error"]["message"], "要告诉用户该走哪个端点"


# ════════════════════════════════════════════════════════════════════════
# ④ 摘要：抽取式（可回原文核对）+ 关键词 + 关键事实
# ════════════════════════════════════════════════════════════════════════
def test_summary_is_extractive_and_every_fact_is_verbatim(tmp_path) -> None:
    body = upload_docx(tmp_path)
    doc_id = body["doc_id"]
    response = client.post(f"/api/documents/{doc_id}/summary")
    assert response.status_code == 200, response.text
    summary = response.json()

    text = client.get(f"/api/documents/{doc_id}/text").text
    assert summary["method"] == docs.SUMMARY_METHOD
    assert "未使用 LLM" in summary["note"] and "未使用向量库" in summary["note"]

    # ① 摘要里的每一句都必须能在原文里**一字不差**找到（抽取式，不是生成式）
    assert summary["sentences"], "摘要不该是空的"
    for sentence in summary["sentences"]:
        assert sentence in text, f"摘要里出现了原文没有的句子（这就是「编」）：{sentence}"
    assert summary["summary"] == "".join(
        sentence if sentence[-1] in "。！？!?；;." else sentence + "。"
        for sentence in summary["sentences"]
    )

    # ② 关键事实的每个值都是原文里的字面量（含上下文也是原文切片）
    assert summary["key_facts"], "文档里明明有一堆数字，关键事实不该是空的"
    for fact in summary["key_facts"]:
        assert fact["value"] in text, f"关键事实的值不在原文里：{fact['value']}"
        assert fact["context"] in text, f"关键事实的上下文不是原文切片：{fact['context']}"
    kinds = {fact["type"] for fact in summary["key_facts"]}
    assert {"money", "percent", "date"} <= kinds, kinds
    assert any("316,412.16" in fact["value"] for fact in summary["key_facts"])

    # ③ 关键词来自词频（出现在原文里、计数 ≥2）
    assert summary["keywords"], "关键词不该是空的"
    for keyword in summary["keywords"]:
        assert keyword["count"] >= 2
        assert keyword["term"] in text.lower()

    # ④ 摘要知道自己是基于哪一版原文（可追溯）
    assert summary["input_sha256"] == body["sha256"]
    assert summary["generated_at"]


def test_summary_is_idempotent_unless_forced(tmp_path) -> None:
    doc_id = upload_docx(tmp_path, "idempotent.docx")["doc_id"]

    first = client.post(f"/api/documents/{doc_id}/summary").json()
    assert first["cached"] is False
    second = client.post(f"/api/documents/{doc_id}/summary").json()
    assert second["cached"] is True, "已生成过就直接给记录里那份，不重算"
    assert second["generated_at"] == first["generated_at"]
    assert second["summary"] == first["summary"]

    forced = client.post(f"/api/documents/{doc_id}/summary?force=true").json()
    assert forced["cached"] is False
    assert forced["summary"] == first["summary"], "同一份原文重算，结果必须一样（确定性）"

    # 摘要写回了记录：重新读单个文档就能看到（刷新恢复不靠内存）
    detail = client.get(f"/api/documents/{doc_id}").json()
    assert detail["summary"]["summary"] == first["summary"]
    assert detail["summary"]["generated_at"] == forced["generated_at"]


def test_summary_limits_are_respected(tmp_path) -> None:
    doc_id = upload_docx(tmp_path, "limits.docx")["doc_id"]
    summary = client.post(
        f"/api/documents/{doc_id}/summary?max_sentences=2&max_keywords=3&max_facts=4"
    ).json()
    assert len(summary["sentences"]) <= 2
    assert len(summary["keywords"]) <= 3
    assert len(summary["key_facts"]) <= 4


# ════════════════════════════════════════════════════════════════════════
# ⑤ 记录可重读 / 原文没了要 410
# ════════════════════════════════════════════════════════════════════════
def test_records_are_relistable_and_detail_matches_upload(tmp_path) -> None:
    first = upload_docx(tmp_path, "a.docx")
    second = upload_docx(tmp_path, "b.docx")

    listing = client.get("/api/documents").json()
    assert listing["total"] == 2
    ids = [item["doc_id"] for item in listing["documents"]]
    assert ids == [second["doc_id"], first["doc_id"]], "新的在前"

    detail = client.get(f"/api/documents/{second['doc_id']}").json()
    for field in ("doc_id", "filename", "chars", "blocks", "sha256", "size_bytes", "suffix"):
        assert detail[field] == second[field], field
    assert "text" not in detail, "默认不带全文（整篇正文不该塞进列表/详情）"

    with_text = client.get(f"/api/documents/{second['doc_id']}?include_text=true").json()
    assert with_text["chars"] == len(with_text["text"].strip())

    assert client.get("/api/documents/d_missing").status_code == 404


def test_deleted_source_file_returns_410(tmp_path) -> None:
    doc_id = upload_docx(tmp_path, "gone.docx")["doc_id"]
    record = state.get_document(doc_id)
    pathlib.Path(record["stored_path"]).unlink()          # 模拟原文被清理掉

    response = client.get(f"/api/documents/{doc_id}/text")
    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "document_file_gone"


# ════════════════════════════════════════════════════════════════════════
# ⑥ 与老端点并存：Excel 那条路一个字节没改
# ════════════════════════════════════════════════════════════════════════
def test_excel_and_document_pipelines_coexist(tmp_path) -> None:
    # 老端点：csv 上传照旧 201，响应字段照旧（这里刻意用 csv，避免为了这条断言重读 23MB xlsx）
    csv = tmp_path / "small.csv"
    csv.write_text("InvoiceNo,Quantity\nA1,3\nA2,5\n", encoding="utf-8")
    with open(csv, "rb") as handle:
        upload = client.post(
            "/api/upload", files={"file": ("small.csv", handle, "text/csv")}
        )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert set(body) >= {"file_id", "filename", "rows", "columns", "column_names", "sha256"}
    assert body["rows"] == 2 and body["column_names"] == ["InvoiceNo", "Quantity"]

    # 老端点拒收 docx（它只认表格）——两条管道各拒各的，谁也没放宽
    docx_path = tmp_path / "coexist.docx"
    make_docx(docx_path)
    with open(docx_path, "rb") as handle:
        rejected = client.post(
            "/api/upload",
            files={"file": ("coexist.docx", handle, "application/octet-stream")},
        )
    assert rejected.status_code == 400, rejected.text
    assert ".docx" in rejected.json()["detail"]

    # 新端点收下 docx 并真的提出文本
    with open(docx_path, "rb") as handle:
        accepted = client.post(
            "/api/documents", files={"file": ("coexist.docx", handle, "application/octet-stream")}
        )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["chars"] > 0

    # 文档计数走**自己的**端点，不塞进 /api/health ——
    # /api/health 是被冻结的既有端点（AC-09），响应形状一个字都不许动，
    # 所以 TASK-003 的计数不能借它的 state 块搭车。
    listing = client.get("/api/documents").json()
    assert listing["total"] == 1
    assert len(listing["documents"]) == 1

    # 老端点的健康检查照旧（老字段一个没动、也没有多出来的字段）
    health = client.get("/api/health").json()
    assert health["state"]["uploads"] == 1
    assert set(health["state"]) == {
        "state_dir", "schema_version", "uploads", "executions", "tasks", "readable",
    }


def test_same_file_twice_gets_two_records_with_same_sha(tmp_path) -> None:
    """同一个文件传两次 = 两条记录（同 sha）——不合并：哪次上传是哪个 doc_id 要分得清。"""
    path = tmp_path / "dup.docx"
    make_docx(path)
    hashes = []
    for _ in range(2):
        with open(path, "rb") as handle:
            hashes.append(
                client.post(
                    "/api/documents", files={"file": ("dup.docx", handle, "application/octet-stream")}
                ).json()
            )
    assert hashes[0]["doc_id"] != hashes[1]["doc_id"]
    assert hashes[0]["sha256"] == hashes[1]["sha256"]
    assert hashes[0]["chars"] == hashes[1]["chars"]
    assert client.get("/api/documents").json()["total"] == 2


# ════════════════════════════════════════════════════════════════════════
# ⑦ engine 层直测（不经 HTTP 的那部分规则）
# ════════════════════════════════════════════════════════════════════════
def test_engine_rejects_unsupported_suffix_without_touching_disk(tmp_path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(docs.DocumentError) as error:
        docs.extract(path)
    assert error.value.code == "document_unsupported"


def test_engine_summarize_is_deterministic() -> None:
    text = "\n".join(DOCX_LINES)
    first = docs.summarize(text)
    second = docs.summarize(text)
    assert first == second, "同一段文本两次摘要必须完全一致（没有随机、没有模型）"
    assert first["chars"] == len(text)
    assert first["fact_counts"]["money"] >= 2


def test_engine_summarize_on_text_without_numbers() -> None:
    """纯文字也不许崩、不许编：没有数字就没有关键事实，关键词照常给。"""
    text = "客户反馈产品质量稳定。客户反馈服务响应及时。产品质量是客户最关心的。" * 2
    summary = docs.summarize(text)
    assert summary["key_facts"] == []
    assert summary["keywords"], "重复出现的词应该被认出来"
    assert summary["sentences"], "至少要有句子"
    for sentence in summary["sentences"]:
        assert sentence in text
