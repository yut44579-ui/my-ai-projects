"""启动脚本。

★ 为什么要有这个脚本，而不是让人随手敲 uvicorn：
  `store.py` 的写入用**进程内锁**（`_LOCK`），这套东西只在单进程下安全。
  两个 worker 同时"读-改-写" gap_item 的计数会互相覆盖，
  现场表现是"待补充问题的次数凭空少了" —— 最难查的那种问题。

  所以把 `workers=1` 写死在代码里，而不是写在 README 里靠人记得。

★ 另外：本项目的降级/熔断档位状态**落库不落内存**（见 D14），
  所以将来真要多实例时，改的是"换共享存储"，不是"加 worker"。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from app import store  # noqa: E402


def main() -> None:
    # ★ Windows 控制台默认 GBK，打印非 GBK 字符（比如 ⚠）会直接抛异常把服务搞死。
    #   被重定向到文件时更容易踩到。所以这里显式改成 utf-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="AI客服 API（单进程）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--reload", action="store_true", help="开发时热重载（仍是单进程）")
    args = ap.parse_args()

    store.init_db()
    print(
        f"AI客服 -> http://{args.host}:{args.port}（单进程 workers=1）\n"
        f"  控制台：http://{args.host}:{args.port}/\n"
        f"  接口文档：http://{args.host}:{args.port}/docs\n"
        f"  [注意] 状态落盘是 SQLite + 进程内锁，不支持多进程"
    )
    uvicorn.run(
        "app.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,  # ★ 写死，别改
        log_level="info",
    )


if __name__ == "__main__":
    main()
