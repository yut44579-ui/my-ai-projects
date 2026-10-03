"""健康检查响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DbStatus(BaseModel):
    connected: bool = Field(description="本次请求是否真实连通 MySQL")
    database: str = Field(description="当前连接的库名")
    dialect: str = Field(description="SQLAlchemy 方言，如 mysql")
    server_version: str | None = Field(default=None, description="MySQL 服务端版本")
    error: str | None = Field(default=None, description="连通失败时的错误摘要")


class HealthResponse(BaseModel):
    status: str = Field(description="ok=全部就绪；degraded=数据库不可用")
    service: str
    version: str
    env: str
    timezone: str = Field(description="D6：固定 Asia/Shanghai")
    time: str = Field(description="Asia/Shanghai 下的当前时间(ISO8601)")
    db: DbStatus
