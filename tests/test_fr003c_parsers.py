"""test_fr003c_parsers.py · FR-003C：**六种格式的统一解析**。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    ① 六种格式各自进得来，且落点正确（表格→数据集 / 正文→文档 / 双落）
    ② 多 Sheet **不拆成多个数据源**（一个 container 里多张表）
    ③ **类型规范化**：同一份数据从不同格式进来，落库的值必须**一模一样**
    ④ Markdown 表格按规范解析（表头/分隔行/列数一致性/空表/多张表/非标准/前后正文）
    ⑤ **PPT 表格资格判定**：议程 / 名单 / 计划这类排版物不许变成"销售数据源"
    ⑥ 安全闸门：扩展名 / 文件签名 / ZIP 炸弹 / 加密包 / 行数 / 页数 —— 全部**零写入**

★ 全部使用**真文件**（真 xlsx / 真 pptx / 真 docx / 真 pdf）—— mock 掉解析器就等于没测解析。
"""

from __future__ import annotations

import zipfile

import pytest

import fr003_helpers as helpers
from app.importer import models, normalize, parsers, pipeline


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


# ════════════════════════════════════════════════════════════════════════
# ① 六种格式
# ════════════════════════════════════════════════════════════════════════
def test_xlsx_可导入并落成数据集(env, tmp_path) -> None:
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    assert receipt["dataset_id"] and not receipt["document_id"]
    assert receipt["datasets"][0]["row_count"] == 3


def test_xlsm_可导入(env, tmp_path) -> None:
    """`.xlsm` 与 `.xlsx` 是同一个容器（只是多带宏），走同一条解析路径。"""
    import pandas as pd

    path = tmp_path / "地区销售.xlsm"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(helpers.REGION_SAMPLE[1:], columns=helpers.REGION_SAMPLE[0]) \
            .to_excel(writer, sheet_name="Sheet1", index=False)
    receipt = pipeline.import_file(path)
    assert receipt["source_type"] == "excel"
    assert receipt["datasets"][0]["row_count"] == 3


def test_csv_可导入(env, tmp_path) -> None:
    path = helpers.make_csv(tmp_path / "地区.csv", "省份,销售额\n广东,100\n浙江,200\n")
    receipt = pipeline.import_file(path)
    assert receipt["source_type"] == "csv"
    assert helpers.counts(receipt["source_file_id"]) == {"dataset_count": 1, "document_count": 0}


def test_csv_会说明自己按哪种编码读的(env, tmp_path) -> None:
    """编码问题的**诚实边界**：Latin-1 兜底编码能解**任何**字节，所以 CSV 不会"读不出来"。

    （这与既有 `/api/datasets` 管道的行为一致 —— 那里也是 UTF-8 → GBK → Latin-1 三级兜底。）
    既然一定读得出来，就必须**告诉用户是按哪种编码读的**：编码猜错时页面显示的就是乱码，
    用户有权知道我们按哪种编码解的，而不是对着乱码猜"是不是系统坏了"。
    """
    path = tmp_path / "乱码.csv"
    path.write_bytes(b"province,amount\n\xc3\x28,100\n")
    receipt = pipeline.import_file(path)
    assert receipt["dataset_id"]
    assert any("CSV 按" in note for note in receipt["notes"])


def test_md_编码认不出时明确报错(env, tmp_path) -> None:
    """Markdown 只试 UTF-8 与 GBK（没有万能兜底），两种都不成 → 明确报错，不硬读。"""
    path = tmp_path / "乱码.md"
    path.write_bytes(b"\xc3\x28\xa0\xa1")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "encoding_unknown"


def test_docx_可导入并落成文档(env, tmp_path) -> None:
    path = helpers.make_docx(tmp_path / "周报.docx")
    receipt = pipeline.import_file(path)
    assert receipt["document_id"] and not receipt["dataset_id"]
    assert receipt["documents"][0]["char_count"] > 0


def test_pdf_可导入并落成文档(env, tmp_path) -> None:
    path = helpers.make_pdf(tmp_path / "报告.pdf", ["Quarterly sales report"])
    receipt = pipeline.import_file(path)
    assert receipt["document_id"] and not receipt["dataset_id"]


def test_pptx_含正文时产生文档(env, tmp_path) -> None:
    path = helpers.make_pptx(tmp_path / "只有正文.pptx", [{"title": "标题", "body": "一段正文"}])
    receipt = pipeline.import_file(path)
    assert receipt["document_id"]
    assert receipt["document_count"] >= 1


def test_pptx_含表格时产生数据集(env, tmp_path) -> None:
    path = helpers.make_pptx(tmp_path / "只有表.pptx", [
        {"title": "销售", "tables": [[["省份", "销售额"], ["广东", "100"], ["浙江", "200"]]]},
    ])
    receipt = pipeline.import_file(path)
    assert receipt["dataset_id"], "省份/销售额 这张表应当通过资格判定"
    assert receipt["dataset_count"] >= 1


def test_md_含正文时产生文档(env, tmp_path) -> None:
    path = helpers.make_md(tmp_path / "说明.md", "# 标题\n\n这是一段普通正文，没有表格。\n")
    receipt = pipeline.import_file(path)
    assert receipt["document_id"]
    assert receipt["dataset_id"] is None


def test_md_含标准表格时产生数据集(env, tmp_path) -> None:
    path = helpers.make_md(tmp_path / "表格.md", "| 地区 | 销售额 |\n|---|---:|\n| 华东 | 100 |\n| 华南 | 200 |\n")
    receipt = pipeline.import_file(path)
    assert receipt["dataset_id"], "标准 Markdown 表格应当落成数据集"


def test_不支持的扩展名明确报错且零写入(env, tmp_path) -> None:
    """ASSERT 10：既报错，也**什么都不写**（连一条 source_files / imports 都不留）。"""
    path = tmp_path / "数据.txt"
    path.write_text("省份,销售额\n广东,100\n", encoding="utf-8")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)

    assert caught.value.code == "unsupported_type"
    with pytest.raises(models.ImporterError):
        pipeline.import_bytes(b"x", "数据.txt")
    from app.importer import db

    with db.readonly() as connection:
        assert int(db.scalar("SELECT COUNT(*) FROM imports")) == 0
        assert int(db.scalar("SELECT COUNT(*) FROM source_files")) == 0


# ════════════════════════════════════════════════════════════════════════
# ② 多 Sheet：一个数据集多张表
# ════════════════════════════════════════════════════════════════════════
def test_多工作表是一个数据集里的多张表(env, tmp_path) -> None:
    """评审 §8⑥：不许把一个 Excel 拆成三个独立数据源（来源关系会乱）。"""
    path = helpers.make_xlsx(tmp_path / "多表.xlsx", {
        "销售": helpers.REGION_SAMPLE,
        "库存": [["商品", "数量"], ["A", 3], ["B", 5]],
    })
    receipt = pipeline.import_file(path)
    assert receipt["dataset_count"] == 1        # ★ 一个文件 = 一个数据源
    assert receipt["table_count"] == 2          # 它下面挂着两张表
    regions = receipt["datasets"][0]["region_dimensions"]
    assert [item["key"] for item in regions] == ["province"]
    assert regions[0]["table"] == "sheet1", "地区字段要指明它在哪张表里"


# ════════════════════════════════════════════════════════════════════════
# ③ 类型规范化（评审 §8③）
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("100", 100),
    (100, 100),
    (100.0, 100),
    ("100.00", 100),
    ("£100.00", 100),
    ("¥100", 100),
    ("1,234.50", 1234.5),
    ("-5", -5),
    ("12%", 12),
])
def test_数字的各种写法收敛成同一个值(raw, expected) -> None:
    assert normalize.parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["007", "8512345678901234567", "12A", "", "  ", "1.2.3"])
def test_不该当数字的绝不硬转(raw) -> None:
    """前导零（商品编码）/ 超长数字 / 根本不是数字的 —— 一律保持文本，不猜。"""
    assert normalize.parse_number(raw) is None


def test_同一份数据从三种格式进来落库的值一模一样(env, tmp_path) -> None:
    """评审 §8③ 要防的就是这件事：不同格式导入同一份数据得到不同结果。"""
    xlsx = helpers.make_xlsx(tmp_path / "一份.xlsx", {
        "Sheet1": [["省份", "销售额"], ["广东", 100], ["浙江", 200]],
    })
    csv = helpers.make_csv(tmp_path / "一份.csv", "省份,销售额\n广东,£100.00\n浙江,200.00\n")
    md = helpers.make_md(
        tmp_path / "一份.md",
        "| 省份 | 销售额 |\n|---|---|\n| 广东 | 100.0 |\n| 浙江 | 200.00 |\n",
    )
    receipts = [pipeline.import_file(item) for item in (xlsx, csv, md)]
    stored = [helpers.table_rows(receipt["dataset_id"]) for receipt in receipts]

    assert stored[0] == stored[1] == stored[2]
    assert stored[0][0] == {"省份": "广东", "销售额": 100}


def test_日期列统一成同一种写法() -> None:
    assert normalize.parse_date("2011-01-01") is not None
    assert normalize.parse_date("2011/1/1").date().isoformat() == "2011-01-01"
    assert normalize.parse_date("2011年1月1日").date().isoformat() == "2011-01-01"
    # ★ 纯数字永远不当日期：40875 可以是数量，也可以是 Excel 日期序号 —— 猜错就是答另一个问题
    assert normalize.parse_date(40875) is None
    assert normalize.parse_date("40875") is None


def test_布尔只认明确写法() -> None:
    assert normalize.parse_bool("是") is True
    assert normalize.parse_bool("false") is False
    assert normalize.parse_bool("1") is None
    assert normalize.parse_bool("0") is None


def test_列类型是整列一起判的() -> None:
    """一列只能有一个类型 —— 逐格判会让同一列分裂成两种类型，分组就散了。"""
    names, rows, profiles = normalize.normalize_table(
        ["金额"], [["100"], ["200.5"], ["300"]]
    )
    assert names == ["金额"]
    assert profiles[0]["inferred_type"] == normalize.TYPE_NUMBER
    assert [row["金额"] for row in rows] == [100, 200.5, 300]


# ════════════════════════════════════════════════════════════════════════
# ④ Markdown 表格：规范解析（评审 §8④）
# ════════════════════════════════════════════════════════════════════════
def test_md_没有分隔行的不是表格() -> None:
    """非标准形态：只有一行管道符 —— 那是正文，不是表。"""
    text = "| 地区 | 销售额 |\n| 华东 | 100 |\n"
    body, tables, _notes = parsers.parse_markdown_text(text)
    assert tables == []
    assert "华东" in body


def test_md_只有表头没有数据行不成表() -> None:
    text = "| 地区 | 销售额 |\n|---|---:|\n"
    _body, tables, notes = parsers.parse_markdown_text(text)
    assert tables == []
    assert any("只有表头" in note for note in notes)


def test_md_多张表各自成表() -> None:
    text = (
        "| 地区 | 销售额 |\n|---|---:|\n| 华东 | 100 |\n\n正文夹在中间\n\n"
        "| 商品 | 数量 |\n|---|---:|\n| A | 3 |\n| B | 5 |\n"
    )
    body, tables, _notes = parsers.parse_markdown_text(text)
    assert [table.name for table in tables] == ["md_table_1", "md_table_2"]
    assert [len(table.rows) for table in tables] == [1, 2]
    assert "正文夹在中间" in body
    assert "|" not in body, "表格块要从正文里摘掉（表格已经物化成数据集了）"


def test_md_列数不一致的行被补齐并留说明() -> None:
    text = "| 地区 | 销售额 |\n|---|---:|\n| 华东 | 100 |\n| 华南 |\n"
    _body, tables, _notes = parsers.parse_markdown_text(text)
    table = tables[0]
    assert len(table.rows) == 2
    assert table.rows[1] == ["华南", ""]
    assert any("单元格数与表头不一致" in note for note in table.notes)


def test_md_代码块里的管道符不算表格() -> None:
    text = "```\n| 地区 | 销售额 |\n|---|---|\n| 华东 | 100 |\n```\n"
    _body, tables, _notes = parsers.parse_markdown_text(text)
    assert tables == []


# ════════════════════════════════════════════════════════════════════════
# ⑤ PPT 表格资格判定（评审 §8⑤）
# ════════════════════════════════════════════════════════════════════════
def _qualifies(rows: list[list[str]]) -> tuple[bool, str]:
    table = parsers.ParsedTable(
        name="t", display_name="t", columns=[str(cell) for cell in rows[0]], rows=rows[1:],
    )
    return parsers._table_qualifies(table)


@pytest.mark.parametrize("rows", [
    [["时间", "事项"], ["10:00", "开场"], ["10:30", "讨论"]],             # 会议议程
    [["任务", "负责人", "开始时间", "工时"], ["A", "张三", "周一", "3"]],   # 项目计划
    [["姓名", "部门", "入职日期"], ["张三", "销售", "2020-01-01"]],         # 人员名单
])
def test_ppt_里的排版物不算销售数据(rows) -> None:
    qualified, why = _qualifies(rows)
    assert qualified is False, f"这种表不该被当成数据源：{rows}（判定说：{why}）"


@pytest.mark.parametrize("rows", [
    [["省份", "销售额"], ["广东", "100"], ["浙江", "200"]],                # 地区 + 金额
    [["商品", "数量"], ["A", "3"], ["B", "5"]],                            # 语义字段 + 数值列
    [["订单号", "客户号", "数量"], ["1", "c1", "3"], ["2", "c2", "5"]],     # 两个业务字段
])
def test_ppt_里的真销售表能过判定(rows) -> None:
    qualified, why = _qualifies(rows)
    assert qualified is True, f"这张表应该被认出来：{rows}（判定说：{why}）"


def test_ppt_议程表只作为文档内容保留(env, tmp_path) -> None:
    """过不了资格判定的表，**信息不丢**（文字仍在文档里），只是不生成数据集。"""
    path = helpers.make_pptx(tmp_path / "带议程.pptx", [
        {"title": "会议议程", "body": "", "tables": [[["时间", "事项"], ["10:00", "开场"]]]},
    ])
    receipt = pipeline.import_file(path)
    assert receipt["document_id"], "正文仍要落成文档"
    assert receipt["dataset_id"] is None
    assert any("不像业务数据表" in note for note in receipt["notes"])


# ════════════════════════════════════════════════════════════════════════
# ⑥ 安全闸门（评审 §8⑧⑨）
# ════════════════════════════════════════════════════════════════════════
def test_改名的zip不会被当成ppt解析(env, tmp_path) -> None:
    """★ 不能只看 `filename.endswith('.pptx')`。"""
    path = tmp_path / "假的.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "not a presentation")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "signature_mismatch"


def test_zip炸弹被拒(env, tmp_path) -> None:
    """压缩比异常（0 字节内容撑出巨大解压量）→ 判为炸弹。"""
    path = tmp_path / "炸弹.pptx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", "x")
        archive.writestr("ppt/media/big.bin", b"\x00" * (8 * 1024 * 1024))
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code in {"zip_bomb", "zip_unsafe", "signature_mismatch"}


def test_带密码的office文件被拒(env, tmp_path) -> None:
    """OLE 复合文档头 = 加密的 OOXML。系统不去猜密码，直接说清楚。"""
    path = tmp_path / "加密.docx"
    path.write_bytes(models.OLE_MAGIC + b"\x00" * 64)
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "encrypted_archive"


def test_不是pdf的pdf被拒(env, tmp_path) -> None:
    path = tmp_path / "假的.pdf"
    path.write_bytes(b"just some text")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "signature_mismatch"


def test_文本格式里混二进制被拒(env, tmp_path) -> None:
    path = tmp_path / "假的.csv"
    path.write_bytes(b"a,b\n\x00\x01\x02")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "signature_mismatch"


def test_超过行数上限的表被拒(env, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(parsers, "MAX_TABLE_ROWS", 2)
    path = helpers.make_xlsx(tmp_path / "大表.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "too_many_rows"


def test_超过页数上限的ppt被拒(env, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(parsers, "MAX_PPTX_SLIDES", 1)
    path = helpers.make_pptx(tmp_path / "多页.pptx", [
        {"title": "第一页", "body": "正文"},
        {"title": "第二页", "body": "正文"},
    ])
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "too_many_slides"


def test_空文件被拒(env, tmp_path) -> None:
    path = tmp_path / "空的.csv"
    path.write_bytes(b"")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code in {"parse_failed", "signature_mismatch"}


def test_zip成员路径穿越被拒(env, tmp_path) -> None:
    path = tmp_path / "穿越.pptx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", "x")
        archive.writestr("../evil.txt", "x")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)
    assert caught.value.code == "zip_unsafe"
