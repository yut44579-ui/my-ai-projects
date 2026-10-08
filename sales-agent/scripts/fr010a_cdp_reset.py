"""fr010a_cdp_reset.py · FR-010-A 浏览器走查（scripts/fr010a_answer_shape_cdp.mjs）的沙箱重置。

    .venv/Scripts/python.exe scripts/fr010a_cdp_reset.py

只动 `outputs/_cdp_run_fr010a/` 这一个沙箱目录（**真实 state/ 与真实 data/app.db 永远不碰**）：

    ① 清掉上一轮的账号 / 物化库 / 原文件副本 / 上传目录 / 文档目录；
    ② 生成一份样例 PDF（文件名与标题**故意不同**，用来验证 A3 报出来的
       文件名是真的文件名，不是拿标题顶的）；
    ③ 重新建表（`init_schema` 是幂等的）。

★ 与 `fr009a_cdp_reset.py` 的关系：那个脚本清的是 FR-009-A 自己的沙箱，
  本脚本清 FR-010-A 的，两份互不干扰。**造 PDF 的部分不重复写** ——
  与它一样调用 `tests/test_documents.py::build_minimal_pdf`（已经验收过的那一个），
  不为了造样例再写第三套 PDF 拼装代码。

★ 为什么建表必须在**服务起来之前**做：`init_schema()` 只在服务启动时跑一次。
  服务跑着的时候把库文件删掉，服务不会重建它，下一次请求会连上一个新建的空文件
  → 满屏 "no such table"（这条教训是从 fr003/fr009a 抄来的，不是猜的）。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SANDBOX = PROJECT_ROOT / "outputs" / "_cdp_run_fr010a"

#: 样例 PDF 的文件名（**故意与标题不同**：这样 A3 报出来的文件名不可能是标题顶替的）
PDF_FILENAME = "季度复盘资料.pdf"
#: 样例 PDF 的首页标题（顺带覆盖 FR-009-A 的标题修复那条路）
PDF_TITLE = "Quarterly Review 2011"


def _write_pdf(path: pathlib.Path) -> None:
    """造一份最小合法 PDF（正文只用 ASCII，见 build_minimal_pdf 的说明）。"""
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    from test_documents import build_minimal_pdf

    path.write_bytes(build_minimal_pdf([f"{PDF_TITLE}\nPlain ascii body for char counting. 12345"]))


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    for name in ("state", "original", "uploads", "documents", "outputs"):
        target = SANDBOX / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (SANDBOX / f"app.db{suffix}").unlink(missing_ok=True)
    for stale in SANDBOX.glob("*"):
        if stale.is_file() and stale.suffix.lower() in (".pdf", ".xlsx", ".csv", ".md", ".pptx", ".docx"):
            stale.unlink()

    _write_pdf(SANDBOX / PDF_FILENAME)

    os.environ.update({
        "SRA_STATE_DIR": str(SANDBOX / "state"),
        "SRA_DB_PATH": str(SANDBOX / "app.db"),
        "SRA_ORIGINAL_DIR": str(SANDBOX / "original"),
        "SRA_UPLOAD_DIR": str(SANDBOX / "uploads"),
        "SRA_DOC_DIR": str(SANDBOX / "documents"),
        "SRA_OUTPUT_DIR": str(SANDBOX / "outputs"),
    })
    from app.importer import db

    db.init_schema()
    print(f"沙箱已重置：{SANDBOX}")
    print(f"  库表：{db.table_names()}")
    print(f"  样例：{[item.name for item in sorted(SANDBOX.glob('*.pdf'))]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
