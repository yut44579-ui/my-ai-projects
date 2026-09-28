"""FR-002C · 记住账号（加固 + 补齐「清除」）

用户原话：「首先是**记住账号**之后，下次登录时就会默认有这个账号。」

这条需求本身**已经实现**（`sra.remembered-name` + 载入回填），所以本 TASK 做的是
"加固 + 补齐缺失的一小块"，不是重做。评审对这条的要求是**极其克制** —— 它不是账号管理系统。

★ 冻结原则（评审原话，也写在 `web/session.js` 的注释里）：
    记住账号 ≠ 记住登录状态 ≠ 记住密码

本文件盯的就是这条边界，逐条对上验收 Gate：
    B1 勾选后登录成功 → 账号名被记住
    B2 重新打开页面 → 账号框自动填充最近一次账号
    B3 重新打开页面 → "记住账号"勾选状态也被保持
    B4 未勾选 → 不保存（且清掉上一次记住的）
    B5 「清除已记住账号」真的能清掉
    B6 localStorage 里**不存在任何密码**
    B7 没新增自动登录（重开页面仍然要求登录）
    B8 没新增多账号管理器（只有一个账号名键）

（B9 登录/注册/忘记密码/验证码/游客无回归、F1 全量 pytest，由整套测试 + 真浏览器走查覆盖，
  见 `scripts/fr002c_remember_cdp.mjs`。）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"


def read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def js() -> str:
    return read("session.js")


def html() -> str:
    return read("index.html")


def body_of(source: str, signature: str) -> str:
    """取出某个顶层函数的函数体（从签名到它自己的那个收尾大括号）。"""
    found = re.search(re.escape(signature) + r"\s*\{(.*?)\n  \}", source, re.S)
    assert found, f"找不到 {signature} 的实现"
    return found.group(1)


# ════════════════════════════════════════════════════════════════════════
# B1 勾选后登录成功 → 账号名被记住
# ════════════════════════════════════════════════════════════════════════
def test_B1_勾选后登录成功才记账号名() -> None:
    """记账号只发生在**登录成功之后**，而且是按勾选框的状态记的。

    失败的那条路（账号或密码不对 / 验证码不对）绝不许写这条记录 ——
    否则敲错一次密码就把一个不存在的账号名记住了。
    """
    source = js()
    line = 'rememberName(remember && remember.checked ? checked.name : "");'
    assert line in source, "登录成功那条路上没有按勾选框保存账号名"
    # 保存之后要顺手把「已记住账号 xxx 清除」那一行对回来（不然用户看不到自己记了谁）
    after = source.split(line, 1)[1][:200]
    assert "renderRememberNote();" in after, "记完账号没有刷新「已记住账号」那一行"
    # 账号 / 密码不对的那条路：清密码、换验证码，但**不许碰**这条记录
    failed = re.search(r'if \(handleCaptchaError\("account", err\)\) return;(.*?)\n    \}',
                       source, re.S)
    assert failed, "找不到登录失败的那条分支"
    assert "rememberName" not in failed.group(1), "登录失败也去写记住账号（敲错密码会记住一个错的账号名）"


# ════════════════════════════════════════════════════════════════════════
# B2 / B3 重新打开页面 → 账号框回填 + 勾选状态保持
# ════════════════════════════════════════════════════════════════════════
def test_B2_B3_重开页面回填账号并保持勾选() -> None:
    """打开页面时把这一块对回本机记着的样子：账号框预填 + 勾选框勾上。

    密码框**不在**这一段里 —— 它永远从空的开始（冻结原则第三条）。
    """
    source = js()
    assert "function applyRemembered()" in source, "找不到「打开页面时回填」的那个函数"
    body = body_of(source, "function applyRemembered()")
    assert "const name = rememberedName();" in body, "回填的口径不是本机记着的那条记录"
    assert "nameInput.value = name;" in body, "账号框没有按记住的名字预填"
    assert "rememberBox.checked = !!name;" in body, "勾选框没有跟着记住的状态走"
    assert "renderRememberNote();" in body, "回填时没有亮出「已记住账号」那一行"
    # 只预填账号这一个框：密码框、验证码一概不许被这段碰
    for forbidden in ("login-pwd", "login-captcha", "reg-"):
        assert forbidden not in body, f"回填时碰了不该碰的框：{forbidden}"
    assert "pwd" not in body.lower() and "password" not in body.lower(), \
        "回填这一段的代码里出现了密码字样"
    # 打开页面（boot）与退出登录回到登录页：两条路都要走这一趟
    assert "applyRemembered();" in body_of(source, "function boot()"), \
        "打开页面时没有回填记住的账号"
    assert "applyRemembered();" in body_of(source, "function logout(reason)"), \
        "退出登录回到登录页时没有回填记住的账号"


# ════════════════════════════════════════════════════════════════════════
# B4 未勾选 → 不保存，且把上一次记住的也清掉
# ════════════════════════════════════════════════════════════════════════
def test_B4_未勾选不保存且清掉上一次记住的() -> None:
    """未勾选时存的必须是**空**（走 removeItem 那条），不是"什么都不做"。

    另外：把勾**摘掉**的那一刻就清 —— 不用等到下次登录才生效
    （用户摘勾的意思就是"别记我了"，拖到下次登录才清会让人以为没生效）。
    """
    source = js()
    assert 'remember.checked ? checked.name : ""' in source, \
        "未勾选时没有把记录清成空（会留着上一次记住的账号）"
    forget = body_of(source, "function rememberName(name)")
    assert "localStorage.setItem(NAME_KEY, name)" in forget
    assert "localStorage.removeItem(NAME_KEY)" in forget, "清记录那条路没有真的删掉这个键"
    # 摘勾就地清
    handler = re.search(r'rememberToggle\.addEventListener\("change", \(\) => \{(.*?)\n    \}\);',
                        source, re.S)
    assert handler, "勾选框没有绑定「摘勾就清」的处理"
    assert 'rememberName("");' in handler.group(1), "摘掉勾没有把上一次记住的账号清掉"
    assert "renderRememberNote();" in handler.group(1), \
        "摘掉勾之后「已记住账号」那一行没有跟着收起来"


# ════════════════════════════════════════════════════════════════════════
# B5 「清除已记住账号」真的能清掉
# ════════════════════════════════════════════════════════════════════════
def test_B5_清除已记住账号真的能清掉() -> None:
    """用户看得见、点得到，而且点下去是**真的删掉**（不是只把输入框擦干净）。

    位置：登录卡片里、记住账号勾选框的下一行，只在"已经记着"的时候出现。
    """
    source, page = js(), html()
    # ① 位置：在登录表单里面、勾选框下面（看得见）
    form = re.search(r'<form class="login-form" id="login-form".*?</form>', page, re.S)
    assert form, "找不到登录表单"
    assert 'id="login-remember"' in form.group(0), "登录表单里没有「记住账号」勾选框"
    note = re.search(r'<p class="remember-note" id="remember-note"[^>]*hidden>', form.group(0))
    assert note, "登录表单里没有「已记住账号」那一行（或它不是默认收起的）"
    assert form.group(0).index('id="login-remember"') < form.group(0).index('id="remember-note"'), \
        "那一行没有排在勾选框下面"
    # ② 形态：写着记的是谁 + 一个点得到的「清除」按钮
    assert 'id="remember-who"' in page and 'id="btn-forget-account"' in page
    assert "已记住账号" in page, "那一行没有说清记的是哪个账号"
    assert ">清除</button>" in page, "缺少「清除」按钮"
    # ③ 真的接了事件（不是点不动的死按钮）
    assert 'const forgetAccountButton = $("btn-forget-account");' in source
    assert 'forgetAccountButton.addEventListener("click", forgetAccount);' in source
    # ④ 点了之后：记录删掉 + 账号框清空 + 勾选框取消 + 那一行收起 + 给人话回执
    body = body_of(source, "function forgetAccount()")
    assert 'rememberName("");' in body, "「清除」没有真的删掉那条记录"
    assert 'nameInput.value = "";' in body, "「清除」之后账号框还留着名字（看着像没清掉）"
    assert "rememberBox.checked = false;" in body, "「清除」之后勾选框还勾着"
    assert "renderRememberNote();" in body, "「清除」之后那一行没有收起来"
    assert "toast(FORGET_TOAST" in body, "「清除」之后没有给用户一句回执"
    assert "FORGET_TOAST" in source and "已清除记住的账号" in source, "回执的话不是人话"
    # 它只是"把本机这条记录删掉"，不是什么危险操作：不许出现确认框那一套
    for banned in ("confirm(", "alert(", "prompt("):
        assert banned not in body, f"清除账号是个轻动作，不该弹 {banned}"


# ════════════════════════════════════════════════════════════════════════
# B6 localStorage 里不存在任何密码
# ════════════════════════════════════════════════════════════════════════
def test_B6_本机存储里不存在任何密码() -> None:
    """逐个键检查：session.js 往本机存的**只有两个键**，里面都只可能是账号名 / 身份描述。

    这条不是"扫一眼没有 password 字样"，而是把 write 的口径锁死：
    写进本机的键名只能是 WHO_KEY 与 NAME_KEY，别的一律算越界。
    """
    source = js()
    written = set(re.findall(r"localStorage\.setItem\(\s*([A-Za-z_$][\w$]*)\s*,", source))
    read_keys = set(re.findall(r"localStorage\.(?:getItem|removeItem)\(\s*([A-Za-z_$][\w$]*)\s*",
                               source))
    assert written == {"WHO_KEY", "NAME_KEY"}, \
        f"本机存储里出现了没申报的写入键：{sorted(written - {'WHO_KEY', 'NAME_KEY'})}"
    assert read_keys <= {"WHO_KEY", "NAME_KEY"}, \
        f"本机存储里出现了没申报的读取键：{sorted(read_keys - {'WHO_KEY', 'NAME_KEY'})}"
    # 键名与键值两处都不许沾密码这个词
    keys = re.findall(r'const (\w*KEY\w*)\s*=\s*"([^"]+)"', source)
    assert keys, "找不到本机存储的键名声明"
    for const_name, key in keys:
        assert const_name in {"WHO_KEY", "NAME_KEY"}, f"多了一个键：{const_name}"
        assert "pwd" not in key and "pass" not in key and "secret" not in key, \
            f"键名里出现了密码字样：{key}"
    assert {k for _, k in keys} == {"sra.who", "sra.remembered-name"}, \
        f"本机存储的键名被改了（老用户的记录会丢）：{sorted(k for _, k in keys)}"
    # 记住账号这条：写进去的必须就是**那个字符串本身**（不是包装过的对象 / 编码过的东西）
    remember = body_of(source, "function rememberName(name)")
    assert "localStorage.setItem(NAME_KEY, name)" in remember, \
        "写进去的不是账号名本身（被包装 / 编码过了）"
    for banned in ("JSON.stringify", "btoa", "encodeURIComponent"):
        assert banned not in remember, f"账号名被 {banned} 处理过才存（存的不再是明文账号名）"
    for banned in ("password", "pwd", "secret"):
        assert banned not in remember.lower(), f"记住账号这条路上出现了密码字样：{banned}"


# ════════════════════════════════════════════════════════════════════════
# B7 没新增自动登录
# ════════════════════════════════════════════════════════════════════════
def test_B7_记住账号不等于自动登录() -> None:
    """记住的那条记录**只**用来预填输入框，绝不参与"要不要给登录页"的判断。

    判断仍然是老的 readWho()（那条"这次是谁"的记录，退出登录就会清掉）；
    记着账号、但没有那条记录 → 照样先给登录页，照样要输密码 + 验证码。
    """
    source = js()
    boot = body_of(source, "function boot()")
    assert "const who = readWho();" in boot, "打开页面的判断不再看本机那条身份记录"
    assert boot.index("const who = readWho();") < boot.index("applyRemembered();"), \
        "回填账号排在了「给不给登录页」的判断之前（顺序反了）"
    # 回填这一段自己不许去写身份记录 / 不许把人"放进来"
    body = body_of(source, "function applyRemembered()")
    for banned in ("writeWho", "WHO_KEY", "enter(", "state.who ="):
        assert banned not in body, f"回填账号这一步顺手把人放进来 / 写了身份记录：{banned}"
    # 预填的只是账号框，密码框照旧是空的
    assert 'nameInput.value = name;' in body
    assert "login-pwd" not in body, "回填时把密码框也填了（那就成了记住密码，越界）"
    # 忘记密码 / 找回流程里的"一次性凭证"照样不许被存起来
    for banned in ("sessionStorage", "document.cookie"):
        assert banned not in source, f"出现了不该有的存储方式：{banned}"


# ════════════════════════════════════════════════════════════════════════
# B8 没新增多账号管理器
# ════════════════════════════════════════════════════════════════════════
def test_B8_只记最近一个账号且没有账号管理器() -> None:
    """最多记 1 个：存的是**一个字符串**，覆盖式写入 —— 换账号不会攒出一串。

    而且登录页上没有账号下拉 / 账号列表 / 账号切换器这类东西。
    """
    source, page = js(), html()
    # ① 存的是一个字符串：读出来直接就是它，没有解析 / 没有数组
    reader = body_of(source, "function rememberedName()")
    assert 'return localStorage.getItem(NAME_KEY) || "";' in reader, \
        "读出来的不是那一个账号名（像是存了列表）"
    for banned in ("JSON.parse", "split(", "[0]"):
        assert banned not in reader, f"这条记录被当成列表读了：{banned}"
    # ② 覆盖式写入：同一个键，写第二次就是换一个（不会有第二个键跟着长出来）
    assert source.count("localStorage.setItem(NAME_KEY") == 1, \
        "账号名被写进了不止一个键（会攒出账号列表）"
    # ③ 登录页上没有任何"账号管理器"的形态（只看登录卡片那一块：
    #    页面别处有工作簿下拉之类的正经控件，与"账号"无关）
    gate = re.search(r'<div class="login-gate" id="login-gate".*?</section>', page, re.S)
    assert gate, "找不到登录页那一块"
    for banned in ("<select", "datalist", "账号列表", "切换账号", "账号切换", "账号管理器"):
        assert banned not in gate.group(0), f"登录页出现了多账号的东西：{banned}"
    # 只记 1 个：这句话要真的写清楚（免得下次有人"顺手"扩成列表）
    assert "最近 1 个" in source or "只记" in source, "代码里没有写清最多只记一个"


# ════════════════════════════════════════════════════════════════════════
# 冻结原则写进代码注释（评审点名的原话）
# ════════════════════════════════════════════════════════════════════════
def test_冻结原则写进代码注释() -> None:
    """评审原话：`记住账号 ≠ 记住登录状态 ≠ 记住密码` —— 必须落在代码里，不只落在文档里。

    （所以这里读的是**原文**，不是剥掉注释之后的版本。）
    """
    source = js()
    assert "记住账号 ≠ 记住登录状态 ≠ 记住密码" in source, \
        "冻结原则没有写进 session.js 的注释（下次很容易被「顺手」扩成记住登录状态）"
    assert "FR-002C" in source, "这一段代码没有标注它属于哪条需求"


# ════════════════════════════════════════════════════════════════════════
# B9 登录 / 注册 / 忘记密码 / 验证码 / 游客：一条没动
# ════════════════════════════════════════════════════════════════════════
def test_B9_登录注册找回验证码游客那几条路一条没动() -> None:
    """本 TASK 只碰"记住账号"这一块，别处的形状逐字对上（改动越界就会被这条抓住）。"""
    source = js()
    # 三栏 + 两张附加卡（FR-001A 的找回 / 改密）还在
    assert 'account: { title: "账号登录"' in source
    assert 'register: { title: "注册账号"' in source
    assert 'guest: { title: "游客登录"' in source
    assert 'reset: "reset-pane", change: "pwd-change-form"' in source
    # 三张验证码（登录 / 注册 / 找回）各在各的位上
    slots = re.search(r"const CAPTCHA_SLOTS = \{(.*?)\n  \};", source, re.S)
    assert slots, "找不到验证码的槽位表"
    for which in ("account:", "register:", "reset:"):
        assert which in slots.group(1), f"验证码少了 {which} 那一张"
    # 忘记密码的四步流程与改密那一屏都还在
    assert "function openReset()" in source and "function beginPasswordChange(" in source
    assert "function logout(reason)" in source and "function clearWho()" in source
    # 游客那一条路也在（受限清单只写在一处，没被复制成第二份）
    assert "const RESTRICTED = {" in source and "GUEST_BAR_TEXT" in source
    # 新加的代码没有伸手去改判定"能不能改密码"这类授权逻辑
    for banned in ("canReset", "isAdmin() &&", "role ==="):
        assert banned not in source.split("function forgetAccount()")[1][:400], \
            f"「清除账号」这一步碰到了授权判断：{banned}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
