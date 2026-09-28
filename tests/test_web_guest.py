"""tests/test_web_guest.py · 游客限制 + 管理员「账号管理」界面的验收（那四条里的 ③④）。

════════════════════════════════════════════════════════════════════════
【用户原话 → 这条测试怎么盯】
════════════════════════════════════════════════════════════════════════
④ 游客：
   · **能用**：提问 / 看表格 / 看周报（只读浏览）
   · **不能用**：导入数据 / 导出报表 / 保存或修改任务 / 账号管理 / 删除操作
   · 提示方式：**只在受限处**显示 ⚠ —— 其它地方不显示（不许满屏）
   · 点受限功能 → 弹窗「需要您先登录才能使用完整服务」+「去登录」按钮
   · 顶部一条**轻量**提示「游客模式 · 部分功能受限」，**不是**满屏遮罩

③ 管理员：「系统设置」里有「账号管理」（待批准列表 + 批准/拒绝 + 已批准列表 + 停用/删除）。

本文件做**静态与结构**的核对（页面/脚本里的字与接线对不对）；
"点下去到底有没有被拦住"由 `scripts/session_check.mjs` 用真源码跑一遍来证明
（那一份是行为证据，这一份保证"接线还在、文案没走样"）。
"""

from __future__ import annotations

import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# 剥注释用**已有那一份**（test_web_login.py 里那个最小状态机），不另写一套 ——
# 两套剥法一定会在某个边界上给出不同结论，到时候分不清是哪套错了。
from test_web_login import strip_comments  # noqa: E402

WEB = PROJECT_ROOT / "web"

# 受限的五类（用户点名的）：动作名 → 界面上那个功能叫什么
RESTRICTED_ACTIONS = ("import", "export", "task", "accounts", "delete")

# 页面上**带 ⚠**的受限入口（id）——正是这些、不多不少
GUARDED_ELEMENT_IDS = (
    "menu-item-import",        # 顶栏菜单：导入数据
    "btn-imp-preview",         # 数据管理：统一导入的「预览」（FR-003 新增，申报进清单）
    "btn-imp-run",             # 数据管理：统一导入的「导入」（FR-003 新增，申报进清单）
    "btn-ds-inspect",          # 数据管理：上传并检查
    "btn-ds-import",           # 数据管理：确认导入
    "btn-upload",              # 旧报表链路：上传表格
    "btn-doc-upload",          # 文档：上传并提取
    "btn-create-task",         # 建任务（保存）
    "btn-run-task",            # 执行任务（会写执行记录）
    "btn-download",            # 下载产出 xlsx
    "btn-accounts-locked",     # 系统设置：账号管理（非管理员看到的那块）
)
# ★ FR-003 新增两个（预览 / 导入）：它们属于用户点名的「不能用：导入数据」那一类，
#   所以挂 `data-guard="import"`；这里同步申报，**不是**为了让它变绿而放宽断言 ——
#   清单仍然是"正好相等"，多一个少一个都会红。
#   顺带说明「按地区」那张卡里的 `btn-region-query` **故意不挂**：它是**只读**查询，
#   与"游客能用：提问 / 看表格 / 看周报（只读浏览）"同类，挂上去就成了满屏 ⚠。
#   `#imp-file-input`（选文件框）也不挂：它自己不下任何动作，动作在「预览/导入」两个按钮上。


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def function_body(js: str, name: str) -> str:
    """取一个函数的函数体（够用就好：靠缩进找结束，不写 JS 解析器）。"""
    match = re.search(rf"function {re.escape(name)}\([^)]*\)\s*\{{(.*?)\n  \}}", js, re.S)
    assert match, f"找不到函数 {name}"
    return match.group(1)


# ════════════════════════════════════════════════════════════════════════
# ④ 游客：提示条 + 弹窗（轻量，不是满屏遮罩）
# ════════════════════════════════════════════════════════════════════════
def test_顶部有一条游客提示条_而且是轻量的():
    html = read("index.html")
    assert 'id="guest-bar"' in html, "没有游客提示条"
    bar = re.search(r'<div class="guest-bar" id="guest-bar"[^>]*>(.*?)</div>\s*\n', html, re.S)
    assert bar, "找不到提示条那段"
    text = bar.group(1)
    assert "游客模式" in text and "部分功能受限" in text, f"提示条上的话不对：{text[:120]}"
    assert 'id="guest-bar-login"' in text and "去登录" in text, "提示条上少了「去登录」"
    # 轻量 = 就是页面上的一条，不许是盖住整页的那层（那条是 popup/遮罩该干的事）
    assert 'id="guest-bar"' in html and html.count('class="guest-bar"') == 1, "提示条只该有一条"
    assert "is-locked" not in text, "提示条不许把界面锁起来"


def test_受限弹窗_文案与按钮都是用户要的那套():
    html = read("index.html")
    modal = re.search(r'<div class="guard-modal" id="guard-modal"[^>]*>(.*?)\n</div>', html, re.S)
    assert modal, "找不到受限弹窗"
    text = modal.group(1)
    assert "需要您先登录才能使用完整服务" in text, "弹窗那句话不是用户要的"
    assert 'id="guard-login"' in text and "去登录" in text, "弹窗上少了「去登录」按钮"
    assert 'id="guard-text"' in text, "弹窗里没有写清「是哪一项受限」的那个位置"
    assert 'id="guard-backdrop"' in text, "弹窗少了可以点掉的背景层"
    # 弹窗是**弹窗**，不是整页盖死的遮罩：它带 role=dialog，可以被关掉
    assert 'role="dialog"' in text, "弹窗没有对话框语义"
    assert 'id="guard-cancel"' in text, "弹窗少了一条「取消」的退路"


def test_受限记号只出现在受限入口上():
    html = read("index.html")
    marked = set(re.findall(r'id="([^"]+)"[^>]*data-guard="([^"]+)"', html))
    ids = {element_id for element_id, _ in marked}
    assert ids == set(GUARDED_ELEMENT_IDS), \
        f"带 ⚠ 的入口跟预期不一致。多了 {ids - set(GUARDED_ELEMENT_IDS)}，" \
        f"少了 {set(GUARDED_ELEMENT_IDS) - ids}"
    for element_id, action in marked:
        assert action in RESTRICTED_ACTIONS, f"{element_id} 挂了一个不认识的动作：{action}"
    # 不受限的地方**一个记号都不许有**：提问、导航、用户区、帮助这些
    for element_id in ("nl-ask", "nl-input", "nav-overview", "nav-weekly", "nav-customers",
                       "nav-products", "nav-anomaly", "btn-user", "btn-menu", "link-help"):
        at = html.find(f'id="{element_id}"')
        assert at > 0, f"页面上找不到 {element_id}"
        tag = html[html.rfind("<", 0, at):html.find(">", at)]
        assert "data-guard" not in tag, f"{element_id} 不该是受限入口（它不受限，不该带 ⚠）"


def test_游客的五个受限动作都在清单里_允许的动作不在():
    js = read("session.js")
    block = re.search(r"const RESTRICTED = \{(.*?)\n  \};", js, re.S)
    assert block, "session.js 里没有受限清单"
    body = block.group(1)
    for action in RESTRICTED_ACTIONS:
        assert f"{action}:" in body, f"受限清单里少了 {action}"
    # 每一项都要有"给人看的名字"和"为什么"，否则弹窗里只能干说一句"不行"
    assert body.count("label:") >= len(RESTRICTED_ACTIONS)
    assert body.count("why:") >= len(RESTRICTED_ACTIONS)
    # 只读浏览类的动作**不许**出现在清单里（进了清单就等于游客用不了）
    for allowed in ("chat", "read", "table", "report", "history"):
        assert f"{allowed}:" not in body, f"「{allowed}」不该被限制（游客能看）"


# ════════════════════════════════════════════════════════════════════════
# ④ 「真的被拦住」的接线：每个受限动作的**第一行**就是拦截
# ════════════════════════════════════════════════════════════════════════
def test_每条受限动作在执行前都先问一句_不然就返回():
    """★ 这条是"不是只弹窗"的结构证据：

    受限的入口函数体里**第一句**必须是对 `allow("…")` 的判断，不通过立刻 return ——
    也就是说：请求发不出去、文件下不下来。行为上跑一遍由 session_check.mjs 负责
    （那里真的调 Session.guard() 并逐个要 false），这里保证接线没被后手改掉。
    """
    js = read("app.js")
    expected = {
        "downloadConversationReport": "export",
        "inspectDatasetFile": "import",
        "importDataset": "import",
    }
    for name, action in expected.items():
        body = function_body(js, name)
        head = next(line.strip() for line in body.splitlines() if line.strip())
        assert head.startswith(f'if (!allow("{action}")) return;'), \
            f"{name} 的第一句不是受限判断，而是：{head}"

    # 三个行内入口（下载产出 / 建任务 / 执行任务）：同样先问再动
    assert 'if (!allow("export")) return;             // 游客：不跳转、不落文件' in js, \
        "「下载产出 xlsx」没有先过受限判断"
    assert 'if (!allow("task")) return;               // 游客：弹窗后就地打住（按钮状态一点不动）' in js, \
        "「建任务」没有先过受限判断"
    assert 'if (!allow("task")) return;                 // 游客：不执行、不留执行记录、按钮状态不动' in js, \
        "「执行任务」没有先过受限判断"
    # 表格导出：按钮挂记号 + 执行处再拦一道（两条路都要堵上）
    assert 'panel.exportTo = async (format) => {\n      if (!allow("export")) return;' in js, \
        "业务表格的导出没有在执行处拦下"
    assert 'guardMark(exportXlsx, "export");' in js and 'guardMark(exportCsv, "export");' in js, \
        "两个导出按钮没有挂上受限记号"
    # 报告下载的两处入口都要拦（周报中心那个按钮 + 报告面板上的按钮）
    assert js.count('if (!allow("export")) return;') >= 4, \
        "报告 / 表格 / 产出下载的拦截点不够（有路没堵上）"


def test_拦截与记号都只认session那一处判断():
    """页面逻辑不许自己判断"我是不是游客" —— 判断只在 session.js 里有一处。"""
    js = strip_comments(read("app.js"))
    # app.js 可以**问**"我是不是游客"（Session.isGuest() ），但不许自己从身份记录里判
    assert 'kind === "guest"' not in js, "app.js 自己判断了游客身份（该走 Session.guard）"
    assert "allow(" in js and "Session.guard" in js, "app.js 没有走 session 那一处判断"
    session = read("session.js")
    assert "function guard(action)" in session, "session.js 里没有那个唯一的判断点"
    assert "function isGuest()" in session, "session.js 里没有唯一的游客判断"


# ════════════════════════════════════════════════════════════════════════
# ③ 管理员：「系统设置 → 账号管理」
# ════════════════════════════════════════════════════════════════════════
def test_系统设置里有账号管理两块_管理员看到的与别人看到的():
    html = read("index.html")
    settings = re.search(r'<section class="page" id="page-settings".*?</section>', html, re.S)
    assert settings, "找不到系统设置页"
    page = settings.group(0)
    assert 'id="set-accounts-card"' in page, "系统设置里没有「账号管理」卡片"
    assert 'id="set-accounts-locked"' in page, "没有给非管理员准备的「需要管理员身份」那块"
    # 管理员那块：待批准 + 已批准，两张表都要在
    assert 'id="set-pending-body"' in page and 'id="set-pending-count"' in page, "少了待批准列表"
    assert 'id="set-approved-body"' in page, "少了已批准列表"
    assert "等待批准" in page and "已批准的账号" in page, "两张表的标题不对"
    # 两块**默认都藏着**，由脚本按身份二选一显示（页面上永远只出现一块）
    for element_id in ("set-accounts-card", "set-accounts-locked"):
        tag = html[html.find(f'id="{element_id}"'):]
        assert 'hidden' in tag[:tag.find(">")], f"{element_id} 默认应当是收起的"


def test_账号管理那个受限按钮点得动_不是只挂个记号():
    """带 ⚠ 的按钮必须**真的能点出弹窗**：只挂记号、点下去没反应，是最糟的一种。

    （`#btn-accounts-locked` 曾经就是这样 —— 记号在、marker 在，但没绑点击。
     这条测试与 `scripts/stepc_guest_cdp.mjs` 的【3】③ 一起盯着它。）
    """
    js = read("app.js")
    body = function_body(js, "bindSettings")
    assert 'allow("accounts")' in body, "「账号管理」那个按钮没有走统一的受限判断"
    assert '$("btn-accounts-locked")' in body, "没绑到那个按钮上"
    init = function_body(js, "init")
    assert "bindSettings()" in init, "bindSettings 没有被初始化调用（绑了也没用）"


def test_账号管理走的是真端点_且只有管理员会去调():
    js = read("app.js")
    body = function_body(js, "loadAccounts")
    assert "API.listAccounts(" in body, "账号列表没走真端点"
    assert "Session.sessionId()" in body, "管理动作没有带上会话编号（后端靠它认人）"
    actions = function_body(js, "accountAction")
    assert "API.deleteAccount(" in actions and "API.reviewAccount(" in actions, \
        "审批 / 删除没有走真端点"
    assert 'action === "delete"' in actions, "删除没有与审批分开处理"
    # 只有管理员会走到拉列表这一步（由 Session.isAdmin 决定，前端不猜角色）
    admin = function_body(js, "renderAccountAdmin")
    assert "Session.isAdmin" in admin, "「账号管理」没有按管理员身份显示"
    assert "if (admin) loadAccounts();" in admin, "不是管理员也会去拉账号列表"
    # 删除是不可逆的：必须先问一句
    assert "window.confirm" in actions, "删除账号没有二次确认"


def test_管理员账号不给停用与删除的按钮():
    """后端会拒，前端就别让人白点一下（唯一的管理员删不得、停不得）。"""
    admin = function_body(read("app.js"), "loadAccounts")
    assert 'item.role === "admin"' in admin, "没区分管理员与普通账号"
    assert "管理员账号不可停用" in admin, "没说明管理员账号为什么没有那两个按钮"


def test_页面文案里不出现技术字样():
    html = strip_comments(read("index.html"))
    js = strip_comments(read("session.js") + read("app.js"))
    for banned in ("/api/", "TASK-", "session_id", "pwd_hash", "role:", "RESTRICTED"):
        assert banned not in html, f"index.html 里出现了技术字样：{banned}"
    # session.js 里出现 session_id 是**接口字段名**（读登录响应 / 拼请求），不是画到界面上的字；
    # app.js 里一次都不该出现 —— 那是"把后端字段名写进渲染语句"的常见入口。
    app = strip_comments(read("app.js"))
    for banned in ("session_id", "pwd_hash", "pwd_salt", "/api/", "TASK-"):
        assert banned not in app, f"页面逻辑里出现了技术字样：{banned}"
