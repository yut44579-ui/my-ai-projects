"""store.py · FR-003A/B：来源链路的**读写层**（SQLite 的 CRUD，没有业务判断）。

════════════════════════════════════════════════════════════════════════
【它负责什么、不负责什么】
════════════════════════════════════════════════════════════════════════
负责：把 source_file / import / dataset / table / column / row / document 七种记录
      安全地写进去、按 id 取出来 —— **只做 IO**。
不负责：幂等判断、事务边界、解析、地区识别（那些在 pipeline.py / parsers.py / regions.py）。

★ 这里同样**没有一处聚合**（见 db.py 顶部）：行是原样写、原样读。
  `load_rows()` 只是 `SELECT payload`，不做 SUM / GROUP BY。

════════════════════════════════════════════════════════════════════════
【JSON 列的处理】
════════════════════════════════════════════════════════════════════════
SQLite 没有"数组/对象"类型，所以 region_dimensions / field_map / samples 这些用 TEXT 存 JSON。
本文件负责两个方向的转换（写时 dumps、读时 loads），**并且只在读的时候兜一次底**：
读到坏 JSON 不抛错、返回空值 —— 库里的坏数据不该让整个数据源列表打不开。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from app.importer import db
from app.importer.models import STATUS_SUCCESS

#: 行的 JSON 序列化参数：紧凑 + 保留中文（人能直接读库排障）
_DUMPS = {"ensure_ascii": False, "separators": (",", ":"), "default": str}


def _dump(value: Any) -> str:
    return json.dumps(value, **_DUMPS)


def _load(text: Any, fallback: Any) -> Any:
    """读 JSON 列（坏数据返回兜底值，不抛错 —— 见文件头）。"""
    if text is None or text == "":
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {key: row[key] for key in row.keys()}


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


# ════════════════════════════════════════════════════════════════════════
# ① source_file（一份文件）
# ════════════════════════════════════════════════════════════════════════
def upsert_source_file(
    connection: sqlite3.Connection,
    *,
    source_file_id: str,
    filename: str,
    source_type: str,
    sha256: str,
    size_bytes: int,
    original_path: str,
    created_at: str,
) -> dict[str, Any]:
    """按 sha256 落一条 source_file（同一份文件只留一条 —— 它表示"这份文件"，不是"这次导入"）。"""
    connection.execute(
        """
        INSERT INTO source_files
            (source_file_id, filename, source_type, sha256, size_bytes,
             original_path, original_sha256, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_file_id) DO UPDATE SET
            filename = excluded.filename,
            original_path = excluded.original_path,
            original_sha256 = excluded.original_sha256
        """,
        (source_file_id, filename, source_type, sha256, int(size_bytes),
         original_path, sha256, created_at),
    )
    return get_source_file(connection, source_file_id) or {}


def get_source_file(
    connection: sqlite3.Connection, source_file_id: str
) -> dict[str, Any] | None:
    return _row_to_dict(connection.execute(
        "SELECT * FROM source_files WHERE source_file_id = ?", (source_file_id,)
    ).fetchone())


def find_source_file_by_sha(
    connection: sqlite3.Connection, sha256: str
) -> dict[str, Any] | None:
    """同一份文件（同 sha256）之前来过没有 —— 决定复用哪条 source_file。"""
    return _row_to_dict(connection.execute(
        "SELECT * FROM source_files WHERE sha256 = ? ORDER BY created_at LIMIT 1", (sha256,)
    ).fetchone())


# ════════════════════════════════════════════════════════════════════════
# ② imports（一次导入动作）
# ════════════════════════════════════════════════════════════════════════
def find_import(
    connection: sqlite3.Connection, sha256: str, parser_version: str
) -> dict[str, Any] | None:
    """同文件 + 同 parser_version 的**最近一次**导入（幂等的判据，见 pipeline.py）。"""
    return _row_to_dict(connection.execute(
        """
        SELECT * FROM imports
        WHERE source_sha256 = ? AND parser_version = ?
        ORDER BY revision DESC LIMIT 1
        """,
        (sha256, parser_version),
    ).fetchone())


def next_revision(connection: sqlite3.Connection, sha256: str) -> int:
    """这份文件的**下一条修订号**（重解析 / 失败重试时用）。

    ★ 口径：revision 数的是**这份文件被导入过几次**，与 parser_version 无关 ——
      所以"换了新解析器再导一次"是 rev2，不是"新解析器下面的 rev1"。
      这样单看 revision 就能回答"这份文件在库里经历过几次导入"（包括失败的那几次），
      而 parser_version 回答的是"那一次是用哪版解析器读的"。两个问题，两个字段。
    """
    row = connection.execute(
        "SELECT MAX(revision) FROM imports WHERE source_sha256 = ?", (sha256,)
    ).fetchone()
    current = row[0] if row and row[0] is not None else 0
    return int(current) + 1


def insert_import(
    connection: sqlite3.Connection,
    *,
    import_id: str,
    source_file_id: str,
    source_filename: str,
    source_sha256: str,
    source_type: str,
    imported_at: str,
    dataset_id: str | None,
    document_id: str | None,
    status: str,
    error_code: str | None,
    parser_version: str,
    revision: int,
    note: str = "",
) -> dict[str, Any]:
    connection.execute(
        """
        INSERT INTO imports
            (import_id, source_file_id, source_filename, source_sha256, source_type,
             imported_at, dataset_id, document_id, status, error_code, parser_version,
             revision, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (import_id, source_file_id, source_filename, source_sha256, source_type,
         imported_at, dataset_id, document_id, status, error_code, parser_version,
         int(revision), note),
    )
    return get_import(connection, import_id) or {}


def get_import(connection: sqlite3.Connection, import_id: str) -> dict[str, Any] | None:
    return _row_to_dict(connection.execute(
        "SELECT * FROM imports WHERE import_id = ?", (import_id,)
    ).fetchone())


def list_imports(
    connection: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    records = _rows_to_dicts(connection.execute(
        "SELECT * FROM imports ORDER BY imported_at DESC, import_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall())
    total = int(connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0])
    return records, total


def imports_of_source_file(
    connection: sqlite3.Connection, source_file_id: str
) -> list[dict[str, Any]]:
    """这份文件被导入过几次（含成功与失败、含不同 revision）—— UI 说清"导入了几次"要看它。"""
    return _rows_to_dicts(connection.execute(
        "SELECT * FROM imports WHERE source_file_id = ? ORDER BY revision", (source_file_id,)
    ).fetchall())


# ════════════════════════════════════════════════════════════════════════
# ③ datasets / dataset_tables / dataset_columns / dataset_rows
# ════════════════════════════════════════════════════════════════════════
def insert_dataset(connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    connection.execute(
        """
        INSERT INTO datasets
            (dataset_id, source_file_id, import_id, name, source_type, table_count,
             row_count, column_count, date_start, date_end, region_dimensions, field_map,
             materialized_at, parser_version, status, analysis_enabled, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["dataset_id"], record.get("source_file_id"), record.get("import_id"),
            record["name"], record["source_type"], int(record.get("table_count") or 0),
            int(record.get("row_count") or 0), int(record.get("column_count") or 0),
            record.get("date_start"), record.get("date_end"),
            _dump(record.get("region_dimensions") or []),
            _dump(record.get("field_map") or {}),
            record["materialized_at"], record["parser_version"], record["status"],
            1 if record.get("analysis_enabled") else 0,
            _dump(record.get("meta") or {}),
        ),
    )
    return get_dataset(connection, record["dataset_id"]) or {}


def get_dataset(connection: sqlite3.Connection, dataset_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    return _decode_dataset(row)


def _decode_dataset(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = {key: row[key] for key in row.keys()}
    record["region_dimensions"] = _load(record.get("region_dimensions"), [])
    record["field_map"] = _load(record.get("field_map"), {})
    record["meta"] = _load(record.get("meta"), {})
    record["analysis_enabled"] = bool(record.get("analysis_enabled"))
    return record


def list_datasets(
    connection: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """数据源列表，**最近入库在前**。

    ★ FR-008 补的并列规则：`materialized_at` 是**秒**精度（`state.now_iso()`），
      同一秒内导入的两份文件会并列；原先用 `dataset_id DESC` 破并列 —— 那是**哈希值**
      的字典序，与"谁后导入"毫无关系。于是"最近导入优先"在真实使用中（连着导两个文件）
      会随机失效，而且**看不出来**。改成 `rowid DESC`：SQLite 的 rowid 就是**真实插入顺序**，
      后插入的一定更大 —— 这就是"最近导入"的机器定义。
    """
    rows = connection.execute(
        "SELECT * FROM datasets ORDER BY materialized_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = int(connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])
    return [record for record in map(_decode_dataset, rows) if record], total


def update_dataset_import_id(
    connection: sqlite3.Connection, dataset_id: str, import_id: str
) -> None:
    """把 import_id 回填到 dataset（imports 行要先拿到 dataset_id 才能插入，见 pipeline.py）。"""
    connection.execute(
        "UPDATE datasets SET import_id = ? WHERE dataset_id = ?", (import_id, dataset_id)
    )


def insert_dataset_table(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO dataset_tables
            (table_id, dataset_id, table_name, display_name, ordinal, row_count, column_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (record["table_id"], record["dataset_id"], record["table_name"],
         record["display_name"], int(record["ordinal"]), int(record["row_count"]),
         int(record["column_count"])),
    )


def list_dataset_tables(
    connection: sqlite3.Connection, dataset_id: str
) -> list[dict[str, Any]]:
    return _rows_to_dicts(connection.execute(
        "SELECT * FROM dataset_tables WHERE dataset_id = ? ORDER BY ordinal", (dataset_id,)
    ).fetchall())


def insert_dataset_columns(
    connection: sqlite3.Connection, records: Sequence[dict[str, Any]]
) -> None:
    connection.executemany(
        """
        INSERT INTO dataset_columns
            (column_id, dataset_id, table_name, ordinal, name, inferred_type, non_null,
             null_count, distinct_count, samples, region_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (record["column_id"], record["dataset_id"], record["table_name"],
             int(record["ordinal"]), record["name"], record["inferred_type"],
             int(record.get("non_null") or 0), int(record.get("null_count") or 0),
             int(record.get("distinct_count") or 0), _dump(record.get("samples") or []),
             record.get("region_key"))
            for record in records
        ],
    )


def list_dataset_columns(
    connection: sqlite3.Connection, dataset_id: str, table_name: str | None = None
) -> list[dict[str, Any]]:
    if table_name is None:
        rows = connection.execute(
            "SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY table_name, ordinal",
            (dataset_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM dataset_columns WHERE dataset_id = ? AND table_name = ? ORDER BY ordinal",
            (dataset_id, table_name),
        ).fetchall()
    decoded = _rows_to_dicts(rows)
    for record in decoded:
        record["samples"] = _load(record.get("samples"), [])
    return decoded


def insert_dataset_rows(
    connection: sqlite3.Connection, dataset_id: str, table_name: str,
    payloads: Sequence[str],
) -> None:
    """**原样**写入物化行（一行一条 JSON；不做任何计算、不写任何聚合结果）。

    入参是**已经序列化好的 JSON 串**（而不是 dict）：pipeline 需要先知道这一批数据
    一共有多少字节，才能在建库前过那道容量闸门（`db.ensure_capacity`）。
    在这里再 dumps 一遍意味着同一批数据序列化两次 —— 大表上是实打实的浪费。
    """
    connection.executemany(
        "INSERT INTO dataset_rows (dataset_id, table_name, row_index, payload) VALUES (?, ?, ?, ?)",
        [(dataset_id, table_name, index, payload) for index, payload in enumerate(payloads)],
    )


def serialize_row(row: dict[str, Any]) -> str:
    """把一个物化行序列化成入库用的 JSON 串（**唯一**的序列化处，供容量估算复用）。"""
    return _dump(row)


def load_rows(
    connection: sqlite3.Connection, dataset_id: str, table_name: str
) -> list[dict[str, Any]]:
    """按写入顺序取回**原始行**（计算层拿它建 DataFrame，见 frames.py）。

    ★ 这里只有 SELECT，没有聚合 —— SQLite 是持久化层，不是计算引擎（见 db.py 顶部）。
    """
    rows = connection.execute(
        "SELECT payload FROM dataset_rows WHERE dataset_id = ? AND table_name = ? ORDER BY row_index",
        (dataset_id, table_name),
    ).fetchall()
    decoded: list[dict[str, Any]] = []
    for row in rows:
        payload = _load(row["payload"], None)
        if isinstance(payload, dict):
            decoded.append(payload)
    return decoded


def count_rows(connection: sqlite3.Connection, dataset_id: str) -> int:
    """这个数据集物化了多少行（**运维计数**，不是业务指标 —— 见 db.py 的说明）。"""
    return int(connection.execute(
        "SELECT COUNT(*) FROM dataset_rows WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()[0])


# ════════════════════════════════════════════════════════════════════════
# ④ documents（正文资料）
# ════════════════════════════════════════════════════════════════════════
def insert_document(connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    connection.execute(
        """
        INSERT INTO documents
            (document_id, source_file_id, import_id, filename, source_type, title, text,
             char_count, block_count, imported_at, parser_version, meta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (record["document_id"], record.get("source_file_id"), record.get("import_id"),
         record["filename"], record["source_type"], record.get("title") or "",
         record.get("text") or "", int(record.get("char_count") or 0),
         int(record.get("block_count") or 0), record["imported_at"],
         record["parser_version"], _dump(record.get("meta") or {})),
    )
    return get_document(connection, record["document_id"]) or {}


def get_document(connection: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM documents WHERE document_id = ?", (document_id,)
    ).fetchone()
    if row is None:
        return None
    record = {key: row[key] for key in row.keys()}
    record["meta"] = _load(record.get("meta"), {})
    return record


def update_document_import_id(
    connection: sqlite3.Connection, document_id: str, import_id: str
) -> None:
    connection.execute(
        "UPDATE documents SET import_id = ? WHERE document_id = ?", (import_id, document_id)
    )


def list_documents(
    connection: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> tuple[list[dict[str, Any]], int]:
    rows = connection.execute(
        "SELECT * FROM documents ORDER BY imported_at DESC, document_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    total = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    records = _rows_to_dicts(rows)
    for record in records:
        record["meta"] = _load(record.get("meta"), {})
    return records, total


def list_document_meta(
    connection: sqlite3.Connection, limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """文档列表（**不含正文**）+ 总数 —— 统一文档视图专用（FR-009-A1）。

    为什么不复用上面的 `list_documents`：那个 `SELECT *` 会把每份文档的**整篇正文**
    读进内存，而统一视图只是要把"有哪些文档"列出来再合并分页 —— 一篇 3000 字的 PDF 正文
    对列表页一个字节都用不上。这里显式列出除 `text` 之外的列（`char_count` 足够表达"多长"），
    合并视图因此可以放心地"取全量再排序"。

    `limit=None` = 不限（默认）：统一视图要按时间合并两个来源，分页得在合并之后做，
    所以这一层不能先截断。单机文档量级下这是最省事也最不会出错的做法。
    """
    sql = (
        "SELECT document_id, source_file_id, import_id, filename, source_type, title, "
        "char_count, block_count, imported_at, parser_version, meta FROM documents "
        "ORDER BY imported_at DESC, document_id DESC"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    rows = connection.execute(sql, params).fetchall()
    total = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    records = _rows_to_dicts(rows)
    for record in records:
        record["meta"] = _load(record.get("meta"), {})
    return records, total


def successful_import_count(
    connection: sqlite3.Connection, source_file_id: str
) -> dict[str, int]:
    """这份文件成功导入了「几个文档 + 几个数据集」（UI 那句"已导入 1 个文档资料 + 2 个数据集"）。

    口径：数**不同的** dataset_id / document_id（同一个 revision 重跑不会重复计数）。
    """
    dataset_row = connection.execute(
        """
        SELECT COUNT(DISTINCT dataset_id) FROM imports
        WHERE source_file_id = ? AND status = ? AND dataset_id IS NOT NULL
        """,
        (source_file_id, STATUS_SUCCESS),
    ).fetchone()
    document_row = connection.execute(
        """
        SELECT COUNT(DISTINCT document_id) FROM imports
        WHERE source_file_id = ? AND status = ? AND document_id IS NOT NULL
        """,
        (source_file_id, STATUS_SUCCESS),
    ).fetchone()
    return {
        "dataset_count": int(dataset_row[0] or 0),
        "document_count": int(document_row[0] or 0),
    }


# ════════════════════════════════════════════════════════════════════════
# 导入记录的"落成了什么"（FR-009-A2）
# ════════════════════════════════════════════════════════════════════════
def document_char_counts(
    connection: sqlite3.Connection, document_ids: Sequence[str]
) -> dict[str, int]:
    """{document_id: 字符数} —— **一次查完这一页涉及的文档**，不逐条查库。

    为什么要批量：导入记录列表一页最多 200 条，逐条 `SELECT` 就是 200 次往返
    （N+1 查询）。这里一条 `IN (...)` 拿完，页面要的"字数"从哪来这件事只在这里定义一次。
    """
    ids = [str(item) for item in document_ids if item]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT document_id, char_count FROM documents WHERE document_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {str(row[0]): int(row[1] or 0) for row in rows}


def dataset_row_counts(
    connection: sqlite3.Connection, dataset_ids: Sequence[str]
) -> dict[str, int]:
    """{dataset_id: 行数} —— 与上面同一个理由（一次查完这一页）。"""
    ids = [str(item) for item in dataset_ids if item]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT dataset_id, row_count FROM datasets WHERE dataset_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {str(row[0]): int(row[1] or 0) for row in rows}


__all__ = [
    "count_rows",
    "dataset_row_counts",
    "document_char_counts",
    "find_import",
    "find_source_file_by_sha",
    "get_dataset",
    "get_document",
    "get_import",
    "get_source_file",
    "imports_of_source_file",
    "insert_dataset",
    "insert_dataset_columns",
    "insert_dataset_rows",
    "insert_dataset_table",
    "insert_document",
    "insert_import",
    "list_dataset_columns",
    "list_dataset_tables",
    "list_datasets",
    "list_document_meta",
    "list_documents",
    "list_imports",
    "load_rows",
    "next_revision",
    "serialize_row",
    "successful_import_count",
    "update_dataset_import_id",
    "update_document_import_id",
    "upsert_source_file",
]
