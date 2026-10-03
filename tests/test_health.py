"""健康检查接口测试（真实断言，不许空断言）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__


def test_health_returns_200(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_reports_real_db_connection(client: TestClient) -> None:
    """db.connected 必须是真去连库得到的结果，且连的是 biz_assistant。"""
    body = client.get("/api/health").json()

    assert body["status"] == "ok", f"数据库未连通：{body['db']}"
    assert body["db"]["connected"] is True
    assert body["db"]["error"] is None
    assert body["db"]["dialect"] == "mysql"
    assert body["db"]["database"] == "biz_assistant"

    # MySQL 8 的版本号形如 8.0.42
    version = body["db"]["server_version"] or ""
    assert version.startswith("8."), f"预期 MySQL 8.x，实际 {version!r}"


def test_health_metadata(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["service"] == "biz-assistant"
    assert body["version"] == __version__
    # D6：时区固定 Asia/Shanghai
    assert body["timezone"] == "Asia/Shanghai"
    assert body["time"].startswith("20"), "time 应为 ISO8601，形如 2026-10-03T19:xx"


def test_health_is_not_hardcoded() -> None:
    """反向验证：连一个不存在的库时必须报 connected=False，证明是真去连了库。"""
    from sqlalchemy import create_engine

    from app.core.config import settings
    from app.db.session import check_connection

    probe = create_engine(
        f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/definitely_not_exists_db?charset=utf8mb4"
    )
    try:
        info = check_connection(engine_override=probe)
        assert info["connected"] is False, "库不存在却报连通，说明没真连库"
        assert info["error"], "连不上时必须给出错误摘要"
    finally:
        probe.dispose()
