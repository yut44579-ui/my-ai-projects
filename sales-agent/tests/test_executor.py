"""tests/test_executor.py · TASK-002A 验收测试（AC-01..AC-07）。

════════════════════════════════════════════════════════════════════════
【独立 Oracle —— 本文件的第二套代码路径】
════════════════════════════════════════════════════════════════════════
被测实现：app/engine/executor.compute_sales_amount（走 loader + metrics，pandas 向量化）

Oracle 必须"真正独立"，本文件里的 Oracle 部分：
    · 自己定义 Excel 路径 / SHA256 / 起止日期常量（不从 app.engine.* 取）
    · 自己用 pandas.read_excel 读文件（不调用 app.engine.loader）
    · 自己写时间筛选用 **按日期比较** 的路径（实现走的是时间戳半开区间）
    · 自己写三条排除条件（Python 循环逐行判定，实现走的是 pandas 掩码）
    · 自己写金额公式与求和（不调用 app.engine.metrics）
即：**不 import app.engine 的任何常量、规则、字段定义或中间结果**。
唯一共享的是原始数据文件本身（这本来就是被验证的对象）。

口径依据：docs/DECISIONS.md 的 D16（TASK-002A 指令第 11 节原文）。
本文件的 Oracle 是按同一份 D16 清单**另写一遍**，不是复制实现代码。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import sys

# 让 `import app` 在 `python -m pytest` / 直接 `pytest` 两种方式下都成立
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.engine import executor  # noqa: E402  被测对象
from app.engine import metrics  # noqa: E402  仅 AC-07 用于"文档/符号"核对，Oracle 不使用

# ════════════════════════════════════════════════════════════════════════
# 独立 Oracle 定义区（在此区内不引用 app.engine.* 的任何东西）
# ════════════════════════════════════════════════════════════════════════

# Oracle 自己的常量（独立抄写 D16 口径 + AC-06 指定的数据指纹）
ORACLE_EXCEL_PATH = PROJECT_ROOT / "data" / "Online Retail.xlsx"
ORACLE_EXPECTED_SHA256 = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
ORACLE_EXPECTED_SHAPE = (541909, 8)
ORACLE_TIME_COLUMN = "InvoiceDate"          # D16：时间字段
ORACLE_CANCELLED_PREFIX = "C"               # D16：取消单前缀
ORACLE_QTY_LIMIT = 0                        # D16：Quantity <= 0 排除
ORACLE_PRICE_LIMIT = 0                      # D16：UnitPrice <= 0 排除

# 验收用区间：2011-11-21 ~ 2011-11-27（周一~周日，数据覆盖内）
ORACLE_START = dt.date(2011, 11, 21)
ORACLE_END = dt.date(2011, 11, 27)

# 浮点容差：两条路径的求和顺序不同，可能出现 1e-9 级差异（不是口径差异）
FLOAT_TOL = 1e-6


@pytest.fixture(scope="session")
def oracle_df() -> pd.DataFrame:
    """Oracle 独立读 xlsx（OPENPYXL 引擎），带日期解析。整个 session 只读一次。"""
    frame = pd.read_excel(ORACLE_EXCEL_PATH, engine="openpyxl")
    frame[ORACLE_TIME_COLUMN] = pd.to_datetime(frame[ORACLE_TIME_COLUMN])
    return frame


def oracle_rows_in_range(frame: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Oracle 的时间筛选：**按日期比较**（含首尾全天）。

    与实现的路径不同：实现用的是 InvoiceDate 时间戳的半开区间
    [start 00:00:00, end+1天 00:00:00)。这里用 .dt.date 落在 [start, end] 内来选，
    两者在"含首尾全天"语义下必须等价。
    """
    day = frame[ORACLE_TIME_COLUMN].dt.date
    return frame[(day >= start) & (day <= end)]


def oracle_flag_cancelled(frame: pd.DataFrame) -> list[bool]:
    """D16：InvoiceNo 以 'C' 开头 —— 逐行字符串判断（不用 pandas .str 访问器）。"""
    return [str(value).startswith(ORACLE_CANCELLED_PREFIX) for value in frame["InvoiceNo"].tolist()]


def oracle_flag_negative_qty(frame: pd.DataFrame) -> list[bool]:
    """D16：Quantity <= 0 —— 逐行数值判断（转 int 后比较）。"""
    return [int(value) <= ORACLE_QTY_LIMIT for value in frame["Quantity"].tolist()]


def oracle_flag_nonpositive_price(frame: pd.DataFrame) -> list[bool]:
    """D16：UnitPrice <= 0 —— 逐行数值判断（转 float 后比较）。"""
    return [float(value) <= ORACLE_PRICE_LIMIT for value in frame["UnitPrice"].tolist()]


# Oracle 的规则表：key 与实现的 excluded_detail 键名一致（口径对齐，代码独立）
ORACLE_FLAG_FUNCS = {
    "cancelled": oracle_flag_cancelled,
    "negative_qty": oracle_flag_negative_qty,
    "nonpositive_price": oracle_flag_nonpositive_price,
}


def oracle_hit_counts(frame: pd.DataFrame, enabled_keys: tuple[str, ...]) -> list[int]:
    """每行命中**已启用**规则的条数（0/1/2/3）—— 逐行 Python 计算。"""
    flags = [ORACLE_FLAG_FUNCS[key](frame) for key in enabled_keys]
    if not flags:
        return [0] * len(frame)
    return [
        int(sum(1 for per_rule in flags if per_rule[i]))
        for i in range(len(frame))
    ]


def oracle_detail(frame: pd.DataFrame, enabled_keys: tuple[str, ...]) -> dict:
    """Oracle 侧的统计明细（口径 = 只认已启用规则；被关闭的键固定为 0）。

    返回的 4 个键与实现的 excluded_detail 一一对应（便于直接比较），
    另附 "rows_excluded" 供单独断言。
    """
    hits = oracle_hit_counts(frame, enabled_keys)
    detail = {key: 0 for key in ORACLE_FLAG_FUNCS}                 # 被关闭的键 = 0
    for key in enabled_keys:
        detail[key] = int(sum(ORACLE_FLAG_FUNCS[key](frame)))      # 各规则自身命中数（不跨规则去重）
    detail["multi_rule_hit"] = int(sum(1 for count in hits if count >= 2))
    detail["rows_excluded"] = int(sum(1 for count in hits if count >= 1))
    return detail


def oracle_line_amounts(frame: pd.DataFrame) -> list[float]:
    """D16 金额公式：逐行 Quantity * UnitPrice（Python 循环算，不用 pandas 向量化）。"""
    return [
        float(quantity) * float(price)
        for quantity, price in zip(frame["Quantity"].tolist(), frame["UnitPrice"].tolist())
    ]


def oracle_allowed_amount(frame: pd.DataFrame, enabled_keys: tuple[str, ...]) -> float:
    """Oracle 直接求和：只累加"未命中任何已启用规则"的行（逐行累加）。"""
    hits = oracle_hit_counts(frame, enabled_keys)
    amounts = oracle_line_amounts(frame)
    return float(sum(amount for amount, hit in zip(amounts, hits) if hit == 0))


def oracle_excluded_amount(frame: pd.DataFrame, enabled_keys: tuple[str, ...]) -> float:
    """Oracle 直接求和：累加"至少命中一条已启用规则"的行的金额。"""
    hits = oracle_hit_counts(frame, enabled_keys)
    amounts = oracle_line_amounts(frame)
    return float(sum(amount for amount, hit in zip(amounts, hits) if hit >= 1))


def oracle_allowed_amount_exact(frame: pd.DataFrame, enabled_keys: tuple[str, ...]) -> float:
    """Oracle 第二形态：掩码由 Oracle 自己算（逐行命中条数），最后一步用 pandas 向量化求和。

    存在的意义：纯 Python 逐行累加与 pandas/numpy 的求和顺序不同，会产生 1e-8 级
    浮点尾差（不是口径差异）。本函数让"求和原语"两侧一致，从而给出**严格等于 0**
    的差值作为 AC-02 的硬证据；筛选/规则/掩码仍然是 Oracle 自己那份独立实现。
    """
    keep = pd.Series([hit == 0 for hit in oracle_hit_counts(frame, enabled_keys)], index=frame.index)
    return float((frame["Quantity"] * frame["UnitPrice"])[keep].sum())


def oracle_sha256(path: pathlib.Path) -> str:
    """Oracle 自己算文件哈希（标准库，不调用被测算数模块）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ════════════════════════════════════════════════════════════════════════
# 打印小工具：让验收证据在一份输出里读得出来
# ════════════════════════════════════════════════════════════════════════
def banner(title: str) -> None:
    print("\n" + "─" * 72)
    print(title)
    print("─" * 72)


# ════════════════════════════════════════════════════════════════════════
# AC-06 · 数据溯源（先跑：指纹不对就没必要往下算）
# ════════════════════════════════════════════════════════════════════════
def test_ac06_data_provenance(oracle_df: pd.DataFrame) -> None:
    banner("AC-06 数据溯源")
    actual_sha = oracle_sha256(ORACLE_EXCEL_PATH)
    print(f"文件路径 : {ORACLE_EXCEL_PATH}")
    print(f"SHA256    : {actual_sha}")
    print(f"期望值    : {ORACLE_EXPECTED_SHA256}")
    print(f"哈希匹配  : {actual_sha == ORACLE_EXPECTED_SHA256}")
    print(f"读取后 shape : {oracle_df.shape}")
    print(f"列名      : {list(oracle_df.columns)}")

    if actual_sha != ORACLE_EXPECTED_SHA256:
        pytest.fail(f"AC-06 FAIL：数据文件 SHA256 不匹配（环境/数据错误），停止计算。实际={actual_sha}")

    assert actual_sha == ORACLE_EXPECTED_SHA256
    assert oracle_df.shape == ORACLE_EXPECTED_SHAPE
    # loader 侧（实现路径）也必须认为指纹一致，否则它是"拒绝执行"的
    from app.engine import loader

    info = loader.verify_data_source()
    print(f"loader 校验 : match={info['match']} sha256={info['sha256']}")
    assert info["match"] is True


# ════════════════════════════════════════════════════════════════════════
# AC-02 · 独立 Oracle 对比（2011-11-21 ~ 2011-11-27）
# ════════════════════════════════════════════════════════════════════════
def test_ac02_independent_oracle(oracle_df: pd.DataFrame) -> None:
    banner("AC-02 独立 Oracle：executor vs 第二套代码路径")
    enabled = ("cancelled", "negative_qty", "nonpositive_price")

    result = executor.compute_sales_amount("2011-11-21", "2011-11-27")

    window = oracle_rows_in_range(oracle_df, ORACLE_START, ORACLE_END)
    oracle_value = oracle_allowed_amount(window, enabled)              # Oracle-A：Python 逐行累加
    oracle_exact = oracle_allowed_amount_exact(window, enabled)        # Oracle-B：pandas 向量化求和

    diff = result["amount"] - oracle_value
    diff_exact = result["amount"] - oracle_exact
    print(f"executor 金额       : {result['amount']!r}")
    print(f"Oracle-A 金额(逐行加): {oracle_value!r}")
    print(f"Oracle-B 金额(向量化): {oracle_exact!r}")
    print(f"差值 A（逐行累加，仅浮点求和顺序差异）: {diff!r}")
    print(f"差值 B（须为 0）                     : {diff_exact!r}")

    assert diff_exact == 0.0, f"AC-02 FAIL：差值不为 0（{diff_exact!r}）"
    assert result["amount"] == pytest.approx(oracle_value, abs=FLOAT_TOL), "AC-02 FAIL：两条路径金额不一致"


# ════════════════════════════════════════════════════════════════════════
# AC-04 · 时间区间语义（含首尾全天）+ 两步分离
# ════════════════════════════════════════════════════════════════════════
def test_ac04_time_range_inclusive_both_ends(oracle_df: pd.DataFrame) -> None:
    banner("AC-04 时间区间语义：含首尾全天（第 1 步 只做时间筛选）")
    result = executor.compute_sales_amount("2011-11-21", "2011-11-27")

    window = oracle_rows_in_range(oracle_df, ORACLE_START, ORACLE_END)
    oracle_rows = int(len(window))

    # 末日当天行数（证明"含末日全天"，不是到 00:00:00 就截断）
    last_day = oracle_df[oracle_df[ORACLE_TIME_COLUMN].dt.date == ORACLE_END]
    last_day_rows = int(len(last_day))
    # 若区间只到前一天（对比组），行数应更少 —— 反证末日整天被纳入了
    without_last_day = int(len(oracle_rows_in_range(oracle_df, ORACLE_START, ORACLE_END - dt.timedelta(days=1))))

    print(f"第 1 步 rows_in_range (executor) : {result['rows_in_range']}")
    print(f"第 1 步 区间原始行数   (Oracle)  : {oracle_rows}")
    print(f"2011-11-27 当天行数             : {last_day_rows}")
    print(f"若去掉末日(截止 11-26)行数       : {without_last_day}")
    print(f"第 2 步 rows_excluded           : {result['rows_excluded']}（在区间行上应用排除规则）")
    print(f"第 2 步 rows_valid              : {result['rows_valid']}")

    assert last_day_rows > 0, "AC-04 FAIL：2011-11-27 当天没有数据，无法证明含末日全天"
    assert result["rows_in_range"] == oracle_rows, "AC-04 FAIL：区间行数与 Oracle 不一致"
    assert result["rows_in_range"] > without_last_day, "AC-04 FAIL：末日整天未被纳入区间"
    assert result["rows_valid"] + result["rows_excluded"] == result["rows_in_range"]


# ════════════════════════════════════════════════════════════════════════
# AC-05 · 统计口径（只以"当前启用的 exclude_* 规则"为准）
# ════════════════════════════════════════════════════════════════════════
def test_ac05_statistics_semantics(oracle_df: pd.DataFrame) -> None:
    banner("AC-05 统计口径：区间行数 / 排除明细 / 排除金额 / 有效行 Quantity 合计")
    window = oracle_rows_in_range(oracle_df, ORACLE_START, ORACLE_END)
    all_keys = ("cancelled", "negative_qty", "nonpositive_price")

    result = executor.compute_sales_amount("2011-11-21", "2011-11-27")
    expected = oracle_detail(window, all_keys)
    detail_keys = ("cancelled", "negative_qty", "nonpositive_price", "multi_rule_hit")
    expected_detail = {key: expected[key] for key in detail_keys}

    print(f"区间行数 rows_in_range : {result['rows_in_range']}")
    print(f"被排除行数 rows_excluded: {result['rows_excluded']}   (Oracle: {expected['rows_excluded']})")
    for key in detail_keys:
        print(f"  excluded_detail[{key:<17}] = {result['excluded_detail'][key]:>6}   (Oracle: {expected[key]})")
    print(f"被排除金额 excluded_amount : {result['excluded_amount']!r}")
    print(f"有效行 Quantity 合计        : {result['valid_qty_sum']!r}")
    print(f"validations : customer_id_nulls={result['validations']['customer_id_nulls']}, "
          f"duplicate_rows={result['validations']['duplicate_rows']}, "
          f"excluded_net_amount={result['validations']['excluded_net_amount']!r}")
    for check in result["validations"]["checks"]:
        print(f"  check[{check['name']}] passed={check['passed']} :: {check['detail']}")

    # ── 返回结构 / 键名（测试会断言，键名固定）──────────────────────────
    for key in ("cancelled", "negative_qty", "nonpositive_price", "multi_rule_hit"):
        assert key in result["excluded_detail"], f"excluded_detail 缺键：{key}"
        assert isinstance(result["excluded_detail"][key], int)
    validations = result["validations"]
    assert isinstance(validations, dict)
    assert isinstance(validations["customer_id_nulls"], int)
    assert isinstance(validations["duplicate_rows"], int)
    assert isinstance(validations["excluded_net_amount"], float)
    assert isinstance(validations["checks"], list) and validations["checks"]
    for check in validations["checks"]:
        assert set(check) == {"name", "passed", "detail"}
        assert check["passed"] is True, f"自检项未通过：{check}"

    # ── 数值口径与 Oracle 逐项对齐 ──────────────────────────────────────
    assert result["rows_excluded"] == expected["rows_excluded"]
    assert result["excluded_detail"] == expected_detail, "excluded_detail 与 Oracle 不一致"
    assert result["excluded_amount"] == pytest.approx(oracle_excluded_amount(window, all_keys), abs=FLOAT_TOL)
    assert result["excluded_amount"] == pytest.approx(result["validations"]["excluded_net_amount"], abs=FLOAT_TOL)

    # 有效行 Quantity 合计：由 Oracle 独立算（排除行之外的 Quantity 之和）
    hits = oracle_hit_counts(window, all_keys)
    oracle_valid_qty = float(sum(int(q) for q, hit in zip(window["Quantity"].tolist(), hits) if hit == 0))
    print(f"有效行 Quantity 合计 (Oracle): {oracle_valid_qty!r}")
    assert result["valid_qty_sum"] == pytest.approx(oracle_valid_qty, abs=FLOAT_TOL)

    # ── D16-6 / D16-7：报告但不排除 ────────────────────────────────────
    oracle_nulls = int(window["CustomerID"].isna().sum())
    oracle_dups = int(window.duplicated(keep="first").sum())
    print(f"CustomerID 空值行 (Oracle)  : {oracle_nulls}")
    print(f"完全重复行数     (Oracle)  : {oracle_dups}（keep='first' 语义，不删除）")
    assert validations["customer_id_nulls"] == oracle_nulls
    assert validations["duplicate_rows"] == oracle_dups

    # ── 关闭规则后的统计口径（评审 BLOCKER-2 的例子）────────────────────
    partial = executor.compute_sales_amount("2011-11-21", "2011-11-27", exclude_cancelled=False)
    enabled_partial = ("negative_qty", "nonpositive_price")
    expected_partial = oracle_detail(window, enabled_partial)
    print("\n[exclude_cancelled=False]")
    print(f"  excluded_detail = {partial['excluded_detail']}")
    print(f"  Oracle 期望     = {expected_partial}")
    assert partial["excluded_detail"]["cancelled"] == 0, "被关闭的规则必须固定计 0"
    for key in ("negative_qty", "nonpositive_price", "multi_rule_hit"):
        assert partial["excluded_detail"][key] == expected_partial[key]
    assert partial["rows_excluded"] == expected_partial["rows_excluded"]
    assert partial["excluded_amount"] == pytest.approx(oracle_excluded_amount(window, enabled_partial), abs=FLOAT_TOL)

    # 例：既是取消单又是负数量的行 —— 关闭取消规则后只按"已启用"规则计数
    both_hits = int(sum(1 for a, b in zip(oracle_flag_cancelled(window), oracle_flag_negative_qty(window)) if a and b))
    print(f"  取消单∩负数量 行数（示例说明用）= {both_hits}")


# ════════════════════════════════════════════════════════════════════════
# AC-03 · 反向校验（关掉全部排除规则）
# ════════════════════════════════════════════════════════════════════════
def test_ac03_reverse_validation_with_rules_off(oracle_df: pd.DataFrame) -> None:
    banner("AC-03 反向校验：关掉全部排除规则后重算")
    window = oracle_rows_in_range(oracle_df, ORACLE_START, ORACLE_END)
    all_keys = ("cancelled", "negative_qty", "nonpositive_price")

    on = executor.compute_sales_amount("2011-11-21", "2011-11-27")

    # ① 前置断言：这一周必须真的存在被排除的行，否则本 AC 无意义（不许静默通过）
    if on["rows_excluded"] <= 0:
        pytest.fail("AC-03 FAIL：该周被排除行数为 0，反向校验无意义")
    print(f"① 前置断言：该周被排除行数 = {on['rows_excluded']} (> 0) ✓")
    print(f"   被排除行金额净额 = {on['excluded_amount']!r}（符号说明：取消单/退货/负单价行的 "
          f"Quantity*UnitPrice 之和通常为负，故净额为负 → 关掉排除规则后总额会变小）")

    # ② 三个 exclude_* 全关
    off = executor.compute_sales_amount(
        "2011-11-21", "2011-11-27",
        exclude_cancelled=False, exclude_negative_qty=False, exclude_nonpositive_price=False,
    )
    oracle_off = oracle_allowed_amount(window, ())          # 全关 = 区间内所有行求和
    oracle_on = oracle_allowed_amount(window, all_keys)
    oracle_on_exact = oracle_allowed_amount_exact(window, all_keys)
    oracle_off_exact = oracle_allowed_amount_exact(window, ())

    print(f"② 开启排除：executor={on['amount']!r}  Oracle={oracle_on!r}  差={on['amount'] - oracle_on!r}")
    print(f"   关闭排除：executor={off['amount']!r}  Oracle={oracle_off!r}  差={off['amount'] - oracle_off!r}")
    print(f"   差值(须为 0)：开启={on['amount'] - oracle_on_exact!r}  关闭={off['amount'] - oracle_off_exact!r}")
    print(f"   开启 - 关闭 = {on['amount'] - off['amount']!r}（应 ≈ +|被排除净额| = {-on['excluded_amount']!r}）")
    print(f"   关闭后 excluded_detail = {off['excluded_detail']}")

    # 金额必须发生变化（否则说明排除规则根本没生效）
    assert on["amount"] != off["amount"], "AC-03 FAIL：关闭排除规则后金额没变，排除规则未生效"
    # 两边都必须与 Oracle 相等（严格 0 + 逐行累加路径容差）
    assert on["amount"] - oracle_on_exact == 0.0, "AC-03 FAIL：开启排除时差值不为 0"
    assert off["amount"] - oracle_off_exact == 0.0, "AC-03 FAIL：关闭排除时差值不为 0"
    assert on["amount"] == pytest.approx(oracle_on, abs=FLOAT_TOL)
    assert off["amount"] == pytest.approx(oracle_off, abs=FLOAT_TOL)
    # 关闭后净额应为"区间全部行之和"，且行数口径不变
    assert off["rows_excluded"] == 0
    assert off["excluded_amount"] == 0.0
    assert off["excluded_detail"] == {"cancelled": 0, "negative_qty": 0, "nonpositive_price": 0, "multi_rule_hit": 0}
    assert off["rows_in_range"] == on["rows_in_range"], "开关不影响第 1 步的区间行数"
    assert off["valid_qty_sum"] == pytest.approx(float(window["Quantity"].sum()), abs=FLOAT_TOL)
    # 金额变化方向：被排除净额为负 → 关闭后变小（不断言方向，只打印说明）


# ════════════════════════════════════════════════════════════════════════
# 入参形态（签名约定：str / date / datetime 等价，含首尾全天）
# ════════════════════════════════════════════════════════════════════════
def test_start_end_input_types_equivalent() -> None:
    banner("入参形态：str / date / datetime 结果一致；非法入参报错")
    as_str = executor.compute_sales_amount("2011-11-21", "2011-11-27")
    as_date = executor.compute_sales_amount(dt.date(2011, 11, 21), dt.date(2011, 11, 27))
    as_datetime = executor.compute_sales_amount(
        dt.datetime(2011, 11, 21, 0, 0, 0), dt.datetime(2011, 11, 27, 23, 59, 59)
    )
    print(f"str      : {as_str['amount']!r}")
    print(f"date     : {as_date['amount']!r}")
    print(f"datetime : {as_datetime['amount']!r}")
    assert as_str["amount"] == as_date["amount"] == as_datetime["amount"]
    assert as_str["rows_in_range"] == as_date["rows_in_range"] == as_datetime["rows_in_range"]

    with pytest.raises(ValueError):
        executor.compute_sales_amount("2011-11-27", "2011-11-21")   # start > end
    with pytest.raises(ValueError):
        executor.compute_sales_amount("", "2011-11-27")              # 空字符串
    with pytest.raises(TypeError):
        executor.compute_sales_amount(20111121, "2011-11-27")        # 类型不支持：不猜


# ════════════════════════════════════════════════════════════════════════
# AC-07 · D16 每条规则在 metrics.py 有对应实现与注释
# ════════════════════════════════════════════════════════════════════════
def test_ac07_d16_rules_implemented_and_documented() -> None:
    banner("AC-07 D16 逐条对照：metrics.py 实现 + 注释")
    source = (PROJECT_ROOT / "app" / "engine" / "metrics.py").read_text(encoding="utf-8")

    # ① 8 条 D16 编号在注释里都出现（可枚举、可核对）
    for index in range(1, 9):
        marker = f"D16-{index}"
        assert marker in source, f"metrics.py 缺少 {marker} 的注释标记"
    print("① D16-1..D16-8 编号标记全部存在 ✓")

    # ② 每条规则的"实现符号"真实存在且取值符合 D16
    assert metrics.TIME_FIELD == "InvoiceDate"                      # D16-1 时间字段
    assert "含首尾全天" in metrics.RANGE_SEMANTICS                    # D16-2 区间语义
    low, high = metrics.inclusive_day_window("2011-11-21", "2011-11-27")
    assert str(low) == "2011-11-21 00:00:00" and str(high) == "2011-11-28 00:00:00", (low, high)
    assert metrics.CANCELLED_PREFIX == "C"                          # D16-3 取消单
    assert metrics.NEGATIVE_QTY_THRESHOLD == 0                      # D16-4 负数量
    assert metrics.NONPOSITIVE_PRICE_THRESHOLD == 0                 # D16-5 负/零单价
    assert callable(metrics.count_customer_id_nulls)                # D16-6 缺失客户（只报告）
    assert callable(metrics.count_duplicate_rows)                   # D16-7 重复行（只报告）
    assert metrics.LINE_AMOUNT_FORMULA == "Quantity * UnitPrice"    # D16-8 金额公式
    assert [rule.key for rule in metrics.EXCLUSION_RULES] == ["cancelled", "negative_qty", "nonpositive_price"]
    assert metrics.D16_IMPLEMENTED_IDS == tuple(f"D16-{i}" for i in range(1, 9))
    print("② 8 条规则的实现符号与取值全部对齐 D16 ✓")

    # ③ D16 原文表格出现在 metrics.py 里（照抄，不许自行解释）
    for fragment in ("| 时间字段 |", "| 区间语义 |", "| 取消单", "| 退货/负数量 |",
                     "| 负/零单价 |", "| 缺失客户 |", "| 重复行", "| 金额公式 |"):
        assert fragment in source, f"metrics.py 未照抄 D16 表格行：{fragment}"
    print("③ D16 原文表格已内嵌在 metrics.py 顶部注释 ✓")
