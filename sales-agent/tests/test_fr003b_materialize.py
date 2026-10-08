"""test_fr003b_materialize.py · FR-003B：**SQLite 物化 + 幂等 + 事务**。

════════════════════════════════════════════════════════════════════════
【这一组钉的是什么】
════════════════════════════════════════════════════════════════════════
    物化     行真的进了库；原文件被消费掉之后，数据仍然查得到
    持久     换一个**真进程**再查一遍，结果与导入时一致（ASSERT 16/17）
    幂等     同 sha + 同解析器版本 → 不重复入库，返回既有 import_id（ASSERT 13）
             同 sha + 解析器版本变了 → 允许重解析，revision + 1
    事务     写库任何一步失败 → 整体回滚，**不留半成品**（ASSERT 14/18）
    限额     库容量超限 → 明确拒绝（不是悄悄写爆磁盘）
    隔离     测试**从不**碰真实 state/ 与真实 data/app.db（ASSERT 19）

★ 这一组一律**直接查库**核对（不用接口的返回自证）—— 接口说成功不算，库里真有才算。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

import fr003_helpers as helpers
from app.importer import db, models, pipeline, region_query, store

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def _row_counts() -> dict[str, int]:
    with db.readonly() as connection:
        return {
            "source_files": int(db.scalar("SELECT COUNT(*) FROM source_files")),
            "imports": int(db.scalar("SELECT COUNT(*) FROM imports")),
            "datasets": int(db.scalar("SELECT COUNT(*) FROM datasets")),
            "dataset_rows": int(db.scalar("SELECT COUNT(*) FROM dataset_rows")),
            "documents": int(db.scalar("SELECT COUNT(*) FROM documents")),
        }


# ════════════════════════════════════════════════════════════════════════
# 物化
# ════════════════════════════════════════════════════════════════════════
def test_物化的行原样躺在库里(env, tmp_path) -> None:
    """ASSERT 15 的前半：数据**在库里**，不是"引用了某个文件"。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)

    rows = helpers.table_rows(receipt["dataset_id"])
    assert rows == [
        {"省份": "广东", "销售额": 100},
        {"省份": "浙江", "销售额": 200},
        {"省份": "江苏", "销售额": 300},
    ]


def test_原文件被消费掉之后数据仍然查得到(env, tmp_path) -> None:
    """ASSERT 15：导入成功后输入文件被移走/删除，dataset 依然可查询。

    连**保留副本**也一起删掉 —— 证明查询真的走库，不是偷偷回读那个文件。
    """
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    assert not path.exists(), "导入成功后输入文件应当已被消费掉"

    preserved = pathlib.Path(receipt["original_path"])
    assert preserved.exists(), "原文件仍要保留一份（评审 #6：原文件 = 证据）"
    preserved.unlink()

    result = region_query.sales_by_region(receipt["dataset_id"])
    assert {row["region"]: row["amount"] for row in result["rows"]} == {
        "广东": 100.0, "浙江": 200.0, "江苏": 300.0,
    }


def test_重启之后数据集还在且结果一致(env, tmp_path) -> None:
    """ASSERT 16/17：换一个**真进程**（不是新开一个连接）再查一遍。

    为什么要用真进程：同一个进程里"还在"可能只是缓存没清；新进程只能从磁盘读，
    读得到才叫持久化。子进程里跑的是**同一份代码 + 同一套环境变量**（隔离目录照旧）。
    """
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    receipt = pipeline.import_file(path)
    before = region_query.sales_by_region(receipt["dataset_id"])

    code = (
        "import json,sys;"
        "from app.importer import region_query;"
        f"print(json.dumps(region_query.sales_by_region({receipt['dataset_id']!r})['rows'],"
        " ensure_ascii=False))"
    )
    finished = subprocess.run(
        [sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True, text=True, timeout=120,
    )
    assert finished.returncode == 0, finished.stderr
    after = json.loads(finished.stdout.strip())
    assert after == before["rows"], "重启后查询结果必须与导入前一致"


# ════════════════════════════════════════════════════════════════════════
# 幂等
# ════════════════════════════════════════════════════════════════════════
def test_重复导入不产生重复业务数据(env, tmp_path) -> None:
    """ASSERT 13：同一份文件再导一次 → 返回**既有** import_id，行数不变。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    first = pipeline.import_file(path, consume_source=False)
    snapshot = _row_counts()

    second = pipeline.import_file(path)

    assert second["idempotent"] is True
    assert second["import_id"] == first["import_id"]
    assert second["dataset_id"] == first["dataset_id"]
    assert _row_counts() == snapshot, "幂等命中不该再写任何一行"


def test_解析器版本变了就允许重解析并产生新的修订(env, tmp_path, monkeypatch) -> None:
    """同文件 + 新 parser_version → 新的 import revision（评审 §8① 的另一半）。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    first = pipeline.import_file(path, consume_source=False)

    monkeypatch.setattr(pipeline, "PARSER_VERSION", "99-test")
    second = pipeline.import_file(path)

    assert second["idempotent"] is False
    assert second["import_id"] != first["import_id"]
    assert second["revision"] == first["revision"] + 1
    # 同一个 source_file（一份文件），但记录里有两次导入
    assert second["source_file_id"] == first["source_file_id"]
    with db.readonly() as connection:
        assert len(store.imports_of_source_file(connection, first["source_file_id"])) == 2


def test_上次失败的文件允许重试(env, tmp_path) -> None:
    """失败不是"这份文件导过了"—— 修好之后再传，必须能真的导进去。"""
    broken = tmp_path / "空的.csv"
    broken.write_text("省份,销售额\n", encoding="utf-8")
    with pytest.raises(models.ImporterError):
        pipeline.import_file(broken)

    fixed = tmp_path / "好的.csv"
    fixed.write_text("省份,销售额\n广东,100\n", encoding="utf-8")
    receipt = pipeline.import_file(fixed)
    assert receipt["status"] == "success"
    assert receipt["idempotent"] is False


# ════════════════════════════════════════════════════════════════════════
# 事务：不留半成品
# ════════════════════════════════════════════════════════════════════════
def test_写库失败则整体回滚_不产生假成功(env, tmp_path, monkeypatch) -> None:
    """ASSERT 18：**提交元数据那一步失败** → 已经写进去的 dataset / document 全部回滚。

    为什么要在最后一步下手：那一步失败时，dataset 与 document 其实都已经插进去了，
    最能证明"整条链路是一个事务"（要是没事务，这里就会留下一份查不到出处的数据）。
    """
    path = helpers.make_pptx(tmp_path / "销售分析.pptx", helpers.PPTX_SAMPLE)

    def boom(*args, **kwargs):
        raise RuntimeError("模拟写库失败")

    monkeypatch.setattr(store, "insert_import", boom)
    with pytest.raises(RuntimeError):
        pipeline.import_file(path)

    counts = _row_counts()
    assert counts["datasets"] == 0, "回滚后不该留下半个数据集"
    assert counts["documents"] == 0, "回滚后不该留下半份文档"
    assert counts["dataset_rows"] == 0
    assert counts["imports"] == 0, "也不该留下一条'成功'的导入记录"
    # 回滚之后那份原文件副本也不该留着（否则盘上多一份谁都查不到的孤儿证据）
    originals = list(pathlib.Path(os.environ["SRA_ORIGINAL_DIR"]).glob("*"))
    assert originals == []


def test_解析失败不留半成品但留一条案底(env, tmp_path) -> None:
    """ASSERT 14：解析失败 → 零 dataset / 零 document；同时留一条 status=failed 的记录。"""
    broken = tmp_path / "空的.csv"
    broken.write_text("省份,销售额\n", encoding="utf-8")       # 只有表头
    with pytest.raises(models.ImporterError):
        pipeline.import_file(broken)

    counts = _row_counts()
    assert counts["datasets"] == 0 and counts["documents"] == 0
    assert counts["imports"] == 1
    record = helpers.all_imports()[0]
    assert record["status"] == "failed"
    assert record["error_code"] == "no_content"
    assert record["dataset_id"] is None and record["document_id"] is None


def test_验身不过的文件零写入(env, tmp_path) -> None:
    """不是"这份文件坏了"，而是"它根本不是它自称的格式" —— 一行都不写。"""
    fake = tmp_path / "假的.pptx"
    fake.write_bytes(b"this is plain text pretending to be a pptx")
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(fake)

    assert caught.value.code == "signature_mismatch"
    assert _row_counts() == {
        "source_files": 0, "imports": 0, "datasets": 0,
        "dataset_rows": 0, "documents": 0,
    }


def test_库容量超限时明确拒绝(env, tmp_path, monkeypatch) -> None:
    """评审 §8⑧：库总容量也要有闸门 —— 超了就说清楚，不悄悄写爆磁盘。"""
    path = helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE})
    monkeypatch.setattr(db, "MAX_DB_BYTES", 1)
    with pytest.raises(models.ImporterError) as caught:
        pipeline.import_file(path)

    assert caught.value.code == "db_capacity_exceeded"
    assert _row_counts()["datasets"] == 0


# ════════════════════════════════════════════════════════════════════════
# 隔离：测试不许碰真实目录
# ════════════════════════════════════════════════════════════════════════
def test_测试不写真实库与真实状态目录(env, tmp_path) -> None:
    """ASSERT 19：跑完一轮导入，真实 `data/app.db` 与真实 `state/` 一个字节都没变。

    这条是**反向证据**：不看"我隔离了没有"，看"真实的那两个东西动没动"。
    """
    watched = [
        PROJECT_ROOT / "data" / "app.db",
        PROJECT_ROOT / "state" / "conversations.json",
        PROJECT_ROOT / "state" / "datasets.json",
    ]
    before = {
        path: (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
        for path in watched
    }

    pipeline.import_file(helpers.make_xlsx(tmp_path / "地区销售.xlsx", {"Sheet1": helpers.REGION_SAMPLE}))
    pipeline.import_file(helpers.make_md(tmp_path / "季度分析.md", helpers.MD_SAMPLE))

    after = {
        path: (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
        for path in watched
    }
    assert before == after, "测试动了真实目录里的文件"
    # 而且这次导入确实写进了**隔离**目录（不是"什么都没发生"导致的假通过）
    assert pathlib.Path(os.environ["SRA_DB_PATH"]).exists()
    assert pathlib.Path(os.environ["SRA_DB_PATH"]) != PROJECT_ROOT / "data" / "app.db"
