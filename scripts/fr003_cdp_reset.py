"""fr003_cdp_reset.py · FR-003 浏览器走查（scripts/fr003_import_cdp.mjs）的沙箱重置。

    .venv/Scripts/python.exe scripts/fr003_cdp_reset.py

干三件事，**全部只动 `outputs/_cdp_run_fr003/` 这一个沙箱目录**：

    ① 清掉上一轮的账号、物化库、原文件副本、上传/文档目录 —— 让走查每次都是从零开始；
    ② 重新生成两份样例表（一份只有 Country、一份带省份）；
    ③ **重新建好 SQLite 的表**。

★ 为什么第 ③ 步必须在这里做，而不是让走查脚本直接删 `app.db`：
  `init_schema()` 只在服务**启动**时跑一次。服务起着的时候把库文件删掉，服务不会重建它；
  下一次请求会连上一个**新建的空文件**，于是满屏 "no such table: datasets"。
  所以重置要连"建表"一起做完（`db.init_schema()` 是幂等的），服务的下一次请求就能看到新表 ——
  服务每一次请求都是新开一个连接（见 `app/importer/db.py::connect`），不需要重启。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SANDBOX = PROJECT_ROOT / "outputs" / "_cdp_run_fr003"

#: 走查用的两份样例（一份没有地区字段、一份有）
FIXTURES: dict[str, dict] = {
    "国家分布.xlsx": {"Country": ["United Kingdom", "Germany", "France"],
                      "销售额": [100, 200, 300]},
    "地区销售.xlsx": {"省份": ["广东", "浙江", "江苏"],
                      "销售额": [100, 200, 300]},
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
    import pandas as pd

    for filename, columns in FIXTURES.items():
        pd.DataFrame(columns).to_excel(SANDBOX / filename, index=False)

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
