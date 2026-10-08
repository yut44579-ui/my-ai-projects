"""test_fr009a_visibility.py · FR-009-A 验收：资料可见性与一致性修复。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么（对应派单里的 A-1 ~ A-5）】
════════════════════════════════════════════════════════════════════════
    A-1 导入一份 PDF → `GET /api/documents` 能返回它，标题不乱码，再查一次还在
    A-2 「导入资料」进来的新文档与「上传并提取」留下的历史文档**都看得见**；
        同一个物理文件不会因为走了两条管道就显示成两份；一个来源坏了不影响另一个
    A-3 导入记录能列出：文件名 / 类型 / 状态 / 时间 / 落成了什么 / 字数或行数，
        点进去能拿到这一条的详情
    A-4 失败的导入**也留一条记录**（status=failed + 可读原因），绝不伪装成成功
    A-5 PDF 标题：UTF-16 误读的要解回来；实在解不出来的必须退回文件名（去扩展名）

全部真文件、真 HTTP、真落库 —— 没有一份 mock：PDF 是现搓的最小合法 PDF，
docx 是 python-docx 现写的，隔离目录由 `fr003_helpers.isolate` 指到 tmp_path。

**不做**：文档问答 / 文档检索是 FR-009-B，不在这一组里（本 TASK 明确不碰）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fr003_helpers as helpers  # noqa: E402
from app import state  # noqa: E402
from app.api import app  # noqa: E402
from app.document_title import is_trustworthy_title, repair_title  # noqa: E402

client = TestClient(app)
WEB = PROJECT_ROOT / "web"

#: 用户实测那份 PDF 在库里存着的标题原文（`\\x00` 夹在字符中间 = UTF-16 字节被当单字节读）。
#: 一字不改地抄自 `data/app.db` 的 documents.title。
REAL_MOJIBAKE = "\x00A\x00I\x00 n8b\x0fR6O\\w\xe5\x8b\xc6^\x93"
REAL_TITLE = "AI 游戏制作知识库"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def upload_docs(files: list[tuple[str, bytes]]) -> list[tuple[str, tuple[str, bytes, str]]]:
    """多文件上传（同名条目重复出现，与前端 api.js 的 FormData 同一形状）。"""
    return [("files", (name, data, "application/octet-stream")) for name, data in files]


def make_utf16_title_pdf(path: pathlib.Path, title: str, *, encoding: str = "utf-16-be") -> pathlib.Path:
    """造一份**首页首行是 UTF-16 误读串**的 PDF（复现用户实测的那类文件）。

    怎么造出来的：把标题按 UTF-16 编码成字节，再按 latin-1 读成字符串写进 PDF 的字面串 ——
    这正是"UTF-16 字节被当单字节编码读"在生产里发生的方式。
    正文其余部分只能用 ASCII（PDF 标准字体的默认编码是 Latin-1，塞中文会在**造文件**这一步就报错）。
    """
    from test_documents import build_minimal_pdf

    mangled = title.encode(encoding).decode("latin-1")
    path.write_bytes(build_minimal_pdf([f"{mangled}\nBody text in plain ascii."]))
    return path


def import_pdf(name: str, data: bytes) -> dict:
    response = client.post("/api/imports", files=upload_docs([(name, data)]))
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def legacy_upload(path: pathlib.Path, name: str) -> dict:
    """走 TASK-003 的「上传并提取」（旧管道，写 state/documents.json）。"""
    with open(path, "rb") as handle:
        response = client.post(
            "/api/documents", files={"file": (name, handle, "application/octet-stream")}
        )
    assert response.status_code == 201, response.text
    return response.json()


# ════════════════════════════════════════════════════════════════════════
# A-1 文档可见
# ════════════════════════════════════════════════════════════════════════
def test_A1_导入的文档在统一列表里可见(env, tmp_path) -> None:
    """Given 用户导入一份 PDF 且成功 → Then GET /api/documents 能返回它，标题不乱码。"""
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    receipt = import_pdf("季度销售.pdf", data)
    assert receipt["ok"] is True

    body = client.get("/api/documents").json()
    assert body["total"] == 1
    doc = body["documents"][0]
    # 同一个逻辑文档：导入回执里的 document_id 与列表里的编号必须对得上
    assert doc["document_id"] == receipt["document_id"]
    assert doc["doc_id"] == receipt["document_id"], "老字段名也要能对上（前端与既有测试都在用）"
    assert doc["filename"] == "季度销售.pdf"
    assert doc["source_type"] == "import" and doc["source_label"]
    assert doc["import_id"] == receipt["import_id"], "文档要能追回它那次导入"
    assert doc["chars"] > 0 and doc["title"]

    # 刷新页面 = 重新请求一次：记录在库里，不是内存态
    again = client.get("/api/documents").json()
    assert [item["document_id"] for item in again["documents"]] == [doc["document_id"]]


def test_A1_导入的文档正文与详情都取得到(env, tmp_path) -> None:
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    document_id = import_pdf("季度销售.pdf", data)["document_id"]

    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["filename"] == "季度销售.pdf"
    assert "text" not in detail, "默认不带全文（整篇正文不该塞进列表/详情）"

    text = client.get(f"/api/documents/{document_id}/text")
    assert text.status_code == 200
    assert "Quarterly sales report" in text.text
    assert text.headers["X-Doc-Chars"] == str(detail["chars"])

    with_text = client.get(f"/api/documents/{document_id}?include_text=true").json()
    assert "Quarterly sales report" in with_text["text"]


# ════════════════════════════════════════════════════════════════════════
# A-2 新旧文档兼容
# ════════════════════════════════════════════════════════════════════════
def test_A2_新旧两个来源的文档都看得见(env, tmp_path) -> None:
    """Given 旧管道有一份、新管道有一份 → Then 统一列表里两份都在。"""
    legacy = legacy_upload(helpers.make_docx(tmp_path / "历史周报.docx"), "历史周报.docx")
    imported = import_pdf("季度销售.pdf", helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes())

    body = client.get("/api/documents").json()
    assert body["total"] == 2
    by_id = {item["document_id"]: item for item in body["documents"]}
    assert set(by_id) == {legacy["doc_id"], imported["document_id"]}
    assert by_id[legacy["doc_id"]]["source_type"] == "legacy"
    assert by_id[imported["document_id"]]["source_type"] == "import"
    # 两个来源的正文都取得到（各走各的取法，但对外是同一个接口）
    for document_id in by_id:
        assert client.get(f"/api/documents/{document_id}/text").status_code == 200


def test_A2_同一个物理文件不会因为来源不同显示成两份(env, tmp_path) -> None:
    """同一个文件走了两条管道 = 同一份物理文件 → 只能显示成**一个**逻辑文档。"""
    path = helpers.make_pdf(tmp_path / "季度销售.pdf")
    data = path.read_bytes()
    legacy_upload(path, "季度销售.pdf")                    # 旧管道：写 JSON
    imported = import_pdf("季度销售.pdf", data)            # 新管道：写 SQLite（同 sha256）

    body = client.get("/api/documents").json()
    assert body["total"] == 1, f"同一个文件被显示成了 {body['total']} 份"
    kept = body["documents"][0]
    assert kept["document_id"] == imported["document_id"], "留下的应当是导入进来的那一条"
    assert any("同一个文件" in note for note in body["source_notes"]), \
        "合并了就该说明，不要静默吞掉一条"


def test_A2_旧来源损坏时列表仍然能返回新文档(env, tmp_path) -> None:
    """一个来源坏了**不许**拖垮整个列表（评审验收 A-2 第三条）。"""
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    imported = import_pdf("季度销售.pdf", data)

    # 把历史文档那份 JSON 写坏（模拟状态文件损坏）
    state.documents_file().write_text("{ 这不是合法 JSON", encoding="utf-8")

    response = client.get("/api/documents")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["documents"][0]["document_id"] == imported["document_id"]
    assert body["source_notes"], "降级了就要说清楚，别装作一切正常"


def test_A2_新来源读不出来时只剩历史文档且不报错(env, tmp_path) -> None:
    """反过来也要成立：库读不出来时，历史文档照常列出来。"""
    legacy = legacy_upload(helpers.make_docx(tmp_path / "历史周报.docx"), "历史周报.docx")
    # 把库文件换成一个"不是 SQLite"的文件（模拟本机存储损坏）
    pathlib.Path(os.environ["SRA_DB_PATH"]).write_bytes(b"not a database")

    body = client.get("/api/documents").json()
    assert body["total"] == 1
    assert body["documents"][0]["document_id"] == legacy["doc_id"]
    assert body["source_notes"]


# ════════════════════════════════════════════════════════════════════════
# A-3 导入记录可见
# ════════════════════════════════════════════════════════════════════════
def test_A3_导入记录带齐页面要的那六列(env, tmp_path) -> None:
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    receipt = import_pdf("季度销售.pdf", data)

    body = client.get("/api/imports").json()
    assert body["total"] == 1
    record = body["imports"][0]
    assert record["import_id"] == receipt["import_id"]
    assert record["filename"] == "季度销售.pdf"          # 文件名
    assert record["source_type_label"] == "PDF 文档"     # 类型
    assert record["status"] == "success" and record["status_label"] == "已入库"   # 状态
    assert record["imported_at"]                          # 时间
    assert record["document_id"] and not record["dataset_id"]   # 落成了什么（文档）
    assert record["document_char_count"] > 0              # 字数或行数

    detail = client.get(f"/api/imports/{receipt['import_id']}").json()
    assert detail["filename"] == "季度销售.pdf"
    assert detail["document_id"] == receipt["document_id"]
    assert detail["source_file"]["filename"] == "季度销售.pdf"


def test_A3_数据集那一路给的是行数(env, tmp_path) -> None:
    """只导数据表时，"字数或行数"那一列要给行数（而不是空着）。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    response = client.post("/api/imports", files=upload_docs([("地区销售.xlsx", path.read_bytes())]))
    assert response.json()["ok_count"] == 1

    record = client.get("/api/imports").json()["imports"][0]
    assert record["dataset_id"] and not record["document_id"]
    assert record["dataset_row_count"] == 3, "三行样例数据 → 三行"
    assert record["document_char_count"] is None


def test_A2_导入记录列表一次查完不逐条查库(env, tmp_path) -> None:
    """N+1 的防线：一页 4 条记录，取"落成了什么"只允许一次 IN 查询（不是每条一次）。"""
    from app.importer import store

    for index in range(4):
        # 四份**内容不同**的 PDF：内容一样的话 sha256 相同 → 幂等命中，只会有一条导入记录
        path = helpers.make_pdf(tmp_path / f"第{index}份.pdf", [f"Report number {index}"])
        import_pdf(f"第{index}份.pdf", path.read_bytes())

    calls: list[str] = []
    original = store.document_char_counts

    def counting(connection, document_ids):
        calls.append("document_char_counts")
        return original(connection, document_ids)

    store.document_char_counts = counting
    try:
        assert client.get("/api/imports").json()["total"] == 4
    finally:
        store.document_char_counts = original
    assert len(calls) == 1, f"一页记录查了 {len(calls)} 次（N+1）"


def test_A3_页面上有导入记录那张表(env) -> None:
    """静态层：数据管理页有这张表、前端真的去读那条接口、失败原因有地方显示。"""
    html, js, api = read("index.html"), read("app.js"), read("api.js")
    assert 'id="imports-body"' in html and 'id="imports-empty"' in html
    assert 'id="import-detail"' in html
    # 六列都在表头里（文件名 / 类型 / 状态 / 时间 / 落成了什么 / 字数或行数）
    for header in ("文件名", "类型", "状态", "时间", "落成了什么", "字数或行数"):
        assert header in html, f"表头缺一列：{header}"
    assert "imports-body" in js and "renderImports" in js
    assert "listImports" in api and "getImport" in api
    # 详情里要说失败原因（后端给的那句），并且只在这条真的失败时出现
    assert "失败原因" in js and 'detail.status === "failed"' in js
    # 前端不自己算字数/行数：直接用后端给的两个字段
    assert "document_char_count" in js and "dataset_row_count" in js


def test_A2_文档表要说清每条从哪个入口进来(env) -> None:
    """两个入口进来的文档合到一张表里，就必须有一列说明来源（否则用户分不清）。"""
    html, js = read("index.html"), read("app.js")
    assert "<th>来源</th>" in html
    assert "source_label" in js


def test_A2_已入库数据源空表不再误导纯文档用户(env) -> None:
    """★ 派单点名的那句误导读者的文案：只导了 PDF 时不许说"还没有，去导入一份带数据的文件"。"""
    js = read("app.js")
    assert "sources-empty-title" in js and "sources-empty-hint" in js, "空态文案必须能按情况换"
    assert "还没有已入库的数据源（但有文档资料）" in js, "要能区分「没导进来」和「导进来了但不产数据集」"
    assert "state.documents.length" in js, "判断依据是**真的有文档资料**，不是别的东西"


# ════════════════════════════════════════════════════════════════════════
# A-4 失败的导入也有记录
# ════════════════════════════════════════════════════════════════════════
def test_A4_解析失败也留一条failed记录_不伪装成success(env, tmp_path) -> None:
    """Given 导入一份解析不了的文件 → Then 记录还在，status=failed，原因可读。"""
    broken = tmp_path / "打不开的.pdf"
    broken.write_bytes(b"%PDF-1.4\n% this is not really a pdf\n")
    response = client.post("/api/imports", files=upload_docs([("打不开的.pdf", broken.read_bytes())]))
    assert response.json()["failed_count"] == 1

    body = client.get("/api/imports").json()
    assert body["total"] == 1, "失败的导入也必须留下一条记录"
    record = body["imports"][0]
    assert record["status"] == "failed"
    assert record["status_label"] == "导入失败", "**绝不**伪装成已入库"
    assert record["error_code"], "要有机器可读的错误码"
    assert record["note"], "要有一句人能读懂的原因"
    assert record["document_id"] is None and record["dataset_id"] is None, "失败不留半成品"

    detail = client.get(f"/api/imports/{record['import_id']}").json()
    assert detail["status"] == "failed" and detail["note"]


def test_A4_失败记录和成功记录在同一个列表里(env, tmp_path) -> None:
    """用户视角：「我导过它，失败了，原因是啥」—— 成功与失败要能在同一张表里一起看到。"""
    good = helpers.make_pdf(tmp_path / "好文件.pdf")
    response = client.post("/api/imports", files=upload_docs([
        ("好文件.pdf", good.read_bytes()),
        ("空的.csv", b"province,amount\n"),                 # 只有表头 → no_content
        ("坏的.docx", b"not a docx at all"),                # 签名不符 → 验身阶段就拒
    ]))
    assert response.json()["ok_count"] == 1

    records = client.get("/api/imports").json()["imports"]
    by_name = {record["filename"]: record for record in records}
    assert by_name["好文件.pdf"]["status"] == "success"
    assert by_name["空的.csv"]["status"] == "failed"
    assert by_name["空的.csv"]["note"], "失败原因要写下来"
    # ★ 验身阶段被拒的那份**故意不留记录**（FR-003B 的 ASSERT：不是它自称的格式 → 一行都不写）。
    #   这一条与"失败的导入也要留记录"的边界写在 TASK 报告里，由 Hermes 裁决 —— 测试照实钉住现状。
    assert "坏的.docx" not in by_name


# ════════════════════════════════════════════════════════════════════════
# A-5 PDF 标题
# ════════════════════════════════════════════════════════════════════════
def test_A5_用户实测那个乱码标题能被解回来(env) -> None:
    """库里那条真标题（一字不改抄下来）应当解回「AI 游戏制作知识库」。"""
    assert repair_title(REAL_MOJIBAKE, "随便什么名字.pdf") == REAL_TITLE
    assert "\x00" not in repair_title(REAL_MOJIBAKE, "随便什么名字.pdf")


@pytest.mark.parametrize("encoding", ["utf-16-be", "utf-16-le", "utf-16"])
def test_A5_UTF16三种排布的标题都要解回来(env, encoding: str) -> None:
    """BE / LE / 带 BOM —— 三种都是"UTF-16 字节被当单字节读"，都要能解回来。"""
    mangled = "Quarterly Report 2011".encode(encoding).decode("latin-1")
    assert repair_title(mangled, "兜底.pdf") == "Quarterly Report 2011"


def test_A5_正常标题一个字都不许改(env) -> None:
    """中文、英文、带数字的标题都是原样通过（修复不能"顺手"改写正常标题）。"""
    for title in ("华南区 11 月销售复盘", "Sales Report 2011-11-21 to 2011-11-27", "季度分析"):
        assert repair_title(title, "别的名字.docx") == title


def test_A5_解不出来时用文件名兜底(env) -> None:
    """Given 标题不可信（控制字符 / 替换符 / 空） → Then 用原始文件名（去扩展名）。"""
    assert repair_title("\x01\x02\x03\x04", "无标题的文档.pdf") == "无标题的文档"
    assert repair_title("a" + "�" * 8, "乱码.pdf") == "乱码"
    assert repair_title("", "空的.pdf") == "空的"
    assert repair_title(None, "没有的.pdf") == "没有的"
    assert repair_title("x" * 1000, "太长的.pdf") == "太长的"


def test_A5_质量校验挡住了解出来但其实是垃圾的结果(env) -> None:
    """解码成功 ≠ 解码正确：这一步是 A-5 的核心判据，单独钉住。"""
    assert is_trustworthy_title("AI 游戏制作知识库") is True
    assert is_trustworthy_title("\x00A\x00I") is False          # 夹着 NUL
    assert is_trustworthy_title("a�") is False                   # 替换符
    assert is_trustworthy_title("\x01\x02") is False             # 控制字符
    assert is_trustworthy_title("") is False
    assert is_trustworthy_title("   ") is False


def test_A5_导入一份UTF16标题的PDF_库里存的是正常标题(env, tmp_path) -> None:
    """端到端：真 PDF → 真导入 → 库里/接口上的标题都不许有乱码，且**不是**靠文件名蒙对的。"""
    path = make_utf16_title_pdf(tmp_path / "季度总结.pdf", "AI Report 2011")
    receipt = import_pdf("季度总结.pdf", path.read_bytes())

    body = client.get("/api/documents").json()
    record = next(item for item in body["documents"] if item["document_id"] == receipt["document_id"])
    assert record["title"] == "AI Report 2011", record["title"]
    assert record["filename"] == "季度总结.pdf"      # 标题与文件名是两回事，都对
    assert "\x00" not in record["title"]


def test_A5_中文标题的PDF_解不出来就用文件名(env, tmp_path) -> None:
    """中文标题经最小 PDF 的 Latin-1 字体流会被改写成不可还原的字节 → 必须退回文件名。"""
    path = make_utf16_title_pdf(tmp_path / "季度总结.pdf", "季度总结报告")
    receipt = import_pdf("季度总结.pdf", path.read_bytes())

    record = next(
        item for item in client.get("/api/documents").json()["documents"]
        if item["document_id"] == receipt["document_id"]
    )
    assert record["title"] == "季度总结", f"解不出来就该退回文件名，而不是留乱码：{record['title']!r}"


def test_A5_没有空字节指纹的字体编码错乱也必须退回文件名(env) -> None:
    """反例（实测出来的）：纯中文的 UTF-16 标题**一个空字节都没有**，指纹找不到。

    它被单字节读出来后仍然"全部可打印"，能骗过质量校验 → 看着像字、其实全错。
    判据是"整串都落在单字节可表示的范围内、又不是纯 ASCII → 必须由某一步解码证实"，
    证实不了就按评审第 5 步退回文件名。
    """
    assert repair_title("[c^ƒ‘;~Ób¥TJ", "季度总结报告.pdf") == "季度总结报告"


def test_A5_单字节范围内的非ASCII标题必须被解码证实才采信(env) -> None:
    """这道闸门的**代价**也要钉住，免得以后有人以为它是白捡的。

    像 "Sales — 2011" / "Café Report" 这种标题，整串都落在单字节可表示的范围内、
    又不是纯 ASCII —— 系统**分不清**它是"本来就长这样"还是"字节被单字节读出来的"，
    于是按 A-5 第 5 步退回文件名（宁可给一个真的名字，也不赌一把把乱码放上去）。"""
    assert repair_title("Sales — 2011", "季度销售.pdf") == "季度销售"
    assert repair_title("Café Report", "季度销售.pdf") == "季度销售"


def test_A5_旧管道上传的文档标题同样过一遍可信化(env, tmp_path) -> None:
    """两条管道共用同一份修复实现（不是只修了一处）。"""
    path = make_utf16_title_pdf(tmp_path / "季度总结.pdf", "AI Report 2011")
    body = legacy_upload(path, "季度总结.pdf")
    assert body["title"] == "AI Report 2011", body["title"]

    stored = state.get_document(body["doc_id"])
    assert stored["title"] == "AI Report 2011", "落库的那份也要是好的"


# ════════════════════════════════════════════════════════════════════════
# 边界：本 TASK 明确**不做**的事
# ════════════════════════════════════════════════════════════════════════
def test_文档可见性修复没有引入文档问答(env) -> None:
    """文档问答 / 检索是 FR-009-B，本 TASK 不许顺手做：不留任何检索入口。"""
    paths = client.get("/openapi.json").json()["paths"]
    banned = ("search", "retriev", "chunk", "embed", "/ask", "document_qa")
    assert not [path for path in paths if any(word in path for word in banned)], \
        f"出现了检索/问答类端点：{paths}"
    assert "/api/documents" in paths and "/api/imports" in paths, "该在的还在"


def test_统一视图的字段清单与评审要求一致(env, tmp_path) -> None:
    """评审点名的那七个字段一个都不能少（前端与将来的文档问答都要靠它们）。"""
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    import_pdf("季度销售.pdf", data)
    record = client.get("/api/documents").json()["documents"][0]
    for field in ("document_id", "source_type", "filename", "title", "created_at", "import_id"):
        assert field in record, f"统一视图缺字段：{field}"
    assert "text" in client.get(
        f"/api/documents/{record['document_id']}?include_text=true"
    ).json(), "text 要在（按需带全文）"


def test_两个来源的定位写在代码注释里(env) -> None:
    """评审要求把两个来源的定位写清楚（代码注释 + 文档）—— 静态钉一句，防止注释被清掉。"""
    source = (PROJECT_ROOT / "app" / "repositories" / "unified_documents.py").read_text("utf-8")
    assert "事实来源" in source and "兼容来源" in source
    assert "拒绝写时双写" in source, "为什么不做双写这条裁决必须留在代码里"


def test_状态文件里没有多余的技术字段泄漏到页面(env, tmp_path) -> None:
    """页面只给业务内容：来源那一列用的是后端给的中文话术，不是 source_type 的英文取值。"""
    js = read("app.js")
    assert "doc.source_label" in js
    assert "doc.source_type" not in js


def test_导入记录的详情不泄漏绝对路径(env, tmp_path) -> None:
    data = helpers.make_pdf(tmp_path / "季度销售.pdf").read_bytes()
    import_id = import_pdf("季度销售.pdf", data)["import_id"]
    raw = json.dumps(client.get(f"/api/imports/{import_id}").json(), ensure_ascii=False)
    assert "D:\\\\" not in raw and "/tmp/" not in raw
