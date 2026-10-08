"""Alembic 运行环境。

连接串不写死在 alembic.ini，而是从应用配置（环境变量 / .env）读取，
保证数据库口令不进版本库。目标库固定为 biz_assistant。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 让本文件能 import backend/app
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402

# 让后续 TASK 的模型模块在此 import，autogenerate 才能看到表。
# 本 TASK（骨架）不建任何业务表，见 docs/DECISIONS.md D11。
import app.models  # noqa: E402,F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连真实 MySQL 执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
