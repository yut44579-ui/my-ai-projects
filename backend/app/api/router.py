"""API 总路由：所有子路由在此挂载，统一加 /api 前缀。

当前挂载：health（健康检查）/ imports（导入预览与入库）/ customers（客户查询）/
          messages（沟通记录，TASK-003）/ ai（AI 回复，TASK-004）。
后续业务路由（汇报 …）继续在此追加。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import customers, health, imports, messages

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(imports.router)
api_router.include_router(customers.router)
api_router.include_router(messages.router)
