"""API 总路由：所有子路由在此挂载，统一加 /api 前缀。

业务路由（客户 / 沟通 / 汇报 …）自 TASK-001 起在此追加。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
