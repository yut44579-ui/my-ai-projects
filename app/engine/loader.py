"""loader.py · 数据加载层：原始 Excel → pandas DataFrame（只做这一件事）。

职责边界（本 TASK 只到这一步）：
    ✅ 把文件读成 DataFrame
    ✅ 数据溯源校验（SHA256 + 行数/列数），不匹配就抛错、**不继续算**
    ❌ 不做任何口径过滤（时间区间、排除规则都在 metrics.py / executor.py）

为什么用 openpyxl 引擎读：
    xlsx 是压缩 xml，pandas 默认要选一个引擎解析；openpyxl 项目已装（写 Excel
    保样式也用它，见 D5），不额外引入依赖（CLAUDE.md 铁律 7）。

为什么要做 SHA256 校验：
    D10「失败不产出错误文档」——数据源变了（被改过/换过）还继续算，产出的报表
    数字就不可信。数据文件哈希对不上属于环境/数据错误，必须当场停下。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

# ── 路径 ────────────────────────────────────────────────────────────────
# 本文件位于 <项目根>/app/engine/loader.py，向上 3 层即项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 原始数据集（真实交易数据，54 万行；严禁修改 data/ 下的原始文件）
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "Online Retail.xlsx"

# ── 数据溯源 ────────────────────────────────────────────────────────────
# 期望哈希由 TASK-002A 指令第 7 节 AC-06 指定（= 冻结的数据快照标识）
EXPECTED_SHA256 = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"

# 期望形状（541,909 行 × 8 列）；与哈希一起作为数据快照的指纹
EXPECTED_SHAPE = (541909, 8)

# ── 字段名 ──────────────────────────────────────────────────────────────
# 时间字段固定为 InvoiceDate（下单时间），见 metrics.py 中 D16 第 1 条
TIME_COLUMN = "InvoiceDate"

# 读取时按时间字段解析（xlsx 里存的是日期时间，解析成 datetime64）
PARSE_DATES = [TIME_COLUMN]

# 分块读取哈希用的大小（23MB 文件，1MB 足够，避免一次性读进内存）
_HASH_CHUNK_SIZE = 1024 * 1024

# 进程内缓存：读一次 23MB xlsx 实测约 108 秒，任务/测试里会反复调用，
# 缓存后避免重复解析（键 = 文件绝对路径字符串，值 = DataFrame）。
# 注意：对外返回的是副本，调用方改 DataFrame 不会污染缓存。
_CACHE: dict[str, pd.DataFrame] = {}


class DataSourceError(RuntimeError):
    """数据源不可用（文件不存在 / 哈希不匹配 / 形状异常）。

    抛出即代表本次执行必须中止（D10：失败不产出错误文档）。
    """


def sha256_of_file(path: str | Path) -> str:
    """计算文件 SHA256（分块读，避免把 23MB 全读进内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def verify_data_source(path: str | Path = DEFAULT_DATA_PATH) -> dict:
    """校验数据源指纹，返回溯源信息（不抛错，由调用方决定怎么处理）。

    返回：{"path", "exists", "size_bytes", "sha256", "expected_sha256", "match"}
    """
    p = Path(path)
    if not p.exists():
        return {
            "path": str(p),
            "exists": False,
            "size_bytes": 0,
            "sha256": None,
            "expected_sha256": EXPECTED_SHA256,
            "match": False,
        }
    actual = sha256_of_file(p)
    return {
        "path": str(p),
        "exists": True,
        "size_bytes": p.stat().st_size,
        "sha256": actual,
        "expected_sha256": EXPECTED_SHA256,
        "match": actual == EXPECTED_SHA256,
    }


def load_raw(path: str | Path = DEFAULT_DATA_PATH, verify: bool = True) -> pd.DataFrame:
    """读取原始 Excel → DataFrame（返回副本，可安全修改）。

    参数：
        path    xlsx 路径，默认 data/Online Retail.xlsx
        verify  True 时先做 SHA256 校验，不匹配直接抛 DataSourceError（**不继续计算**）

    抛错：
        DataSourceError —— 文件不存在 / 哈希不匹配 / 读出来是空的
    """
    key = str(Path(path).resolve())

    if verify:
        info = verify_data_source(path)
        if not info["exists"]:
            raise DataSourceError(f"数据源文件不存在：{info['path']}")
        if not info["match"]:
            raise DataSourceError(
                "数据源 SHA256 不匹配，已停止计算（环境/数据错误）：\n"
                f"  实际：{info['sha256']}\n  期望：{info['expected_sha256']}"
            )

    if key not in _CACHE:
        df = pd.read_excel(path, engine="openpyxl", parse_dates=PARSE_DATES)
        if df.empty:
            raise DataSourceError(f"读出的数据为空：{key}")
        _CACHE[key] = df

    # 返回副本：缓存对象只读使用，避免调用方原地修改（如新增列）后再被别的调用方看到
    return _CACHE[key].copy()
