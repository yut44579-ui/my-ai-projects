"""健康检查接口测试（真实断言，不许空断言）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__
from app.core.config import settings


def test_health_returns_200(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_reports_real_db_connection(client: TestClient) -> None:
    """db.connected 必须是真去连库得到的结果，且连的是**当前配置**的库。

    ★ 这里不许写死库名：同一份代码要在 biz_assistant / biz_assistant_int 等多个库上跑
      （例如 worktree 的整合库），写死任何具体库名都会让测试在别的库上必然失败。
      断言"等于当前 Settings 里的 db_name"才是这条 AC 的本意：连的必须是配置指向的那个库。
    """
    body = client.get("/api/health").json()

    assert body["status"] == "ok", f"数据库未连通：{body['db']}"
    assert body["db"]["connected"] is True
    assert body["db"]["error"] is None
    assert body["db"]["dialect"] == "mysql"
    assert body["db"]["database"] == settings.db_name

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


def test_health_reports_degraded_when_db_unreachable(client: TestClient, monkeypatch) -> None:
    """连不上库时必须如实报 degraded + error，不许假装 ok。"""
    from app.api.routes import health as health_route

    def fake_check_connection(engine_override=None) -> dict:
        return {
            "connected": False,
            "database": "biz_assistant",
            "dialect": "mysql",
            "server_version": None,
            "error": (
                "OperationalError: (1045, \"Access denied for user "
                "'root'@'localhost' (using password: NO)\")"
            ),
        }

    monkeypatch.setattr(health_route, "check_connection", fake_check_connection)
    resp = client.get("/api/health")
    body = resp.json()

    assert body["status"] == "degraded", "连不上库却报 ok 就是撒谎"
    assert body["db"]["connected"] is False
    assert body["db"]["server_version"] is None
    assert body["db"]["error"], "连不上库时必须带出 error，不许吞掉"


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
