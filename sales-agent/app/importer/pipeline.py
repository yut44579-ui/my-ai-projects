"""pipeline.py · FR-003A/B：**统一导入管道**（parse → validate → materialize → commit）。

════════════════════════════════════════════════════════════════════════
【一次导入的完整顺序（评审 §8② 指定，不许换）】
════════════════════════════════════════════════════════════════════════
    ① 认后缀          不认 → 报错，**零写入**（连 source_files 都不写）
    ② 算 sha256 + 幂等查  同 sha + 同 parser_version 且上次成功 → 直接返回既有 import_id
    ③ 验身            签名/ZIP 安全检查不过 → 报错，**零写入**
    ④ 解析            读成正文 + 表；失败 → 记一条 failed 的 import（**案底**），不产生半成品
    ⑤ 保留原文件      copy 到 data/original/（证据）—— **先搬文件，后写库**
    ⑥ 事务            materialize（dataset/document/columns/rows）+ commit metadata 一个事务
    ⑦ 消费输入文件    成功后把输入文件删掉（"已物化"的语义；原文件副本仍在）

任何一步失败：库里的东西**一起回滚**，绝不出现"dataset 建了一半"或"库里没东西但接口说成功"
（ASSERT 14/18 钉的就是这个）。第 ⑦ 步放在事务**之后**：库没写成，输入文件必须还在。

════════════════════════════════════════════════════════════════════════
【③④ 之间那道线为什么划在这里】
════════════════════════════════════════════════════════════════════════
    验身没过 = **这个文件根本不是它自称的那种格式**（改个后缀就想进来）
              → 我们压根不认识它，不留任何痕迹（ASSERT 10 的"零写入"）
    验身过了 = 是份真文件，只是坏 / 太大 / 编码不对
              → 失败也要留案底（error_code 那一列才有意义），但绝不落 dataset/document

════════════════════════════════════════════════════════════════════════
【幂等（评审 §8①）】
════════════════════════════════════════════════════════════════════════
    相同 sha256 + 相同 parser_version 且上次成功 → **不重复入库**，返回既有 import_id
    相同 sha256 + parser_version 变了          → 允许重解析，revision + 1（新的 import 记录）
    相同 sha256 + 上次失败                     → 允许重试（revision + 1）

★ 判据放在**一个事务外**读、写在**事务内**再查一遍：真正的串行化靠 `BEGIN IMMEDIATE`
  （db.transaction），读锁与写在同一个事务里，并发导入不会各插一份。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import shutil
from pathlib import Path
from typing import Any

from app import state
from app.datasets import registry as dataset_registry
from app.document_title import repair_title
from app.importer import db, normalize, parsers, regions, store
from app.importer.models import (
    ImporterError,
    MAX_FILE_BYTES,
    MAX_TABLE_ROWS,
    PARSER_VERSION,
    SOURCE_TYPES,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    source_type_of,
)

#: 物化后的数据集状态：这份数据的行**已经在库里了**，所以它是"已入库"（与 STEP A 的
#: "已登记 · 分析未开通"不是一回事 —— 那一条指的是"引用外文件、还没物化"）。
STATUS_MATERIALIZED = "materialized"
STATUS_LABEL_MATERIALIZED = "已入库"

#: 金额列候选（给"按地区"这类维度计算用；**只认列名，不做业务换算**）
_SALES_AMOUNT_GUESSES: tuple[str, ...] = (
    "销售额", "销售金额", "金额", "销售", "sales", "salesamount", "amount",
    "revenue", "turnover", "gmv",
)
_QUANTITY_GUESSES: tuple[str, ...] = ("数量", "qty", "quantity", "销量")
_UNIT_PRICE_GUESSES: tuple[str, ...] = ("单价", "price", "unitprice", "售价")
_DATE_GUESSES: tuple[str, ...] = ("日期", "时间", "date", "period", "期间", "月份", "invoice date")


# ════════════════════════════════════════════════════════════════════════
# 小工具
# ════════════════════════════════════════════════════════════════════════
def sha256_of_file(path: str | Path) -> str:
    """文件全量 SHA-256（分块读，不把文件整个吃进内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_title(parsed: parsers.ParsedFile, filename: str) -> str:
    """这份文档对外显示的标题（FR-009-A3）。

    为什么在**这一层**做：`parsers` 只拿得到落盘路径（`in_xxxx_原名.pdf` 这种带前缀的临时名），
    拿不到用户看到的那个文件名；兜底必须用**用户认识的那个名字**，所以修复放在知道
    `filename` 的地方。可信化规则本身在 `app/document_title.py`（与旧管道共用同一份实现）。
    """
    return repair_title(parsed.document_title, filename)


def _safe_stem(name: str) -> str:
    """文件名消毒：只留文件名本身 + 去掉路径非法字符（与 api_datasets 同一套规则）。"""
    base = Path(str(name or "upload")).name.strip() or "upload"
    return "".join(char for char in base if char not in '\\/:*?"<>|') or "upload"


def _source_file_id_for(sha256: str) -> str:
    """同一份文件复用同一个 source_file_id（双落关链与"这份文件导了几次"都靠它）。"""
    with db.readonly() as connection:
        existing = store.find_source_file_by_sha(connection, sha256)
    return str(existing["source_file_id"]) if existing else state.new_id("sf")


# ════════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════════
def import_file(
    path: str | Path,
    *,
    filename: str | None = None,
    suffix: str | None = None,
    name: str | None = None,
    consume_source: bool = True,
) -> dict[str, Any]:
    """把一份文件导入系统（幂等、事务、可追溯）。

    返回一份**导入回执**，形如：

        {"import_id": "imp_…", "source_file_id": "sf_…", "status": "success",
         "dataset_id": "ds_…", "document_id": "doc_…",
         "dataset_count": 1, "document_count": 1, "table_count": 2,
         "summary_text": "销售分析.pptx → 已导入 1 个文档资料 + 1 个数据集（含 2 张表）",
         "datasets": [...], "documents": [...]}
    """
    target = Path(path)
    display_name = filename or target.name
    actual_suffix = str(suffix if suffix is not None else target.suffix).lower()

    # ── ① 认后缀（不认就报错，此时**一个字节都没写**）─────────────────────
    source_type = source_type_of(actual_suffix)
    if source_type is None:
        raise ImporterError(
            "unsupported_type",
            f"不支持的文件类型：{actual_suffix or '(无后缀)'}。"
            f"支持的是 {sorted(SOURCE_TYPES)}（Excel / CSV / Markdown / PPT / Word / PDF）。",
        )

    size_bytes = target.stat().st_size
    if size_bytes > MAX_FILE_BYTES:
        raise ImporterError(
            "file_too_large",
            f"文件 {size_bytes / 1048576:.1f}MB 超过 {MAX_FILE_BYTES // 1048576}MB 上限 —— "
            f"请先拆分或抽样后再导入。",
        )

    sha256 = sha256_of_file(target)

    # ── ② 幂等（同文件 + 同解析器版本，上次成功 → 直接返回既有 import_id）──
    with db.readonly() as connection:
        previous = store.find_import(connection, sha256, PARSER_VERSION)
        if previous is not None and previous["status"] == STATUS_SUCCESS:
            return _idempotent_receipt(connection, previous)

    # ── ③ 验身（不过就报错：这个文件不是它自称的格式，不留痕迹）─────────────
    parsers.verify_file(target, actual_suffix)

    # ── ④ 解析（失败 → 留案底，不留半成品）───────────────────────────────
    try:
        parsed = parsers.parse_file(target, actual_suffix, verify=False)
    except ImporterError as exc:
        _record_failure(
            sha256=sha256, filename=display_name, suffix=actual_suffix,
            source_type=source_type, size_bytes=size_bytes, error=exc,
        )
        raise

    # ── ⑤ 保留原文件（证据）—— 先搬文件，后写库 ───────────────────────────
    preserved = _preserve_original(target, sha256, display_name)

    # ── ⑥ 事务：materialize + commit metadata ─────────────────────────────
    try:
        with db.transaction() as connection:
            receipt = _materialize(
                connection, parsed=parsed, sha256=sha256, size_bytes=size_bytes,
                filename=display_name, source_type=source_type,
                preserved_path=preserved, name=name,
            )
    except BaseException:
        # 库回滚了 → 那份副本也不该留着（否则磁盘上有一份谁都查不到的"孤儿证据"）
        try:
            preserved.unlink(missing_ok=True)
        except OSError:                                        # pragma: no cover
            pass
        raise

    # ── ⑦ 消费输入文件（"已物化"的语义；原文件副本仍在 data/original/）────
    if consume_source:
        try:
            target.unlink(missing_ok=True)
        except OSError:                                        # pragma: no cover
            pass                                     # 删不掉不是错误：库里已经有数据了
    return receipt


def import_bytes(
    data: bytes, filename: str, *, name: str | None = None, consume_source: bool = True
) -> dict[str, Any]:
    """从内存里导入（HTTP 上传流程用）：先落一份**临时文件**，再走同一条管道。

    为什么必须先落盘：sha256 / ZIP 安全检查 / pandas 读 Excel 都要一个真实路径上的文件；
    在内存里再实现一套"等价"的读法，等于把同一条管道写两遍（两遍迟早不一致）。
    临时文件落在项目的上传目录里（与既有上传同一条存储），导入成功后被
    `import_file(consume_source=True)` 删掉。
    """
    safe = _safe_stem(filename)
    suffix = Path(safe).suffix.lower()
    if source_type_of(suffix) is None:
        raise ImporterError(
            "unsupported_type",
            f"不支持的文件类型：{suffix or '(无后缀)'}。"
            f"支持的是 {sorted(SOURCE_TYPES)}（Excel / CSV / Markdown / PPT / Word / PDF）。",
        )
    if len(data) > MAX_FILE_BYTES:
        raise ImporterError(
            "file_too_large",
            f"文件 {len(data) / 1048576:.1f}MB 超过 {MAX_FILE_BYTES // 1048576}MB 上限。",
        )
    staging = state.upload_dir() / f"{state.new_id('in')}_{safe}"
    staging.write_bytes(data)
    try:
        return import_file(
            staging, filename=filename, suffix=suffix, name=name, consume_source=consume_source
        )
    except BaseException:
        staging.unlink(missing_ok=True)              # 失败不留临时件（与既有上传同一条约定）
        raise


# ════════════════════════════════════════════════════════════════════════
# ⑥ 物化（**只在事务内被调用**）
# ════════════════════════════════════════════════════════════════════════
def _materialize(
    connection: Any,
    *,
    parsed: parsers.ParsedFile,
    sha256: str,
    size_bytes: int,
    filename: str,
    source_type: str,
    preserved_path: Path,
    name: str | None,
) -> dict[str, Any]:
    """把解析结果写进库（同一个事务里，调用方负责 BEGIN/COMMIT/ROLLBACK）。"""
    now = state.now_iso()
    source_file_id = _source_file_id_for(sha256)
    store.upsert_source_file(
        connection,
        source_file_id=source_file_id,
        filename=filename,
        source_type=source_type,
        sha256=sha256,
        size_bytes=size_bytes,
        original_path=str(preserved_path),
        created_at=now,
    )

    dataset_id: str | None = None
    document_id: str | None = None
    dataset_summary: dict[str, Any] | None = None
    document_summary: dict[str, Any] | None = None

    # ★ "落不落成数据集"由解析器决定（它才知道这张表过没过资格判定），这里只问结果。
    if parsed.has_tables:
        dataset_id, dataset_summary = _materialize_dataset(
            connection, parsed=parsed, source_file_id=source_file_id,
            sha256=sha256, size_bytes=size_bytes, filename=filename,
            source_type=source_type, preserved_path=preserved_path, name=name, now=now,
        )
    if parsed.has_document:
        document_id, document_summary = _materialize_document(
            connection, parsed=parsed, source_file_id=source_file_id,
            filename=filename, source_type=source_type, now=now,
        )

    revision = store.next_revision(connection, sha256)
    import_id = state.new_id("imp")
    store.insert_import(
        connection,
        import_id=import_id, source_file_id=source_file_id, source_filename=filename,
        source_sha256=sha256, source_type=source_type, imported_at=now,
        dataset_id=dataset_id, document_id=document_id, status=STATUS_SUCCESS,
        error_code=None, parser_version=PARSER_VERSION, revision=revision,
        note="；".join(parsed.notes[:5]),
    )
    if dataset_id:
        store.update_dataset_import_id(connection, dataset_id, import_id)
    if document_id:
        store.update_document_import_id(connection, document_id, import_id)

    counts = store.successful_import_count(connection, source_file_id)
    return {
        "import_id": import_id,
        "source_file_id": source_file_id,
        "source_filename": filename,
        "source_sha256": sha256,
        "source_type": source_type,
        "parser_version": PARSER_VERSION,
        "revision": revision,
        "status": STATUS_SUCCESS,
        "status_label": "已入库",
        "idempotent": False,
        "imported_at": now,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "dataset_count": counts["dataset_count"],
        "document_count": counts["document_count"],
        "table_count": len(parsed.tables),
        "datasets": [dataset_summary] if dataset_summary else [],
        "documents": [document_summary] if document_summary else [],
        "notes": list(parsed.notes),
        "summary_text": _summary_text(
            filename, counts["document_count"], counts["dataset_count"], len(parsed.tables)
        ),
        "original_path": str(preserved_path),
        "original_kept": True,
    }


def _summary_text(filename: str, documents: int, datasets: int, tables: int) -> str:
    """UI 上那一句"这份文件导成了什么"（评审 #3 点名，不许写成笼统的"导入成功"）。

    ★ 措辞说明：一个文件 = **一个 dataset container**（多 Sheet / 多张表是它下面的多张表，
      评审 §8⑥ 明确不许拆成多个独立数据源）。所以"几个数据集"与"几张表"是两个数，
      两个都报出来 —— 只报其中一个都会让用户对不上账。
    """
    parts: list[str] = []
    if documents:
        parts.append(f"{documents} 个文档资料")
    if datasets:
        table_note = f"（含 {tables} 张表）" if tables > 1 else ""
        parts.append(f"{datasets} 个数据集{table_note}")
    detail = " + ".join(parts) if parts else "0 条内容"
    return f"{filename} → 已导入 {detail}"


def _materialize_dataset(
    connection: Any,
    *,
    parsed: parsers.ParsedFile,
    source_file_id: str,
    sha256: str,
    size_bytes: int,
    filename: str,
    source_type: str,
    preserved_path: Path,
    name: str | None,
    now: str,
) -> tuple[str, dict[str, Any]]:
    """把 ParsedFile 里的表物化成一个 dataset（多张表都在它下面）。"""
    dataset_id = state.new_id("ds")
    tables_payload: list[tuple[parsers.ParsedTable, list[str], list[dict], list[dict]]] = []
    total_rows = 0
    total_bytes = 0
    all_profiles: list[dict[str, Any]] = []

    for ordinal, table in enumerate(parsed.tables, start=1):
        if len(table.rows) > MAX_TABLE_ROWS:                 # 双保险（parsers 已经拦过）
            raise ImporterError(
                "too_many_rows",
                f"「{table.display_name}」有 {len(table.rows):,} 行，超过 {MAX_TABLE_ROWS:,} 行上限。",
            )
        names, rows, profiles = normalize.normalize_table(table.columns, table.rows)
        payloads = [store.serialize_row(row) for row in rows]
        total_bytes += sum(len(payload) for payload in payloads)
        total_rows += len(rows)
        for profile in profiles:
            profile["table_name"] = table.name
        all_profiles.extend(profiles)
        tables_payload.append((table, payloads, profiles, names))

    db.ensure_capacity(total_bytes + size_bytes)

    dimension_hits = regions.detect_dimensions(all_profiles)
    region_hits = [hit for hit in dimension_hits if hit["category"] == "region"]
    region_keys = {(hit["table_name"], hit["field"]): hit["key"] for hit in dimension_hits}

    first_table_columns = tables_payload[0][3] if tables_payload else []
    field_map = dataset_registry.guess_mapping(first_table_columns)
    for guess, column in _measure_field_map(first_table_columns).items():
        field_map.setdefault(guess, column)

    date_start, date_end = _date_bounds(all_profiles)

    store.insert_dataset(connection, {
        "dataset_id": dataset_id,
        "source_file_id": source_file_id,
        "import_id": None,
        "name": (name or "").strip() or Path(filename).stem or "未命名数据源",
        "source_type": source_type,
        "table_count": len(tables_payload),
        "row_count": total_rows,
        "column_count": len(first_table_columns),
        "date_start": date_start,
        "date_end": date_end,
        "region_dimensions": [
            {"key": hit["key"], "field": hit["field"], "label": hit["label"],
             "table": hit["table_name"], "dimension_label": hit["dimension_label"]}
            for hit in region_hits
        ],
        "field_map": field_map,
        "materialized_at": now,
        "parser_version": PARSER_VERSION,
        "status": STATUS_MATERIALIZED,
        "analysis_enabled": True,          # ★ 已物化 = 这批数据**能**参与确定性计算
        "meta": {
            "source_filename": filename,
            "original_path": str(preserved_path),
            "original_sha256": sha256,
            "materialized_at": now,
            "parser_version": PARSER_VERSION,
            "detected_dimensions": dimension_hits,
            "notes": list(parsed.notes),
        },
    })

    for ordinal, (table, payloads, profiles, names) in enumerate(tables_payload, start=1):
        store.insert_dataset_table(connection, {
            "table_id": state.new_id("tb"),
            "dataset_id": dataset_id,
            "table_name": table.name,
            "display_name": table.display_name,
            "ordinal": ordinal,
            "row_count": len(payloads),
            "column_count": len(names),
        })
        store.insert_dataset_columns(connection, [
            {
                "column_id": state.new_id("col"),
                "dataset_id": dataset_id,
                "table_name": table.name,
                "ordinal": index + 1,
                "name": profile["name"],
                "inferred_type": profile["inferred_type"],
                "non_null": profile["non_null"],
                "null_count": profile["null_count"],
                "distinct_count": profile["distinct_count"],
                "samples": profile["samples"],
                "region_key": region_keys.get((table.name, profile["name"])),
            }
            for index, profile in enumerate(profiles)
        ])
        store.insert_dataset_rows(connection, dataset_id, table.name, payloads)

    record = store.get_dataset(connection, dataset_id) or {}
    return dataset_id, _dataset_summary(record)


def _measure_field_map(columns: list[str]) -> dict[str, str]:
    """识别"金额/数量/单价/日期"这几列（**只认列名**，不做任何换算与计算）。

    它存在的理由：按地区看销售额时，计算层要知道"用哪一列当金额"。识别不出来时
    计算层会退到"数量 × 单价"（见 regions_query.py），再不行就明确报错 —— 不猜。
    """
    mapping: dict[str, str] = {}
    normalized = {_norm(column): column for column in columns}
    for key, guesses in (
        ("sales_amount", _SALES_AMOUNT_GUESSES),
        ("quantity", _QUANTITY_GUESSES),
        ("unit_price", _UNIT_PRICE_GUESSES),
        ("date", _DATE_GUESSES),
    ):
        for guess in guesses:
            # ★ 候选词也要按同一套规则规范化（"Invoice Date" 要能与 invoicedate 对上），
            #   否则"日期这列能不能认出来"就取决于候选词里写的是哪种拼法。
            hit = normalized.get(_norm(guess))
            if hit is not None:
                mapping[key] = hit
                break
    return mapping


def _norm(name: str) -> str:
    return "".join(str(name).strip().lower().split()).replace("_", "").replace("-", "")


def _date_bounds(profiles: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """数据集的时间范围（从**日期类型的列**里取样例的最小/最大值）。

    为什么只从 samples 取：samples 是数据字典里给"看到全貌"用的前几个不同值，
    真正的全量边界会在计算时由 pandas 算（那里才是唯一算数的地方）。这里只做展示。
    """
    dates: list[str] = []
    for profile in profiles:
        if str(profile.get("inferred_type")) != normalize.TYPE_DATE:
            continue
        for value in profile.get("samples") or []:
            text = str(value)[:10]
            try:
                _dt.date.fromisoformat(text)
            except ValueError:
                continue
            dates.append(text)
    if not dates:
        return None, None
    return min(dates), max(dates)


def _dataset_summary(record: dict[str, Any]) -> dict[str, Any]:
    """数据集在导入回执里的样子（业务字段 + 地区维度，审计字段另放 `audit`）。"""
    meta = record.get("meta") or {}
    return {
        "dataset_id": record.get("dataset_id"),
        "name": record.get("name"),
        "row_count": record.get("row_count"),
        "table_count": record.get("table_count"),
        "column_count": record.get("column_count"),
        "date_range": {"start": record.get("date_start"), "end": record.get("date_end")},
        "status": record.get("status"),
        "status_label": STATUS_LABEL_MATERIALIZED,
        "region_dimensions": record.get("region_dimensions") or [],
        "field_map": record.get("field_map") or {},
        "materialized_at": record.get("materialized_at"),
        "audit": {
            "source_file_id": record.get("source_file_id"),
            "original_path": meta.get("original_path"),
            "original_sha256": meta.get("original_sha256"),
            "parser_version": record.get("parser_version"),
        },
    }


def _materialize_document(
    connection: Any,
    *,
    parsed: parsers.ParsedFile,
    source_file_id: str,
    filename: str,
    source_type: str,
    now: str,
) -> tuple[str, dict[str, Any]]:
    """把正文物化成一条 document。"""
    document_id = state.new_id("doc")
    text = parsed.document_text
    title = _display_title(parsed, filename)
    store.insert_document(connection, {
        "document_id": document_id,
        "source_file_id": source_file_id,
        "import_id": None,
        "filename": filename,
        "source_type": source_type,
        "title": title,
        "text": text,
        "char_count": len(text),
        "block_count": int(parsed.document_meta.get("blocks") or 0),
        "imported_at": now,
        "parser_version": PARSER_VERSION,
        "meta": {**parsed.document_meta, "notes": list(parsed.notes)},
    })
    return document_id, {
        "document_id": document_id,
        "filename": filename,
        "title": title,
        "char_count": len(text),
        "source_type": source_type,
        "imported_at": now,
    }


# ════════════════════════════════════════════════════════════════════════
# 幂等回执 / 失败案底 / 原文件保留
# ════════════════════════════════════════════════════════════════════════
def _idempotent_receipt(connection: Any, previous: dict[str, Any]) -> dict[str, Any]:
    """幂等命中时的回执：**返回既有 import_id**，一个字节都不重复写。"""
    counts = store.successful_import_count(connection, str(previous["source_file_id"]))
    dataset = (
        store.get_dataset(connection, str(previous["dataset_id"]))
        if previous.get("dataset_id") else None
    )
    document = (
        store.get_document(connection, str(previous["document_id"]))
        if previous.get("document_id") else None
    )
    tables = store.list_dataset_tables(connection, str(previous["dataset_id"])) \
        if previous.get("dataset_id") else []
    return {
        "import_id": previous["import_id"],
        "source_file_id": previous["source_file_id"],
        "source_filename": previous["source_filename"],
        "source_sha256": previous["source_sha256"],
        "source_type": previous["source_type"],
        "parser_version": previous["parser_version"],
        "revision": previous["revision"],
        "status": STATUS_SKIPPED,
        "status_label": "已导入（未重复入库）",
        "idempotent": True,
        "imported_at": previous["imported_at"],
        "dataset_id": previous.get("dataset_id"),
        "document_id": previous.get("document_id"),
        "dataset_count": counts["dataset_count"],
        "document_count": counts["document_count"],
        "table_count": len(tables),
        "datasets": [_dataset_summary(dataset)] if dataset else [],
        "documents": [{
            "document_id": document["document_id"],
            "filename": document["filename"],
            "title": document.get("title") or "",
            "char_count": document.get("char_count"),
            "source_type": document.get("source_type"),
            "imported_at": document.get("imported_at"),
        }] if document else [],
        "notes": ["这份文件上次已经导入过（内容与解析版本都没变），这次没有重复入库。"],
        "summary_text": _summary_text(
            str(previous["source_filename"]), counts["document_count"],
            counts["dataset_count"], len(tables),
        ),
        "original_path": (dataset or {}).get("meta", {}).get("original_path", ""),
        "original_kept": True,
    }


def _record_failure(
    *, sha256: str, filename: str, suffix: str, source_type: str,
    size_bytes: int, error: ImporterError,
) -> None:
    """解析失败留一条**案底**（status=failed + error_code），不产生任何 dataset/document。

    为什么值得留：用户第二天问"我那份表呢"，翻到这条记录就知道是"编码认不出来"还是
    "行数超限"。没有它，失败就只剩浏览器里的一句提示。
    ★ 留案底失败本身也不许影响主流程（吞掉异常）—— 案底是为了排障，不是为了再制造一个故障。
    """
    try:
        with db.transaction() as connection:
            source_file_id = _source_file_id_for(sha256)
            store.upsert_source_file(
                connection, source_file_id=source_file_id, filename=filename,
                source_type=source_type, sha256=sha256, size_bytes=size_bytes,
                original_path="", created_at=state.now_iso(),
            )
            store.insert_import(
                connection,
                import_id=state.new_id("imp"), source_file_id=source_file_id,
                source_filename=filename, source_sha256=sha256, source_type=source_type,
                imported_at=state.now_iso(), dataset_id=None, document_id=None,
                status=STATUS_FAILED, error_code=error.code,
                parser_version=PARSER_VERSION,
                revision=store.next_revision(connection, sha256),
                note=error.message[:500],
            )
    except Exception:                                          # noqa: BLE001 —— 见上
        pass


def _preserve_original(path: Path, sha256: str, filename: str) -> Path:
    """把原文件复制到 `data/original/`（评审 #6：原文件 = 证据，物化后仍然保留）。

    同名冲突用 sha 前 12 位打头区分 —— 两份同名不同内容的文件必须各留一份。
    """
    target = db.original_dir() / f"{sha256[:12]}_{_safe_stem(filename)}"
    if not target.exists():
        shutil.copy2(path, target)
    return target


# ════════════════════════════════════════════════════════════════════════
# 导入前预览（FR-003E：识别到的格式 / 字段 / 落成什么 / 地区候选）
# ════════════════════════════════════════════════════════════════════════
def preview_file(path: str | Path, *, filename: str | None = None,
                 suffix: str | None = None) -> dict[str, Any]:
    """**只看不导入**：格式 / 会不会落成文档与数据集 / 字段与类型 / 地区字段候选。

    刻意不写任何库：用户还没确认，系统就不该产生"一份数据源"这种事实。
    """
    target = Path(path)
    display_name = filename or target.name
    actual_suffix = str(suffix if suffix is not None else target.suffix).lower()
    source_type = source_type_of(actual_suffix)
    if source_type is None:
        raise ImporterError(
            "unsupported_type",
            f"不支持的文件类型：{actual_suffix or '(无后缀)'}。支持的是 {sorted(SOURCE_TYPES)}。",
        )
    parsers.verify_file(target, actual_suffix)
    parsed = parsers.parse_file(target, actual_suffix, verify=False)

    table_views: list[dict[str, Any]] = []
    all_hits: list[dict[str, Any]] = []
    for table in parsed.tables:
        names, rows, profiles = normalize.normalize_table(table.columns, table.rows)
        hits = regions.detect_dimensions(profiles)
        for hit in hits:
            all_hits.append({**hit, "table": table.name})
        table_views.append({
            "table_name": table.name,
            "display_name": table.display_name,
            "source_ref": table.source_ref,
            "row_count": len(rows),
            "column_count": len(names),
            "columns": [
                {"name": profile["name"], "type": profile["inferred_type"],
                 "type_label": profile["type_label"], "non_null": profile["non_null"],
                 "null_count": profile["null_count"], "distinct": profile["distinct_count"],
                 "samples": profile["samples"][:3],
                 "region_key": next((hit["key"] for hit in hits
                                     if hit["field"] == profile["name"]), None)}
                for profile in profiles
            ],
            "preview_rows": rows[:5],
            "notes": table.notes,
        })

    region_hits = [hit for hit in all_hits if hit["category"] == "region"]
    return {
        "filename": display_name,
        "suffix": actual_suffix,
        "source_type": source_type,
        "source_type_label": SOURCE_TYPES[actual_suffix]["label"],
        "will_create_document": parsed.has_document,
        "will_create_dataset": bool(parsed.tables),
        "document_chars": len(parsed.document_text),
        "document_title": _display_title(parsed, display_name),
        "table_count": len(parsed.tables),
        "tables": table_views,
        "region_dimensions": region_hits,
        "other_dimensions": [hit for hit in all_hits if hit["category"] != "region"],
        "has_region": bool(region_hits),
        "notes": list(parsed.notes),
    }


__all__ = [
    "STATUS_LABEL_MATERIALIZED",
    "STATUS_MATERIALIZED",
    "import_bytes",
    "import_file",
    "preview_file",
    "safe_stem",
    "sha256_of_file",
    "summary_text",
]

#: 对外别名（HTTP 层要用这两个；私有名不跨模块引用 —— 见 api_imports.py 的调用处）
safe_stem = _safe_stem
summary_text = _summary_text
