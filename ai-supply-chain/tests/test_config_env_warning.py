"""缺 .env 时的启动告警测试。

背景：仓库里只有 .env.example，缺 .env 时 db_password 回落到空串，
连库会报 (1045, "Access denied ... (using password: NO)")。
配置层必须在启动阶段就把这件事说清楚，而不是让人去猜。

注意：本文件不删除、不改写真实的 .env —— 一律用 tmp_path 与 monkeypatch 模拟。
"""

from __future__ import annotations

import logging

from app.core import config as config_module
from app.core.config import settings, warn_if_env_file_missing

LOGGER_NAME = "app.core.config"


def _warnings(caplog: logging.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_warns_when_env_file_missing(tmp_path, caplog) -> None:
    """缺 .env 时必须产生恰好一条 warning，且提示怎么修。"""
    missing = tmp_path / ".env"
    assert not missing.exists()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        warned = warn_if_env_file_missing(missing)

    assert warned is True
    records = _warnings(caplog)
    assert len(records) == 1, f"应恰好告警一次，实际 {len(records)} 次"

    message = records[0].getMessage()
    assert "未找到 .env" in message
    assert ".env.example" in message, "必须告诉用户怎么修（copy .env.example .env）"


def test_no_warning_when_env_file_exists(tmp_path, caplog) -> None:
    """.env 存在时不得误报。"""
    present = tmp_path / ".env"
    present.write_text("DB_NAME=biz_assistant\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        warned = warn_if_env_file_missing(present)

    assert warned is False
    assert _warnings(caplog) == []


def test_warning_leaks_no_credentials(tmp_path, caplog) -> None:
    """告警文案里绝不能出现口令明文或完整连接串。"""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        warn_if_env_file_missing(tmp_path / ".env")

    text = "\n".join(r.getMessage() for r in _warnings(caplog))
    assert text, "应当有告警可供检查"

    if settings.db_password:
        assert settings.db_password not in text, "告警里泄漏了口令明文"
    assert "mysql+pymysql" not in text, "告警里泄漏了连接串"


def test_settings_load_warns_only_once(tmp_path, caplog, monkeypatch) -> None:
    """「只打一次」：反复加载配置也只告警一次，不会每个请求刷屏。"""
    monkeypatch.setattr(config_module, "ENV_FILE", tmp_path / ".env")  # 指向不存在的路径
    config_module.get_settings.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            config_module.get_settings()
            config_module.get_settings()
            config_module.get_settings()

        records = _warnings(caplog)
        assert len(records) == 1, f"应只告警一次，实际 {len(records)} 次"
        assert "未找到 .env" in records[0].getMessage()
    finally:
        # 清空缓存（ENV_FILE 由 monkeypatch 在测试结束后自动还原）。
        # 各模块持有的是模块级 settings 常量，不受影响。
        config_module.get_settings.cache_clear()


def test_real_env_file_exists_so_no_warning() -> None:
    """本机交付物自带 .env（不进 git）：装载时不应有告警。"""
    assert config_module.ENV_FILE.is_file(), (
        f"缺少 {config_module.ENV_FILE}，请照 README「首次运行」建好 .env"
    )
    assert warn_if_env_file_missing() is False
