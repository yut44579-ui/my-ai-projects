"""tests/test_web.py · TASK-004A 前端验收测试（静态层 + 契约层）。

════════════════════════════════════════════════════════════════════════
【本文件测什么 / 不测什么】
════════════════════════════════════════════════════════════════════════
本文件用 FastAPI 的 `TestClient`（进程内）测**能测死的那部分**：

  ① 静态挂载真的生效（GET / 返回 web/index.html，三个静态文件 200）；
  ② 挂载**没有吃掉** /api/*（既有路由优先级更高，未知 /api/xxx 仍返回统一错误体 JSON）；
  ③ 五区块在页面上真实存在（五个 section 的 id + 五个标题）；
  ④ 页面代码里**没有**假东西：无外部 CDN/框架、无 setTimeout 假装成功、无 TODO 占位、
     无写死的业务数字（316412.16 / 19950 之类）；
  ⑤ 前端确实调用了那五个真实端点（辅助证据；AC-10 的主证据是报告里逐个按钮的调用链）。

**不测**（也不该用 TestClient 测）：浏览器里的交互闭环 —— 那由 `scripts/web_e2e.py`
打**真服务 + 真 HTTP**（十步闭环）验证，浏览器走查由 Hermes 用 CDP 完成（不装 Playwright）。
"""

from __future__ import annotations

import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402

WEB_DIR = PROJECT_ROOT / "web"
client = TestClient(app)

# 五个区块的 id 与标题（标题是用户能看见的"这不是 demo index"的最小证据）
BLOCKS = [
    ("block-data", "①"),
    ("block-task", "②"),
    ("block-spec", "③"),
    ("block-run", "④"),
    ("block-history", "⑤"),
]

# 前端**必须**调用的真实端点（缺一个说明有区块是摆设）
REQUIRED_ENDPOINTS = ["/api/upload", "/api/tasks", "/run", "/runs", "/api/health", "/openapi.json"]

# 禁止出现的造假特征（辅助扫描；主证据是报告里的调用链）
FORBIDDEN_IN_FRONTEND = ["setTimeout", "mock", "Mock", "TODO", "FIXME", "占位"]

# 禁止硬编码的业务结果（这些数字只能来自后端响应）
FORBIDDEN_NUMBERS = ["316412.16", "316,412.16", "19950", "19,950"]


# ════════════════════════════════════════════════════════════════════════
# ① 静态挂载
# ════════════════════════════════════════════════════════════════════════
def test_frontend_dir_exists() -> None:
    assert (WEB_DIR / "index.html").is_file(), "web/index.html 不存在"
    for name in ("app.js", "api.js", "style.css"):
        assert (WEB_DIR / name).is_file(), f"web/{name} 不存在"


def test_root_serves_index_html() -> None:
    response = client.get("/")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    for block_id, number in BLOCKS:
        assert f'id="{block_id}"' in body, f"页面缺少区块 {block_id}"
        assert number in body, f"页面缺少区块序号 {number}"
    assert "<h2>" in body


def test_static_assets_served() -> None:
    expectations = {
        "/app.js": ("javascript", "DOMContentLoaded"),
        "/api.js": ("javascript", "class ApiError"),
        "/style.css": ("css", "--error"),
    }
    for path, (kind, marker) in expectations.items():
        response = client.get(path)
        assert response.status_code == 200, f"{path} → {response.status_code}"
        assert kind in response.headers["content-type"], f"{path} 的 content-type 不对"
        assert marker in response.text, f"{path} 里没有预期内容 {marker!r}"


# ════════════════════════════════════════════════════════════════════════
# ② 挂载不得影响既有 API
# ════════════════════════════════════════════════════════════════════════
def test_api_routes_still_win_over_static_mount() -> None:
    health = client.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["task"] == "TASK-002D"


def test_unknown_api_path_still_returns_unified_error_json() -> None:
    """/api/* 的未知路径必须仍走 API 的错误体（不是静态文件的 404 页面）。"""
    response = client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    payload = response.json()
    assert "error" in payload and "code" in payload["error"]
    assert "detail" in payload                      # 冻结的旧契约字段仍在


# ════════════════════════════════════════════════════════════════════════
# ③④ 页面内容：真区块 / 无造假
# ════════════════════════════════════════════════════════════════════════
def test_page_has_no_external_dependency() -> None:
    """不得引任何外部脚本/样式（框架、CDN 一律禁止）。"""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"页面引了外部资源：{external}"
    for marker in ("vue", "react", "jquery", "cdn."):
        assert marker not in html.lower().replace("禁止引入 vue/react", ""), f"页面疑似引了 {marker}"


def test_frontend_has_no_fake_shortcuts() -> None:
    for name in ("app.js", "api.js", "index.html", "style.css"):
        body = (WEB_DIR / name).read_text(encoding="utf-8")
        for marker in FORBIDDEN_IN_FRONTEND:
            assert marker not in body, f"{name} 里出现禁止的造假特征：{marker!r}"


def test_frontend_has_no_hardcoded_business_numbers() -> None:
    for name in ("app.js", "api.js", "index.html"):
        body = (WEB_DIR / name).read_text(encoding="utf-8")
        for number in FORBIDDEN_NUMBERS:
            assert number not in body, f"{name} 里写死了业务数字：{number}"


def test_frontend_calls_the_real_endpoints() -> None:
    body = (WEB_DIR / "api.js").read_text(encoding="utf-8") + (WEB_DIR / "app.js").read_text(encoding="utf-8")
    for endpoint in REQUIRED_ENDPOINTS:
        assert endpoint in body, f"前端没有调用 {endpoint}"


def test_download_uses_backend_download_url() -> None:
    """下载按钮只能用后端返回的 download_url，不许前端自己拼产出路径。"""
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "download_url" in app_js
    assert "/api/download" not in app_js, "app.js 自己拼了下载路径（应由后端 download_url 提供）"


def test_every_dom_id_used_by_js_exists_in_html() -> None:
    """app.js 里 `$("xxx")` 取的每个 id 必须在 index.html 里存在。

    这是**没有浏览器时**能对"页面会不会一开就报 null 错"做的最有效静态检查：
    id 拼错 → getElementById 返回 null → 一操作就 TypeError（页面看着能用，点一下就炸）。
    （真正的运行时检查由 Hermes 用 CDP 开 Edge 走查完成。）
    """
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    used_ids = set(re.findall(r'\$\("([^"]+)"\)', app_js))
    missing = sorted(used_ids - html_ids)
    assert not missing, f"app.js 用了 index.html 里不存在的 id：{missing}"
    # 反向：除了**结构性容器**（区块外壳 / 折叠面板，用 id 只为锚定与定位，本来就不该被 JS 取），
    # 其余 id 都必须被 JS 用到 —— 否则就是"页面上的静态摆设"。
    structural = {"block-data", "block-task", "block-spec", "block-run", "block-history",
                  "spec-json-wrap", "task-metrics-field"}
    unused = sorted(html_ids - used_ids - structural)
    assert not unused, f"index.html 里有没被 JS 用到的 id（疑似静态摆设）：{unused}"


def test_metric_catalog_comes_from_backend() -> None:
    """指标目录必须从后端 /openapi.json 读（读不到才兜底，且页面会标注）。"""
    api_js = (WEB_DIR / "api.js").read_text(encoding="utf-8")
    assert "/openapi.json" in api_js
    assert "fallback" in api_js
