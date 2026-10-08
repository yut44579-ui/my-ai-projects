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
import json
import os
import time
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


# ════════════════════════════════════════════════════════════════════════
# FR-011 ① · 把"Excel → DataFrame"这一步的结果落盘缓存（**唯一的改动点**）
# ════════════════════════════════════════════════════════════════════════
# 由来：读一次 22.6MB / 541,909 行实测约 250 秒（本文件顶上那句"约 108 秒"是旧机器上的旧数）。
# 每一次重启服务、每一个隔离验证实例都要重付一遍 —— 这是演示与迭代的最大障碍。
# 做法：把**解析结果本身**按"文件内容 + 读取参数 + 缓存格式版本"落到 state/cache/，
# 下次直接读回来（秒级），不再重新解析 Excel。
#
# 边界（写在这里免得被当成"顺手加的优化"）：
#   · 只动 load_raw() 里"怎么把 Excel 变成 DataFrame"那一件事（`_read_frame` 一个函数）；
#   · 缓存里存的**就是当初 read_excel 的结果**（pickle 原样往返：dtype 不猜、不转、不丢），
#     所以"命中"与"没命中"返回的 DataFrame 逐位一致（测试直接钉这一点）；
#   · 缓存只是加速件，不是真相来源：坏了/写不进去/版本对不上 → 一律退回原路径重解析；
#   · 它不参与任何口径、不做任何过滤与换算，也不改变"返回副本"与 SHA256 校验的语义。
#
# 为什么是 pickle 而不是 parquet（**别再改回去，这是个实测过的死路**）：
#   本表 `InvoiceNo` 既有 `C536379` 这种取消单号、又有纯数字（object 列里混着 str 与 int），
#   pyarrow 写 parquet 时要把 object 列定成一个具体类型 → 实测直接抛：
#       ArrowInvalid("Could not convert 'C536379' with type str: tried to convert to int64")
#   即便把列先转成字符串再写，读回来 dtype 也变了（object → str），抽样值也不再逐位相同；
#   而"命中与不命中必须逐位一致"是本 TASK 的硬判据 —— 做不到就不能上（见派单 §一）。
#   pickle 不做类型推断，读写往返 dtype 原样（tests/conftest.py 的测试提速缓存里
#   已经踩过同一个坑并写下了同样的结论，这里**复用它验证过的方案**，不另立一套）。
_CACHE_FORMAT_VERSION = "1"        # 缓存格式版本：格式一变，键就变 → 旧文件自然不命中
_CACHE_ENV = "SRA_DATA_CACHE"      # 安全阀：=0 时既不读也不写（排查"是不是缓存作怪"）
_STATE_DIR_ENV = "SRA_STATE_DIR"   # 与 app/state.py 同一个变量：缓存是运行期产物，跟 state/ 走
_CACHE_SUBDIR = "cache"
_CACHE_PREFIX = "raw"
_READ_ENGINE = "openpyxl"          # 唯一改动点用的引擎（指纹里带上它）


def _cache_disabled() -> bool:
    """安全阀：`SRA_DATA_CACHE=0` → 完全走原路径（读也不读、写也不写）。"""
    return os.environ.get(_CACHE_ENV, "1").strip() == "0"


def _cache_dir() -> Path:
    """缓存目录（运行期产物）：`<SRA_STATE_DIR 或 项目根>/state` 下的 `cache/`。

    为什么跟着 SRA_STATE_DIR 走：测试与脚本会把 state 目录隔离到临时目录，
    缓存必须一起走 —— 否则跑测试就会往仓库的真实 state/ 里写文件。
    """
    override = os.environ.get(_STATE_DIR_ENV)
    root = Path(override) if override else PROJECT_ROOT / "state"
    return root / _CACHE_SUBDIR


#: 读取参数指纹：读取参数变了（比如以后 PARSE_DATES 加了列），读出来的**不是同一张表**，
#: 必须分开缓存 —— 否则会把"没解析日期的表"喂给需要日期的那条路径。
_PARSE_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "engine": _READ_ENGINE,
            "parse_dates": [str(column) for column in PARSE_DATES],
            "format": _CACHE_FORMAT_VERSION,
        },
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()[:10]


def _cache_paths(sha256: str) -> tuple[Path, Path]:
    """缓存键 → （数据文件, 元信息文件）。键 = 源文件 SHA256 + 读取参数/格式指纹。"""
    stem = f"{_CACHE_PREFIX}-{sha256[:16]}-{_PARSE_FINGERPRINT}"
    directory = _cache_dir()
    return directory / f"{stem}.pkl", directory / f"{stem}.meta.json"


def _index_signature(frame: pd.DataFrame) -> list | None:
    """索引签名（`read_excel` 出来的恒是 RangeIndex；其它形态如实记 None = 不做索引对账）。"""
    index = frame.index
    if isinstance(index, pd.RangeIndex):
        return ["RangeIndex", int(index.start), int(index.stop), int(index.step)]
    return None


def _cache_meta(path: str | Path, sha256: str, frame: pd.DataFrame) -> dict:
    """元信息：把"这份缓存是谁、长什么样"记全 —— 命中时要拿它逐项对账。"""
    return {
        "format": _CACHE_FORMAT_VERSION,
        "parse_fingerprint": _PARSE_FINGERPRINT,
        "source": str(path),
        "source_sha256": sha256,               # 全量哈希（文件名里只有前 16 位）
        "shape": list(frame.shape),
        "columns": [str(column) for column in frame.columns],
        "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        "index": _index_signature(frame),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _say(message: str) -> None:
    """往启动日志写一行（**失败也不许抛** —— 缓存的问题绝不能影响主流程）。"""
    try:
        print(message, flush=True)
    except Exception:
        pass


def _read_from_cache(path: str | Path, sha256: str) -> pd.DataFrame | None:
    """命中且**可信**时返回当初解析的结果；任何一处对不上都返回 None（调用方重解析）。

    这不是"缓存优先"，是"缓存必须自证"：文件在、元信息在、格式与参数指纹一致、
    全量哈希一致、形状等于 EXPECTED_SHAPE、列序与每列 dtype 一致、索引签名一致
    —— 少一条就当没有缓存，宁可多等一次解析，也不把一张来路不明的表喂给计算。
    """
    payload, meta_path = _cache_paths(sha256)
    if not payload.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        meta.get("format") != _CACHE_FORMAT_VERSION
        or meta.get("parse_fingerprint") != _PARSE_FINGERPRINT
        or meta.get("source_sha256") != sha256
    ):
        return None
    try:
        frame = pd.read_pickle(payload)
    except Exception:
        return None
    if not isinstance(frame, pd.DataFrame):
        return None
    if list(frame.shape) != list(meta.get("shape", [])) or list(frame.shape) != list(EXPECTED_SHAPE):
        return None
    if [str(column) for column in frame.columns] != meta.get("columns"):
        return None
    if {str(column): str(dtype) for column, dtype in frame.dtypes.items()} != meta.get("dtypes"):
        return None
    if _index_signature(frame) != meta.get("index"):
        return None
    return frame


def _write_to_cache(path: str | Path, sha256: str, frame: pd.DataFrame) -> None:
    """把解析结果落盘（best-effort：写不进去只是"下次还得再解析一遍"）。

    顺序：数据文件先写成 `.tmp` 再**原子替换**，元信息后写（读的那一侧永远看不到半截文件；
    崩溃在两步之间只会留下"有数据没元信息"的残件，而那种残件在读取时会被直接拒掉）。
    """
    payload, meta_path = _cache_paths(sha256)
    # 临时文件名带上进程号：预热线程与"同一时刻打进来的请求"可能同时写同一个键
    # （见 prewarm.py 的线程边界说明）—— 各写各的临时文件，谁也踩不到谁。
    tmp_payload = payload.with_name(f"{payload.name}.{os.getpid()}.tmp")
    tmp_meta = meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")
    try:
        payload.parent.mkdir(parents=True, exist_ok=True)
        frame.to_pickle(tmp_payload)
        tmp_payload.replace(payload)
        tmp_meta.write_text(
            json.dumps(_cache_meta(path, sha256, frame), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_meta.replace(meta_path)
    except Exception as exc:
        _say(f"[FR-011] 解析缓存落盘失败（不影响正确性，退回原速）：{exc!r}")
        for leftover in (tmp_payload, tmp_meta):
            try:
                leftover.unlink()
            except OSError:
                pass


def _read_frame(path: str | Path) -> pd.DataFrame:
    """**本 TASK 唯一的改动点**：Excel → DataFrame 这一步（原本是就地一句 read_excel）。

    顺序：算源文件哈希 → 磁盘缓存命中就用它 → 否则真解析一遍（read_excel，与原来那一句
    逐字相同）→ 顺手把结果落盘。返回的 DataFrame 与原来那一句出来的**逐位一致**。
    """
    sha256 = None if _cache_disabled() else sha256_of_file(path)

    if sha256 is not None:
        cached = _read_from_cache(path, sha256)
        if cached is not None:
            return cached

    frame = pd.read_excel(path, engine=_READ_ENGINE, parse_dates=PARSE_DATES)

    # 形状不对的解析结果不落盘（写进去也永远不会被读回：读取时会因形状对账不过而被拒）；
    # "读少了必须停"那道闸仍然在 load_raw() 里，缓存绕不过它 —— 这里只是不落垃圾。
    if sha256 is not None and list(frame.shape) == list(EXPECTED_SHAPE):
        _write_to_cache(path, sha256, frame)
    return frame


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
        df = _read_frame(path)          # ★ FR-011：唯一改动的一行（原来是就地一句 read_excel）
        if df.empty:
            raise DataSourceError(f"读出的数据为空：{key}")
        # 哈希对得上但形状不对 = 解析出问题（读少列/少行），同样必须停下（D10）
        if df.shape != EXPECTED_SHAPE:
            raise DataSourceError(
                f"数据形状与预期不符，已停止计算：实际 {df.shape}，预期 {EXPECTED_SHAPE}"
            )
        _CACHE[key] = df

    # 返回副本：缓存对象只读使用，避免调用方原地修改（如新增列）后再被别的调用方看到
    return _CACHE[key].copy()
