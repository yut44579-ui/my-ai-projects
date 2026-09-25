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

from app.ai.answer import (  # noqa: E402
    SECTION_ACTIONS,
    SECTION_CONTRIBUTION,
    SECTION_TITLES,
    SECTION_WHAT,
    SECTION_WHY,
    currency_guard,
)
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

# 深色设计规格（docs/设计规格-深色企业级.md）里必须落地的 token。
# 2026-09-24 用户要求「UI 参照腾讯 TDesign 官方 Design Token」，
# 于是自造的深蓝黑（#080B12/#0A111C/#0F1622/#1E293B）换成 TDesign 灰度阶，
# 主色 #2563EB 换成 TDesign 深色品牌色（brand-8）。这里钉的是**换血后的真实值**：
#   三层明度阶梯 = gray-14 页面 / gray-13 区块 / gray-12 卡片；描边 = gray-11；主色 = brand-8。
REQUIRED_TOKENS = ["#181818", "#242424", "#2c2c2c", "#393939", "#4582e6"]


def read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def strip_comments(source: str) -> str:
    """把**注释**剥掉，只留"会画到屏幕上的东西"。

    「界面不许出现技术细节」这条针对的是用户看得见的文字；注释是留给开发者的，
    用户看不见（`// GET /api/tasks/{id} 的响应` 这种说明留着反而有用）。
    所以检查前先剥注释：HTML 的 `<!-- -->` + JS/CSS 的 `/* */` 与行尾 `//`。

    为什么不是简单的按行切：`//` 可能出现在字符串里（`"https://…"`）或正则里
    （`replace(/[&<>"']/g, …)`），按行切会误伤或漏切 —— 这里用一个最小状态机，
    只在**引号/正则之外**才算注释。它不追求完整 JS 语法，够本项目用即可。
    """
    out: list[str] = []
    i, n = 0, len(source)
    quote = ""            # 当前是否在字符串里（" / ' / `）
    previous = ""         # 上一个非空白字符，用来判断 `/` 是除号还是正则开头
    while i < n:
        char = source[i]
        pair = source[i:i + 2]
        if quote:
            if char == "\\":            # 转义，连吃两个字符
                out.append(source[i:i + 2])
                i += 2
                continue
            if char == quote:
                quote = ""
            out.append(char)
            i += 1
            continue
        if source[i:i + 4] == "<!--":   # HTML 注释
            end = source.find("-->", i + 4)
            i = n if end < 0 else end + 3
            continue
        if pair == "/*":                # 块注释（CSS/JS 通用）
            end = source.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if pair == "//":                # 行注释
            end = source.find("\n", i)
            i = n if end < 0 else end
            continue
        # 正则字面量：`/` 出现在"表达式位置"（前面是 ( , = : [ ! & | ? { } ; 或行首）时
        # 才是正则，否则是除号。正则内部可能有引号和 `/`（字符类里），必须整段吃掉，
        # 否则 `/[&<>"']/g` 里的 `'` 会被当成字符串开头，把后面整段代码都算进字符串。
        if char == "/" and previous in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";"):
            j = i + 1
            in_class = False
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                elif source[j] == "/" and not in_class:
                    break
                elif source[j] == "\n":
                    break
                j += 1
            out.append(source[i:j + 1])
            i = j + 1
            previous = "/"
            continue
        if char in "\"'`":
            quote = char
        if not char.isspace():
            previous = char
        out.append(char)
        i += 1
    return "".join(out)


def test_frontend_dir_exists() -> None:
    assert (WEB_DIR / "index.html").is_file(), "web/index.html 不存在"
    for name in ("app.js", "api.js", "session.js", "style.css"):
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
        "/session.js": ("javascript", "requireLogin"),
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


def test_ui_tokens_are_declared_once_and_used() -> None:
    """TDesign token 化：**一处声明、全局引用**，别处不许再硬编码色值/圆角。

    用户 2026-09-24：「在 web/style.css 顶部用 :root 声明上表所有变量（一处声明），
    其余地方不许再出现硬编码色值/圆角」。这条把它变成可执行的检查：
      ① 每个 var() 引用的变量都真的有声明（避免拼错变量名 → 悄悄用了继承值/初始值）；
      ② :root 之外没有 #rrggbb / rgba() 字面量（语义色底纹那几处也在 :root 里声明）。
    """
    css = read("style.css")
    declared = set(re.findall(r"^\s*(--[A-Za-z0-9-]+)\s*:", css, re.M))
    used = set(re.findall(r"var\((--[A-Za-z0-9-]+)\)", css))   # 要求右括号：注释里的 var(--td-*) 不算
    assert not used - declared, f"引用了没声明的变量（拼错会静默失效）：{sorted(used - declared)}"
    # :root 块之外的硬编码色值
    root_match = re.search(r":root\s*\{(.*?)\n\}", css, re.S)
    assert root_match, "style.css 顶部没有 :root 声明块"
    # 注释里写"gray-14 就是 #181818"是给人看的说明，不算硬编码 → 先剥注释
    outside = strip_comments(css.replace(root_match.group(0), ""))
    for pattern, what in ((r"#[0-9a-fA-F]{6}\b", "十六进制色值"), (r"rgba?\(", "rgba 色值")):
        hits = sorted(set(re.findall(pattern, outside)))
        assert not hits, f":root 之外还有硬编码{what}：{hits}"
    for radius in re.findall(r"border-radius:\s*([^;]+);", outside):
        assert "var(" in radius or radius.strip() in ("0", "0px", "inherit"), \
            f":root 之外还有硬编码圆角：{radius.strip()}"
    # TDesign 的档位 token 必须在（表格行高/按钮高度/间距不许到处手写 padding）
    for tier in ("--td-compact-height", "--td-medium-height", "--td-loose-height"):
        assert tier in declared, f"缺少 TDesign 尺寸档位 {tier}"
    # 圆角默认收紧到 3px（大圆角只留给头像/胶囊）
    assert "--td-radius-default: 3px" in css, "控件圆角没有收到 TDesign 的 3px"
    for radius_token in ("--td-radius-round: 999px", "--td-radius-circle: 50%"):
        assert radius_token in css, f"缺少 {radius_token}（头像/胶囊要用）"
    # 交互元素必须补齐 hover / active / disabled / focus
    for state in (":hover", ":active", ":disabled", ":focus-visible"):
        assert state in css, f"交互元素缺少 {state} 状态"


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
    # 自然语言入口（顶栏 + hero + 示例 chip）：TASK-004 已交付、TASK-006 又补了两个 chip
    # （客户排行 / 退货分析）—— 新能力必须在页面上点得到，不能只藏在后端接口里。
    for control in ("nl-input", "nl-ask", "hero-nl-input", "hero-nl-btn",
                    "chip-summary", "chip-trend", "chip-products",
                    "chip-customers", "chip-returns"):
        match = re.search(rf'<[^>]*id="{control}"[^>]*>', html)
        assert match, f"找不到控件 {control}"
        assert "disabled" not in match.group(0), f"{control} 还是禁用的（TASK-004 已交付，应可用）"
    # 禁用理由要写清楚（业务话术），但**不许**再写"归哪个开发阶段"——
    # 用户 2026-09-24 明确要求界面不出现 TASK-xxx 这类开发进度标注。
    assert "INERT_HINT" in js and "尚未实现" in js
    assert "TASK-0" not in strip_comments(js) and "TASK-0" not in strip_comments(html)
    assert "已开启" not in html and "已开启" not in js


# ════════════════════════════════════════════════════════════════════════
# ⑦-b 界面只给业务语言：技术实现细节一律不渲染（后端字段照旧、只是前端不画）
# ════════════════════════════════════════════════════════════════════════
def test_界面不渲染技术细节_后端字段照旧() -> None:
    """用户 2026-09-24：Intent JSON / 调用的工具 / 数字闸门说明 / POST 路径 / TASK 标注都不许上界面。

    这条只查"画出来的东西"：注释里留开发说明是可以的（用户看不见），
    所以先把注释剥掉再查 —— 后端字段一个都没删（那些由 tests/test_chat.py 钉着）。
    """
    html, js = strip_comments(read("index.html")), strip_comments(read("app.js"))
    for text, name in ((html, "index.html"), (js, "app.js"), (strip_comments(read("session.js")), "session.js")):
        # 「白名单」是**企业安全术语**（访问控制白/黑名单，一条尚未启用的能力说明），
        # 但它在 数据管理 页是产品自己的产物）。这里只钉真正刺眼的三类。
        for banned in ("TASK-0", "/api/", "数字闸门", "Intent"):
            assert banned not in text, f"{name} 里还有技术细节：{banned}"
    # 链路面板只剩「问题 / 事实 / 回答」三块，Intent 与工具那两块连壳都不留
    for gone in ('id="chat-intent"', 'id="chat-tool"', 'id="chat-step-intent"', 'id="chat-step-tool"'):
        assert gone not in html, f"{gone} 还挂在页面上（撤掉就要连壳一起撤）"
    for gone in ("chat-intent", "chat-tool", "renderChatTool", "allowed_count"):
        assert gone not in js, f"app.js 还在渲染 {gone}"
    # 用户该看到的四段仍然在：标题由**后端**给（前端只画 section.title，不自己攒文案），
    # 所以这半钉在 answer.py 的常量上；前端"确实渲染了后端标题"钉在下一行。
    assert "title.textContent = section.title" in js, "前端没有用后端给的分段标题"
    for key in (SECTION_WHAT, SECTION_CONTRIBUTION, SECTION_WHY, SECTION_ACTIONS):
        title = SECTION_TITLES[key]
        assert title.startswith("【") and title.endswith("】"), f"分段标题格式变了：{title}"


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
    for name in ("app.js", "api.js", "session.js", "index.html", "style.css"):
        body = read(name)
        for marker in FORBIDDEN_IN_FRONTEND:
            assert marker not in body, f"{name} 里出现禁止的造假特征：{marker!r}"


def test_frontend_has_no_hardcoded_business_numbers() -> None:
    for name in ("app.js", "api.js", "index.html"):
        body = read(name)
        for number in FORBIDDEN_NUMBERS:
            assert number not in body, f"{name} 里写死了业务数字：{number}"


def test_frontend_currency_comes_from_backend() -> None:
    """金额单位（人民币「元」）**只能**从 capabilities 的 currency 声明取，前端不许写死单位。

    数据里没有货币字段 —— 前端硬编码一个「元/¥」就是"猜一个单位"，
    换了数据集（或改了声明）两边立刻对不上。
    """
    js = read("app.js")
    assert "caps.currency" in js, "前端没有读后端声明的 currency（单位从哪来？）"
    assert "currency.symbol" in js, "没有用声明里的符号"
    assert 'id="kpi-currency"' in read("index.html"), "KPI 币种图标没有可被 JS 填写的落点"


def test_frontend_has_no_wrong_currency_marks() -> None:
    """四份前端源码里**不许出现任何写死的错误币种字样**（沿用后端那把尺子：answer.currency_guard）。

    为什么复用后端函数而不是在前端测试里另写一条正则：币种错误只在**一处**定义
    （`tools.DATASET_CURRENCY` 声明的是人民币「元」），"什么算写错单位"也该只有一处定义 ——
    否则两边尺子不一致，闸门挡得住模型、挡不住前端。
    """
    for name in ("app.js", "api.js", "index.html", "style.css"):
        found = currency_guard(read(name))
        assert found == [], f"{name} 里出现写死的错误币种字样：{found}"


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
# （`um-status-*` = 用户菜单里那四个状态项，session.js 用 `um-status-${状态名}` 拼出来）
STRUCTURAL_ID_PREFIXES = ("page-", "nav-", "rp-pane-", "um-status-", "ltab-")
STRUCTURAL_IDS = {"trend-fill"}


def test_every_dom_id_used_by_js_exists_in_html() -> None:
    html_ids = set(re.findall(r'id="([^"]+)"', read("index.html")))
    used = set(re.findall(r'\$\("([^"]+)"\)', read("app.js")))
    used |= set(re.findall(r'\$\("([^"]+)"\)', read("session.js")))
    missing = sorted(used - html_ids)
    assert not missing, f"页面逻辑用了 index.html 里不存在的 id：{missing}"


def test_no_static_ornament_ids_in_html() -> None:
    """反向：HTML 里的 id 必须被 JS 用到，否则就是"页面上的静态摆设"。

    （页面/导航/Tab 面板的 id 是 JS 运行时按前缀拼的，见 STRUCTURAL_ID_PREFIXES；
      trend-fill 是 SVG 渐变，由 url(#trend-fill) 引用。）
    """
    html_ids = set(re.findall(r'id="([^"]+)"', read("index.html")))
    js = read("app.js") + read("session.js")
    referenced = set(re.findall(r'"([A-Za-z][A-Za-z0-9_-]*)"', js))
    referenced |= set(re.findall(r"'([A-Za-z][A-Za-z0-9_-]*)'", js))
    unused = sorted(
        html_id for html_id in html_ids - referenced
        if html_id not in STRUCTURAL_IDS
        and not html_id.startswith(STRUCTURAL_ID_PREFIXES)
    )
    assert not unused, f"index.html 里有没被 JS 用到的 id（疑似静态摆设）：{unused}"


# ════════════════════════════════════════════════════════════════════════
# ⑫ TASK-006：客户 / 商品能力在页面上真的可达
# ════════════════════════════════════════════════════════════════════════
def test_T006_两个新chip把问题原文写在HTML上() -> None:
    """新能力要能在页面上点一下就去问（问题原文写在 `data-question`，不是 JS 里另抄一份）。"""
    html = read("index.html")
    for chip_id, question_text in (
        ("chip-customers", "客户"),
        ("chip-returns", "退货"),
    ):
        match = re.search(rf'<button[^>]*id="{chip_id}"[^>]*data-question="([^"]+)"', html)
        assert match, f"{chip_id} 缺少 data-question"
        assert question_text in match.group(1), f"{chip_id} 的问法不对：{match.group(1)}"
    js = read("app.js")
    assert '"chip-customers"' in js and '"chip-returns"' in js, "新 chip 没绑点击事件"


def test_T006_页面支持深链提问() -> None:
    """`#/<route>?ask=<问题>` —— 真浏览器（Edge --headless --dump-dom）能靠它做端到端取证。

    前端**不解释问题**：原文从 URL 取出来，交给与手动点击完全同一个提交函数。
    """
    js = read("app.js")
    assert "askFromHash" in js
    assert 'get("ask")' in js
    assert "askQuestion(question)" in js
    # 自动提问必须发生在能力清单与页面骨架就绪之后（否则渲染会踩到 null）
    assert js.index("await refreshAll()") < js.index("askFromHash();")


def test_T006_客户与商品页不再写尚未接入() -> None:
    """TASK-006 交付后，页面上任何"客户/商品维度分析尚未接入"的说法都成了假话。"""
    html = read("index.html")
    assert "客户维度分析尚未接入" not in html
    assert "产品维度分析尚未接入" not in html
    assert "客户聚合与排行尚未接入" not in html
    # 替代文案必须把用户指到真能用的入口
    assert "自然语言问答" in html


# ════════════════════════════════════════════════════════════════════════
# ⑬ STEP A：真表格 / 顶栏重排 / 前端清理 / 导入导出工作流
#
# 这些是**静态**检查（能在没有浏览器时抓住"改回去"）：真实的表格内容、后端排序、
# 分页数字、导出文件由 scripts/stepa_e2e.py 打真服务 + 真浏览器验（那份是真证据）。
# ════════════════════════════════════════════════════════════════════════
STEPA_TABLES = ("customers", "products", "sales", "raw")


def test_STEPA_四张业务表都有容器与空状态() -> None:
    html, js = read("index.html"), read("app.js")
    for table in STEPA_TABLES:
        assert f'id="tbl-{table}"' in html, f"少了 {table} 表的容器"
        assert f'id="tbl-{table}-empty"' in html, f"{table} 表没有空状态"
    # 四张表的规格由**一处**定义（表名 / 容器 / 空状态 / 搜索列）
    assert "TABLE_SPECS" in js
    for table in STEPA_TABLES:
        assert f'table: "{table}"' in js, f"TABLE_SPECS 里没有 {table}"


def test_STEPA_分页排序筛选导出全部走后端() -> None:
    """前端只做「用户操作 → 请求参数 → 渲染结果」，绝不拿已有数据自己排。

    这条钉的是评审的硬条件：前端排序会形成第二套计算路径（HTTP API = A、页面 = B）。
    """
    js = read("app.js")
    assert "API.readTable(" in js, "业务表没有走 /api/tables/{table}"
    assert "API.exportTable(" in js, "导出没有走 /api/tables/{table}/export"
    # 点表头 → 改 sort/order → **重新请求**（不是本地重排）
    assert "panel.query.sort = column.key" in js
    assert "panel.query.page = 1" in js and "panel.load()" in js
    assert "API.readTable(spec.table, params)" in js
    # 表格行**从不**在前端被排序 / 分页切片
    for forbidden in ("items.sort(", "items.slice(", ".localeCompare(b.sales_amount"):
        assert forbidden not in js, f"前端在拿已取回的行自己排：{forbidden}"


def test_STEPA_销售表是一张表_维度与指标() -> None:
    """不许做成 daily/weekly/country 三个重复页面：一张表切维度与指标。"""
    html, js = read("index.html"), read("app.js")
    assert 'SALES_DIMENSIONS = [["day", "按日"], ["week", "按周"], ["country", "按国家"]]' in js
    for metric in ("销售额", "订单数", "客户数", "客单价"):
        assert f'"{metric}"' in js, f"指标里少了「{metric}」"
    for dimension in ("day", "week", "country"):
        assert f'"{dimension}"' in js
    # 只有一张销售表容器，也没有 daily_sales / weekly_sales / country_sales 这类页面
    assert html.count('id="tbl-sales"') == 1
    for stray in ("daily_sales", "weekly_sales", "country_sales"):
        assert stray not in html and stray not in js, f"出现了重复页面：{stray}"
    # 换维度 / 指标要重新向后端要数据（不是前端换列名）
    assert "dimensionSelect.addEventListener" in js and "metricSelect.addEventListener" in js


def test_STEPA_原始数据默认只给一页() -> None:
    """54 万行不许默认全塞：原始数据是第二层的独立入口，默认 50 行。"""
    html, js = read("index.html"), read("app.js")
    assert "TABLE_PAGE_SIZES = [20, 50, 100, 200]" in js
    assert "page_size: 50" in js, "业务表默认不是 50 行一页"
    assert 'href="#/raw"' in html, "原始数据不是独立入口"
    assert "原始数据" in html


def test_STEPA_顶栏只剩五件套且不切字() -> None:
    """用户反馈的「UI 排布顺序以及视野方面受到严重遮挡」：8 个元素硬塞 → 按钮被压成单字。"""
    html, css = read("index.html"), read("style.css")
    top = re.search(r'<header class="topbar".*?</header>', html, re.S)
    assert top, "找不到顶栏"
    top_html = top.group(0)
    for piece in ('class="brand"', 'id="datasource-chip"', 'id="nl-input"',
                  'id="nl-ask"', 'id="btn-menu"', 'class="user-area"'):
        assert piece in top_html, f"顶栏少了 {piece}"
    # 能力清单那种长文案不在顶栏（它会把「分析」挤成竖排残字）
    assert 'id="nl-note"' not in top_html
    assert "hero-nl-note" in html, "顶栏撤掉的说明没有落到首页（能力清单得有地方显示）"
    # 弹性分工：提问框吃满剩余宽度；按钮一律不压缩、不换行
    assert ".topbar .nl-entry input { flex: 1 1 auto" in css
    assert ".topbar .btn, .topbar .icon-btn { flex: 0 0 auto; white-space: nowrap; }" in css
    # 窄窗口下只收窄（省略号 + hover），不许把提问框整块藏起来
    assert ".nl-entry { display: none" not in css
    assert "text-overflow: ellipsis" in css


def test_STEPA_页面不出现技术实现信息() -> None:
    """用户铁律（docs/前端展示规范.md）：技术实现留在代码里，界面上只给业务内容。

    只查"会画到屏幕上的东西"——注释与后端字段名不算（先剥注释）。
    `openapi` 只作为**比较用的枚举值**出现在 app.js 里（判断清单是否同步），不渲染，故不在名单；
    `file_id` 是后端字段名（属性访问），也不渲染，所以这里钉的是**渲染出来的标签**。
    """
    html, js = strip_comments(read("index.html")), strip_comments(read("app.js"))
    for text, name in ((html, "index.html"), (js, "app.js"), (strip_comments(read("session.js")), "session.js")):
        for banned in ("TASK-", "/api/", "sha256", "哈希", "stored_path",
                       "数字闸门", "白名单", "Repository", "Executor", "pytest"):
            assert banned not in text, f"{name} 里还有技术实现信息：{banned}"
    # 以前挂在页面上的字段标签（哈希 / 文件编号 / 落盘路径）连字符串都不许留
    for label in ("数据哈希", "文件编号", "落盘路径", "文档编号"):
        assert label not in html and label not in js, f"页面上还有「{label}」"
    assert "shortHash" not in js, "短哈希辅助函数没删干净（删了才不会再被拼进页面）"
    # 首页两张卡改成真表格，不再写"本卡片不单独铺一张表"这种实现说明
    assert "不单独铺一张" not in html and "本卡片不单独" not in html


def test_STEPA_首页客户与产品是真数据() -> None:
    """用户原话：需要像 Excel 表格那样有实际数据来反映客户真实情况。"""
    html, js = read("index.html"), read("app.js")
    for root in ("ov-customers", "ov-products"):
        assert f'id="{root}"' in html and f'id="{root}-empty"' in html
    # 取数与业务表同一个后端查询（page_size=5 + 后端排序），前端只渲染
    assert "API.readTable(spec.table" in js
    assert 'page_size: 5' in js and 'sort: "sales_amount"' in js
    assert "renderOverviewTops" in js


def test_STEPA_导入向导工作流与诚实边界() -> None:
    """上传 → 检查（工作表/前 N 行/字段映射）→ 登记；后端没实现的能力必须如实说。"""
    html, js = read("index.html"), read("app.js")
    for control in ("ds-file-input", "ds-inspect", "ds-sheet", "ds-name",
                    "ds-preview-table", "ds-preview-count", "ds-mapping",
                    "btn-ds-inspect", "btn-ds-import", "ds-import-result"):
        assert f'id="{control}"' in html, f"导入向导少了 {control}"
    assert "API.inspectDataset(" in js and "API.importDataset(" in js
    # 登记结果照抄后端的话（"分析未开通"），不美化成"导入成功即可分析"
    assert "analysis_note" in js
    # 数据集输入 vs 文档输入：两个入口、两类后缀，没有把上传升级成万能入口
    assert 'accept=".xlsx,.xlsm,.csv"' in html
    assert 'accept=".docx,.pdf"' in html


# ════════════════════════════════════════════════════════════════════════
# ⑮ 顶栏「数据源」那一栏不许永远停在「读取中」（用户实测反馈）
#
# 用户原话：「一直在加载是什么鬼」。实测接口全是 200（health 0.06s / datasets 2.7s），
# 浏览器控制台零错误 —— 是**前端状态没落地**：健康检查那一段要等所有请求都结束才渲染，
# 任何一条慢请求都能把它拖在"读取中"上。三条修法：先到先画 / 没读到说人话 / 请求有上限。
# ════════════════════════════════════════════════════════════════════════
def test_顶栏数据源不许永远停在读取中() -> None:
    js, api_js, css = read("app.js"), read("api.js"), read("style.css")
    # ① 健康检查一到就先画顶栏那一栏（不等其它请求）
    assert "loadHealth().then(renderDatasourceChip).catch(renderDatasourceUnknown)" in js, \
        "健康检查没有「先到先画」，一条慢请求就能把它拖在读取中"
    assert "function renderDatasourceChip()" in js
    # ② 这一轮没读到 → 人话 + 说清能做什么（不是继续挂着"读取中…"）
    assert "function renderDatasourceUnknown()" in js
    assert "暂时读不到，可刷新页面重试" in js
    assert "if (!state.health) renderDatasourceUnknown();" in js
    # 首屏那几个静态壳（数据源 / 模板 / 状态 / 校验）也要一起落地
    for anchor in ("fact-data", "fact-template", "fact-state", "fact-code"):
        assert anchor in js
    # ③ 健康检查本身有上限：超时按"读不到"处理（用浏览器原生能力，不起定时器）
    assert "timeoutSignal(8000)" in api_js and "AbortSignal.timeout" in api_js
    assert 'err.name === "TimeoutError"' in api_js
    assert "读取超时" in api_js
    # 登录页阶段不显示数据源状态（主界面整块不渲染 —— 也就不会被看到"读取中"）
    assert "body.is-locked .app { display: none; }" in css


def test_用hidden收放的元素不会被display压住() -> None:
    """`hidden` 属性会被**自带 display 的 class** 压过去 —— 这是本项目踩过的真坑：

    `.spinner` 写了 `display: inline-block`，于是 `hidden` 的转圈照样显示在按钮上，
    看着就像"一直在加载"（用户实测截图里就是它）。凡是自带 display 的元素，
    要么给它补一条 `.xxx[hidden] { display: none }`，要么别用 hidden 收放。
    """
    import re as _re

    css, html = read("style.css"), read("index.html")
    js = read("app.js") + read("session.js")
    display_classes = set()
    for match in _re.finditer(r"\.([a-zA-Z][\w-]*)\s*\{([^}]*)\}", css):
        if _re.search(r"\bdisplay\s*:\s*(?!none)", match.group(2)):
            display_classes.add(match.group(1))
    guarded = set(_re.findall(r"\.([a-zA-Z][\w-]*)\[hidden\]", css))
    hidden_ids = set(_re.findall(r'(?:hide|show)\(\$?"([\w-]+)"\)', js))
    hidden_ids |= set(_re.findall(r'\$\("([\w-]+)"\)\.hidden', js))
    problems = []
    for element in _re.finditer(r"<[a-z]+[^>]*id=\"([\w-]+)\"[^>]*>", html):
        tag, element_id = element.group(0), element.group(1)
        if element_id not in hidden_ids:
            continue
        for group in _re.findall(r'class="([^"]+)"', tag):
            for name in group.split():
                if name in display_classes and name not in guarded:
                    problems.append((element_id, name))
    assert not problems, f"这些元素用 hidden 收不住（自带 display 的 class 没兜底）：{problems}"
    # 已经踩过的那个必须留着兜底
    assert ".spinner[hidden] { display: none; }" in css


# ════════════════════════════════════════════════════════════════════════
# ⑯ 排序状态机 + 时间范围（用户 2026-09-25 报的两个问题）
#
# 这一组只钉**前端那半**（静态层）：排序状态存在哪、四条规则各自的代码落点、
# 文字提示在不在。真正的行为证据由 `scripts/sort_range_cdp.mjs` 打真浏览器量
# （点表头 → 切指标 → 翻页，看后端返回的顺序有没有被覆盖）。
#
# 为什么必须钉死"按日不许改成指标降序"：那是本次最容易搞反的一条 ——
# 用户抱怨"127 排在 147 前面"，看着像排序 bug，其实是**时间序列本来就该按日期读**。
# 换成按订单数降序能让他当场满意，但按日这张表的阅读语义就毁了（评审冻结规格第 3 条）。
# ════════════════════════════════════════════════════════════════════════
def test_排序状态机_切指标不许覆盖人工排序():
    js = read("app.js")
    # 状态只有一处：panel.sort_source（default = 还没人工排过 / user = 用户点过表头）
    assert 'sort_source: "default"' in js, "没有排序来源状态"
    assert 'panel.sort_source = "user"' in js, "点表头没有把状态置为 user"
    assert 'panel.sort_source = "default"' in js, "缺少回到 default 的路径（切维度 / 重置）"
    # Rule 2：只有 default 态才让排序跟着指标走 —— user 态必须原样保留
    assert 'if (panel.sort_source !== "user")' in js, "切指标时会覆盖用户的人工排序"
    # Rule 3/4：切维度与点表头都要重新向后端要数据（不是本地重排）
    assert "dimensionSelect.addEventListener" in js and "metricSelect.addEventListener" in js
    assert "panel.query.sort = column.key" in js
    # Rule 5：翻页不许碰排序 —— 否则第一页按订单数、第二页按销售额，分页就废了
    prev_at = js.index("prev.addEventListener")
    paging = js[prev_at:js.index("if (spec.sales) {", prev_at)]      # 只取翻页那两个监听器
    assert "prev.addEventListener" in paging and "next.addEventListener" in paging
    assert "sort" not in paging, "翻页路径里混进了排序逻辑"
    assert "dimensionSelect" not in paging and "metricSelect" not in paging, \
        "翻页路径里改了维度/指标"


def test_当前排序除了表头高亮还要有一句白话():
    """评审实测：只高亮表头，用户根本没注意到"现在按什么排"。三层提示缺一不可。"""
    js, css = read("app.js"), read("style.css")
    assert "当前排序：" in js, "没有「当前排序」文字行"
    assert "sort-line" in js and ".sort-line" in css, "文字行没有样式落点"
    assert "sortLineText" in js, "排序文案没有统一出处"
    assert ".table th.sorted-asc" in css and ".table th.sorted-desc" in css, "表头高亮丢了"


def test_时间范围显示数据覆盖而不是数据源登记时间():
    """用户报的"填 2026 年却弹出 2010 年"根因：页面上挂着数据源的**登记时间**，
    被他当成了数据时间。业务主界面只许显示数据覆盖区间。"""
    js = strip_comments(read("app.js"))
    assert "数据覆盖：" in js, "没有「数据覆盖」文案"
    assert "dataset.date_range" in js, "覆盖范围不是从数据覆盖区间取的"
    assert "更新于" not in js, "业务表上还挂着数据源的登记时间"
    # 只钉**业务表**这一处（数据管理页的数据源清单显示登记时间是合理的，留着）
    assert "payload.dataset.updated_at" not in js, "业务表还在读登记时间字段"


def test_时间范围越界要即时提示并可一键用数据范围():
    js = read("app.js")
    assert "超出数据范围" in js, "越界时没有提示"
    assert "使用数据范围" in js, "没有「使用数据范围」快捷入口"
    assert "refreshRangeHint" in js, "提示没有随输入即时刷新"
    assert "input.min" in js and "input.max" in js, "日期框没有钉上数据覆盖边界"
