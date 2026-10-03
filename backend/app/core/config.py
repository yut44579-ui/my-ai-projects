"""应用配置。

所有配置项一律从环境变量读取（可经项目根目录的 .env 注入），
源码中不允许出现任何数据库口令、密钥等硬编码值。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> 上溯三级即项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 本地配置文件。缺失时不会静默通过：warn_if_env_file_missing() 会在配置加载阶段
# 打一条 warning，提示照 README 建 .env（否则会以空口令连库并报 1045）。
ENV_FILE = PROJECT_ROOT / ".env"

logger = logging.getLogger(__name__)


def warn_if_env_file_missing(env_file: Path | None = None) -> bool:
    """`.env` 不存在时打一条 warning，返回本次是否打了。

    文案里只出现文件路径，绝不出现口令、连接串等敏感值。
    本函数自身无状态；「只打一次」由调用方 get_settings() 的 lru_cache 保证。
    """
    target = ENV_FILE if env_file is None else Path(env_file)
    if target.is_file():
        return False

    logger.warning(
        "未找到 .env（%s），配置回落到默认值；"
        "若数据库连不通，请先 copy .env.example .env 并填写凭据",
        target,
    )
    return True


class Settings(BaseSettings):
    """运行期配置（环境变量优先，其次 .env 文件）。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 应用 ---
    app_name: str = "biz-assistant"
    app_env: str = Field(default="dev")
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    api_prefix: str = "/api"

    # D6：时区固定 Asia/Shanghai
    timezone: str = Field(default="Asia/Shanghai")

    # --- 数据库（MySQL 8，独立库 biz_assistant）---
    # 完整连接串优先；未提供时由下列分项拼装。
    database_url: str | None = Field(default=None)
    db_host: str = Field(default="127.0.0.1")
    db_port: int = Field(default=3306)
    db_user: str = Field(default="root")
    db_password: str = Field(default="")
    db_name: str = Field(default="biz_assistant")
    db_echo: bool = Field(default=False)

    # --- CORS（前端 Vite 开发服务器）---
    cors_origins: str = Field(default="http://127.0.0.1:5173,http://localhost:5173")

    @field_validator("db_name")
    @classmethod
    def _guard_db_name(cls, v: str) -> str:
        """硬边界：本服务只允许连接自己的独立库。"""
        forbidden = {"sales_report_agent", "sales_report", "sales-report-agent"}
        if v.strip().lower() in forbidden:
            raise ValueError(
                f"禁止连接 sales-report-agent 的数据库：{v!r}（见 docs/DECISIONS.md D1）"
            )
        return v

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 2.x 使用的连接串（PyMySQL 驱动）。"""
        if self.database_url:
            return self.database_url
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """加载配置。

    lru_cache 保证进程内只执行一次 —— 缺 .env 的 warning 因此也只打一次，
    不会每个请求刷屏（请求路径上不会再调用本函数）。
    """
    warn_if_env_file_missing()
    return Settings()


settings = get_settings()
