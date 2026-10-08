"""fr009a_cdp_reset.py · FR-009-A 浏览器走查（scripts/fr009a_visibility_cdp.mjs）的沙箱重置。

    .venv/Scripts/python.exe scripts/fr009a_cdp_reset.py

只动 `outputs/_cdp_run_fr009a/` 这一个沙箱目录（**真实 state/ 与真实 data/app.db 永远不碰**）：

    ① 清掉上一轮的账号 / 物化库 / 原文件副本 / 上传目录 / 文档目录；
    ② 生成两份样例文件：
         · `AI游戏制作知识库.pdf` —— **标题是 UTF-16 误读串**的那份（复现用户实测的乱码）；
         · `打不开的.pdf`        —— 后缀是 .pdf、内容不是 PDF（走"失败的导入也要留记录"那条路）；
    ③ 重新建表（`init_schema` 是幂等的）。

★ 为什么第 ③ 步必须在这里做：`init_schema()` 只在服务**启动**时跑一次。服务起着的时候
  把库文件删掉，服务不会重建它，下一次请求会连上一个新建的空文件 → 满屏 "no such table"。
  （这一条是从 `fr003_cdp_reset.py` 抄来的教训，不是猜的。）

★ 那份 PDF 的造法**复用 tests/ 里已经验收过的最小 PDF 生成器**（`build_minimal_pdf`）——
  不为了造一份样例文件再写一套 PDF 拼装代码（那种代码写第二遍就会和第一遍不一样）。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SANDBOX = PROJECT_ROOT / "outputs" / "_cdp_run_fr009a"

#: 样例 PDF 里那份**被 UTF-16 误读的标题**（用户实测那份的成因，一字不改的形态）
PDF_TITLE = "Quarterly Report 2011"

#: 文件名叫这个 —— **故意与标题不一样**：这样"页面上显示的标题"就不可能是文件名兜底
#: 兜出来的那一个，只能是真从文件里解出来的（证据更强）。
PDF_FILENAME = "季度资料.pdf"


def _write_utf16_title_pdf(path: pathlib.Path) -> None:
    """造一份首页首行是 UTF-16 误读串的 PDF（正文只能用 ASCII，见 build_minimal_pdf 的说明）。

    ★ 标题只用 ASCII：PDF 标准字体的字面串按 Adobe StandardEncoding 解，个别字节
      （如 0xC6 → ˘）会被映成另一个码位、字节还原不回来 —— 那种标题的正确答案是
      "退回文件名"（A-5 的第 5 步，测试里单独钉着）。而 ASCII 的 UTF-16 字节不受影响，
      于是这一份能真正演示"**解回来**"那条路。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    from test_documents import build_minimal_pdf

    mangled = PDF_TITLE.encode("utf-16-be").decode("latin-1")
    path.write_bytes(build_minimal_pdf([f"{mangled}\nBody text in plain ascii. 12345"]))


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    # ① 清沙箱里的运行数据（账号 / 库 / 副本 / 上传 / 文档），但保留目录本身
    for name in ("state", "original", "uploads", "documents", "outputs"):
        target = SANDBOX / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        (SANDBOX / f"app.db{suffix}").unlink(missing_ok=True)
    # 沙箱**根目录**下上一轮留下的样例文件也要清掉（只清上面那几个子目录的话，
    # 改过名字的旧样例会一直留在那儿，走查脚本又会去挑它）
    for stale in SANDBOX.glob("*"):
        if stale.is_file() and stale.suffix.lower() in (".pdf", ".xlsx", ".csv", ".md", ".pptx", ".docx"):
            stale.unlink()

    # ② 样例文件
    _write_utf16_title_pdf(SANDBOX / PDF_FILENAME)
    (SANDBOX / "打不开的.pdf").write_bytes(b"%PDF-1.4\n% this is not really a pdf\n")

    # ③ 把环境指到沙箱，然后幂等建表
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
