"""executor.py · 计算入口：按 D16 口径算指定时间段的销售额（数字全部由代码算 —— D4）。

执行顺序（**两步分离**，不要混在一起看，这是 AC-04 明确要求的）：
    第 1 步 时间筛选：把区间内的**所有原始行**取出来（不做任何排除）
                      → rows_in_range
    第 2 步 排除规则：在第 1 步的结果上，按**当前启用**的 exclude_* 规则剔行
                      → rows_excluded / excluded_detail / excluded_amount
                      → amount = 剩下的有效行 sum(Quantity * UnitPrice)

统计口径（AC-05 / 评审 BLOCKER-2，只认"当前启用的 exclude_* 规则"）：
    rows_excluded       = 至少命中一条**已启用**排除规则的去重行数
    excluded_detail     = 只统计**已启用**规则；被关闭规则对应的计数键**固定为 0**
    multi_rule_hit      = 同时命中 ≥2 条**已启用**排除规则的去重行数
    excluded_amount     = 最终因**已启用**规则被排除的行的 sum(Quantity * UnitPrice)
    例：exclude_cancelled=False 且某行同时是取消单 + 负数量
        → cancelled 计 0（规则已关）、negative_qty 计 1、multi_rule_hit 计 0（已启用里只命中 1 条）、
          rows_excluded 计 1、该行计入 excluded_amount。

为什么把 validations 一起返回：
    D11 要求执行记录绑定校验结果；报表出数必须自证"口径没跑偏"（行数守恒、金额守恒）。
"""

from __future__ import annotations

import datetime as _dt
import time

import pandas as pd

from app.engine import loader, metrics

# 浮点比较容差：金额是 float 求和，等值判断留 1e-6 的相对/绝对余量
_FLOAT_TOL = 1e-6


def compute_sales_amount(
    start: str | _dt.date | _dt.datetime,
    end: str | _dt.date | _dt.datetime,
    exclude_cancelled: bool = True,
    exclude_negative_qty: bool = True,
    exclude_nonpositive_price: bool = True,
) -> dict:
    """算 [start, end] 区间（含首尾全天）的销售额，口径见 metrics.py（D16）。

    参数：
        start / end                'YYYY-MM-DD' 或 ISO 时间字符串 / date / datetime；
                                   语义 = 含首尾全天（start 00:00:00 ~ end 23:59:59.999）
        exclude_cancelled          是否排除取消单（InvoiceNo 以 'C' 开头）
        exclude_negative_qty       是否排除退货/负数量（Quantity <= 0）
        exclude_nonpositive_price  是否排除负/零单价（UnitPrice <= 0）
                                   —— 三个开关是给**反向校验**用的：关掉规则后重算同一区间，
                                      若数字不变，说明排除规则根本没生效（测试会这么验）。

    返回：
        {
          "amount": float,                # 有效行 sum(Quantity * UnitPrice)
          "rows_in_range": int,           # 第 1 步：区间内原始行数（未排除）
          "rows_excluded": int,           # 第 2 步：命中已启用规则的去重行数
          "excluded_amount": float,       # 被排除行的 sum(Quantity * UnitPrice)（通常为负）
          "excluded_detail": {"cancelled": int, "negative_qty": int, "nonpositive_price": int,
                              "multi_rule_hit": int},
          "validations": dict,            # 数据体检 + 自检项（键名见下）
          "seconds": float,               # 本次计算耗时（含读数据；读一次约百秒，见 loader 缓存）
          # 便于报告/复核的补充字段：
          "rows_valid": int, "valid_qty_sum": float, "range": [str, str]
        }

    validations 固定含：
        customer_id_nulls   int    CustomerID 为空的行数（D16-6：报告但不排除）
        duplicate_rows      int    df.duplicated(keep="first").sum() 语义的重复行数（D16-7：不删）
        excluded_net_amount float  被排除行 Quantity*UnitPrice 净额（= excluded_amount，通常为负）
        checks              list   其余自检项 [{name, passed, detail}, ...]
        注：以上 customer_id_nulls / duplicate_rows 均在**区间内**的行上统计（算法作用的总体）。

    抛错：
        DataSourceError  数据源缺失/哈希不匹配（loader 抛，直接中止，不产出数字）
        ValueError       start/end 非法或 start > end
    """
    started = time.perf_counter()

    # ── 0. 取数 + 数据溯源（哈希不匹配会在这里抛错，不继续算 —— D10）────────
    df = loader.load_raw()

    # ── 第 1 步：时间筛选（含首尾全天 → 半开区间，D16-2）──────────────────
    low, high = metrics.inclusive_day_window(start, end)
    in_range_mask = (df[metrics.TIME_FIELD] >= low) & (df[metrics.TIME_FIELD] < high)
    window = df.loc[in_range_mask]                 # 区间内**原始行**，此处不做任何排除
    rows_in_range = int(len(window))

    # ── 第 2 步：排除规则（只认当前启用的规则）────────────────────────────
    enabled_rules = metrics.resolve_enabled_rules(
        exclude_cancelled=exclude_cancelled,
        exclude_negative_qty=exclude_negative_qty,
        exclude_nonpositive_price=exclude_nonpositive_price,
    )

    # 每条已启用规则的命中掩码（在区间内的行上算）
    hit_masks: dict[str, pd.Series] = {
        rule.key: rule.mask(window).astype(bool) for rule in enabled_rules
    }

    # 命中条数（每行）：0 = 有效行，1 = 命中一条规则，≥2 = 命中多条规则
    # 布尔掩码相加天然去重：一行无论命中几条规则，只算一行
    if hit_masks:
        hit_count = pd.concat(hit_masks, axis=1).sum(axis=1)
    else:
        hit_count = pd.Series(0, index=window.index, dtype="int64")

    excluded_mask = hit_count >= 1                      # 至少命中一条已启用规则 → 被排除
    rows_excluded = int(excluded_mask.sum())
    multi_rule_hit = int((hit_count >= 2).sum())        # 同时命中 ≥2 条已启用规则的行数

    # excluded_detail：已启用规则给出各自命中数；**被关闭的规则键固定为 0**
    excluded_detail: dict[str, int] = {rule.key: 0 for rule in metrics.EXCLUSION_RULES}
    for rule in enabled_rules:
        excluded_detail[rule.key] = int(hit_masks[rule.key].sum())
    excluded_detail["multi_rule_hit"] = multi_rule_hit

    # ── 金额（D16-8：逐行 Quantity * UnitPrice，再求和）───────────────────
    amounts = metrics.line_amount(window)
    excluded_amount = float(amounts[excluded_mask].sum())       # 被排除行的净额（通常为负）
    amount = float(amounts[~excluded_mask].sum())               # 有效行金额 = 最终销售额
    valid_qty_sum = float(window.loc[~excluded_mask, "Quantity"].sum())

    # ── 数据体检 + 自检（validations）──────────────────────────────────────
    customer_id_nulls = metrics.count_customer_id_nulls(window)      # D16-6
    duplicate_rows = metrics.count_duplicate_rows(window)            # D16-7

    total_in_range = float(amounts.sum())
    rows_valid = rows_in_range - rows_excluded
    # 用另一条表达式重算被排除金额（loc + Series.mul，按索引对齐），
    # 用来抓"掩码与索引没对齐"这类经典 pandas 坑（如某步 reset_index 后错位）
    excluded_amount_recomputed = float(
        window.loc[excluded_mask, "Quantity"].mul(window.loc[excluded_mask, "UnitPrice"]).sum()
    )
    checks = [
        {
            "name": "row_identity",
            "passed": rows_in_range == rows_valid + rows_excluded,
            "detail": f"rows_in_range={rows_in_range} == rows_valid={rows_valid} + rows_excluded={rows_excluded}",
        },
        {
            "name": "amount_identity",
            "passed": abs(total_in_range - (amount + excluded_amount)) <= max(_FLOAT_TOL, abs(total_in_range) * _FLOAT_TOL),
            "detail": f"区间总额={total_in_range} == amount={amount} + excluded_amount={excluded_amount}",
        },
        {
            "name": "excluded_amount_recomputed",
            "passed": abs(excluded_amount - excluded_amount_recomputed) <= max(_FLOAT_TOL, abs(excluded_amount) * _FLOAT_TOL),
            "detail": f"掩码求和={excluded_amount} vs loc 重算={excluded_amount_recomputed}",
        },
        {
            "name": "disabled_rules_report_zero",
            "passed": all(
                excluded_detail[rule.key] == 0
                for rule in metrics.EXCLUSION_RULES
                if rule not in enabled_rules
            ),
            "detail": "已关闭的排除规则，其 excluded_detail 计数固定为 0",
        },
        {
            "name": "multi_rule_hit_not_greater_than_rows_excluded",
            "passed": multi_rule_hit <= rows_excluded,
            "detail": f"multi_rule_hit={multi_rule_hit} <= rows_excluded={rows_excluded}",
        },
        {
            "name": "no_nan_in_line_amount",
            "passed": not bool(amounts.isna().any()),
            "detail": "Quantity * UnitPrice 无 NaN",
        },
    ]

    seconds = time.perf_counter() - started
    return {
        "amount": amount,
        "rows_in_range": rows_in_range,
        "rows_excluded": rows_excluded,
        "excluded_amount": excluded_amount,
        "excluded_detail": excluded_detail,
        "validations": {
            "customer_id_nulls": customer_id_nulls,
            "duplicate_rows": duplicate_rows,
            "excluded_net_amount": excluded_amount,
            "checks": checks,
        },
        "seconds": seconds,
        "rows_valid": rows_valid,
        "valid_qty_sum": valid_qty_sum,
        "range": [str(metrics.to_date(start)), str(metrics.to_date(end))],
    }
