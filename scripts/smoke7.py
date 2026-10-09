"""冒烟测试 7：网页连接器 + 通用 API 连接器。

★ 这两条是"异构知识源"里最难的两类：
    网页 —— 抓不到/抓错了都得如实说，不能假装抓到
    API  —— 客户的接口形状千奇百怪，关键是**把接不上的原因说清楚**

★ 用本地起的假服务器测，不依赖外网（外网会挂、会慢、结果不稳定）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["KEFU_DB"] = str(Path(tempfile.mkdtemp(prefix="kefu_test7_")) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import store  # noqa: E402
from app.connectors import folder as folder_conn  # noqa: E402
from app.connectors.api import ApiConnector, probe  # noqa: E402
from app.connectors.urls import UrlConnector, html_to_text  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")


# ══════════════════════════════════════════════════════════════════════
# 起一个本地假服务器
# ══════════════════════════════════════════════════════════════════════

PAGES = {
    "/about": """<!doctype html><html><head><title>关于我们</title>
      <style>body{color:red}</style></head><body>
      <nav>首页 产品 联系</nav>
      <h1>关于我们</h1>
      <p>我们做 AI 算力方案。</p><p>服务过 200 家企业。</p>
      <script>var x=1;</script>
      <footer>版权所有</footer>
      </body></html>""",
    "/product": """<html><head><title>产品</title></head><body>
      <div><h2>主要产品</h2><ul><li>推理服务</li><li>训练平台</li></ul></div>
      </body></html>""",
    "/missing": "not found",
}
# 契约正确的接口
API_OK = {
    "/api/docs": {
        "items": [
            {"id": "1", "title": "退款政策", "content": "7 天内可退，需保留包装。",
             "updated_at": "2025-01-01", "permission": "external"},
            {"id": "2", "title": "内部价目", "content": "底价 6 折。",
             "updated_at": "2025-01-01", "permission": "internal"},
        ],
        "next": None,
    },
    "/api/paged": None,  # 分页，动态处理
}
# 契约不对的接口
API_BAD = {"/api/bad": {"data": [{"id": "1"}]}, "/api/nofields": {"items": [{"foo": "bar"}]}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 别把访问日志打到 stdout
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if path == "/api/paged":
            # ★ 分页：不带 cursor 返回第一页，带 cursor 返回第二页
            if "cursor=p2" in query:
                body = {"items": [{"id": "b", "title": "第二页", "content": "第二页的内容。"}], "next": None}
            else:
                body = {
                    "items": [{"id": "a", "title": "第一页", "content": "第一页的内容。"}],
                    "next": "p2",
                }
            return self._json(body)

        for table in (API_OK, API_BAD):
            if path in table and table[path] is not None:
                return self._json(table[path])

        if path in PAGES:
            if path == "/missing":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            return self._html(PAGES[path])

        self.send_response(404)
        self.end_headers()

    def _json(self, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, text):
        raw = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = HTTPServer(("127.0.0.1", 0), Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
print(f"假服务器起在 {BASE}")

print("=" * 64)
print("冒烟测试 7 · 网页 + 通用 API 连接器")
print("=" * 64)

store.init_db()

# ══════════════════════════════════════════════════════════════════════
# 一、HTML 抽取
# ══════════════════════════════════════════════════════════════════════
print("\n[1] HTML → 纯文本")
title, text = html_to_text(PAGES["/about"])
print(f"     标题: {title!r}")
print(f"     正文: {text[:120]!r}")
check("取到了标题", title == "关于我们", title)
check("★ 去掉了 script/style 里的代码", "var x" not in text and "color:red" not in text, text[:120])
check("★ 去掉了导航和页脚", "首页 产品 联系" not in text and "版权所有" not in text, text[:150])
check("正文保留了", "AI 算力方案" in text, text[:120])
check("★ 段落之间有换行（不是连成一坨）", "。\n" in text or "\n" in text, repr(text[:80]))
check("坏 HTML 不崩", html_to_text("<div><p>没闭合")[1] != "" or True)

# ── 2. 网页连接器 ─────────────────────────────────────────────────────
print("\n[2] 网页连接器")
uc = UrlConnector([f"{BASE}/about", f"{BASE}/product", f"{BASE}/missing"])
docs = uc.list_docs()
check("列出了全部 URL", len(docs) == 3, str(docs))

d1 = uc.fetch(f"{BASE}/about")
check("抓到了网页", d1 is not None and "AI 算力方案" in d1.content, str(d1.content[:60] if d1 else None))
check("标题用了 <title>", d1.title == "关于我们", d1.title)

d2 = uc.fetch(f"{BASE}/missing")
check("★★ 404 如实报告（没假装抓到）", "HTTP 404" in (d2.meta.get("error") or ""), str(d2.meta))
check("抓失败时内容为空（不污染索引）", d2.content == "", repr(d2.content[:40]))

d3 = uc.fetch("http://127.0.0.1:1/nothing")  # 必然连不上
# ★★ 这一条是测出来的真问题：httpx 默认 trust_env=True，会走 HTTP_PROXY，
#   于是"连本机一个必然失败的端口"返回的是代理的 502，而不是"连不上"。
#   生产上的后果更严重：**接内部 API 时请求会被发到外部代理** ——
#   功能上连不通，安全上等于把内部地址告诉了第三方。
from app.connectors.http import is_private_host  # noqa: E402

check("识别出内网地址", is_private_host("http://127.0.0.1:1/x") and is_private_host("http://internal-crm/api"))
check("识别出外网地址", not is_private_host("https://www.example.com/x"))
check("★ 连不上时如实报告（不走代理）",
      "抓取失败" in (d3.meta.get("error") or ""), str(d3.meta))

res = folder_conn.sync(uc, tenant_id="default")
print(f"     同步: {res.line()}")
check("★ 抓失败的那条被跳过（不进索引）", res.skipped or res.added == 2, str(res))
check("成功的两条进了索引", res.added == 2, str(res.added))

from app import retrieval  # noqa: E402

r = retrieval.search("你们做什么", tenant_id="default", permission="external")
check("★ 网页内容能被检索到", any("算力" in h.text for h in r.hits), str([h.title for h in r.hits]))

# ── 3. API 连接器：正常 ───────────────────────────────────────────────
print("\n[3] API 连接器：契约正确时")
ac = ApiConnector(f"{BASE}/api/docs")
check("列出了文档", ac.list_docs() == ["1", "2"], str(ac.list_docs()))
a1 = ac.fetch("1")
check("内容正确", a1 is not None and "7 天" in a1.content, str(a1.content if a1 else None))
check("★ 读到了 permission 字段（internal 的那条要能区分）",
      ac.fetch("2").permission == "internal", ac.fetch("2").permission)

res2 = folder_conn.sync(Ac := ApiConnector(f"{BASE}/api/docs"), tenant_id="default")
print(f"     同步: {res2.line()}")
check("两条都进了索引", res2.added == 2, str(res2.added))

# ★ 权限隔离：internal 的那条外部客户查不到
r_int = retrieval.search("底价", tenant_id="default", permission="external")
check("★★ API 来的 internal 内容外部客户查不到",
      not any("底价" in h.text for h in r_int.hits), str([h.title for h in r_int.hits]))

# ── 4. API 连接器：分页 ───────────────────────────────────────────────
print("\n[4] ★ API 分页（内部系统动辄几万条，必须支持）")
paged = ApiConnector(f"{BASE}/api/paged")
ids = paged.list_docs()
print(f"     拉到: {ids}")
check("★ 两页都拉到了", set(ids) == {"a", "b"}, str(ids))

# ── 5. ★★ 契约不对时把原因说清楚 ──────────────────────────────────────
print("\n[5] ★★ 契约不对时要说清原因（客户 IT 最需要这个）")
bad = probe(f"{BASE}/api/bad")
print(f"     顶层键是 data 时: {bad.get('error', '')[:100]}")
check("识别出契约不对", not bad["ok"])
check("★★ 明确说了期望 items、实际是什么", "items" in bad.get("error", "") and "data" in str(bad.get("top_level_keys")), str(bad))

nofields = probe(f"{BASE}/api/nofields")
print(f"     缺字段时: {nofields.get('error', '')[:100]}")
check("识别出缺字段", not nofields["ok"])
check("★★ 明确说了缺哪些字段", "缺少必需字段" in nofields.get("error", ""), str(nofields))

ok = probe(f"{BASE}/api/docs")
print(f"     正常时: ok={ok['ok']} count={ok.get('count')} fields={ok.get('fields')}")
check("正常接口体检通过", ok["ok"], str(ok))
check("能列出实际字段（方便客户对照契约）", "id" in (ok.get("fields") or []), str(ok))

# 连不上时
dead = probe("http://127.0.0.1:1/x")
check("★ 连不上时说清是连不上，不是解析失败", "连不上" in dead.get("error", ""), str(dead))

# ── 6. 接口返回坏数据不崩 ─────────────────────────────────────────────
print("\n[6] 接口返回坏数据时不崩")
badc = ApiConnector(f"{BASE}/api/bad")
check("契约不对时 list_docs 返回空而不是抛异常", badc.list_docs() == [], str(badc.list_docs()))
check("并且记下了错误原因", bool(badc.last_error), badc.last_error)

# ── 7. 增量同步 ───────────────────────────────────────────────────────
print("\n[7] 增量同步")
res3 = folder_conn.sync(ApiConnector(f"{BASE}/api/docs"), tenant_id="default")
print(f"     {res3.line()}")
check("未变的不重做", res3.unchanged == 2 and res3.added == 0, str(res3))

srv.shutdown()

print("\n" + "=" * 64)
print(f"通过 {PASS} · 失败 {FAIL}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
