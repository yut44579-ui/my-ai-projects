"""test_fr010b_b8.py · FR-010-B 的**两件小修**（派单 §二 B8①②）。

════════════════════════════════════════════════════════════════════════
【B8① 读取路径补标题可信化（FR-009-A 的遗留缺陷）】
════════════════════════════════════════════════════════════════════════
`repair_title` 原来只在**写入**时调用（上传 / 导入管道），于是 FR-009-A 之前就已经入库的
旧文档标题仍然是乱码（实测：《AI游戏制作知识库.pdf》在库里存的是 `'\\x00A\\x00I\\x00 n8b…'`）。
本 TASK 在**读取路径**（`app/repositories/unified_documents.py` 的列表 / 详情）也过一遍修复：

    · **只做读时适配，不做写时双写**（评审 013 口径）——
      写时双写会制造"SQLite 写成功、JSON 写失败"这类新的不一致；
    · 修复规则**一个字没改**（还是 `app/document_title.py::repair_title`）；
    · 库里那份原始记录**不动**（读时修，不是把库改了）。

════════════════════════════════════════════════════════════════════════
【B8② 相对时间说法的"锚点"要看得见（FR-010-A 的遗留）】
════════════════════════════════════════════════════════════════════════
「这个月卖了多少」按既有规则锚到数据集最后一天（→ 2011-12-09）。这条假设**保持原文**，
但在提示行里补一句可见的话（复用 A 的 notice 机制）：
    · 真被当成一个时间范围用了 → 说清"按哪天换算、取了哪一段、这一段完不完整"；
    · 没被用上（取的是整个数据集区间）→ 也说清（否则用户会把全区间当成"本月"）。
★ **不许改变既有相对时间规则本身**（那是 FR-007/009 冻结的口径）—— 这里只加可见性。
"""

from __future__ import annotations

import pathlib
import sqlite3
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
from app.ai import service  # noqa: E402
from app.api import app  # noqa: E402
from app.document_title import repair_title  # noqa: E402
from app.repositories import unified_documents  # noqa: E402

client = TestClient(app)

#: 用户在真实库里那份 PDF 的标题原文（一字不改地抄自 `data/app.db` 的 documents.title）
REAL_MOJIBAKE = "\x00A\x00I\x00 n8b\x0fR6O\\w\xe5\x8b\xc6^\x93"
REAL_TITLE = "AI 游戏制作知识库"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    paths = helpers.isolate(tmp_path, monkeypatch)
    helpers.reset()
    yield paths


def _insert_legacy_document(document_id: str, title: str, filename: str, tmp_path: pathlib.Path) -> None:
    """直接往库里塞一条**标题是乱码**的旧文档（模拟"修复之前就已经入库"的那条记录）。

    走库表本身（不是走导入管道）：这样才真的复现"写的时候还没这道修复"的历史状态 ——
    走管道的话，写时修复会把标题修好，那就测不到读路径了。
    """
    from app.importer import db, store

    body = "Quarterly review body with enough text to be a real document."
    source_file_id = state.new_id("sf")
    with db.transaction() as connection:
        store.upsert_source_file(
            connection, source_file_id=source_file_id, filename=filename,
            source_type="pdf", sha256="0" * 64, size_bytes=len(body),
            original_path="", created_at=state.now_iso(),
        )
        store.insert_document(connection, {
            "document_id": document_id, "source_file_id": source_file_id,
            "filename": filename, "source_type": "pdf", "title": title,
            "text": body, "char_count": len(body), "block_count": 1,
            "imported_at": state.now_iso(), "parser_version": "test",
            "meta": {"block_unit": "page"},
        })


# ════════════════════════════════════════════════════════════════════════
# ① 读取路径补标题可信化
# ════════════════════════════════════════════════════════════════════════
def test_B8_1_已入库的乱码标题在列表里被修好(env, tmp_path) -> None:
    """Given 库里存着一条标题是乱码的旧文档 → Then 列表读出来的是**干净的标题**。"""
    _insert_legacy_document("doc_mojibake", REAL_MOJIBAKE, "AI游戏制作知识库.pdf", tmp_path)

    body = client.get("/api/documents").json()
    assert body["total"] == 1
    record = body["documents"][0]
    assert record["title"] == REAL_TITLE, f"读路径没有修标题：{record['title']!r}"
    assert "\x00" not in record["title"]
    # 文件名原样（标题修的是"从文件里提出来"的那个字段，文件名不是它）
    assert record["filename"] == "AI游戏制作知识库.pdf"


def test_B8_1_详情路径同样被修好(env, tmp_path) -> None:
    _insert_legacy_document("doc_mojibake2", REAL_MOJIBAKE, "AI游戏制作知识库.pdf", tmp_path)
    detail = client.get("/api/documents/doc_mojibake2").json()
    assert detail["title"] == REAL_TITLE, detail["title"]


def test_B8_1_只做读时适配_库里那份一个字没动(env, tmp_path) -> None:
    """★ 评审 013 口径：**读时适配，不做写时双写** —— 库里的原值必须还是那个乱码串。

    这条是「读时适配」与「顺手迁移数据」的分界线：本 TASK 不迁移、不改写库，
    所以回滚只需要回滚代码，不需要回滚数据。
    """
    _insert_legacy_document("doc_mojibake3", REAL_MOJIBAKE, "AI游戏制作知识库.pdf", tmp_path)
    # 三条读路径都走一遍（HTTP 列表 / HTTP 详情 / 仓储层直接读）
    client.get("/api/documents")
    client.get("/api/documents/doc_mojibake3")
    assert unified_documents.get_document("doc_mojibake3")["title"] == REAL_TITLE

    with sqlite3.connect(env["SRA_DB_PATH"]) as connection:
        stored = connection.execute(
            "SELECT title FROM documents WHERE document_id = 'doc_mojibake3'"
        ).fetchone()[0]
    assert stored == REAL_MOJIBAKE, "读路径把库里的数据改了 —— 那不是读时适配"


def test_B8_1_标题本来就好的不许被改坏(env, tmp_path) -> None:
    """修复是"过一遍质量校验"，不是"一律重解码" —— 好标题必须原样出来。"""
    for title in ("季度销售复盘", "Quarterly Report 2011", "AI 游戏制作知识库"):
        assert repair_title(title, "别的名字.pdf") == title
    _insert_legacy_document("doc_good", "季度销售复盘", "季度销售复盘.pdf", tmp_path)
    record = client.get("/api/documents").json()["documents"][0]
    assert record["title"] == "季度销售复盘"


def test_B8_1_修不出来就退回文件名(env, tmp_path) -> None:
    """实在解不出来的（全是控制字符）→ 退回**文件名去扩展名**，绝不把乱码放给用户看。"""
    _insert_legacy_document("doc_bad", "\x01\x02\x03\x04", "无标题的文档.pdf", tmp_path)
    record = client.get("/api/documents").json()["documents"][0]
    assert record["title"] == "无标题的文档", record["title"]


def test_B8_1_统一文档层的两个来源都过修复() -> None:
    """读路径只有**一处**实现（`unified_documents`），两个来源共用同一份修复。"""
    source = (PROJECT_ROOT / "app" / "repositories" / "unified_documents.py").read_text("utf-8")
    assert source.count("repair_title(") == 2, "两个来源（新导入 / 历史文档）各要过一次"
    assert "只做读时适配，不做写时双写" in source


# ════════════════════════════════════════════════════════════════════════
# ② 相对时间说法的可见提示
# ════════════════════════════════════════════════════════════════════════
#: 数据集画像（只用到首末两天；不读真实数据，这条测的是**文案逻辑**）
PROFILE = {"first_day": "2010-12-01", "last_day": "2011-12-09"}


def _params(start: str, end: str) -> dict:
    return {"start": start, "end": end}


def test_B8_2_锚到数据集最后一天时说得明明白白() -> None:
    """「这个月」按最后一天换算成 12 月，而数据只到 12-09 —— 必须说出来。"""
    notice = service._relative_time_notice("这个月卖了多少？", _params("2011-12-01", "2011-12-09"), PROFILE)
    assert "「这个月」" in notice
    assert "按数据集最后一天 2011-12-09 换算" in notice
    assert "取 2011-12-01 ~ 2011-12-09" in notice
    assert "还差 22 天没有数据" in notice and "不完整" in notice


def test_B8_2_完整的一段就说完整() -> None:
    notice = service._relative_time_notice("上个月卖了多少？", _params("2011-11-01", "2011-11-30"), PROFILE)
    assert "取 2011-11-01 ~ 2011-11-30" in notice
    assert "这一段是完整的" in notice
    assert "不完整" not in notice


def test_B8_2_相对说法没被用上时也要说() -> None:
    """取的是整个数据集区间（相对说法没换算）→ 用户更要知道"这不是本月"。"""
    notice = service._relative_time_notice(
        "这个月卖了多少？", _params("2010-12-01", "2011-12-09"), PROFILE
    )
    assert "「这个月」这次没有被当成一个时间范围" in notice
    assert "全区间 2010-12-01 ~ 2011-12-09" in notice


def test_B8_2_没有相对说法就不多嘴() -> None:
    assert service._relative_time_notice("2011年11月卖了多少？", _params("2011-11-01", "2011-11-30"), PROFILE) == ""
    assert service._relative_time_notice("这个月卖了多少？", {}, PROFILE) == ""


def test_B8_2_说法与实际取的时间对不上就不解释() -> None:
    """★ 宁可不提示，也不给一句与事实不符的说明（解释错了比不说更糟）。"""
    assert service._relative_time_notice("这个月卖了多少？", _params("2011-01-01", "2011-06-30"), PROFILE) == ""


def test_B8_2_提示走notice并且正文一个数没动(env) -> None:
    """端到端：正文与参数**一个都没变**，只是 notice 末尾多了一句可见的提示。"""
    import re

    record = service.ask("这个月卖了多少？", use_llm=False)
    assert record["status"] == "ok"
    # 正文仍然是"单值"形态（一个数），提示一个字都没进正文
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} ~ \d{4}-\d{2}-\d{2} 销售额：[\d,]+(\.\d+)?元。",
                        record["answer"]["text"]), record["answer"]["text"]
    notice = str(record["notice"])
    assert "「这个月」" in notice, notice
    # 提示排在数据范围那句之后（不改既有那几句，也不抢正文的位置）
    assert "数据范围：" in notice
