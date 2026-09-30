"""test_loader_disk_cache.py · FR-011 ① 验收：Excel → DataFrame 的**落盘缓存**。

════════════════════════════════════════════════════════════════════════
【这个文件钉的是什么】
════════════════════════════════════════════════════════════════════════
派单 §一 的验收 2/3/4/5/6 —— 一句话判据是：

    **缓存命中与不命中，返回的 DataFrame 必须逐位一致**（行数 / 列序 / 每列 dtype /
    索引 / 抽样 100 行逐值）。

所以本文件里最硬的一条是 `test_命中缓存与未命中逐位一致`：
  · 先拿"独立的一次 read_excel"当基准（**就是被替换掉的那一句原样实现**）；
  · 再走 `load_raw()`（冷：真解析 + 落盘）；
  · 然后把 `pd.read_excel` 换成一个**会抛错的探针**再走一次 —— 这一趟要是还能拿到
    同一张表，就说明它真的来自磁盘缓存，一个字都没读 Excel；
  · 最后把基准与命中结果逐列 dtype、逐行索引、前 100 行逐值比一遍。

另外几条同样是派单点名的：SHA256 不匹配照旧抛错、坏缓存被拒并回退重建、
形状不符照旧抛错（伪造缓存也绕不过）、`SRA_DATA_CACHE=0` 完全走原路径、
缓存落在 state/ 下且已被 gitignore。

【为什么必须跑真实数据、真实缓存】
不是 mock：读的是 `data/Online Retail.xlsx`（用 tests/conftest.py 的测试提速缓存，
真正解析只发生一次），写的是真 pickle 文件，比的是真 DataFrame。
本文件**一个业务数字都没写死**：所有期望值都来自那次独立 read_excel。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from app.engine import loader  # noqa: E402

#: 被替换掉的那一句原样实现（基准的唯一来源 —— 期望值全部由它现算）
ORIGINAL_READ = {"engine": "openpyxl", "parse_dates": ["InvoiceDate"]}


@pytest.fixture(scope="module")
def cache_home(tmp_path_factory: pytest.TempPathFactory):
    """本模块的缓存落盘根目录；**跑完就删**（别把一个 36MB 的 pkl 留在临时目录里）。"""
    root = tmp_path_factory.mktemp("loader_disk_cache")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def isolated(cache_home, tmp_path, monkeypatch):
    """每个用例一个**全新的** state 目录（= 全新缓存目录），并用完清掉进程内缓存。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SRA_STATE_DIR", str(state_dir))
    monkeypatch.delenv("SRA_DATA_CACHE", raising=False)
    loader._CACHE.clear()
    yield state_dir
    loader._CACHE.clear()


def _baseline() -> pd.DataFrame:
    """基准 = 原来那一句 read_excel 的结果（**独立算一遍**，不是从缓存里抄）。"""
    return pd.read_excel(loader.DEFAULT_DATA_PATH, **ORIGINAL_READ)


def _assert_bitwise_same(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    """"逐位一致"的判据：行数 / 列序 / 每列 dtype / 索引 / 抽样 100 行逐值。"""
    assert actual.shape == expected.shape
    assert list(actual.columns) == list(expected.columns), "列序必须一致"
    assert [str(dtype) for dtype in actual.dtypes] == [str(dtype) for dtype in expected.dtypes], \
        "每列 dtype 必须一致"
    assert actual.index.equals(expected.index), "索引必须一致"
    pd.testing.assert_frame_equal(actual, expected, check_exact=True, check_dtype=True)

    sample_rows = 100
    pd.testing.assert_frame_equal(actual.head(sample_rows), expected.head(sample_rows), check_exact=True)
    for row in range(sample_rows):
        for column in expected.columns:
            got, want = actual.at[row, column], expected.at[row, column]
            if pd.isna(want):
                assert pd.isna(got), f"第 {row} 行 {column}：期望空值，实际 {got!r}"
            else:
                assert got == want and type(got) is type(want), \
                    f"第 {row} 行 {column}：期望 {want!r}（{type(want).__name__}），实际 {got!r}（{type(got).__name__}）"


def _boom(*args, **kwargs):
    raise AssertionError("这一步不该再读 Excel —— 说明缓存没有命中")


def _count_read_excel(monkeypatch) -> dict:
    """把 pd.read_excel 包一层计数（**不改行为**，只数它被真正调了几次）。"""
    real = pd.read_excel
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(pd, "read_excel", wrapper)
    return calls


def _cache_files(sha256: str) -> tuple[Path, Path]:
    return loader._cache_paths(sha256)


# ════════════════════════════════════════════════════════════════════════
# ① 核心：命中与不命中逐位一致（验收 2）
# ════════════════════════════════════════════════════════════════════════
def test_命中缓存与未命中逐位一致(isolated, monkeypatch) -> None:
    expected = _baseline()
    sha256 = loader.sha256_of_file(loader.DEFAULT_DATA_PATH)
    payload, meta_path = _cache_files(sha256)

    assert not payload.exists(), "这个用例应当从**没有缓存**开始"

    cold = loader.load_raw()                                  # 冷：真解析 + 落盘
    assert payload.is_file() and meta_path.is_file(), "冷启动应当把解析结果落成缓存"
    _assert_bitwise_same(cold, expected)

    loader._CACHE.clear()                                     # 抹掉进程内缓存，逼它走磁盘
    monkeypatch.setattr(pd, "read_excel", _boom)              # 探针：命中就不许再读 Excel
    hit = loader.load_raw()
    _assert_bitwise_same(hit, expected)


def test_缓存元信息记全了失效所需的每一项(isolated) -> None:
    loader.load_raw()
    sha256 = loader.sha256_of_file(loader.DEFAULT_DATA_PATH)
    _, meta_path = _cache_files(sha256)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert meta["format"] == loader._CACHE_FORMAT_VERSION
    assert meta["parse_fingerprint"] == loader._PARSE_FINGERPRINT
    assert meta["source_sha256"] == sha256
    assert meta["shape"] == list(loader.EXPECTED_SHAPE)
    assert meta["columns"] == ["InvoiceNo", "StockCode", "Description", "Quantity",
                               "InvoiceDate", "UnitPrice", "CustomerID", "Country"]
    assert meta["dtypes"]["Quantity"] == "int64"
    assert meta["dtypes"]["InvoiceDate"] == "datetime64[us]"
    assert meta["index"] == ["RangeIndex", 0, loader.EXPECTED_SHAPE[0], 1]


def test_读取参数指纹变了就不再命中(isolated, monkeypatch) -> None:
    """参数/格式指纹进键：变一个字节 → 旧文件不再是这一版的缓存，必须重解析。"""
    loader.load_raw()
    loader._CACHE.clear()
    monkeypatch.setattr(loader, "_PARSE_FINGERPRINT", "另一个指纹")
    calls = _count_read_excel(monkeypatch)

    loader.load_raw()

    assert calls["n"] == 1, "指纹变了必须重新解析，不许拿旧缓存顶替"


# ════════════════════════════════════════════════════════════════════════
# ② 坏缓存被拒 + 回退重建（验收 4）
# ════════════════════════════════════════════════════════════════════════
def _corrupt(payload: Path, meta_path: Path, kind: str, good: pd.DataFrame, sha256: str) -> None:
    if kind == "garbage":                     # 写了一半 / 被人动过：整块不是 pickle
        payload.write_bytes(b"this is not a pickle at all")
    elif kind == "truncated":                 # 只剩前 4096 字节（典型的写一半崩溃）
        payload.write_bytes(payload.read_bytes()[:4096])
    elif kind == "wrong_shape":               # 内容自洽但形状不对（少行）
        good.head(10).to_pickle(payload)
        meta_path.write_text(json.dumps(loader._cache_meta(payload, sha256, good.head(10)),
                                        ensure_ascii=False), encoding="utf-8")
    elif kind == "wrong_dtype":               # 形状对、dtype 被改过（Quantity 降成 int32）
        altered = good.copy()
        altered["Quantity"] = altered["Quantity"].astype("int32")
        altered.to_pickle(payload)
        # 元信息**不动**：读回来的 dtype 与元信息对不上 → 拒
    else:                                     # pragma: no cover - 用例写错了才会到这
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["garbage", "truncated", "wrong_shape", "wrong_dtype"])
def test_坏缓存被拒并回退重建(isolated, monkeypatch, kind: str) -> None:
    sha256 = loader.sha256_of_file(loader.DEFAULT_DATA_PATH)
    payload, meta_path = _cache_files(sha256)
    good = _baseline()

    loader.load_raw()                                  # 先有一份好缓存
    assert payload.is_file()
    _corrupt(payload, meta_path, kind, good, sha256)

    loader._CACHE.clear()
    calls = _count_read_excel(monkeypatch)
    again = loader.load_raw()

    assert calls["n"] == 1, f"{kind}：坏缓存必须被拒并**真正重解析一次**"
    _assert_bitwise_same(again, good)

    # 重建后的缓存必须是好的：再走一遍磁盘（Excel 探针）还要能命中
    loader._CACHE.clear()
    monkeypatch.setattr(pd, "read_excel", _boom)
    _assert_bitwise_same(loader.load_raw(), good)


def test_伪造成形状相符的缓存也绕不过形状闸(isolated) -> None:
    """形状闸在 load_raw() 里（读少列/少行必须停）—— 缓存层被绕过去也拦得住。"""
    small = Path(str(isolated)) / "small.xlsx"
    small.parent.mkdir(parents=True, exist_ok=True)
    # 列名与冻结数据集**完全相同**（否则连 parse_dates 那一步都过不去，测的就不是形状闸了），
    # 只把行数砍到 2 行 → 走的正是"读少了必须停"那条路。
    columns = ["InvoiceNo", "StockCode", "Description", "Quantity",
               "InvoiceDate", "UnitPrice", "CustomerID", "Country"]
    pd.DataFrame([["536365", "85123A", "WHITE HANGING HEART", 6,
                   pd.Timestamp("2010-12-01 08:26:00"), 2.55, 17850.0, "United Kingdom"],
                  ["536366", "71053", "WHITE METAL LANTERN", 2,
                   pd.Timestamp("2010-12-01 08:28:00"), 3.39, 17850.0, "United Kingdom"]],
                 columns=columns).to_excel(small, index=False, engine="openpyxl")

    with pytest.raises(loader.DataSourceError) as first:
        loader.load_raw(small, verify=False)
    assert "形状与预期不符" in str(first.value)

    # 手搓一份"形状像样、列数与 EXPECTED_SHAPE 相同"的缓存喂给它
    sha256 = loader.sha256_of_file(small)
    payload, meta_path = _cache_files(sha256)
    payload.parent.mkdir(parents=True, exist_ok=True)
    forged = pd.DataFrame({f"column_{index}": [1] for index in range(8)})
    forged.to_pickle(payload)
    meta_path.write_text(json.dumps(loader._cache_meta(small, sha256, forged), ensure_ascii=False),
                         encoding="utf-8")

    with pytest.raises(loader.DataSourceError) as second:
        loader.load_raw(small, verify=False)
    assert "形状与预期不符" in str(second.value), "伪造缓存不许让它悄悄返回一张别的表"


# ════════════════════════════════════════════════════════════════════════
# ③ SHA256 校验不因缓存而放松（验收 3）
# ════════════════════════════════════════════════════════════════════════
def test_SHA256不匹配时仍然抛错且不落缓存(isolated) -> None:
    bogus = Path(str(isolated)) / "bogus.xlsx"
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_bytes(b"not the frozen snapshot")

    with pytest.raises(loader.DataSourceError) as error:
        loader.load_raw(bogus)

    message = str(error.value)
    assert "数据源 SHA256 不匹配，已停止计算（环境/数据错误）" in message
    assert loader.EXPECTED_SHA256 in message
    assert not list(loader._cache_dir().glob("*")) if loader._cache_dir().exists() else True, \
        "校验没过就不该有任何缓存产物"


# ════════════════════════════════════════════════════════════════════════
# ④ 安全阀：SRA_DATA_CACHE=0 完全走原路径（验收 5）
# ════════════════════════════════════════════════════════════════════════
def test_安全阀关闭时既不读也不写缓存(isolated, monkeypatch) -> None:
    expected = _baseline()
    monkeypatch.setenv("SRA_DATA_CACHE", "0")
    calls = _count_read_excel(monkeypatch)

    frame = loader.load_raw()

    assert calls["n"] == 1, "安全阀关闭时必须走原来的那一句 read_excel"
    _assert_bitwise_same(frame, expected)
    assert not loader._cache_dir().exists(), "安全阀关闭时一个缓存文件都不许写"


def test_缓存目录跟着_SRA_STATE_DIR_走(isolated) -> None:
    """（同时是"测试不会往真实 state/ 里写"的证据：缓存目录永远在隔离目录下）"""
    assert loader._cache_dir() == Path(str(isolated)) / "cache"
    assert loader._cache_dir() != PROJECT_ROOT / "state" / "cache"


# ════════════════════════════════════════════════════════════════════════
# ⑤ 仍然返回副本（缓存命中也不例外）
# ════════════════════════════════════════════════════════════════════════
def test_命中缓存时返回的仍是副本(isolated, monkeypatch) -> None:
    loader.load_raw()                                  # 建缓存
    loader._CACHE.clear()
    monkeypatch.setattr(pd, "read_excel", _boom)       # 这一趟只能来自磁盘缓存

    first = loader.load_raw()
    first.loc[:, "Quantity"] = 0                       # 调用方原地改

    loader._CACHE.clear()
    second = loader.load_raw()
    assert (second["Quantity"] != 0).any(), "缓存里的那份被调用方改坏了（返回的应当是副本）"


# ════════════════════════════════════════════════════════════════════════
# ⑥ 缓存文件在 state/ 下且已被 gitignore（验收 6）
# ════════════════════════════════════════════════════════════════════════
def test_缓存目录在_state_下且被_gitignore忽略() -> None:
    probe = PROJECT_ROOT / "state" / "cache" / "raw-0123456789abcdef-0123456789.pkl"
    result = subprocess.run(
        ["git", "check-ignore", "-v", str(probe)],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"缓存文件没被 gitignore：{probe}"
    assert "state/" in result.stdout, result.stdout
