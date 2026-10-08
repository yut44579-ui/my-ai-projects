"""导入编排：preview（只解析不入库）与 commit（真入库）。

铁律（TASK-001 §三、§五）：
  · preview / commit 的 sha256 必须一致 —— 两次上传的不是同一份文件就拒绝
  · 同一 sha256 重复提交 → 拒绝并提示「该文件已导入过，batch_id=N」
  · 计数恒等式 rows_total = rows_created + rows_deduplicated + rows_skipped（代码里断言守住）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import CustomerSourceType
from app.models.import_batch import BatchSourceType, ImportBatch, ImportBatchStatus
from app.services.dedupe import ImportContext, RowIssue, resolve_customer, validate_row
from app.services.errors import ImportErrorCode, ImportFailure
from app.services.mapping import (
    FIELD_LABELS,
    TARGET_FIELDS,
    auto_map_columns,
    resolve_mapping,
)
from app.services.tabular import TableData, parse_table, sha256_of

# 每处理这么多行 flush 一次（★ 绝不逐行 commit：会把 500 行的导入拖成分钟级）
FLUSH_EVERY = 500


@dataclass
class CommitResult:
    batch: ImportBatch
    skipped_reasons: list[dict] = field(default_factory=list)
    skipped_columns: list[dict] = field(default_factory=list)
    # 去重冲突（供人工看）：{"customer_id": 新客户, "conflict_with": [既有客户 id], "kind": ...}
    conflicts: list[dict] = field(default_factory=list)


def preview_import(
    raw: bytes,
    filename: str,
    *,
    header_row: int = 1,
    sheet: str | None = None,
) -> dict:
    """只解析、不入库。返回列名 + 自动映射 + 置信度 + 前 3 行样本 + 编码/sheet/表头行号/sha256。"""
    table = parse_table(raw, filename, header_row=header_row, sheet=sheet)
    columns = auto_map_columns(table.headers)  # 别名冲突在此报 ambiguous_mapping
    digest = sha256_of(raw)

    sample_rows = [
        {header: row[index] for index, header in enumerate(table.headers)}
        for row in table.rows[:3]
    ]

    return {
        "filename": filename,
        "file_sha256": digest,
        "file_kind": table.file_kind,
        "encoding": table.encoding,
        "encoding_note": table.encoding_note,
        "sheet_names": table.sheet_names,
        "sheet_used": table.sheet_used,
        "sheet_note": table.sheet_note,
        "header_row": table.header_row,
        "ignored_leading_rows": table.ignored_leading_rows,
        "headers": table.headers,
        "rows_total": len(table.rows),
        "columns": [
            {"column": c.column, "target": c.target, "confidence": c.confidence} for c in columns
        ],
        "unmapped_columns": [c.column for c in columns if c.target is None],
        "sample_rows": sample_rows,
        "target_fields": [
            {"key": key, "label": FIELD_LABELS[key]} for key in TARGET_FIELDS
        ],
    }


def _parse_source_type(value: str | None) -> BatchSourceType:
    if value is None:
        return BatchSourceType.TEST
    try:
        return BatchSourceType(value.strip().upper())
    except ValueError as exc:
        allowed = " / ".join(item.value for item in BatchSourceType)
        raise ImportFailure(
            ImportErrorCode.INVALID_MAPPING, f"source_type 只能是 {allowed}，收到 {value!r}"
        ) from exc


def commit_import(
    db: Session,
    raw: bytes,
    filename: str,
    *,
    sha256: str | None = None,
    source_type: str | None = None,
    mapping: list[dict] | None = None,
    header_row: int = 1,
    sheet: str | None = None,
    encoding: str | None = None,
) -> CommitResult:
    """真正入库。文件级失败抛 ImportFailure（零入库）。"""
    actual_sha = sha256_of(raw)

    # ★ sha256 校验：防止预览与提交之间文件被换
    if sha256 and sha256.strip().lower() != actual_sha:
        raise ImportFailure(
            ImportErrorCode.SHA256_MISMATCH,
            "上传文件与预览时的不是同一份（sha256 不一致），请重新预览后再提交",
            detail={"expected": sha256.strip().lower(), "actual": actual_sha},
        )

    # ★ 幂等：同一份文件不允许导入两次
    existing = db.execute(
        select(ImportBatch).where(ImportBatch.file_sha256 == actual_sha)
    ).scalars().first()
    if existing is not None:
        raise ImportFailure(
            ImportErrorCode.DUPLICATE_IMPORT,
            f"该文件已导入过，batch_id={existing.id}",
            http_status=409,
            detail={"batch_id": existing.id, "imported_at": existing.imported_at.isoformat()},
        )

    batch_source = _parse_source_type(source_type)

    # 解析（文件级失败在这里抛出：empty_file / unsupported_format / too_large /
    # too_many_rows / parse_error / ambiguous_mapping）
    table: TableData = parse_table(
        raw, filename, header_row=header_row, sheet=sheet, encoding=encoding
    )
    by_target, skipped_columns = resolve_mapping(table.headers, mapping)

    batch = ImportBatch(
        filename=filename,
        file_sha256=actual_sha,
        status=ImportBatchStatus.SUCCESS,  # 处理完按实际结果改写
        source_type=batch_source,
        rows_total=len(table.rows),
        rows_created=0,
        rows_deduplicated=0,
        rows_skipped=0,
        skipped_reasons_json=[],
        skipped_columns_json=skipped_columns,
        evidence_ref="",  # 拿到 id 后立刻用代码生成（禁止手填）
    )
    db.add(batch)
    db.flush()  # 需要 id 才能生成 evidence_ref / 给客户挂 batch_id
    batch.evidence_ref = f"import_batch:{batch.id}"

    context = ImportContext(
        batch_id=batch.id,
        source_type=CustomerSourceType(batch_source.value),
    )

    skipped_reasons: list[dict] = []
    conflicts: list[dict] = []
    created = deduplicated = 0

    for offset, raw_row in enumerate(table.rows):
        source_row = header_row + offset + 1  # 文件里的真实行号，方便用户回 Excel 定位
        fields = {
            target: (raw_row[index] if index < len(raw_row) else "")
            for target, index in by_target.items()
        }

        row, issue = validate_row(fields, source_row)
        if issue is not None:
            skipped_reasons.append(issue.to_json())
        else:
            assert row is not None  # validate_row 保证二选一
            result = resolve_customer(row, db, context)
            if result.action == "created":
                created += 1
                if result.conflict_ids:
                    conflicts.append(
                        {
                            "customer_id": result.customer.id if result.customer else None,
                            "conflict_with": result.conflict_ids,
                            "kind": "multiple_matches",
                        }
                    )
                elif result.dedupe_state.value == "PENDING_REVIEW":
                    conflicts.append(
                        {
                            "customer_id": result.customer.id if result.customer else None,
                            "conflict_with": [],
                            "kind": "no_match_key",
                        }
                    )
            else:
                deduplicated += 1

        if (offset + 1) % FLUSH_EVERY == 0:
            db.flush()

    rows_skipped = len(skipped_reasons)
    rows_total = len(table.rows)
    # ★ 计数恒等式：不成立说明代码有 bug，宁可报错也不许编数据
    if rows_total != created + deduplicated + rows_skipped:
        raise RuntimeError(
            f"计数恒等式被破坏：rows_total={rows_total} != "
            f"created={created} + dedup={deduplicated} + skipped={rows_skipped}"
        )

    batch.rows_created = created
    batch.rows_deduplicated = deduplicated
    batch.rows_skipped = rows_skipped
    batch.skipped_reasons_json = skipped_reasons
    batch.status = ImportBatchStatus.PARTIAL if rows_skipped else ImportBatchStatus.SUCCESS

    db.commit()
    db.refresh(batch)

    return CommitResult(
        batch=batch,
        skipped_reasons=skipped_reasons,
        skipped_columns=skipped_columns,
        conflicts=conflicts,
    )


__all__ = ["CommitResult", "FLUSH_EVERY", "commit_import", "preview_import"]
