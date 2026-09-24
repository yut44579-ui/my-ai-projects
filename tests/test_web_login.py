"""tests/test_web_login.py · STEP B-UI：登录页 / 用户区 / 在线状态四态的静态验收。

════════════════════════════════════════════════════════════════════════
【本文件测什么 / 不测什么】
════════════════════════════════════════════════════════════════════════
测（没有浏览器也能抓住"改回去"的那部分）：
  ① 登录页真的在页面上（账号 / 游客两个入口、脚本顺序、初始是"显示"）；
  ② 顶栏用户区 = 头像 + 名字 + 状态点 + 状态文字 + 用户菜单；
  ③ 四态的颜色与文字一处声明、四处落点；
  ④ **忙碌中由真实分析请求驱动**（评审点名的那条：不许前端自己 setStatus("busy")）；
  ⑤ 密码只做长度检查 —— 不摘要、不存、不比对、不发令牌；
  ⑥ 用户看得见的地方没有技术字样，也没有把本机状态吹成安全能力；
  ⑦ 未开通的入口（忘记密码 / 注册 / 个人设置）点了有话说；
  ⑧ 退出即清本机会话。

不测：浏览器里的样子与点击手感 —— 那份由 `scripts/session_check.mjs`
（node 跑**同一份** session.js 源文件，验状态迁移）+ Hermes 的 CDP 走查覆盖。
本文件不假装能替浏览器。

★ 本轮**不做**真实认证（那是后端的事）：不比对密码、不发令牌、不建服务端会话。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

WEB_DIR = PROJECT_ROOT / "web"

LOGIN_IDS = ("login-gate", "login-form", "login-name", "login-pwd", "btn-login",
             "login-name-msg", "login-pwd-msg", "btn-pwd-eye", "login-remember",
             "ltab-account", "ltab-guest", "btn-guest", "guest-pane", "guest-name",
             "link-forgot", "link-register", "toast")
STATUS_ORDER = ("online", "busy", "away", "offline")


def read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def strip_comments(source: str) -> str:
    """剥掉注释，只留"会画到屏幕上的东西"（与 test_web.py 同一套最小状态机）。"""
    out: list[str] = []
    i, n = 0, len(source)
    quote = ""
    previous = ""
    while i < n:
        char = source[i]
        pair = source[i:i + 2]
        if quote:
            if char == "\\":
                out.append(source[i:i + 2])
                i += 2
                continue
            if char == quote:
                quote = ""
            out.append(char)
            i += 1
            continue
        if source[i:i + 4] == "<!--":
            end = source.find("-->", i + 4)
            i = n if end < 0 else end + 3
            continue
        if pair == "/*":
            end = source.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if pair == "//":
            end = source.find("\n", i)
            i = n if end < 0 else end
            continue
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


# ════════════════════════════════════════════════════════════════════════
# B-01 / B-03 登录页与用户区真的在页面上
# ════════════════════════════════════════════════════════════════════════
def test_B01_登录页在页面上且先于主界面() -> None:
    html = read("index.html")
    for control in LOGIN_IDS:
        assert f'id="{control}"' in html, f"登录页少了 {control}"
    # 两个入口：账号登录 + 游客登录（扫码登录本轮已砍）
    assert "账号登录" in html and "游客登录" in html
    assert "扫码" not in html, "扫码登录本轮不做"
    # 打开页面时登录页是**显示**的（hidden 由 session.js 按本机记录决定）
    gate = re.search(r'<div class="login-gate" id="login-gate"[^>]*>', html)
    assert gate and "hidden" in gate.group(0), "登录页初始状态不对（应是显示）"
    # 脚本顺序：session.js 排在 app.js 之前（先决定给不给登录页，再读后端数据）
    assert html.index('src="/session.js"') < html.index('src="/app.js"')
    # 未登录时主界面被锁住（滚动也锁）
    assert "body.is-locked" in read("style.css")


def test_B03_顶栏用户区是头像加名字加状态() -> None:
    html = read("index.html")
    css = read("style.css")
    top = re.search(r'<header class="topbar".*?</header>', html, re.S)
    assert top, "找不到顶栏"
    top_html = top.group(0)
    for piece in ('id="btn-user"', 'id="avatar"', 'id="user-name"',
                  'id="user-dot"', 'id="user-status-text"', 'id="user-menu"'):
        assert piece in top_html, f"顶栏用户区少了 {piece}"
    for status in STATUS_ORDER:
        assert f'id="um-status-{status}"' in top_html, f"用户菜单少了 {status} 项"
    assert 'id="um-settings"' in top_html and 'id="um-logout"' in top_html
    assert top_html.count('id="um-status-') == 4
    # 加了用户区不许把「分析」挤成竖排（STEP A 的教训）
    assert ".topbar .nl-entry input { flex: 1 1 auto" in css
    assert ".topbar .btn, .topbar .icon-btn { flex: 0 0 auto; white-space: nowrap; }" in css
    # 窄窗口先收状态文字、再收名字 —— 名字不能一窄就整个消失
    assert ".user-status-text { display: none; }" in css


# ════════════════════════════════════════════════════════════════════════
# B-04 / B-06 四态：颜色一处声明、文字有落点、忙碌中由真请求驱动
# ════════════════════════════════════════════════════════════════════════
def test_B04_四态的颜色与文字都落地了() -> None:
    css, js = read("style.css"), read("session.js")
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(0)
    for token, value in (("--st-online", "#22c55e"), ("--st-busy", "#ef4444"),
                         ("--st-away", "#f59e0b")):
        assert f"{token}: {value}" in root, f"状态色没在 :root 里声明：{token}"
    assert "--st-offline: var(--td-gray-color-8)" in root, "离线色应复用 TDesign 的灰"
    for status in STATUS_ORDER:
        assert f".dot.st-{status}" in css, f"缺状态点样式 .dot.st-{status}"
    for text in ("在线", "忙碌中", "离开", "离线"):
        assert f'"{text}"' in js, f"状态文字缺 {text}"
    assert '"user-status-text"' in js and "user-dot" in js


def test_B06_忙碌中由真实分析请求驱动() -> None:
    """评审原话：busy 必须由分析任务状态驱动，不许前端 setStatus("busy") 就当真的在忙。"""
    app_js, session_js = read("app.js"), read("session.js")
    assert "Session.trackRequest(API.chat(asked))" in app_js, \
        "提交提问没有走真实请求的生命周期（忙碌中会变成假的）"
    body = re.search(r"function trackRequest\(promise\)\s*\{(.*?)\n  \}", session_js, re.S)
    assert body, "找不到 trackRequest"
    inner = body.group(1)
    assert 'setStatus("busy"' in inner, "请求发出时没有进入忙碌中"
    assert inner.count('setStatus("online"') >= 2, "成功/失败两条路都要回到在线"
    assert 'setStatus("busy"' not in app_js, "app.js 不该自己设忙碌状态"
    # 离开态靠同一个 1 秒心跳；阈值默认 10 分钟，可用地址栏短阈值演示
    assert "AWAY_MINUTES = 10" in session_js, "离开阈值默认不是 10 分钟"
    assert "setInterval(tick, 1000)" in session_js
    assert 'get("away")' in session_js, "缺少验收用的短阈值开关"


# ════════════════════════════════════════════════════════════════════════
# B-05 / B-11 只记名字，绝不碰密码；不做任何真实认证
# ════════════════════════════════════════════════════════════════════════
def test_B05_B11_只做形态校验且不碰密码() -> None:
    js = read("session.js")
    assert "name.length < 2" in js and "name.length > 20" in js
    assert "password.length < 6" in js
    for banned in ("sha256", "SHA256", "hash(", "crypto", "btoa", "token", "Token",
                   "sessionStorage", "cookie", "fetch("):
        assert banned not in js, f"session.js 里出现了不该有的东西：{banned}"
    assert "sra.remembered-name" in js and "rememberName" in js
    stored = re.search(r"JSON\.stringify\(\{(.*?)\}\)", js)
    assert stored, "找不到本机记录的写法"
    assert "name" in stored.group(1) and "kind" in stored.group(1)
    assert "password" not in stored.group(1) and "pwd" not in stored.group(1)


# ════════════════════════════════════════════════════════════════════════
# B-10 用户看得见的地方没有技术字样，也不吹安全
# ════════════════════════════════════════════════════════════════════════
def test_B10_界面不出现技术字样也不吹安全() -> None:
    html = strip_comments(read("index.html"))
    js = strip_comments(read("app.js") + read("session.js"))
    for banned in ("TASK-", "/api/", "openapi", "白名单", "数字闸门", "sha256",
                   "鉴权", "RBAC", "未启用", "不校验登录身份"):
        assert banned not in html, f"index.html 里还有技术字样：{banned}"
    for banned in ("TASK-", "/api/", "openapi", "鉴权", "RBAC", "未启用"):
        assert banned not in js, f"页面逻辑里还有技术字样：{banned}"
    # 后端字段名（file_id）只许出现在"读响应 / 拼请求"的语句里，
    # 一行里同时出现"渲染调用"和它，就说明它被画到界面上了
    for line in js.splitlines():
        if "file_id" not in line:
            continue
        for call in ("textContent", "setText", "innerHTML", "innerText", "appendChild",
                     "createTextNode", "title =", "text("):
            assert call not in line, f"这一行把后端字段名画到界面上了：{line.strip()[:90]}"
    # 如实标注"这是本机模式"，并且不宣称账号在线
    assert "本地模式" in html, "登录页没有如实标注这是本机模式"
    assert "不保存、不比对" in html, "没有说清密码只是形态检查"
    assert "游客" in html and "随机" in html, "游客名的含义没说清楚"
    assert "本机状态" in read("session.js"), "状态提示没有说清这是本机状态"


# ════════════════════════════════════════════════════════════════════════
# 诚实边界：未开通的入口有话说；退出即清
# ════════════════════════════════════════════════════════════════════════
def test_B05_未开通的入口给人话提示() -> None:
    html, js = read("index.html"), read("session.js")
    for label in ("忘记密码", "立即注册", "个人设置"):
        assert label in html, f"缺少入口：{label}"
    for control in ("link-forgot", "link-register", "link-help", "link-privacy", "um-settings"):
        assert f'id="{control}"' in html, f"缺少未开通入口：{control}"
        assert f'"{control}"' in js, f"{control} 没有绑定点击事件（会变成点不动的死按钮）"
    assert "尚未开通" in js
    assert "toast" in js and 'id="toast"' in html, "缺少人话提示的落点"


def test_B08_退出即清本机会话() -> None:
    js = read("session.js")
    body = re.search(r"function logout\(\)\s*\{(.*?)\n  \}", js, re.S)
    assert body, "找不到 logout"
    inner = body.group(1)
    assert "clearWho()" in inner, "退出没有清本机记录"
    assert 'setStatus("offline"' in inner, "退出后状态不是离线"
    assert 'show($("login-gate"))' in inner, "退出后没有回到登录页"
    assert "resetForm()" in inner, "退出后没有把表单复位（密码框要清空）"
    boot = re.search(r"function boot\(\)\s*\{(.*?)\n  \}", js, re.S).group(1)
    assert "readWho()" in boot and 'setStatus("online"' in boot, "刷新恢复没接上"


# ════════════════════════════════════════════════════════════════════════
# 真的跑一遍：node 执行 web/session.js（登录→提问→离开→手动切态→退出）
# ════════════════════════════════════════════════════════════════════════
def test_B13_session逻辑真的跑得起来() -> None:
    """node 不在的环境直接 skip —— 静态检查验不了"状态迁移对不对"，这个能。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，跳过（不影响其余检查）")
    script = PROJECT_ROOT / "scripts" / "session_check.mjs"
    assert script.is_file(), "缺少 scripts/session_check.mjs"
    done = subprocess.run([node, str(script)], capture_output=True, text=True,
                          cwd=str(PROJECT_ROOT), timeout=180)
    tail = "\n".join(done.stdout.strip().splitlines()[-20:])
    assert done.returncode == 0, f"session.js 的行为检查没通过：\n{tail}\n{done.stderr[-800:]}"
    assert "未通过 0" in done.stdout, f"检查里有失败项：\n{tail}"
