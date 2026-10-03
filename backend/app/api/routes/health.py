"""健康检查路由。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from app import __version__
from app.core.config import settings
from app.db.session import check_connection
from app.schemas.health import DbStatus, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> HealthResponse:
    """真实连一次 MySQL 后返回服务与数据库状态。"""
    db = check_connection()
    return HealthResponse(
        status="ok" if db["connected"] else "degraded",
        service=settings.app_name,
        version=__version__,
        env=settings.app_env,
        timezone=settings.timezone,
        time=datetime.now(ZoneInfo(settings.timezone)).isoformat(),
        db=DbStatus(**db),
    )
