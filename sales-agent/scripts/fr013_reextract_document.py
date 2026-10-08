"""scripts/fr013_reextract_document.py · FR-013 库内坏数据的**重抽修复**（一次性运维脚本）。

════════════════════════════════════════════════════════════════════════
【为什么需要它】
════════════════════════════════════════════════════════════════════════
FR-013 改的是 `app/engine/docs.py::extract_pdf` 的**提取逻辑**，但库里的正文是
**缺陷时期提出来、原样存进去的** —— 实测 `documents` 表里那一行：

    doc_df339e02ee88  AI游戏制作知识库.pdf   char_count=3295  其中 511 个 NUL

改解析器**不会自动改库里的旧数据**：`unified_documents.get_text()` 对 FR-003 的库内文档
是"直接把存的正文读出来"（不自称能重放），所以不重抽一遍，用户读到的还是那串乱码。

════════════════════════════════════════════════════════════════════════
【怎么做（复用既有能力，不另造一套）】
════════════════════════════════════════════════════════════════════════
  · 找哪些行要修：用**修好的那个模块自己的** `docs._looks_broken` 当判据 ——
    不另写一份"怎么算乱码"的规则（两份判据迟早不一致）
  · 重抽：调 `docs.extract(原件路径)`（原件路径取自 `source_files.original_path`）
  · 修标题：调既有的 `app.document_title.repair_title`（FR-009-A3 那个轮子，不重写）
  · 写回：**原地更新同一行**（document_id / source_file_id / import_id 都不动）——
    引用这份文档的地方（会话引用、详情页、检索）因此不用改一个字节
  · 审计：往该行 `meta` 里写 `fr013_reextracted_at` / `fr013_chars_before` 等字段，
    并在 stdout 打出**改前 / 改后对比**

★ 重抽之后正文**仍然可疑**的行：**不写库**，原样保留 + 打印原因。
  宁可留着旧的坏数据并让人看见，也不把一个"同样坏但看起来动过了"的结果写进去。

════════════════════════════════════════════════════════════════════════
【怎么跑】
════════════════════════════════════════════════════════════════════════
    .venv/Scripts/python.exe scripts/fr013_reextract_document.py            # 只看不改（dry-run）
    .venv/Scripts/python.exe scripts/fr013_reextract_document.py --apply     # 真写库

跑之前先备份：`cp data/app.db data/app.db.bak-$(date +%Y%m%d_%H%M%S)`
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import state  # noqa: E402
from app.document_title import repair_title  # noqa: E402
from app.engine import docs  # noqa: E402
from app.importer import db, store  # noqa: E402

FR013_MARK = "fr013_reextracted_at"


def _nul(text: str) -> int:
    return text.count("\x00")


def _dump_meta(meta: dict) -> str:
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def _source_file(connection, source_file_id: str) -> dict | None:
    return store.get_source_file(connection, source_file_id)


def repair(*, apply: bool) -> int:
    """扫一遍 documents 表，返回"需要修的行数"。"""
    with db.readonly() as connection:
        records, _total = store.list_documents(connection, limit=10_000)
        sources = {
            record["document_id"]: (
                store.get_source_file(connection, record["source_file_id"])
                if record.get("source_file_id") else None
            )
            for record in records
        }

    targets = [record for record in records if docs._looks_broken(record.get("text") or "")]
    print(f"documents 表共 {len(records)} 行；正文可疑（需重抽）{len(targets)} 行")
    if not targets:
        print("没有要修的行 —— 库里的正文都是干净的。")
        return 0

    for record in targets:
        document_id = record["document_id"]
        filename = record.get("filename") or ""
        before_text = record.get("text") or ""
        print("=" * 72)
        print(f"[{document_id}] {filename}")
        print(f"  改前：char_count={record.get('char_count')}  NUL={_nul(before_text)}")
        print(f"        标题={record.get('title')!r}")
        print(f"        正文前 60 字={before_text[:60]!r}")

        source = sources.get(document_id) or {}
        stored = str(source.get("original_path") or "")
        if not stored or not pathlib.Path(stored).is_file():
            print(f"  ✗ 原件不在（{stored or '库里没记原件路径'}）—— 跳开，不写库")
            continue

        try:
            extracted = docs.extract(stored, record.get("suffix") or pathlib.Path(stored).suffix)
        except docs.DocumentError as exc:
            print(f"  ✗ 重抽失败（{exc.code}）：{exc.message} —— 跳开，不写库")
            continue

        after_text = extracted["text"]
        if docs._looks_broken(after_text):
            print(f"  ✗ 重抽之后**仍然可疑**（NUL={_nul(after_text)}）—— 保留原行，不写库")
            continue

        title = repair_title(extracted.get("title", ""), filename)
        meta = {
            "chars": extracted["chars"],
            "blocks": extracted["blocks"],
            "block_unit": extracted["block_unit"],
            "outline": list(extracted.get("outline") or []),
            "warnings": list(extracted.get("warnings") or []),
            "extra": dict(extracted.get("extra") or {}),
            FR013_MARK: state.now_iso(),
            "fr013_chars_before": int(record.get("char_count") or 0),
            "fr013_nul_before": _nul(before_text),
        }
        print(f"  改后：char_count={len(after_text)}  NUL={_nul(after_text)}")
        print(f"        标题={title!r}")
        print(f"        正文前 60 字={after_text[:60]!r}")
        for warning in meta["warnings"]:
            print(f"        ⚠ {warning}")

        if not apply:
            print("  （dry-run：没有写库；加 --apply 才真写）")
            continue

        with db.transaction() as connection:
            connection.execute(
                """
                UPDATE documents
                   SET text = ?, title = ?, char_count = ?, block_count = ?, meta = ?
                 WHERE document_id = ?
                """,
                (after_text, title, len(after_text), int(extracted["blocks"]),
                 _dump_meta(meta), document_id),
            )
        print("  ✓ 已原地更新（document_id 不变）")

    return len(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FR-013：把库里正文是乱码的文档重抽一遍")
    parser.add_argument("--apply", action="store_true", help="真写库（不带就是 dry-run）")
    args = parser.parse_args(argv)
    print(f"数据库：{db.db_path()}")
    repair(apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
