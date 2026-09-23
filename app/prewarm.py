"""prewarm.py · 冷启动预热（服务启动时在**后台线程**里把数据集读进进程缓存）。

════════════════════════════════════════════════════════════════════════
【为什么需要这个文件】
════════════════════════════════════════════════════════════════════════
服务重启后**第一次提问要等约 100 秒** —— 全部卡在 `loader.load_raw()` 第一次读
22MB Excel 上（读完之后热态只要 7~10 秒，缓存已建好）。用户等的是"第一问"，
不是"服务启动"，所以把这一次读盘**提前到启动时，并且放到后台线程里**：

    · 提前     → 用户第一问不再干等（100 秒从"用户等待"变成"服务启动时后台做"）
    · 后台线程 → 不阻塞应用就绪：`/api/health` 与静态页面立刻可用，服务不会"起不来"
    · 不改确定性 → 就是提前调用同一个 `tools.warm_up()`（内部还是那个
      `loader.load_raw()` + 进程级 `_CACHE`），读表/口径/计算的代码路径一个字没动 ——
      预热与否，算出来的数必须**逐位相同**（测试里钉了这一点）

线程安全的边界（说清楚，免得被当成隐患）：预热线程与某个"同一时刻打进来"的请求
有可能同时读盘，最坏情况是白读一遍 22MB；`_CACHE[key] = df` 是幂等的赋值，
谁后写都一样，不会算出两个不同的结果。

════════════════════════════════════════════════════════════════════════
【为什么不写进 /api/health，也不新增状态端点】
════════════════════════════════════════════════════════════════════════
`/api/health` 是**冻结的 Legacy Contract**（12 个端点之一，响应形状是逐键断言过的），
往里加字段就是改形状。而预热已经把"等 100 秒"这件事从用户身上挪走了 ——
不需要再让客户端轮询一个"数据好了没"的状态位。要看状态的话，本模块的
`STATE` 就是给测试与日志用的观察点（不进任何响应体）。
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any, AsyncIterator

# 观察点：预热跑到哪一步了（**只在进程内**，不进任何 HTTP 响应）
STATE: dict[str, Any] = {
    "started": False,
    "done": False,
    "report": None,     # tools.warm_up() 的返回值（行数/边界/币种/耗时）
    "error": None,      # 预热失败的原因（失败不影响服务：第一问会自己现读）
}

# 启动日志用的 logger（uvicorn 的日志级别下 INFO 默认不显示，只留痕不刷屏）
LOGGER_NAME = "sra.prewarm"


def _say(message: str) -> None:
    """往启动日志写一行（**失败也不许抛**：stdout 编码/关闭都会让 print 炸）。"""
    try:
        print(message, flush=True)
    except Exception:
        pass


def _run() -> None:
    """后台线程主体：预热 → 记录结果。**任何异常都不许炸到服务**。"""
    import logging

    from app.ai import tools

    logger = logging.getLogger(LOGGER_NAME)
    try:
        report = tools.warm_up()
    except Exception as exc:            # 预热失败绝不影响服务可用性：第一问会自己现读
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("数据集预热失败（不影响服务：首次提问会现读）: %s", STATE["error"])
        _say(f"[预热] 失败（不影响服务，首次提问会现读）：{STATE['error']}")
    else:
        STATE["report"] = report
        logger.info("数据集预热完成：%s", report)
        _say(
            f"[预热] 数据集已读入缓存：{report['rows']} 行 × {report['column_count']} 列，"
            f"覆盖 {report['first_day']} ~ {report['last_day']}，"
            f"币种 {report['currency']}，耗时 {report['seconds']} 秒"
        )
    finally:
        STATE["done"] = True


_THREAD: threading.Thread | None = None


def start_background() -> threading.Thread:
    """起一个 daemon 线程做预热（**同一个进程只起一次**，重复调用返回已有线程）。"""
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    if _THREAD is None:
        _THREAD = threading.Thread(target=_run, name="dataset-prewarm", daemon=True)
        STATE["started"] = True
        _THREAD.start()
    return _THREAD


@contextlib.asynccontextmanager
async def lifespan(_app: Any) -> AsyncIterator[None]:
    """FastAPI 的 lifespan：**只做一件事** —— 启动后台预热线程，然后放行。

    特意不在这里 await 预热结果：等了就等于把 100 秒加到启动上，
    服务要等数据读完才肯接受连接 —— 那比"第一问慢"更糟。
    """
    start_background()
    yield
