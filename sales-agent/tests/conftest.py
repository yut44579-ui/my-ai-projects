"""tests/conftest.py · 测试提速基建（TASK-002 提速项）

════════════════════════════════════════════════════════════════════════
【为什么要这个文件】
════════════════════════════════════════════════════════════════════════
全量 pytest 的绝大部分时间**不是花在"算"上**，而是花在反复解析那一个 22MB 的
`data/Online Retail.xlsx` 上。三处各自解析、彼此不共享：

  ① `app/api.py::_read_table`      —— 每次上传都要把落盘的**临时副本**重新 read_excel 一遍；
  ② `app/engine/loader.py::load_raw` —— 缓存键是**文件路径**，而测试里每个临时副本的名字都不同 → 永不命中；
  ③ `tests/test_executor.py` 的独立 Oracle —— 按设计它**必须**自己 read_excel 一遍（这是它的价值）。

于是同一个字节完全相同的数据被解析 N 次，每次约一分钟。

════════════════════════════════════════════════════════════════════════
【怎么提速（只动测试侧，被测代码一行不改）】
════════════════════════════════════════════════════════════════════════
把 `pandas.read_excel` 包一层**以"文件内容 SHA256 + 读取参数"为键**的缓存：

    · 命中内存缓存            → 直接返回副本
    · 命中磁盘缓存（pkl）     → 读 pkl（秒级）
    · 都没命中                → 走真正的 read_excel，并把结果落成 pkl 给下次用

内容相同的文件（上传副本 / 冻结快照 / Oracle 读的同一份）在整机范围内只会被真正解析
**一次**；之后每次都是读缓存。缓存落在 `data/.cache/`（已 gitignore，不是源码）。

━━ 为什么是 pickle，不是 parquet（这是个踩过的坑，别再改回去）━━
第一版写的是 parquet，结果是**磁盘缓存一次都没命中过**：`Online Retail.xlsx` 的
`InvoiceNo` 列里既有 `C536379` 这种取消单号、又有纯数字，pandas 读出来是 object 列，
而 pyarrow 写 parquet 时要把 object 列定成一个具体类型 → 直接抛

    ArrowInvalid: Could not convert 'C536379' with type str: tried to convert to int64

于是每次都是"真正解析 90 多秒 → 写缓存失败 → 下次再解析 90 多秒"，只落了个内存缓存
（进程内有效，跨进程等于没有）。改成 pickle 后：**dtype 原样往返**，不猜类型、不丢精度、
不做任何类型推断 —— 缓存回来的 DataFrame 与当初 read_excel 出来的**逐列 dtype 一致**。
这正是本文件开头承诺的"只改数据怎么进内存，不改数据是什么"。

（pickle 的通用风险在这里不成立：缓存是本地测试自用、内容是我们自己那张表的副本，
 且键里带源文件全量 SHA256；不接收外部输入。）

为什么键要带上"读取参数"：`loader.load_raw` 读的时候 `parse_dates=InvoiceDate`，
而 `_read_table` / Oracle 不解析日期 —— 两者的结果 DataFrame **不是同一张表**，
必须分开缓存，否则就是"把没解析日期的表喂给需要日期的那条路径"。

════════════════════════════════════════════════════════════════════════
【提速的边界（这些是真工作量，不许也不能省）】
════════════════════════════════════════════════════════════════════════
真实指标计算、Excel 渲染、读回自检、真 HTTP 调用、真落盘 —— 一律照跑，一秒不省。
**断言逻辑一个字没改**：本文件只改"数据怎么进内存"，不改"数据是什么"。
返回的一律是 **副本**（缓存里那份只读使用），避免用例之间互相污染。
缓存坏了/写不进去都只是"退回原速"，不影响任何一条断言。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

import pandas as pd
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / ".cache"

# 小文件本来就只有几十毫秒，缓存是噪音（这一层只服务那一个 22MB 的大表）
MIN_CACHE_BYTES = 4 * 1024 * 1024

# 逃生舱：SRA_TEST_CACHE=0 时完全不插手，用于排查"是不是缓存在作怪"
ENABLED = os.environ.get("SRA_TEST_CACHE", "1") != "0"

_original_read_excel = pd.read_excel
_memory: dict[str, pd.DataFrame] = {}
_stats = {"hit_memory": 0, "hit_disk": 0, "parsed": 0, "bypassed": 0, "parse_seconds": 0.0}


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _variant_of(kwargs: dict) -> str:
    """读取参数指纹：不同参数读出来的不是同一张表，必须分开缓存。"""
    payload = json.dumps(
        {key: str(value) for key, value in sorted(kwargs.items())},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def _cache_key(path, kwargs: dict) -> str | None:
    """算出缓存键；不值得/不能缓存时返回 None（立刻退回原函数）。"""
    target = pathlib.Path(str(path))
    try:
        if not target.is_file() or target.stat().st_size < MIN_CACHE_BYTES:
            return None
    except OSError:
        return None
    return f"{_sha256(target)[:16]}-{_variant_of(kwargs)}"


def _paths(key: str) -> tuple[pathlib.Path, pathlib.Path]:
    return CACHE_DIR / f"{key}.pkl", CACHE_DIR / f"{key}.meta.json"


def _disk_cache_is_sane(key: str) -> bool:
    """缓存体必须配一份元信息（全量 SHA256 / 形状），缺一不可信。"""
    payload, meta = _paths(key)
    return payload.is_file() and meta.is_file()


def _read_meta(key: str) -> dict | None:
    """读元信息（读不出来就当没有 —— 缓存是加速件，不是真相来源）。"""
    try:
        return json.loads(_paths(key)[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_disk_cache(key: str, frame: pd.DataFrame, kwargs: dict, path) -> None:
    payload, meta = _paths(key)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = payload.with_suffix(".pkl.tmp")
        frame.to_pickle(tmp)
        tmp.replace(payload)                      # 原子替换：并发跑测试不会读到半截文件
        meta.write_text(
            json.dumps(
                {
                    "source": str(path),
                    "source_sha256": _sha256(pathlib.Path(str(path))),
                    "read_kwargs": {key_: str(value) for key_, value in sorted(kwargs.items())},
                    "shape": list(frame.shape),
                    "columns": [str(column) for column in frame.columns],
                    "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:                      # 落盘失败只是"下次还得再解析一遍"，不影响正确性
        print(f"[提速] 磁盘缓存落盘失败（不影响正确性，退回原速）：{exc!r}")


def _cached_read_excel(path=None, *args, **kwargs):
    """pandas.read_excel 的缓存外壳：内容相同 → 只真正解析一次。"""
    if not ENABLED or args or path is None:
        _stats["bypassed"] += 1
        return _original_read_excel(path, *args, **kwargs)

    key = _cache_key(path, kwargs)
    if key is None:
        _stats["bypassed"] += 1
        return _original_read_excel(path, *args, **kwargs)

    cached = _memory.get(key)
    if cached is not None:
        _stats["hit_memory"] += 1
        return cached.copy()

    if _disk_cache_is_sane(key):
        try:
            frame = pd.read_pickle(_paths(key)[0])
            # 读到形状对不上的缓存（写了一半 / 被人动过）→ 当作没有缓存，重新解析。
            # 宁可多花 90 秒，也不能把一张形状不对的表悄悄喂给测试 —— 那才是真正危险的"假通过"。
            meta = _read_meta(key)
            if meta and list(frame.shape) != meta.get("shape"):
                raise ValueError(f"缓存形状与元信息不符：{list(frame.shape)} != {meta.get('shape')}")
            _stats["hit_disk"] += 1
            _memory[key] = frame
            return frame.copy()
        except Exception as exc:
            print(f"[提速] 磁盘缓存读失败，改为重新解析：{exc!r}")

    started = time.perf_counter()
    frame = _original_read_excel(path, *args, **kwargs)
    elapsed = time.perf_counter() - started
    _stats["parsed"] += 1
    _stats["parse_seconds"] += elapsed
    print(f"\n[提速] 真正解析一次大表：{pathlib.Path(str(path)).name} "
          f"shape={frame.shape} 用时 {elapsed:.1f}s（其余调用将命中缓存）")
    _memory[key] = frame
    _write_disk_cache(key, frame, kwargs, path)
    return frame.copy()


if ENABLED:
    # 必须在测试模块被 import（收集阶段）之前就装上：测试模块里 `import pandas as pd`
    # 拿到的是同一个模块对象，属性查找发生在调用时，所以这里是生效的。
    pd.read_excel = _cached_read_excel


# ══════════════════════════════════════════════════════════════════════════
# ★ FR-003F：**全局测试隔离**（不是"某个测试文件记得隔离"，而是"谁都别想污染真实目录"）
# ══════════════════════════════════════════════════════════════════════════
# 为什么要有这一条：本项目**已经犯过这个错** —— e2e 把大量提问写进了真实的历史记录
# （评审 §8 原文：「数据库使用独立测试路径，测试不得修改真实 SRA_STATE_DIR」，
#   并点名"这是 Hermes 已经犯过的错（607 条测试污染）"）。
# 之前每个测试文件各自 monkeypatch，**漏一个文件就是一个污染源**，而且漏不漏没人查。
#
# 所以这里改成**反向的默认值**：整个测试会话一开始就把六个路径指到临时目录，
# 任何测试（包括将来新写的、忘了写隔离的）都跑不到真实 state/ 与真实 data/app.db。
# 单个测试仍可用 monkeypatch.setenv 覆盖成自己更细的临时目录（那是缩小范围，不是放开）。
#
# ★ 只设**默认值**：已经有人在环境里显式指定的值不覆盖（尊重调用方的意图）。
_ISOLATED_PATHS: dict[str, str] = {}


@pytest.fixture(scope="session", autouse=True)
def _isolate_sra_paths(tmp_path_factory: pytest.TempPathFactory) -> None:
    """把状态目录 / 数据库 / 各类落盘目录全部指到**会话级临时目录**。"""
    root = tmp_path_factory.mktemp("sra_isolation")
    defaults = {
        "SRA_STATE_DIR": root / "state",
        "SRA_UPLOAD_DIR": root / "uploads",
        "SRA_DOC_DIR": root / "documents",
        "SRA_OUTPUT_DIR": root / "outputs",
        # FR-003B：物化库与原文件副本 —— 不隔离这一条，测试就会往真实的 data/app.db 里写
        "SRA_DB_PATH": root / "app.db",
        "SRA_ORIGINAL_DIR": root / "original",
    }
    for key, path in defaults.items():
        if not os.environ.get(key):
            os.environ[key] = str(path)
        _ISOLATED_PATHS[key] = os.environ[key]
    for path in (root / "state", root / "uploads", root / "documents", root / "original"):
        path.mkdir(parents=True, exist_ok=True)
    yield


def isolated_paths() -> dict[str, str]:
    """本会话实际用的隔离路径（给"证明没写真实目录"的测试当证据用）。"""
    return dict(_ISOLATED_PATHS)


@pytest.fixture(scope="session", autouse=True)
def _report_speedup() -> None:
    """收尾打印缓存命中情况（给人看的证据，不是断言）。"""
    yield
    if not ENABLED:
        print("\n[提速] SRA_TEST_CACHE=0：本run 未启用缓存")
        return
    average = _stats["parse_seconds"] / _stats["parsed"] if _stats["parsed"] else 0.0
    print(
        "\n[提速] read_excel 缓存统计："
        f"命中内存 {_stats['hit_memory']} 次 / 命中磁盘 {_stats['hit_disk']} 次 / "
        f"真正解析 {_stats['parsed']} 次（平均 {average:.1f}s）/ 未插手 {_stats['bypassed']} 次"
    )
