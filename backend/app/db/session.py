"""数据库引擎与会话管理（SQLAlchemy 2.x）。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine: Engine = create_engine(
    settings.sqlalchemy_url,
    echo=settings.db_echo,
    pool_pre_ping=True,   # 连接失效自动重连，避免 MySQL 断连后报错
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每请求一个 Session，退出时自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection(engine_override: Engine | None = None) -> dict:
    """真实连一次 MySQL，返回连通状态（不写死）。

    返回示例：
        {"connected": True, "database": "biz_assistant",
         "dialect": "mysql", "server_version": "8.0.42", "error": None}

    engine_override 仅用于测试：换一个指向别处的引擎，验证本函数确实在连库，
    而不是恒返回 True。
    """
    target = engine_override if engine_override is not None else engine
    info: dict = {
        "connected": False,
        "database": target.url.database or settings.db_name,
        "dialect": target.dialect.name,
        "server_version": None,
        "error": None,
    }
    try:
        with target.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
            current_db = conn.execute(text("SELECT DATABASE()")).scalar_one()
        info["connected"] = True
        info["server_version"] = str(version)
        info["database"] = current_db or settings.db_name
    except Exception as exc:  # noqa: BLE001 —— 健康检查需吞掉异常并如实上报
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info
