"""tests/test_help_privacy.py · 登录页底部「帮助 / 隐私」两栏的验收。

════════════════════════════════════════════════════════════════════════
【本文件盯的是什么】
════════════════════════════════════════════════════════════════════════
这两栏最容易变成"看着像文档、其实在骗人"的地方。所以这里两件事一起做：

  ① **内容齐不齐**：帮助要五部分（能问什么 / 数据从哪来 / 数据里没有什么 /
     数字怎么算的 / 常见问题），隐私要四项（存哪 / 密码怎么存 / 会不会外发 / 怎么清除）；
  ② ★ **内容是不是真的**：帮助里写的那 7 类问题，**真的跑一遍**（走真计算），
     必须落到对应的那 7 个计算上；帮助里说"问这些会被拒"的维度，**真的问一遍**，
     必须真的被拒。**文档说自己能干什么，就得真能干**——这一条是这份文件的价值所在。

另外两条硬要求：
  · 用户看得见的字里**不许有技术字样**（接口路径、字段名、算法名、任务编号…）；
  · 隐私那一栏必须**如实**说"提问会外发、数字由本机算、上传的文件不外发"——
    这是用户能据以做判断的信息，含糊其辞等于骗人。
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402
from app.ai import intent as intent_module  # noqa: E402

client = TestClient(app)
WEB_DIR = PROJECT_ROOT / "web"

# 用户看得见的地方不许出现的词（业务语言之外的"实现细节"）
BANNED = ("/api/", "TASK-", "哈希", "salt", "pbkdf2", "sha256", "schema", "Repository",
          "端点", "鉴权", "RBAC", "captcha", "SVG", "token", "未启用")

# 帮助里那 7 类示例（原文必须与页面上一字不差），以及它该落到哪个计算上
HELP_EXAMPLES = (
    ("销售汇总", "2011年11月一共卖了多少？", "sales_summary"),
    ("销售趋势", "2011年11月的销售趋势，按日看", "sales_trend"),
    ("产品排行", "2011年11月卖得最好的5个产品", "top_products"),
    ("两区间比较", "2011年11月和2011年10月的销售额哪个高？", "sales_compare"),
    ("国家分布", "2011年11月销售额最高的10个国家", "sales_breakdown_by_country"),
    ("客户分析", "2011年11月销售额最高的10个客户", "customer_analysis"),
    ("商品分析", "2011年11月各商品的退货情况，前10名", "product_analysis"),
)

# 帮助里说"问不了"的维度（问题必须真的被拒）
REFUSED_QUESTIONS = (
    "2011年11月各区域的销售额",
    "2011年11月各省份的销售额",
    "2011年11月各城市的销售额",
    "2011年11月各门店的销售额",
    "2011年11月各渠道的销售额",
    "2011年11月每个销售员的销售额",
)


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
        if char in "\"'`":
            quote = char
        if not char.isspace():
            previous = char
        out.append(char)
        i += 1
    return "".join(out)


def drawer_html() -> str:
    """只取抽屉那一段的 HTML（帮助 + 隐私两栏都在里面）。"""
    html = read("index.html")
    start = html.index('id="info-drawer"')
    end = html.index('<div class="toast"', start) if '<div class="toast"' in html[start:] else len(html)
    return html[start:end]


def visible_text(fragment: str) -> str:
    """"用户看得见的那层字"：剥注释、剥标签、留下真正会显示的文字。"""
    without_comments = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    without_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", without_comments, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", without_scripts)).strip()


# ════════════════════════════════════════════════════════════════════════
# ① 两栏都"真的在"，不是占位提示
# ════════════════════════════════════════════════════════════════════════
def test_H01_帮助与隐私都是真内容而不是尚未开通():
    html, js = read("index.html"), read("session.js")
    assert 'id="info-drawer"' in html and 'id="info-pane-help"' in html \
        and 'id="info-pane-privacy"' in html, "抽屉或两个内容栏不在页面上"
    # 两个入口都绑了"打开抽屉"，而不是弹一句"尚未开通"
    assert "openInfo" in js and '"link-help", "help"' in js and '"link-privacy", "privacy"' in js
    assert "帮助文档还没提供" not in js and "隐私说明还没提供" not in js, "帮助/隐私还是占位提示"
    # 关闭这条路要有（否则用户被关在里面）
    assert 'id="btn-info-close"' in html and 'id="info-backdrop"' in html
    assert "closeInfo" in js and '"Escape"' in js, "少了关闭抽屉的方式"


def test_H01_帮助五个部分齐全():
    text = visible_text(drawer_html())
    for part in ("一、能问什么", "二、数据从哪来", "三、数据里没有什么",
                 "四、数字是怎么算出来的", "五、常见问题"):
        assert part in text, f"帮助少了「{part}」"


def test_H01_帮助里七类问题与至少四条常见问题都在():
    text = visible_text(drawer_html())
    for label, question, _tool in HELP_EXAMPLES:
        assert label in text, f"帮助少了「{label}」这一类"
        assert question in text, f"帮助里「{label}」的示例问题不是原文：{question}"
    assert "做一份销售周报" in text, "帮助里没提周报"
    # 常见问题：四条（用问号数）
    faq = drawer_html()
    questions = re.findall(r"<dt>([^<]*？)</dt>", faq)
    assert len(questions) >= 4, f"常见问题少于 4 条：{questions}"
    for keyword in ("最后几天", "客户数", "导出", "数据源"):
        assert any(keyword in q for q in questions), f"常见问题里没有问到「{keyword}」"


def test_H02_隐私四项齐全():
    text = visible_text(drawer_html())
    for part in ("一、你的数据存在哪", "二、密码怎么存", "三、会不会把数据发到外面去",
                 "四、怎么清掉"):
        assert part in text, f"隐私少了「{part}」"
    # 四类数据各说了一句
    for kind in ("账号信息", "提问与回答记录", "你上传的数据文件", "报表产出"):
        assert kind in text, f"没说清这一类数据存在哪：{kind}"
    # 清除的两条路
    assert "退出登录" in text and "彻底清除" in text, "没说清怎么清掉数据"


def test_H03_隐私如实说了外发那一条():
    """★ 最容易含糊的一条：提问会外发，但数字是本机算的，上传的文件不外发。"""
    text = visible_text(drawer_html())
    assert "会发出去" in text and "不会发出去" in text, "没有把'发什么/不发什么'分开说清"
    assert "你提的问题原话" in text, "没说清问题原话会外发"
    assert "数据文件本身" in text and "明细行不会上传" in text, "没说清上传的数据文件不外发"
    assert "模型" in text and "本机程序" in text and "算出来的" in text, \
        "没说清'数字由本机算、模型只组织语言'"
    assert "编出来的数字会被核对挡掉" in text, "没提数字核对这道闸门"


def test_H04_两栏里没有技术字样():
    fragment = drawer_html()
    html_clean = strip_comments(fragment)
    for banned in BANNED:
        assert banned not in html_clean, f"帮助/隐私里出现了技术字样：{banned}"
    text = visible_text(fragment)
    for banned in BANNED:
        assert banned not in text, f"帮助/隐私渲染出来的字里有技术字样：{banned}"
    # 页面逻辑里也不许把技术字样**画到界面上**
    js = strip_comments(read("session.js"))
    for banned in ("TASK-", "/api/", "openapi"):
        assert banned not in js, f"登录页逻辑里出现了技术字样：{banned}"
    # 后端的字段名（captcha_id / image_svg）只许出现在"读响应 / 拼请求"的语句里 ——
    # 一行里同时出现"渲染调用"和它，就说明它被画到用户看得见的地方了。
    # （与 test_web_login.py 里 file_id 那条同一条约定：不是禁止出现在代码里，是禁止出现在界面上。）
    for line in js.splitlines():
        if "captcha_id" not in line and "image_svg" not in line:
            continue
        for call in ("textContent", "setText", "innerHTML", "innerText", "appendChild",
                     "createTextNode", "title =", "text("):
            assert call not in line, f"这一行把后端字段名画到界面上了：{line.strip()[:90]}"


def test_H05_帮助里的数字都是读出来的不是写死的():
    """数据源名 / 范围 / 行数 / 客户数 / 国家数 / 币种 / 最后一天 / 导出格式 —— 八个槽位都要有 id，
    而且由页面逻辑填（换数据源会跟着变），不许把 541909 这种数字写死在页面里。"""
    html, js = read("index.html"), read("session.js")
    for slot in ("help-source", "help-range", "help-rows", "help-customers", "help-countries",
                 "help-currency", "help-last-day", "help-unsupported", "help-report-formats"):
        assert f'id="{slot}"' in html, f"帮助里少了动态槽位 {slot}"
        assert f'"{slot}"' in js, f"{slot} 没有被页面逻辑填过（会永远停在占位）"
    assert "541,909" not in html and "541909" not in html, "帮助里把行数写死了"
    assert "2010-12-01" not in html and "2011-12-09" not in html, "帮助里把数据范围写死了"
    # 读不到时说的是人话，不是摆一个假数字
    assert "暂时读不到" in js


def test_H05_登录页底部那句提示是诚实的():
    """底部那句要如实：只存加密摘要、不存原文；忘记密码找不回；可注册或用游客。"""
    note = visible_text(re.search(r'<p class="login-note">.*?</p>', read("index.html"), re.S).group(0))
    assert "加密摘要" in note and "原文" in note, "没说清密码只存摘要不存原文"
    assert "无法用邮件找回" in note, "没如实说清忘记密码的后果"
    assert "注册新账号" in note and "游客" in note, "没给出能走的两条路"
    for fake in ("发送邮件", "重置邮件", "邮件已发送", "验证码已发送", "找回密码链接"):
        assert fake not in note, f"底部提示在承诺做不到的事：{fake}"


# ════════════════════════════════════════════════════════════════════════
# ② ★ 文档说自己能干什么，就得真能干
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("label,question,tool", HELP_EXAMPLES)
def test_H06_帮助里写的每一类问题真的能算(label: str, question: str, tool: str):
    """把帮助里的示例问题**原样**问一遍，必须落到对应的那套计算上（不是"大概能懂"）。"""
    assert tool in intent_module.COMPUTE_INTENTS, f"{tool} 不在可计算的问题类型里"
    response = client.post("/api/chat", json={"question": question, "use_llm": False})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("status") in ("ok", "degraded"), \
        f"帮助里的「{label}」示例没被接住：status={payload.get('status')} 「{question}」"
    actual = (payload.get("tool") or {}).get("name")
    assert actual == tool, f"「{question}」落到了 {actual}，帮助里写的是 {label}"


def test_H06_帮助里说不能问的维度真的都被拒():
    for question in REFUSED_QUESTIONS:
        payload = client.post("/api/chat", json={"question": question, "use_llm": False}).json()
        assert payload.get("status") == "unsupported", \
            f"「{question}」应当被明确拒绝，实际 status={payload.get('status')}"
        notice = payload.get("notice") or ""
        assert "数据不支持" in notice or "没有" in notice, f"拒绝时没给理由：{notice}"


def test_H06_帮助里说的不能问清单与后端声明一致():
    """页面上的"数据里没有什么"是从后端读的（同源），不是手抄的 —— 后端多一条页面就多一条。"""
    declared = client.get("/api/chat/capabilities").json()["unsupported"]["dimensions"]
    assert declared, "后端没有声明不能问哪些维度"
    joined = " ".join(declared)
    for keyword in ("区域", "省份", "城市", "门店", "渠道", "销售员"):
        assert keyword in joined, f"后端声明的不能问清单里少了「{keyword}」"
    js = read("session.js")
    assert "unsupported" in js and "dimensions" in js, "页面没有从后端读这份清单（会两边走散）"


def test_H06_帮助里说的口径与真算出来的对得上():
    """帮助里写"客户维度只算有客户号的成交"，那就得真有一条这样的说明跟结果一起出来。"""
    payload = client.post("/api/chat", json={
        "question": "2011年11月销售额最高的5个客户", "use_llm": False,
    }).json()
    text = " ".join(str(item) for item in (payload.get("facts") or []))
    text += str(payload.get("answer") or "")
    assert "客户号" in text or "覆盖率" in text or "CustomerID" in text, \
        "客户分析没有说明「只算有客户号的成交」（帮助里承诺了会有）"
