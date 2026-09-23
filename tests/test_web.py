"""tests/test_web.py · TASK-002 前端验收测试（静态层 + 契约层）。

════════════════════════════════════════════════════════════════════════
【本文件测什么 / 不测什么】
════════════════════════════════════════════════════════════════════════
本文件用 FastAPI 的 `TestClient`（进程内）测**能测死的那部分**：

  ① 静态挂载真的生效（GET / 返回 web/index.html，三个静态文件 200）；
  ② 挂载**没有吃掉** /api/*（既有路由优先级更高，未知 /api/xxx 仍返回统一错误体 JSON）；
  ③ 三栏骨架在页面上真实存在（左导航 7 项 / 中央 7 个页面 / 右侧面板 Tab）；
  ④ 每个页面都有**空状态**（不是只有标题）—— 空状态是实现的一部分，不是装饰；
  ⑤ 深色设计规格真的落地了（三层明度阶梯的三个色值 + 1px 描边色 + 主色 + 响应式断点）；
  ⑥ 五种 UI 状态在代码里都有落点（loading / empty / success / error / disabled）；
  ⑦ 页面代码里**没有**假东西：无外部 CDN/框架、无 setTimeout 假装成功、无 TODO、
     无写死的业务数字（316412.16 / 19950 之类）、没有"已开启"这类假状态；
  ⑧ 前端确实调用了那几条真实端点（辅助证据；主证据是报告里逐个按钮的调用链）。

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

# 八个页面（与 web/index.html 的 <section class="page" id="page-xxx">、app.js 的 ROUTES 一致）
# 「异常发现」是指令里点名的第六个导航页（AC-04：总览/销售/客户/产品/异常/周报），归口 TASK-008
ROUTES = ["overview", "sales", "customers", "products", "anomaly", "weekly", "data", "settings"]

# 前端**必须**调用的真实端点（缺一个说明有区块是摆设）
REQUIRED_ENDPOINTS = ["/api/upload", "/api/tasks", "/api/executions", "/api/health",
                      "/run", "/runs", "/openapi.json"]

# 禁止出现的造假特征（辅助扫描；主证据是报告里的调用链）
FORBIDDEN_IN_FRONTEND = ["setTimeout", "mock", "Mock", "TODO", "FIXME", "占位", "已开启"]

# 禁止硬编码的业务结果（这些数字只能来自后端响应）
FORBIDDEN_NUMBERS = ["316412.16", "316,412.16", "19950", "19,950"]

# 深色设计规格（docs/设计规格-深色企业级.md）里必须落地的 token
REQUIRED_TOKENS = ["#080B12", "#0F1622", "#0A111C", "#1E293B", "#2563EB"]


def read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def test_frontend_dir_exists() -> None:
    assert (WEB_DIR / "index.html").is_file(), "web/index.html 不存在"
    for name in ("app.js", "api.js", "style.css"):
        assert (WEB_DIR / name).is_file(), f"web/{name} 不存在"


# ════════════════════════════════════════════════════════════════════════
# ① 静态挂载
# ════════════════════════════════════════════════════════════════════════
def test_root_serves_index_html() -> None:
    response = client.get("/")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    for route in ROUTES:
        assert f'id="page-{route}"' in body, f"页面缺少 page-{route}"
        assert f'id="nav-{route}"' in body, f"导航缺少 nav-{route}"
    # 三栏容器 + 顶栏
    assert 'class="topbar"' in body and 'class="sidebar"' in body and 'class="workspace"' in body
    assert 'class="rightpanel"' in body
    for marker in ("AI 结论", "行动计划", "系统状态", "数据管理"):
        assert marker in body, f"页面缺少关键区块：{marker}"


def test_static_assets_served() -> None:
    expectations = {
        "/app.js": ("javascript", "DOMContentLoaded"),
        "/api.js": ("javascript", "class ApiError"),
        "/style.css": ("css", "--bg-page"),
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
    for path in ("/api/tasks", "/api/executions"):
        assert client.get(path).status_code == 200, path


def test_unknown_api_path_still_returns_unified_error_json() -> None:
    """/api/* 的未知路径必须仍走 API 的错误体（不是静态文件的 404 页面）。"""
    response = client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    payload = response.json()
    assert "error" in payload and "code" in payload["error"]
    assert "detail" in payload                      # 冻结的旧契约字段仍在


# ════════════════════════════════════════════════════════════════════════
# ③④ 三栏骨架 + 每页都有明确空状态
# ════════════════════════════════════════════════════════════════════════
def test_every_page_has_an_empty_state() -> None:
    """每个页面都必须有空状态块（不能只有标题）—— 空状态是实现的一部分。"""
    html = read("index.html")
    for route in ROUTES:
        match = re.search(
            rf'<section class="page" id="page-{route}".*?(?=<section class="page"|</main>)',
            html, re.S,
        )
        assert match, f"找不到 page-{route} 区块"
        section = match.group(0)
        assert 'class="empty"' in section, f"page-{route} 没有空状态块（不能只有标题）"
        assert "empty-title" in section and "empty-hint" in section, \
            f"page-{route} 的空状态缺少标题/说明"


def test_layout_is_three_column_and_responsive() -> None:
    css = read("style.css")
    assert "grid-template-areas" in css, "布局不是网格三栏"
    for area in ("topbar", "sidebar", "workspace", "rightpanel"):
        assert area in css, f"栅格缺少 {area} 区域"
    assert "@media" in css, "没有响应式断点（窗口尺寸变化会挤坏布局）"
    assert "--w-sidebar: 200px" in css and "--w-rightpanel: 355px" in css, "三栏宽度不符合设计规格"
    assert "--h-topbar: 60px" in css, "顶栏高度不符合设计规格"


def test_dark_enterprise_tokens_are_present() -> None:
    """设计规格的三条高级感来源：三层明度阶梯 / 1px 描边 / 高密度小字 + 单点大数字。"""
    css = read("style.css")
    for token in REQUIRED_TOKENS:
        assert token in css, f"缺少设计 token {token}"
    assert "1px solid var(--border)" in css, "卡片不是 1px 描边（深色主题不该用阴影堆叠）"
    assert "tabular-nums" in css, "数字没有等宽对齐（表格会歪）"
    assert "font-size: 30px" in css, "KPI 数值不是设计规格里的 28~32px 大字号"


def test_five_ui_states_have_real_anchors() -> None:
    """五种状态都要有实际落点（不是写在文档里的口号）。"""
    html, css, js = read("index.html"), read("style.css"), read("app.js")
    assert "spinner" in css and "spinner" in js, "缺少 loading 态"
    assert 'class="empty"' in html, "缺少 empty 态"
    assert ".badge.success" in css and '"success"' in js, "缺少 success 态"
    assert "banner error" in css or ".banner.error" in css, "缺少 error 态"
    assert "errorBanner" in js, "错误没有统一出口"
    assert ":disabled" in css and "disabled" in html, "缺少 disabled 态"


def test_disabled_controls_explain_themselves() -> None:
    """禁用的控件必须写明"为什么禁用 / 归哪个 TASK"，且不得出现"已开启"这类假状态。

    TASK-004 之后自然语言入口**已经真的接通**，所以它从"禁用名单"里出来了：
    这里反过来断言它**不再禁用**（启用它正是那个 TASK 的交付物之一）。
    剩下的三个企业化开关仍然必须是"禁用 + 能解释为什么"。
    """
    html, js = read("index.html"), read("app.js")
    for control in ("sec-rbac", "sec-acl", "sec-circuit"):
        match = re.search(rf'<[^>]*id="{control}"[^>]*>', html)
        assert match, f"找不到控件 {control}"
        assert "disabled" in match.group(0), f"{control} 不是禁用状态（企业化能力尚未实现）"
    # 自然语言入口（顶栏 + hero + 三个示例 chip）：TASK-004 已交付，必须可用
    for control in ("nl-input", "nl-ask", "hero-nl-input", "hero-nl-btn",
                    "chip-summary", "chip-trend", "chip-products"):
        match = re.search(rf'<[^>]*id="{control}"[^>]*>', html)
        assert match, f"找不到控件 {control}"
        assert "disabled" not in match.group(0), f"{control} 还是禁用的（TASK-004 已交付，应可用）"
    assert "INERT_HINT" in js and "TASK-011" in js and "TASK-012" in js and "TASK-013" in js
    assert "已开启" not in html and "已开启" not in js


# ════════════════════════════════════════════════════════════════════════
# ⑦ 无造假
# ════════════════════════════════════════════════════════════════════════
def test_page_has_no_external_dependency() -> None:
    """不得引任何外部脚本/样式（框架、CDN 一律禁止）。"""
    html = read("index.html")
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == [], f"页面引了外部资源：{external}"
    for marker in ("vue", "react", "jquery", "cdn."):
        assert marker not in html.lower(), f"页面疑似引了 {marker}"


def test_frontend_has_no_fake_shortcuts() -> None:
    for name in ("app.js", "api.js", "index.html", "style.css"):
        body = read(name)
        for marker in FORBIDDEN_IN_FRONTEND:
            assert marker not in body, f"{name} 里出现禁止的造假特征：{marker!r}"


def test_frontend_has_no_hardcoded_business_numbers() -> None:
    for name in ("app.js", "api.js", "index.html"):
        body = read(name)
        for number in FORBIDDEN_NUMBERS:
            assert number not in body, f"{name} 里写死了业务数字：{number}"


def test_kpi_and_trend_come_from_backend() -> None:
    """KPI 与走势图必须由后端响应渲染出来（不是写死的示例曲线）。"""
    js = read("app.js")
    assert "amount_display" in js and "rows_in_range" in js, "KPI 没有用后端字段"
    assert "listExecutions" in js, "走势图没有读 /api/executions"
    assert "show(svg)" in js and "hide(svg)" in js, "走势图没有「有数据才画、没数据就空状态」的分支"
    html = read("index.html")
    assert re.search(r'<svg id="trend-chart"[^>]*hidden', html), "走势图默认应是隐藏（空状态）"


def test_frontend_calls_the_real_endpoints() -> None:
    body = read("api.js") + read("app.js")
    for endpoint in REQUIRED_ENDPOINTS:
        assert endpoint in body, f"前端没有调用 {endpoint}"


def test_download_uses_backend_download_url() -> None:
    """下载按钮只能用后端返回的 download_url，不许前端自己拼产出路径。"""
    app_js = read("app.js")
    assert "download_url" in app_js
    assert "/api/download" not in app_js, "app.js 自己拼了下载路径（应由后端 download_url 提供）"


def test_refresh_restores_from_backend() -> None:
    """刷新恢复必须靠重新请求后端，而不是靠内存/本地存储里的残留状态。"""
    js = read("app.js")
    assert "location.hash" in js, "路由不是 hash 路由（刷新后停不在同一页）"
    assert "refreshAll" in js and "loadTasks" in js and "loadExecutions" in js
    assert "localStorage" not in js, "用了 localStorage 存状态（刷新恢复必须来自后端）"


def test_metric_catalog_comes_from_backend() -> None:
    """指标目录必须从后端 /openapi.json 读（读不到才兜底，且页面会标注）。"""
    api_js = read("api.js")
    assert "/openapi.json" in api_js
    assert "fallback" in api_js


# ════════════════════════════════════════════════════════════════════════
# DOM id 交叉检查（没有浏览器时，对"一打开就 null 崩"最有效的静态检查）
# ════════════════════════════════════════════════════════════════════════
# 结构性 id：由 JS **运行时拼接**或由 CSS/SVG 内部引用，本来就不该出现在字符串字面量里
STRUCTURAL_ID_PREFIXES = ("page-", "nav-", "rp-pane-")
STRUCTURAL_IDS = {"trend-fill"}


def test_every_dom_id_used_by_js_exists_in_html() -> None:
    html_ids = set(re.findall(r'id="([^"]+)"', read("index.html")))
    used = set(re.findall(r'\$\("([^"]+)"\)', read("app.js")))
    missing = sorted(used - html_ids)
    assert not missing, f"app.js 用了 index.html 里不存在的 id：{missing}"


def test_no_static_ornament_ids_in_html() -> None:
    """反向：HTML 里的 id 必须被 JS 用到，否则就是"页面上的静态摆设"。

    （页面/导航/Tab 面板的 id 是 JS 运行时按前缀拼的，见 STRUCTURAL_ID_PREFIXES；
      trend-fill 是 SVG 渐变，由 url(#trend-fill) 引用。）
    """
    html_ids = set(re.findall(r'id="([^"]+)"', read("index.html")))
    js = read("app.js")
    referenced = set(re.findall(r'"([A-Za-z][A-Za-z0-9_-]*)"', js))
    referenced |= set(re.findall(r"'([A-Za-z][A-Za-z0-9_-]*)'", js))
    unused = sorted(
        html_id for html_id in html_ids - referenced
        if html_id not in STRUCTURAL_IDS
        and not html_id.startswith(STRUCTURAL_ID_PREFIXES)
    )
    assert not unused, f"index.html 里有没被 JS 用到的 id（疑似静态摆设）：{unused}"
