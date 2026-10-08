"""engine 包：确定性执行层（数字一律由这里的代码算，LLM 不参与算数 —— D4）。

模块分工：
    loader.py     读原始 Excel → DataFrame（含数据溯源校验 SHA256）
    metrics.py    销售额口径清单（D16）逐条落地：时间字段/区间语义/排除规则/金额公式
    executor.py   计算入口 compute_sales_amount(...)，组装 loader + metrics 出指标

对外只暴露一个计算函数，方便上层（services / 测试）调用。
"""

from app.engine.executor import compute_sales_amount

__all__ = ["compute_sales_amount"]
