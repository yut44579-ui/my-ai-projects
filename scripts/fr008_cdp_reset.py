"""fr008_cdp_reset.py · FR-008 浏览器走查（scripts/fr008_region_chat_cdp.mjs）的沙箱重置。

    .venv/Scripts/python.exe scripts/fr008_cdp_reset.py

干三件事，**全部只动 `outputs/_cdp_run_fr008/` 这一个沙箱目录**：

    ① 清掉上一轮的账号、物化库、原文件副本、上传/文档目录 —— 让走查每次都是从零开始；
    ② 重新生成走查用的样例表（带「省份」列的销售数据，评审 §五 A 段那三行）；
    ③ **重新建好 SQLite 的表**（原因见 fr003_cdp_reset.py 里那段说明：
       服务起着的时候删库文件不会重建表，下一次请求会连上新建的空库）。

★ 与 FR-003 那份的差别只有两处：沙箱目录名、样例内容（这里是"聊天里问地区"用的一份带省份的表）。
  其余逐字照旧 —— 两个走查共用同一套"起服务 → 重置沙箱 → 开浏览器"的做法，不另造一套。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SANDBOX = PROJECT_ROOT / "outputs" / "_cdp_run_fr008"

#: 走查用的样例：一行表头 + 评审给的三行（广东100 / 浙江200 / 江苏300）
FIXTURES: dict[str, str] = {
    "地区销售.csv": "省份,销售额\n广东,100\n浙江,200\n江苏,300\n",
}


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

    # ② 重新生成样例表
    for filename, text in FIXTURES.items():
        (SANDBOX / filename).write_text(text, encoding="utf-8")

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
    print(f"  样例：{sorted(FIXTURES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
