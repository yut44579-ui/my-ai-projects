"""tests/test_report_export.py · 报告的导出格式：Word（默认）/ Excel / Markdown。

════════════════════════════════════════════════════════════════════════
【这条需求的原话与病灶】
════════════════════════════════════════════════════════════════════════
用户实测反馈「下载不了」——下载其实成功了，但下到的是 **.md**：Windows 上双击打不开
（没有关联程序），体感就是"下载不了"；而且用户要的是"像 Excel 那样有实际数据",
给一个纯文本文件本来就不对路。

所以默认格式改成 **Word**（周报是文档形态：标题 / 摘要 / 分节 / 真表格 / 结论建议），
另给 **Excel**（要拿数字继续算的场合）与 **Markdown**（前端预览那段文本）。

════════════════════════════════════════════════════════════════════════
【本文件怎么验（真文件、真读回）】
════════════════════════════════════════════════════════════════════════
不打桩、不假装：走真 HTTP（TestClient）拿到**真字节**，再用
`python-docx` / `openpyxl` **读回来**，逐项核对标题、分节、表格行列数、关键数字：

    ① 导出的三种格式都来自**同一份冻结报告**（下载端点是现渲染，不重算）；
    ② Word 里是真的表格（不是把 markdown 拍成文本），标题层级落在 Heading 样式上；
    ③ Excel 是多工作表，数字是**真数字**（带数字格式），不是一串文本；
    ④ 三份文件里的指标数字与页面/记录里的**逐位相同**（同源铁律）。

被替换的只有 `app.ai.llm.chat` 这一个网络出口（单元测试不能依赖外网）：
报告正文压根不经过模型，所以"模型写错数字"影响不到导出物。
"""

from __future__ import annotations

import io
import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app.ai import export_docs, llm  # noqa: E402
from app.api import app  # noqa: E402

client = TestClient(app)

WEEKLY_QUESTION = "帮我根据本星期的销售数据做一份销售周报"
PLAIN_QUESTION = "2011年11月一共卖了多少？"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """四个目录指到临时目录：导出物与记录都落临时盘，绝不写进仓库里的真实 state/。"""
    monkeypatch.setenv("SRA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SRA_DOC_DIR", str(tmp_path / "documents"))
    monkeypatch.setenv("SRA_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SRA_OUTPUT_DIR", str(tmp_path / "outputs"))
    return tmp_path


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """关掉模型通道（**不**改任何数据）：结论段走代码降级文案，导出照样成立。"""
    monkeypatch.setattr(llm, "available", lambda: False)
    return None


@pytest.fixture
def weekly() -> dict:
    response = client.post("/api/chat", json={"question": WEEKLY_QUESTION, "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()


def _download(conversation_id: str, fmt: str):
    return client.get(f"/api/conversations/{conversation_id}/report/export", params={"format": fmt})


def _docx(conversation_id: str) -> Document:
    response = _download(conversation_id, "docx")
    assert response.status_code == 200, response.text
    return Document(io.BytesIO(response.content))


def _xlsx(conversation_id: str):
    response = _download(conversation_id, "xlsx")
    assert response.status_code == 200, response.text
    return load_workbook(io.BytesIO(response.content))


def _headings(document: Document, level: int) -> list[str]:
    style = f"Heading {level}"
    return [p.text.strip() for p in document.paragraphs if p.style.name == style and p.text.strip()]


def _sheet_rows(sheet) -> list[list]:
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


# ════════════════════════════════════════════════════════════════════════
# 1. 格式清单与文件名（默认 Word、语义化文件名、前端不拼文件名）
# ════════════════════════════════════════════════════════════════════════
def test_默认导出是Word_文件名语义化(weekly):
    export = weekly["answer"]["export"]
    assert export["default_format"] == "docx"
    report = weekly["report_document"]
    # 文件名用「至」而不是下划线，扩展名按格式补（主干由报告给出）
    assert export["filename"] == f"{report['filename']}.docx"
    assert "至" in export["filename"] and "_" not in export["filename"]
    assert re.fullmatch(r"销售周报-\d{4}-\d{2}-\d{2}至\d{4}-\d{2}-\d{2}\.docx", export["filename"]), \
        export["filename"]
    # 三种格式的清单齐全，标签是人话
    choices = {item["format"]: item for item in export["formats"]}
    assert list(choices) == ["docx", "xlsx", "md"]
    assert choices["docx"]["label"] == "Word 文档"
    assert choices["xlsx"]["label"] == "Excel 工作簿"
    assert choices["md"]["label"] == "Markdown 文本"
    for item in choices.values():
        assert item["filename"].startswith(report["filename"])
        assert item["filename"].endswith("." + item["format"] if item["format"] != "md" else ".md")
        assert item["mime"].count("/") == 1 and " " not in item["mime"]   # 内容类型格式合法
    # markdown 那段文本照旧在（页面预览用它），一个字都没重排
    assert export["markdown"].startswith("# 销售周报 · ")


def test_能力端点声明默认格式与可选格式():
    body = client.get("/api/chat/capabilities").json()
    assert body["report"]["export"] == "docx"
    labels = [item["label"] for item in body["report"]["export_formats"]]
    assert labels == ["Word 文档", "Excel 工作簿", "Markdown 文本"]


# ════════════════════════════════════════════════════════════════════════
# 2. Word：读回来验标题层级 / 真表格 / 结论建议 / 口径脚注
# ════════════════════════════════════════════════════════════════════════
def test_下载Word_读回来有标题层级与真表格(weekly):
    response = _download(weekly["conversation_id"], "docx")
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert response.headers["content-type"].startswith(mime)
    disposition = response.headers["content-disposition"]
    # 中文文件名按 RFC 5987 编码（现代浏览器认 filename*），另附 ASCII 兜底
    assert "filename*=UTF-8''" in disposition and "attachment" in disposition
    assert "sales-report-" in disposition

    document = _docx(weekly["conversation_id"])
    report = weekly["report_document"]
    title = document.paragraphs[0].text
    assert title.startswith(report["title"]) and report["period_label"] in title

    # 一级标题 = 报告的分节 + 「结论与建议」（分节一个都不能少）
    level1 = _headings(document, 1)
    for section in report["sections"]:
        assert section["title"] in level1, f"Word 里缺分节：{section['title']}"
    assert "结论与建议" in level1
    # 【为什么】/【建议行动】是二级标题（层级不能乱）
    level2 = _headings(document, 2)
    assert "【为什么】" in level2 and "【建议行动】" in level2
    for block in report["sections"][3]["blocks"]:              # 结构那一节的块标题
        assert block["title"] in level2

    # **真表格**：指标表 / 趋势表 / 国家表 / 商品表各一张，行列数与报告一致
    tables = document.tables
    assert len(tables) == 4, f"表格数不对：{len(tables)}"
    metrics = report["sections"][1]["table"]
    assert [cell.text for cell in tables[0].rows[0].cells] == metrics["headers"]
    assert len(tables[0].rows) == len(metrics["rows"]) + 1      # 表头另算一行
    for index, row in enumerate(metrics["rows"]):
        assert [cell.text for cell in tables[0].rows[index + 1].cells] == row

    # 结论建议的正文 = 记录里那两段（一个字都不重写）
    body_text = "\n".join(p.text for p in document.paragraphs)
    sections = {item["key"]: item for item in weekly["answer"]["sections"]}
    assert sections["why"]["text"].strip() in body_text
    assert sections["actions"]["text"].strip() in body_text
    # 文档自己写清楚数据范围与金额单位（不靠页面上的提示）
    assert str(weekly["data_profile"]["first_day"]) in body_text
    assert "数据范围" in body_text and "金额单位" in body_text


# ════════════════════════════════════════════════════════════════════════
# 3. Excel：读回来验多工作表 / 真数字 / 数字格式
# ════════════════════════════════════════════════════════════════════════
def test_下载Excel_读回来是多工作表且数字是真数字(weekly):
    response = _download(weekly["conversation_id"], "xlsx")
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    workbook = _xlsx(weekly["conversation_id"])
    assert workbook.sheetnames == ["摘要与建议", "核心指标", "趋势", "结构-国家", "结构-商品"]

    report = weekly["report_document"]
    metrics = report["sections"][1]["table"]
    sheet = workbook["核心指标"]
    rows = _sheet_rows(sheet)
    head_index = next(index for index, row in enumerate(rows)
                      if row and str(row[0]).strip() == metrics["headers"][0])
    assert [str(value) for value in rows[head_index]] == metrics["headers"]

    # 第一行数据：销售额那一行 —— 单元格必须是**数字**，且等于页面上那个展示值
    data_row = rows[head_index + 1]
    display = metrics["rows"][0][1]              # 例如 "1,509,496.33元"
    number = float(display.replace("元", "").replace(",", ""))
    assert data_row[0] == "销售额"
    assert isinstance(data_row[1], (int, float)) and abs(float(data_row[1]) - number) < 0.005
    cell = sheet.cell(row=head_index + 2, column=2)
    assert cell.number_format == "#,##0.00"      # 带数字格式，不是裸值
    # 变化率那一列按百分比写（格式里带 %），值 = 展示值 / 100
    rate_cell = sheet.cell(row=head_index + 2, column=5)
    assert "%" in rate_cell.number_format

    # 趋势表：表头下面就是报告里的那些桶，一个不多一个不少
    trend_table = report["sections"][2]["table"]
    trend_rows = _sheet_rows(workbook["趋势"])
    trend_head = next(index for index, row in enumerate(trend_rows)
                      if row and str(row[0]).strip() == trend_table["headers"][0])
    assert [str(value) for value in trend_rows[trend_head]] == trend_table["headers"]
    data = [row for row in trend_rows[trend_head + 1:] if row[0] not in (None, "")]
    assert len(data) == len(trend_table["rows"])
    assert [str(row[0]) for row in data] == [row[0] for row in trend_table["rows"]]   # 桶边界照旧
    # 每行金额都写成了真数字（能直接求和），不是 "57,165.19元" 这种文本
    assert all(isinstance(row[1], (int, float)) for row in data)

    # 摘要与建议：结论两段与口径脚注都在
    summary_text = "\n".join(str(value) for row in _sheet_rows(workbook["摘要与建议"])
                             for value in row if value is not None)
    sections = {item["key"]: item for item in weekly["answer"]["sections"]}
    assert sections["why"]["text"].strip() in summary_text
    assert "数据范围" in summary_text


# ════════════════════════════════════════════════════════════════════════
# 4. Markdown：与页面预览的那段文本一字不差
# ════════════════════════════════════════════════════════════════════════
def test_下载Markdown_与页面预览同一段文本(weekly):
    response = _download(weekly["conversation_id"], "md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.content.decode("utf-8") == weekly["answer"]["export"]["markdown"]


# ════════════════════════════════════════════════════════════════════════
# 5. 同源：三份文件里的关键数字与记录里的**逐位相同**
# ════════════════════════════════════════════════════════════════════════
def test_三种格式的数字同源(weekly):
    report = weekly["report_document"]
    metrics = report["sections"][1]["table"]
    expected = [[str(cell) for cell in row] for row in metrics["rows"]]

    # Word：表格里的展示值
    document = _docx(weekly["conversation_id"])
    word_rows = [[cell.text for cell in row.cells] for row in document.tables[0].rows[1:]]
    assert word_rows == expected

    # Excel：数值换算回去（去掉千分位/单位）与展示值一致
    rows = _sheet_rows(_xlsx(weekly["conversation_id"])["核心指标"])
    head_index = next(index for index, row in enumerate(rows)
                      if row and str(row[0]).strip() == metrics["headers"][0])
    for offset, row in enumerate(expected):
        excel_row = rows[head_index + 1 + offset]
        for column, display in enumerate(row):
            value = excel_row[column]
            if column == 0:
                assert value == display
                continue
            if "%" in display:                       # 变化率：写成小数 + 百分比格式
                assert abs(float(value) - float(display.rstrip("%")) / 100) < 1e-6
                continue
            assert abs(float(value) - float(display.replace("元", "").replace(",", ""))) < 0.005

    # Markdown：表格里也是同一串数字
    markdown = weekly["answer"]["export"]["markdown"]
    for row in expected:
        assert "| " + " | ".join(row) + " |" in markdown


# ════════════════════════════════════════════════════════════════════════
# 6. 诚实边界：没有报告的问答不产出文件；格式不认识就明说
# ════════════════════════════════════════════════════════════════════════
def test_没有报告的问答不产出文件():
    response = client.post("/api/chat", json={"question": PLAIN_QUESTION, "use_llm": False})
    record = response.json()
    assert record["report_document"] is None
    export = _download(record["conversation_id"], "docx")
    assert export.status_code == 400
    payload = export.json()
    assert payload["error"]["code"] == "report_not_available"
    assert "报告" in payload["error"]["message"]


def test_未知会话与未知格式都明说(weekly):
    missing = _download("c_不存在", "docx")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "conversation_not_found"

    unknown = _download(weekly["conversation_id"], "pdf")
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "report_format_unknown"


# ════════════════════════════════════════════════════════════════════════
# 7. 导出这一层**不许再算一遍数**（同源铁律的静态证明）
# ════════════════════════════════════════════════════════════════════════
def test_导出模块不碰计算与模型():
    source = (PROJECT_ROOT / "app" / "ai" / "export_docs.py").read_text(encoding="utf-8")
    for forbidden in ("pandas", "import app", "from app", "executor", "llm", "requests", "httpx"):
        assert forbidden not in source, f"导出模块里出现了它不该碰的东西：{forbidden}"
    # 三种格式都由同一个入口渲染（内容、文件名、内容类型一起给出）
    assert "def render_report(" in source
    for renderer in ("def render_docx(", "def render_xlsx("):
        assert renderer in source
    # 真表格与真数字：docx 用 Table Grid，xlsx 写数字格式
    assert "Table Grid" in source and "number_format" in source
    # 不做 PDF：本机没有 PDF 库，装一个就是新增依赖；自己拼字节流是造轮子。
    # 查的是**有没有引入 PDF 库**（注释里说明"为什么不做 PDF"是允许的）
    assert not re.search(r"reportlab|fpdf|weasyprint|pikepdf|pdfkit|xhtml2pdf", source, re.I)
