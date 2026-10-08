#!/usr/bin/env python
"""serve.py · 启动 API 服务（**单进程**，这是刻意的）。

    .venv/Scripts/python.exe scripts/serve.py            # 默认 127.0.0.1:8000
    .venv/Scripts/python.exe scripts/serve.py --port 9000

为什么要一个启动脚本，而不是让人随手敲 uvicorn 命令：
    `app/state.py` 的存储是 **JSON 文件 + 进程内锁**（D8 的 MySQL 落库推迟到 TASK-007，
    依据 D:\\GPT_Project_Reviews\\decisions\\003-task-layer.md）。这套东西**只在单进程下安全**：
    两个 worker 同时"读-改-写" tasks.json / executions.json 会互相覆盖，
    现场表现是"执行记录凭空少了几条" —— 最难查的那种问题。
    所以这里把 `workers=1` 写死在代码里（而不是写在 README 里靠人记得），
    谁想改多进程，先看到这句话、先去落库（TASK-007）。

    uvicorn 自己也不会重复加载：本项目没开 reload，也没有用 gunicorn 多 worker 包一层。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="sales-report-agent API 服务（单进程）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认只监听本机）")
    parser.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发时热重载（reload 也是单进程）")
    args = parser.parse_args()

    print(
        f"sales-report-agent API → http://{args.host}:{args.port}（单进程 workers=1）\n"
        f"  接口文档：http://{args.host}:{args.port}/docs\n"
        f"  健康检查：http://{args.host}:{args.port}/api/health\n"
        f"  ⚠️ 状态落盘是 JSON + 进程内锁，**不支持多进程**（见本文件头；MySQL 落库属 TASK-007）",
        flush=True,
    )
    uvicorn.run(
        "app.api:app",
        host=args.host,
        port=args.port,
        workers=1,          # ← 写死：JSON 落盘的并发安全性只保证单进程（见文件头）
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
