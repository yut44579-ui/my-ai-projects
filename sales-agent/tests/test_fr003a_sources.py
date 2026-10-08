"""test_fr003a_sources.py · FR-003A：**统一 Import / Source 模型**。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
三层来源链路（评审 §10 点名）：

    source_file ── 一份**文件**（一个 sha256 就是一份文件）
        ├── import ── 一次**导入动作**（哪个文件、哪版解析器、落了什么、成没成）
        │      ├── dataset_id
        │      └── document_id
        └── …

以及那句 UI 话的依据：**"销售分析.pptx → 已导入 1 个文档资料 + N 个数据集"**
（评审 #3：不许笼统写"导入成功"）。

★ 这一组不碰数字，只钉"来源可追溯"。数字那部分在 test_fr003d。
"""

from __future__ import annotations

import pytest

import fr003_helpers as helpers
from app.importer import db, pipeline, store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """每个用例一套干净的隔离目录 + 建好表。"""
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


# ════════════════════════════════════════════════════════════════════════
# 表结构
# ════════════════════════════════════════════════════════════════════════
def test_七张表都在(env) -> None:
    """评审给的库表清单：datasets / dataset_columns / dataset_rows / documents / imports。"""
    names = set(db.table_names())
    assert {
        "source_files", "imports", "datasets", "dataset_tables",
        "dataset_columns", "dataset_rows", "documents",
    } <= names


def test_导入记录带齐评审点名的字段(env, tmp_path) -> None:
    """`imports` 那一行必须有：import_id / source_filename / source_sha256 / source_type /
    imported_at / dataset_id / document_id / status / error_code / parser_version。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    digest = helpers.sha256_of(path)          # ★ 导入成功后输入文件会被消费掉，哈希要**先**算
    receipt = pipeline.import_file(path)

    with db.readonly() as connection:
        record = store.get_import(connection, receipt["import_id"])
    assert record is not None
    for field in ("import_id", "source_filename", "source_sha256", "source_type",
                  "imported_at", "dataset_id", "document_id", "status", "error_code",
                  "parser_version"):
        assert field in record, f"imports 表少了评审点名的字段：{field}"
    assert record["source_sha256"] == digest
    assert record["status"] == "success"
    assert record["error_code"] is None
    assert record["parser_version"] == pipeline.PARSER_VERSION
    # 数据集这一侧落了，文档那一侧本来就没有（Excel 只产数据集）
    assert record["dataset_id"]
    assert record["document_id"] is None


# ════════════════════════════════════════════════════════════════════════
# 双落：document 与 dataset 共享同一个 source_file_id
# ════════════════════════════════════════════════════════════════════════
def test_双落的文档与数据集共享同一个来源文件编号(env, tmp_path) -> None:
    """★ ASSERT 12 在模型层的证据：两条落点的 `source_file_id` 必须**逐字相同**。"""
    path = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE)
    receipt = pipeline.import_file(path)

    assert receipt["documents"], "这份 PPT 有正文，应该落成一条文档资料"
    assert receipt["datasets"], "这份 PPT 有销售表，应该落成一个数据集"

    with db.readonly() as connection:
        import_record = store.get_import(connection, receipt["import_id"])
        dataset = store.get_dataset(connection, import_record["dataset_id"])
        document = store.get_document(connection, import_record["document_id"])

    assert dataset["source_file_id"] == import_record["source_file_id"] == \
        document["source_file_id"]
    assert document["document_id"] == import_record["document_id"]


def test_同一个文件只占一条来源文件记录(env, tmp_path) -> None:
    """一个 sha256 就是**一份文件**：重复导入不该长出第二条 source_files。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    first = pipeline.import_file(path, consume_source=False)
    original = tmp_path / "副本.xlsx"
    original.write_bytes(path.read_bytes())
    second = pipeline.import_file(original)

    assert second["idempotent"] is True
    assert second["source_file_id"] == first["source_file_id"]
    with db.readonly() as connection:
        total = db.scalar("SELECT COUNT(*) FROM source_files")
    assert total == 1


def test_一句话说清这份文件导成了什么(env, tmp_path) -> None:
    """UI 那句话的来源：**不是**笼统的"导入成功"，而是文档几个、数据集几个、几张表。"""
    path = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE)
    receipt = pipeline.import_file(path)

    assert receipt["summary_text"].startswith("销售分析.pptx → 已导入")
    assert "1 个文档资料" in receipt["summary_text"]
    assert "1 个数据集" in receipt["summary_text"]
    assert receipt["document_count"] == 1
    assert receipt["dataset_count"] == 1
    assert receipt["table_count"] == 1        # 议程表没过资格判定，只有 1 张表真的入库


def test_来源链路可反查(env, tmp_path) -> None:
    """从一份文件出发，能答出"它被导入过几次、落成了什么"（评审 §10 的审计诉求）。"""
    path = helpers.make_md(tmp_path / "季度分析.md", helpers.MD_SAMPLE)
    receipt = pipeline.import_file(path)

    with db.readonly() as connection:
        imports = store.imports_of_source_file(connection, receipt["source_file_id"])
        counts = store.successful_import_count(connection, receipt["source_file_id"])
    assert len(imports) == 1
    assert counts == {"dataset_count": 1, "document_count": 1}


def test_数据集记录带齐物化字段(env, tmp_path) -> None:
    """数据源记录里必须能回答"哪份文件、哪版解析器、什么时候物化的、地区字段是什么"。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    with db.readonly() as connection:
        dataset = store.get_dataset(connection, receipt["dataset_id"])

    assert dataset["source_file_id"] == receipt["source_file_id"]
    assert dataset["parser_version"] == pipeline.PARSER_VERSION
    assert dataset["materialized_at"]
    assert dataset["status"] == pipeline.STATUS_MATERIALIZED
    assert dataset["region_dimensions"], "省份这一列应该在地区维度里"
    assert dataset["meta"]["original_sha256"] == receipt["source_sha256"]


def test_每张表与每一列都记进了数据字典(env, tmp_path) -> None:
    """多 Sheet 也走同一条：一个数据集 -> 多张表 -> 各自的列画像。"""
    path = helpers.make_xlsx(tmp_path / "多表.xlsx", {
        "销售": helpers.REGION_SAMPLE,
        "库存": [["商品", "数量"], ["A", 3], ["B", 5]],
    })
    receipt = pipeline.import_file(path)
    with db.readonly() as connection:
        tables = store.list_dataset_tables(connection, receipt["dataset_id"])
        columns = store.list_dataset_columns(connection, receipt["dataset_id"])

    assert {table["table_name"] for table in tables} == {"sheet1", "sheet2"}
    # 两张表各自的行数（销售 3 行、库存 2 行）—— 一个数据集下面装多张表，不拆成多个数据源
    assert {table["row_count"] for table in tables} == {2, 3}
    assert next(t for t in tables if t["table_name"] == "sheet1")["row_count"] == 3
    assert next(t for t in tables if t["table_name"] == "sheet2")["row_count"] == 2
    assert any(column["name"] == "省份" for column in columns)
    assert any(column["name"] == "数量" for column in columns)
    # 列画像里要有"非空/唯一值"这两个判定地区字段要用的数
    province = next(column for column in columns if column["name"] == "省份")
    assert province["non_null"] == 3 and province["null_count"] == 0
    assert province["distinct_count"] == 3
    assert province["region_key"] == "province"
