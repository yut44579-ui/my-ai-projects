"""ai · 自然语言层（TASK-004：把大白话变成"确定性计算 + 人话"）。

════════════════════════════════════════════════════════════════════════
【这一层在整个系统里的位置】
════════════════════════════════════════════════════════════════════════
    web/  ──「华南区上个月卖了多少？」
      ↓
    app/api_chat.py             HTTP 端点（薄壳：收问题、回记录）
      ↓
    app/ai/routing.py           ★ FR-007 路由（纯代码）：这句问题**想做什么**
      ↓
    app/ai/service.py           编排：解析 → 校验 → 调工具 → 组织回答 → 落盘
      ↓  ┌──────────────────────────────┬───────────────────────────────┐
         ↓                              ↓
    app/ai/intent.py               app/ai/tools.py
    LLM 只做"把话解析成 Intent"      **白名单工具**：确定性计算
      ↓                              ↓
    app/ai/llm.py                  app/engine/{metrics,executor,loader}
    DeepSeek 客户端                 （冻结资产，只读，一行不改）
      ↑                              ↓
      └──────── app/ai/answer.py ────┘
              LLM 只做"把事实组织成人话"

════════════════════════════════════════════════════════════════════════
【铁律（CLAUDE.md #1 + Gate 条件 + ARCHITECTURE.md:43）】
════════════════════════════════════════════════════════════════════════
**LLM 绝不碰数字。** 回答里出现的每一个数字，都必须能追溯到 `tools.py` 里
某次确定性计算的输出。本层对 LLM 的用法只有两个方向：

    1. 进：自然语言 → Intent JSON（结构化，不是数字）
    2. 出：事实 dict → 人话（**且 answer.py 会用"数字核对闸门"检查它没自己造数**）

`engine` 不依赖 `ai`（单向依赖）：数字计算可以脱离 LLM 单独测 —— 这条边界是本层
存在的理由，不是装饰。所以本层的任何文件都不许被 `app/engine/**` import。
"""

from __future__ import annotations

from app.ai import (
    answer,
    arithmetic,
    dashboard,
    general,
    intent,
    llm,
    routing,
    service,
    system_info,
    tools,
)

# ★ FR-007 的三个非销售模块与销售链路**平级**：
#   routing（路由，纯代码）/ arithmetic（受限 AST 计算器）/ general（系统帮助 + 概念问答）
#   —— 它们都在调销售工具**之前**结束分支，且 general 不 import tools（见 D24）。
# ★ FR-010-A 追加两个：system_info（系统自身的问题：导入记录/身份/能力边界，同样不碰销售数据）
#   与 dashboard（轻量看板：把**已有的**确定性结果摆成卡片，不新造任何口径）。
__all__ = [
    "answer", "arithmetic", "dashboard", "general", "intent", "llm",
    "routing", "service", "system_info", "tools",
]
