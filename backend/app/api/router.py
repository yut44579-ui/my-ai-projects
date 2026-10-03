"""API 总路由：所有子路由在此挂载，统一加 /api 前缀。

当前挂载：health（健康检查）/ imports（导入预览与入库）/ customers（客户查询）
          / handover（人工接管三态，TASK-005）
          / reports（汇报快照与下钻，TASK-007）
          / messages（沟通记录，TASK-003）/ ai（AI 回复，TASK-004）。

★ 并行线合并（integration）：各线的 include_router 全部保留，每个路由都要注册。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import ai, customers, handover, health, imports, messages, reports

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(imports.router)
api_router.include_router(customers.router)
api_router.include_router(handover.router)
api_router.include_router(reports.router)
api_router.include_router(messages.router)
api_router.include_router(ai.router)
