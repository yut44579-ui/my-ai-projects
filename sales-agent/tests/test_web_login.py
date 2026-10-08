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
             # FR-002C 记住账号：记的是谁看得见 + 一个点得到的「清除」
             "remember-note", "remember-who", "btn-forget-account",
             "ltab-account", "ltab-guest", "btn-guest", "guest-pane", "guest-name",
             "link-forgot", "link-register", "toast",
             # 图形验证码（登录一张、注册一张：输入框 + 图 + 出错提示）
             "login-captcha", "login-captcha-img", "login-captcha-msg",
             "reg-captcha", "reg-captcha-img", "reg-captcha-msg",
             # 帮助 / 隐私抽屉
             "info-drawer", "info-pane-help", "info-pane-privacy", "btn-info-close")
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


def test_B01_登录页整屏铺满且不露主界面() -> None:
    """用户实测反馈：「没有铺满整个页面」—— 原来是"半透明遮罩 + 居中卡片"，
    卡片浮在被压暗的主界面之上，观感就是个弹窗。

    现在：整块铺满视口、左右分栏各占一半、锁住时主界面**不渲染**
    （既不露底，也不会把主界面那些「读取中…」混进页面文本里）。
    """
    css = read("style.css")
    gate = re.search(r"\.login-gate\s*\{(.*?)\}", css, re.S).group(1)
    assert "position: fixed" in gate and "inset: 0" in gate
    assert "place-items: center" not in gate, "还在把登录页当居中卡片"
    assert "padding" not in gate, "外层还留着边距（铺不满）"
    shell = re.search(r"\.login-shell\s*\{(.*?)\}", css, re.S).group(1)
    assert "min-height: 100%" in shell and "grid-template-columns" in shell
    # 分栏用 minmax(0, …)：内容再宽也不会把页面撑出横向滚动条
    assert "minmax(0, 1.08fr)" in shell and "minmax(0, .92fr)" in shell
    # 表单区：外层铺满，里面的内容限宽 420px 只为好读
    card = re.search(r"\.login-card\s*\{(.*?)\}", css, re.S).group(1)
    assert "justify-content: center" in card and "var(--bg-page)" in card
    inner = re.search(r"\.login-card-inner\s*\{(.*?)\}", css, re.S).group(1)
    assert "max-width: 420px" in inner and "margin: 0 auto" in inner
    brand_inner = re.search(r"\.login-brand-inner\s*\{(.*?)\}", css, re.S).group(1)
    assert "max-width" in brand_inner
    # 锁住时主界面不渲染（不露底 + 页面文本里不会出现它的"读取中"）
    assert "body.is-locked .app { display: none; }" in css
    # 窄屏：上下堆叠（不许横向滚动）；矮窗口再收紧一次间距
    narrow = re.search(r"@media \(max-width: 900px\)\s*\{(.*?)\n\}", css, re.S).group(1)
    assert ".login-shell { grid-template-columns: minmax(0, 1fr); }" in narrow
    assert "@media (max-height: 760px)" in css
    # 登录页自己不许出现"还在读"的话（读到的数字直接出现，读不到就说读不到）
    gate_html = re.search(r'<div class="login-gate".*?</div>\s*</div>\s*</div>', read("index.html"), re.S)
    assert gate_html, "找不到登录页那块结构"
    assert "读取中" not in gate_html.group(0), "登录页上还有「读取中」这种等待文案"


def test_B01_登录表单是账号形态而不是填姓名() -> None:
    """用户实测反馈：「不要弄姓名这个，按照正常腾讯 QQ 那种账号形式的」。

    形态 = 账号 + 密码 + 记住账号 + 忘记密码 / 注册账号 + 一个登录按钮。
    （本轮起注册与登录都是**真的**：密码由后端存成不可还原的校验值并比对 ——
      但**前端这一层**依旧不碰密码运算，B-05/B-11 那几条盯着这一点。）
    """
    html, js = read("index.html"), read("session.js")
    assert "姓名" not in html and "姓名" not in js, "登录页还留着「姓名」这个说法"
    assert ">账号<" in html, "字段标签没改成「账号」"
    assert 'placeholder="手机号 / 邮箱 / 用户名"' in html, "账号框的提示语不是「手机号 / 邮箱 / 用户名」"
    assert 'maxlength="40"' in html, "账号框长度上限没跟着校验一起放宽到 40"
    assert "记住账号" in html and "记住我" not in html, "「记住我（只记名字）」没改成「记住账号」"
    assert "登 录" in html and "进入系统" not in html, "主按钮不是 QQ 那种「登 录」"
    assert "忘记密码" in html and "注册账号" in html, "底部少了忘记密码 / 注册账号"
    assert "登录后使用完整功能" in html and "填个名字" not in html, "副标题还是「填个名字就能进」"
    assert "登录后使用完整功能" in js, "切标签时的副标题没跟着改"
    # 记住账号存的是账号本身，键名沿用旧写法（老用户的「记住」不会丢）
    assert "sra.remembered-name" in js
    # 游客那一栏说清"不留身份、退出后名字会变"
    assert "不留身份" in html and "不留身份" in js
    # 登录后右上角的口径也是账号，不再叫姓名；而且要跟上"账号是本机注册的、登录时对过密码"
    assert "账号（本机注册，登录时已校验密码）" in js, "用户区身份说明没跟上账号已经是真的"
    assert "名字由你自己填" not in js
    # 还没做的入口给人话提示，且说清是"尚未开通"而不是做成点不动的死按钮
    # （"该功能尚未开通"这句话现在只剩「个人设置」那一栏在用 —— 帮助/隐私已经是真内容了）
    assert "尚未开通" in js and "个人设置" in js
    # 但「注册」不在"尚未开通"之列：它已经是真栏目（点一下就切到注册表单）
    assert "注册还没做" not in js and "注册还没做" not in html, "注册还留着「还没做」那套说辞"


def test_B01_密码框用眼睛图标而不是显示隐藏文字() -> None:
    """用户原话：「不要文字的 要一个眼睛的标志那种」。

    图标一律**内联 SVG**（原生前端，不引图标库）：睁眼 = 点了显示明文，
    闭眼（加斜线）= 明文已显示，点了藏回去。按钮里不许再有「显示 / 隐藏」两个字。
    """
    html, css, js = read("index.html"), read("style.css"), read("session.js")
    eye = re.search(r'<button class="pwd-eye".*?</button>', html, re.S)
    assert eye, "找不到密码框那个按钮"
    block = eye.group(0)
    assert ">显示<" not in block and ">隐藏<" not in block, "按钮里还写着「显示 / 隐藏」两个字"
    assert block.count("<svg") == 2, "眼睛图标应当是页面里现成的两个 SVG（睁眼 / 闭眼）"
    assert 'class="eye eye-open"' in block and 'class="eye eye-off"' in block, "两个图标缺一个"
    assert 'aria-label="显示密码"' in block and 'title="显示密码"' in block, \
        "初始态（掩码）的标签与提示应当是「显示密码」"
    # 线条风格跟页面其它图标一致：换算到 16px 后描边 ~1.2px、端点与拐角都是圆的
    eye_stroke = float(re.search(r'stroke-width="([\d.]+)"', block).group(1))
    assert abs(eye_stroke * 16 / 20 - 1.2) < 0.06, "眼睛图标的描边粗细跟其它图标对不上"
    assert 'stroke-linecap="round"' in block and 'stroke-linejoin="round"' in block, \
        "线条端点 / 拐角不是圆的（跟页面其它图标风格不一致）"
    assert 'stroke="currentColor"' in block, "图标没跟着文字颜色走（hover 变不了色）"
    assert "aria-hidden=\"true\"" in block and 'focusable="false"' in block, \
        "装饰性 SVG 要让读屏跳过（意思由按钮的 aria-label 说）"
    # 热区 ≥ 24×24，且弱化色 → hover 品牌色
    pwd_eye = re.search(r"\.pwd-eye\s*\{(.*?)\}", css, re.S).group(1)
    assert "width: 24px" in pwd_eye and "height: 24px" in pwd_eye, "眼睛热区不足 24×24（会难点）"
    assert "var(--td-font-white-3)" in pwd_eye, "默认色应当是弱化的白（--td-font-white-3）"
    assert ".pwd-eye:hover { color: var(--td-brand-color);" in css, "hover 没有变品牌色"
    # 两态靠一个类切换，图标跟着状态走（不是固定一个眼睛）
    assert ".pwd-eye .eye-off { display: none; }" in css
    assert ".pwd-eye.is-shown .eye-open { display: none; }" in css
    assert ".pwd-eye.is-shown .eye-off { display: block; }" in css
    # 状态由 JS 一处管：切类 + 同步 aria-label / title / aria-pressed
    assert "setEyeState" in js and 'classList.toggle("is-shown", shown)' in js
    for piece in ('"隐藏密码"', '"显示密码"', '"aria-label"', '"title"', '"aria-pressed"'):
        assert piece in js, f"眼睛状态没同步到 {piece}"
    assert 'button.textContent = shown ? "显示"' not in js, "还在往按钮里写文字"
    # 复位（退出登录）时回到睁眼
    reset = re.search(r"function resetForm\(\)\s*\{(.*?)\n  \}", js, re.S).group(1)
    assert "setEyeState(false)" in reset, "退出登录后眼睛没复位"
    # 不引图标库、不引外部字体
    for banned in ("iconfont", "font-awesome", "fontawesome", "cdn."):
        assert banned not in html.lower(), f"引了外部图标资源：{banned}"


def test_B01_图标型按钮只画图标且都带提示() -> None:
    """顺手扫出来的"图标型但写成文字"的按钮：刷新任务列表 / 刷新执行记录 / 收起正文。

    「分析」「登录」「确认导入」这种**动作**按钮保持文字 —— 这里只管纯 UI 开关。
    """
    html = read("index.html")
    for control, label in (("btn-reload-tasks", "刷新任务列表"),
                           ("btn-refresh-exec", "刷新执行记录"),
                           ("btn-doc-close", "收起正文")):
        found = re.search(rf'<button id="{control}".*?</button>', html, re.S)
        assert found, f"找不到 {control}"
        block = found.group(0)
        assert "icon-only" in block, f"{control} 没标成图标型按钮"
        assert block.count("<svg") == 1, f"{control} 的图标不是内联 SVG"
        assert f'title="{label}"' in block and f'aria-label="{label}"' in block, \
            f"{control} 少了 tooltip / 无障碍标签（会变成一个看不懂的图标）"
        # 线条风格与页面其它图标一致：换算到 16px 显示尺寸后描边都是 ~1.2px + 圆角端点
        # （眼睛图标是 20 格 1.5px、刷新是 24 格 1.8px —— 画布不同，落到屏幕上是同一个粗细）
        view_box = int(re.search(r'viewBox="0 0 (\d+) \d+"', block).group(1))
        stroke = float(re.search(r'stroke-width="([\d.]+)"', block).group(1))
        assert abs(stroke * 16 / view_box - 1.2) < 0.06, \
            f"{control} 描边换算到 16px 后是 {stroke * 16 / view_box:.2f}px，跟眼睛图标对不上"
        assert 'stroke-linecap="round"' in block and 'stroke-linejoin="round"' in block, \
            f"{control} 的线条端点/拐角不是圆的"
        assert 'stroke="currentColor"' in block, f"{control} 的图标没有跟着文字颜色走"
        # 按钮里不许再留着原来的文字（图标 + 文字 = 又变回文字按钮）
        for text in ("刷新任务列表", "刷新", "收起"):
            assert f">{text}<" not in block, f"{control} 里还留着「{text}」文字"
    # 纯动作按钮保持文字
    for keep in (">分析<", ">登 录<", ">确认导入<"):
        assert keep in html, f"纯动作按钮不该改成图标：{keep}"
    # 图标型按钮的样式：方形热区（至少一个中号控件那么宽）
    css = read("style.css")
    assert ".btn.icon-only, .icon-btn.icon-only" in css
    assert "width: var(--td-medium-height)" in css


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
    """前端这一层**永远**不碰密码运算：不比、不摘要、不加密、不落盘。

    本轮起"对不对"是真的了 —— 但那件事**整个在后端**（`app/accounts.py`），
    前端只把输入框里的东西原样递过去。这条测试盯着的是这道边界：
    session.js 里一旦出现摘要/编码/fetch/令牌这类词，就说明有人把密码处理挪到前端来了。
    """
    js = read("session.js")
    assert "account.length < 2" in js and "account.length > 40" in js
    assert "password.length < 6" in js
    for banned in ("sha256", "SHA256", "hash(", "crypto", "btoa",
                   "sessionStorage", "cookie", "fetch("):
        assert banned not in js, f"session.js 里出现了不该有的东西：{banned}"
    # ★ FR-001A 起的唯一一处放宽（评审冻结的三层授权决定了前端必须**提交凭证**）：
    #   忘记密码流程里有两样**不透明的一次性值**要由页面转手递回后端 ——
    #   `reset_token`（第①步 → 第②步）与 `ticket`（第②步 → 第③步）。
    #   它们进了内存、用完即废，页面拿它们做不了任何判断（"能不能改密码"由后端说了算）。
    #   放宽的同时把口子收得更紧：只允许这几种写法出现，别的"令牌"字样照样算越界；
    #   而且它们**不许被存起来**（sessionStorage / cookie / localStorage 仍然全禁）。
    allowed = {"token", "reset_token", "resetToken", "state.reset.resetToken",
               "data.reset_token", "reset_token_invalid"}
    for word in sorted(set(re.findall(r"[A-Za-z_.]*[Tt]oken[A-Za-z_.]*", js))):
        assert word in allowed, f"session.js 里出现了没申报的令牌字样：{word}"
    for ticket_word in sorted(set(re.findall(r"[A-Za-z_.]*[Tt]icket[A-Za-z_.]*", js))):
        assert ticket_word in {"ticket", "state.reset.ticket", "data.ticket",
                               "ticket_invalid"}, \
            f"session.js 里出现了没申报的票据字样：{ticket_word}"
    assert "sra.remembered-name" in js and "rememberName" in js
    # 本机那条记录只写三个字段：账号名、入口类型、显示名 —— 逐字锁住（不是"扫一眼没有密码"）
    stored = re.search(r"function writeWho\(who\)\s*\{(.*?)\n  \}", js, re.S)
    assert stored, "找不到本机记录的写法"
    body = stored.group(1)
    assert "name: who.name" in body and "kind: who.kind" in body
    assert "display_name: who.displayName" in body
    for forbidden in ("password", "pwd", "secret"):
        assert forbidden not in body, f"本机记录里写进了不该写的东西：{forbidden}"


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
    # 密码这件事要说**真话**（本轮起它真的被保存与比对，只是存的是不可还原的校验值）：
    # 说清"存的是什么、原文不存"，并且不许假装能发邮件找回（本机没有邮件可发）
    assert "加密摘要" in html and "原文" in html, "没说清密码是怎么存的"
    assert "无法用邮件找回" in html, "没如实说清忘记密码的后果"
    # 不许假装能做的能力（"验证码"本身已经是真功能了，所以这里盯的是"发出去的验证码"这类假动作）
    for fake in ("发送邮件", "重置邮件", "邮件已发送", "短信", "验证码已发送", "短信验证码"):
        assert fake not in html and fake not in js, f"页面在假装能做的能力：{fake}"
    assert "游客" in html and "随机" in html, "游客名的含义没说清楚"
    assert "本机状态" in read("session.js"), "状态提示没有说清这是本机状态"


# ════════════════════════════════════════════════════════════════════════
# 诚实边界：未开通的入口有话说；退出即清
# ════════════════════════════════════════════════════════════════════════
def test_B05_登录页每个入口点了都有反应_且不留假按钮() -> None:
    """底下一排入口的**现状**（这轮盘点过一遍，免得哪一栏又悄悄退回占位）：

        注册账号 → 真的切到注册表单（不留"尚未开通"）
        帮助     → 真的打开帮助抽屉
        隐私     → 真的打开隐私抽屉
        忘记密码 → 真的进找回流程（FR-001A 起它不再是"弹一句找不回"）
        个人设置 → 仍然"尚未开通"（这一栏确实还没做，就如实说）

    底线是同一条：**要么真能用，要么如实说没做**；不许出现点不动的死按钮。

    ★ FR-001A 改写了「忘记密码」这一条：上一版它只弹一句"本机无法找回原密码"，
      本版起点它是**进入四步找回流程**（账号+验证码 → 恢复码 → 新密码 → 完成）。
      其它三条入口的判据一个字没动。
    """
    html, js = read("index.html"), read("session.js")
    for label in ("忘记密码", "注册账号", "帮助", "隐私"):
        assert label in html, f"缺少入口：{label}"
    for control in ("link-forgot", "link-register", "link-help", "link-privacy", "um-settings"):
        assert f'id="{control}"' in html, f"缺少入口：{control}"
        assert f'"{control}"' in js, f"{control} 没有绑定点击事件（会变成点不动的死按钮）"
    # 帮助 / 隐私：真打开抽屉，不是提示条
    assert "帮助文档还没提供" not in js and "隐私说明还没提供" not in js
    assert "openInfo" in js
    # 忘记密码：点一下**进流程**（四步卡真的在页面上、四步的按钮也都绑了事件）
    forgot = re.search(r'const forgot = \$\("link-forgot"\);(.*?)\);', js, re.S)
    assert forgot, "找不到「忘记密码」的处理"
    assert "openReset" in forgot.group(1), "「忘记密码」没有进找回流程"
    assert "function openReset()" in js, "找不到找回流程的入口函数"
    for step_id in ("reset-step-1", "reset-step-2", "reset-step-3", "reset-step-4"):
        assert f'id="{step_id}"' in html, f"找回流程缺了 {step_id}"
    for button_id in ("btn-reset-step1", "btn-reset-step2", "btn-reset-step3", "btn-reset-done"):
        assert f'id="{button_id}"' in html and f'"{button_id}"' in js, \
            f"{button_id} 没接上（会变成点不动的死按钮）"
    # 本机仍然**找不回原密码**，也仍然不承诺任何"发出去的东西"——
    # 这件真话不许因为流程做出来了就悄悄删掉（它写在帮助里，见 test_help_privacy）
    for fake in ("发送邮件", "重置邮件", "短信"):
        assert fake not in js and fake not in html, f"页面在承诺做不到的事：{fake}"
    # 个人设置：确实没做，就如实说"尚未开通"
    assert "尚未开通" in js and "NOT_READY" in js
    assert "toast" in js and 'id="toast"' in html, "缺少人话提示的落点"


def test_B14_登录页有验证码且能点击换图() -> None:
    """登录与注册两张表单都要有验证码：4 位输入框 + 图 + "点图换一张"的出路。

    图必须是**图片**（<img> 的 src 由页面逻辑从后端拿到的图填进去），
    不是页面上的文字 —— 否则"看图填字"这道题就白出了。
    """
    html, js = read("index.html"), read("session.js")
    for which, input_id in (("login", "login-captcha"), ("reg", "reg-captcha")):
        assert f'id="{input_id}"' in html and f'id="{which}-captcha-img"' in html
        assert f'id="{which}-captcha-btn"' in html, f"{which} 的验证码图不是可点的"
        block = re.search(rf'<div class="captcha-row">.*?</div>', html, re.S)
        assert block, "找不到验证码那一行的结构"
        assert f'maxlength="4"' in html
        assert f'alt="验证码图片，可点击更换"' in html, "验证码图缺无障碍说明"
        assert f'aria-label="验证码图片，可点击更换"' in html
    # 图片的地址由页面逻辑填（从后端拿图），点一下换一张
    assert "loadCaptcha" in js and 'data:image/svg+xml' in js
    assert "img.src" in js
    assert "addEventListener(\"click\", reload)" in js, "点图没有换一张"
    # 提交时把"图 + 用户填的字"一起发出去
    assert "captchaPayload" in js and "captcha_id" in js and "captcha_text" in js
    # 三种验证码错法各有各的人话（用户要知道下一步干什么）
    for message in ("验证码不对，请重新输入。", "验证码已过期，已自动更换一张。",
                    "请输入图中的 4 个字符。"):
        assert message in js, f"缺少这条人话：{message}"
    assert "尝试次数过多" in js, "连续失败之后的提示没接上"
    # 退出/重登时验证码要换新的（旧的一次性，留着只会让人"填对了还被拒"）
    reset = re.search(r"function resetForm\(\)\s*\{(.*?)\n  \}", js, re.S).group(1)
    assert 'loadCaptcha("account")' in reset and 'loadCaptcha("register")' in reset


def test_B08_退出即清本机会话() -> None:
    js = read("session.js")
    body = re.search(r"function logout\([^)]*\)\s*\{(.*?)\n  \}", js, re.S)
    assert body, "找不到 logout"
    inner = body.group(1)
    assert "clearWho()" in inner, "退出没有清本机记录"
    assert 'setStatus("offline"' in inner, "退出后状态不是离线"
    assert 'show($("login-gate"))' in inner, "退出后没有回到登录页"
    assert "resetForm()" in inner, "退出后没有把表单复位（密码框要清空）"
    # 本版新增的两条（都要在退出这条路上）：
    #   ① 服务端那条本机会话也清掉（清不到不算失败，见 accounts.close_session）
    #   ② 游客的受限记号要跟着摘掉（游客态才有 ⚠，退出后一个都不该留）
    assert "authLogout" in inner, "退出没有把服务端那条本机会话清掉"
    assert "applyGuards()" in inner, "退出后没把受限入口上的 ⚠ 记号摘掉"
    assert "closeGuardModal()" in inner, "退出时没有把受限弹窗收起来"
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
