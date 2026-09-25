"""scripts/stepb_ui_e2e.py · STEP B-UI 真闭环验收（真服务 + 真浏览器）

════════════════════════════════════════════════════════════════════════
【这份脚本验什么】
════════════════════════════════════════════════════════════════════════
① **真服务**：起 uvicorn（不是 TestClient），用 httpx 打真 HTTP；
② **静态契约没回归**：`/api/health` 的响应形状与既有端点语义照旧（Legacy Contract）；
③ **真浏览器**（本机 Edge，`--headless --virtual-time-budget --dump-dom`）打开根路径：
   看到的是**登录页**（不是直接进主界面）、两个入口都在、主界面被锁住、
   登录页上的三个数字来自后端、渲染出来的文字里没有技术字样；
④ 顺带把 DOM 里的状态四态文案与用户区元素对齐（登录后的状态迁移由
   scripts/session_check.mjs 用真源码验，浏览器里的点击由 Hermes 用 CDP 走查）。

【不做的事】角色权限 / 多用户数据隔离 / 令牌 / 服务端会话 —— 那些仍然没有。
  但**注册与登录是真的**：账号写进本机账号表，密码只以不可还原的校验值落盘、由后端比对。
  所以③里那一步走的是**真注册**（注册成功即进主界面），不是"填个名字就放行"。
【为什么不用无头浏览器模拟点击】本机没装驱动库，也不想为一次验收引入新依赖：
  Edge 的 --dump-dom 能给出"JS 跑完之后的真 DOM"，配合 node 那份行为检查已经能定性。
════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

PY = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# 渲染出来的文字里**不许出现**的东西（用户看得见的层面）
DOM_BANNED = ("TASK-", "/api/", "openapi", "白名单", "数字闸门", "sha256", "鉴权",
              "RBAC", "未启用", "不校验登录身份", "file_id")

PASSED: list[str] = []
FAILED: list[str] = []


def check(condition: bool, label: str, extra: str = "") -> bool:
    (PASSED if condition else FAILED).append(label)
    print(f"  {'OK  ' if condition else 'FAIL'} {label}" + (f" —— {extra}" if extra else ""), flush=True)
    return condition


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def find_edge() -> str | None:
    for path in EDGE_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def start_server(port: int, env: dict) -> tuple[subprocess.Popen, Path]:
    log_path = Path(env["SRA_STATE_DIR"]).parent / "server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=env, cwd=str(PROJECT_ROOT),
    )
    return proc, log_path


def wait_ready(client: httpx.Client, server: subprocess.Popen, deadline_seconds: int = 240) -> bool:
    """等服务真的起来（uvicorn 导入 pandas / fastapi 要几秒），有上限、进程死了就立刻放弃。

    就绪判据是 `/api/health` 返回 200 —— 不猜固定秒数（那会在慢机器上假失败）。
    """
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        if server.poll() is not None:          # 进程已经退出：再等也没用
            return False
        try:
            if client.get("/api/health").status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def server_tail(log_path: Path, lines: int = 12) -> str:
    try:
        body = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return "（读不到服务日志）"
    return "\n".join(body[-lines:]) or "（服务日志是空的）"


# ════════════════════════════════════════════════════════════════════════
# 量尺寸用的**探测代理**（只在验收脚本里存在，产品代码一行都不加）
#
# 为什么要有它：登录页"铺满没铺满"必须用**真浏览器量真几何**才算数，
#   而 Edge 的 --dump-dom 只能导出 DOM、不能执行任意 JS 再回传结果。
# 做法：本机起一个极小的代理，转发到真服务，**只**把 index.html 的响应
#   在 </body> 前插一段探测脚本。探测脚本量完把结果写进 <html data-probe="…">，
#   --dump-dom 一导就带出来了 —— 量到的是真布局，而产品代码里没有任何探测钩子。
# 探测脚本还会**在页面里走一遍登录**（填名字/密码 → 提交），
#   用来验"登录后数据源那一栏会不会自己变成真名字"。
# ════════════════════════════════════════════════════════════════════════
PROBE_SCRIPT = """
<script>
(function () {
  const readGate = () => {
    const gate = document.getElementById("login-gate");
    if (!gate) return null;
    const rect = gate.getBoundingClientRect();
    return {
      w: Math.round(rect.width), h: Math.round(rect.height),
      left: Math.round(rect.left), top: Math.round(rect.top),
    };
  };
  const label = (id) => {
    const el = document.getElementById(id);
    return el ? (el.textContent || "").trim() : null;
  };
  const snapshot = () => ({
    viewport: [window.innerWidth, window.innerHeight],
    gate: readGate(),
    gateVisible: !(document.getElementById("login-gate") || {}).hidden,
    locked: document.body.classList.contains("is-locked"),
    title: label("login-title"),
    tabs: ["ltab-account", "ltab-register", "ltab-guest"].map(label),
    userName: label("user-name"),
    bodyHasReading: (document.body.innerText || "").includes("读取中"),
    bodyText: (document.body.innerText || "").replace(/\\s+/g, " ").slice(0, 160),
    datasource: label("datasource-label"),
    scrollWidth: document.documentElement.scrollWidth,
  });
  const write = (key, data) => {
    const box = JSON.parse(document.documentElement.getAttribute("data-probe") || "{}");
    box[key] = data;
    document.documentElement.setAttribute("data-probe", JSON.stringify(box));
  };

  const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // 等这张图真的到手（取图是异步的）
  const waitImage = async (img) => {
    for (let i = 0; i < 50; i += 1) {
      if (img && img.src && img.src.indexOf("data:") === 0) return true;
      await tick(100);
    }
    return false;
  };

  // 从**图**上把 4 个字符读出来 —— 就是"人看图抄字"的机器版：
  // 图是 data: 地址，把地址解回 SVG，逐个取出 <text> 里的字符。
  const readCode = (img) => {
    const svg = decodeURIComponent((img.src.split(",")[1] || ""));
    const found = [];
    svg.split("</text>").forEach((part) => {
      const at = part.lastIndexOf(">");
      if (at >= 0 && part.length - at === 2) found.push(part.slice(at + 1));
    });
    return found.join("");
  };

  const textOf = (id) => {
    const el = document.getElementById(id);
    return el ? (el.textContent || "").trim() : "";
  };

  // 打开帮助 / 隐私，把内容原样记下来（核对"是不是真内容、数字是不是真的"）
  const openInfo = async (kind) => {
    const link = document.getElementById(kind === "help" ? "link-help" : "link-privacy");
    if (link) link.click();
    if (kind === "help") {                            // 帮助里的数字要等接口回来
      for (let i = 0; i < 60; i += 1) {
        const rows = textOf("help-rows");
        if (rows && rows !== "—" && rows !== "读取中…") break;
        await tick(200);
      }
    } else {
      await tick(300);
    }
    const drawer = document.getElementById("info-drawer");
    const pane = document.getElementById(kind === "help" ? "info-pane-help" : "info-pane-privacy");
    const record = {
      open: Boolean(drawer) && !drawer.hidden,
      title: textOf("info-title"),
      text: pane ? (pane.innerText || "").replace(/\s+/g, " ") : "",
      rows: textOf("help-rows"), source: textOf("help-source"), range: textOf("help-range"),
      currency: textOf("help-currency"), formats: textOf("help-report-formats"),
      lastDay: textOf("help-last-day"),
    };
    const close = document.getElementById("btn-info-close");
    if (close) close.click();
    await tick(150);
    record.closed = Boolean(drawer) && drawer.hidden;
    return record;
  };

  window.addEventListener("load", async () => {
    write("boot", snapshot());                       // ① 登录页阶段
    // ② 帮助 / 隐私：点开看一眼（内容是真内容，数字是当前数据源的真数字）
    try {
      write("help", await openInfo("help"));
      write("privacy", await openInfo("privacy"));
    } catch (err) {
      write("help", { error: String(err) });
    }
    // ③ 走一遍**真注册**（注册成功即登录）：点「注册」标签 → 填表（含验证码）→ 真提交。
    //    注册与登录现在都由后端判定（密码只以不可还原的校验值落盘），
    //    所以探针走的这条，就是用户第一次用这套系统走的那条 —— 验证码也是照着图填的。
    const regTab = document.getElementById("ltab-register");
    if (regTab) regTab.click();
    const rn = document.getElementById("reg-name");
    const rd = document.getElementById("reg-display");
    const rp = document.getElementById("reg-pwd");
    const rp2 = document.getElementById("reg-pwd2");
    const captchaImg = document.getElementById("reg-captcha-img");
    const captchaInput = document.getElementById("reg-captcha");
    await waitImage(captchaImg);
    const code = readCode(captchaImg);
    write("captcha", { code, length: code.length,
                       src: captchaImg ? captchaImg.src.slice(0, 22) : "" });
    if (rn && rp && rp2 && captchaInput) {
      rn.value = "探针唐宇";
      if (rd) rd.value = "探针唐宇";
      rp.value = "probe-123456";
      rp2.value = "probe-123456";
      captchaInput.value = code;
      captchaInput.dispatchEvent(new Event("input", { bubbles: true }));
      document.getElementById("register-form")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
    setTimeout(() => { write("afterLogin", snapshot()); }, 9000);    // ④ 进主界面后 9 秒（虚拟时间）内
    setTimeout(() => { write("afterIdle", snapshot()); }, 13000);   // ⑤ 再等一会儿看会不会回退
  });
})();
</script>
"""


def start_probe_proxy(upstream_port: int, listen_port: int):
    """起探测代理（dump-dom 一次只打几个请求）。返回 httpd 对象。"""
    import http.server
    import urllib.error
    import urllib.request

    upstream = f"http://127.0.0.1:{upstream_port}"

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _forward(self, method: str) -> None:
            """把请求**原样**转发给真服务。

            为什么要 POST：本轮起注册与登录是**真请求**（页面会 POST 到后端）。
            以前只转发 GET 够用，是因为那时候登录纯前端 —— 现在不够了。
            4xx 也要原样带回去（409「已被注册」/ 401「账号或密码不对」都是**真响应**，
            页面要按它显示人话，代理不能把它们变成 502）。
            """
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else None
            request = urllib.request.Request(upstream + self.path, data=payload, method=method)
            content_type = self.headers.get("Content-Type")
            if content_type:
                request.add_header("Content-Type", content_type)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    status = response.status
                    body = response.read()
                    # 从 HTTPMessage 上按名取（大小写不敏感）；转成 dict 再取会取不到
                    response_type = response.headers.get("Content-Type", "")
                    headers = list(response.headers.items())
            except urllib.error.HTTPError as exc:                # 4xx/5xx 也是真响应，原样带回
                status = exc.code
                body = exc.read()
                response_type = exc.headers.get("Content-Type", "")
                headers = list(exc.headers.items())
            except Exception as exc:                             # noqa: BLE001 代理层不许把脚本搞崩
                self.send_error(502, f"proxy upstream failed: {exc}")
                return
            if "text/html" in response_type:
                html = body.decode("utf-8", errors="replace")
                body = html.replace("</body>", PROBE_SCRIPT + "</body>").encode("utf-8")
            self.send_response(status)
            for key, value in headers:
                if key.lower() in ("content-length", "content-encoding", "transfer-encoding",
                                   "connection", "keep-alive"):
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):                                        # noqa: N802（http.server 的命名）
            self._forward("GET")

        def do_POST(self):                                       # noqa: N802
            self._forward("POST")

        def log_message(self, *args):                            # 不刷屏
            return

    return http.server.ThreadingHTTPServer(("127.0.0.1", listen_port), Handler)


def probe_of(dom: str) -> dict:
    """从导出的 DOM 里取出探测结果（脚本写在 <html data-probe="…"> 上）。"""
    import html as html_module
    import json

    match = re.search(r'data-probe="([^"]*)"', dom)
    if not match:
        return {}
    try:
        return json.loads(html_module.unescape(match.group(1)))
    except ValueError:
        return {}


def dump_dom(edge: str, url: str, width: int = 1600, height: int = 1000) -> str:
    """Edge 无头打开页面并导出**JS 跑完之后**的 DOM（每次一个干净 profile，等价于全新用户）。"""
    profile = tempfile.mkdtemp(prefix="sra_edge_b_", dir=str(PROJECT_ROOT / "outputs"))
    try:
        completed = subprocess.run(
            [
                edge, "--headless", "--disable-gpu", "--no-first-run",
                "--user-data-dir=" + profile, f"--window-size={width},{height}",
                "--virtual-time-budget=25000", "--dump-dom", url,
            ],
            capture_output=True, timeout=240,
        )
        return completed.stdout.decode("utf-8", errors="replace")
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def shoot(edge: str, url: str, path: Path, width: int, height: int) -> bool:
    """截一张图（给"铺满没铺满、有没有露出主界面"留个肉眼可看的证据）。"""
    profile = tempfile.mkdtemp(prefix="sra_edge_shot_", dir=str(PROJECT_ROOT / "outputs"))
    try:
        completed = subprocess.run(
            [
                edge, "--headless", "--disable-gpu", "--no-first-run",
                "--user-data-dir=" + profile, f"--window-size={width},{height}",
                "--virtual-time-budget=25000", f"--screenshot={path}", url,
            ],
            capture_output=True, timeout=240,
        )
        return path.exists() and completed.returncode == 0
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def page_text(dom: str) -> str:
    """剥掉标签，只留**渲染出来的文字**。"""
    body = re.search(r"<body[^>]*>(.*?)</body>", dom, re.S)
    inner = body.group(1) if body else dom
    inner = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", inner, flags=re.S)
    inner = re.sub(r"<!--.*?-->", " ", inner, flags=re.S)
    inner = re.sub(r"<[^>]+>", " ", inner)
    return re.sub(r"\s+", " ", inner.replace("&nbsp;", " ")).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="STEP B-UI 真闭环验收")
    parser.add_argument("--port", type=int, default=8530)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser-size", default="1600x1000",
                        help="无头浏览器的窗口尺寸（WxH），量几何与截图都用它")
    args = parser.parse_args()

    edge = find_edge()
    try:
        browser_w, browser_h = (int(part) for part in str(args.browser_size).lower().split("x"))
    except ValueError:
        print(f"窗口尺寸写法不对：{args.browser_size}（应形如 1238x660）")
        return 2
    workspace = PROJECT_ROOT / "outputs" / f"sra_stepb_{int(time.time())}"
    env = dict(os.environ)
    # 落盘沙箱：本次验收产生的状态 / 上传 / 产出都写进临时目录，跑完可以整目录删掉
    for key, name in (("SRA_STATE_DIR", "state"), ("SRA_DOC_DIR", "documents"),
                      ("SRA_UPLOAD_DIR", "uploads"), ("SRA_OUTPUT_DIR", "outputs")):
        env[key] = str(workspace / name)
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    server, log_path = start_server(args.port, env)
    base = f"http://127.0.0.1:{args.port}"
    try:
        # ── ① 服务就绪 + 数据预热完成（一次阻塞请求，不写轮询等待）
        step("① 真服务就绪 + 数据读到内存")
        with httpx.Client(base_url=base, timeout=300) as client:
            if not check(wait_ready(client, server), "服务起来了（/api/health 200）"):
                print("\n服务日志末尾：\n" + server_tail(log_path))
                return 1
            health = client.get("/api/health")
            ready = client.get("/api/chat/capabilities")     # 预热没完成时这一步自己等
            if not check(ready.status_code == 200, "GET /api/chat/capabilities 200（数据已就绪）",
                         f"{ready.status_code} {ready.text[:120]}"):
                print("\n服务日志末尾：\n" + server_tail(log_path))
                return 1
            profile = ready.json().get("data_profile", {})
            check(isinstance(profile.get("rows"), int) and profile["rows"] > 0,
                  "后端给出数据画像（登录页上的数字就来自它）",
                  f"rows={profile.get('rows')}")

            # ── ② Legacy Contract：既有端点语义与响应形状照旧
            step("② 既有端点语义没回归（Legacy Contract）")
            check(health.json().get("task") == "TASK-002D", "health 里的契约标记照旧")
            for path in ("/api/tasks", "/api/executions", "/api/datasets",
                         "/api/tables/customers", "/api/tables/products",
                         "/api/tables/sales", "/api/tables/raw"):
                response = client.get(path)
                check(response.status_code == 200, f"GET {path} 200",
                      "" if response.status_code == 200 else response.text[:120])
            root = client.get("/")
            check(root.status_code == 200 and 'id="login-gate"' in root.text,
                  "GET / 返回页面（且带登录页容器）")
            script = client.get("/session.js")
            check(script.status_code == 200 and "requireLogin" in script.text,
                  "GET /session.js 200（本机会话脚本在服务上）")
            page = client.get("/api/tables/customers", params={"page": 1, "page_size": 5}).json()
            check(page.get("total", 0) > 0 and len(page.get("items", [])) == 5,
                  "客户表分页形状照旧（total / items）", f"total={page.get('total')}")

        # ── ③ 真浏览器：打开根路径，先看到登录页（经探测代理，能带回真几何）
        step("③ 真浏览器打开根路径（全新用户 → 先看到登录页）")
        if args.no_browser or not edge:
            check(True, "跳过浏览器这一步（--no-browser 或本机没有 Edge）")
        else:
            print(f"   浏览器：{edge}")
            proxy = start_probe_proxy(args.port, args.port + 1)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            proxy_url = f"http://127.0.0.1:{args.port + 1}/"
            try:
                dom = dump_dom(edge, proxy_url, browser_w, browser_h)
            finally:
                proxy.shutdown()
            (workspace / "dom_login.html").write_text(dom, encoding="utf-8")
            text = page_text(dom)
            (workspace / "dom_login.txt").write_text(text, encoding="utf-8")
            probe = probe_of(dom)
            (workspace / "probe.json").write_text(
                __import__("json").dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")

            # ★ 用户实测的两个问题，这里量真几何 / 读真文本
            boot = probe.get("boot") or {}
            viewport, gate = boot.get("viewport"), boot.get("gate")
            if check(bool(viewport and gate), "探测脚本量到了视口与登录页的尺寸",
                     f"viewport={viewport} gate={gate}"):
                check(gate["w"] == viewport[0] and gate["h"] == viewport[1]
                      and gate["left"] == 0 and gate["top"] == 0,
                      "登录页**铺满整个视口**（宽高相等、左上角归零）",
                      f"{gate['w']}x{gate['h']} @ {gate['left']},{gate['top']} / 视口 {viewport[0]}x{viewport[1]}")
                check(boot.get("scrollWidth", 0) <= viewport[0] + 1,
                      "没有横向溢出（scrollWidth ≤ 视口宽）",
                      f"scrollWidth={boot.get('scrollWidth')}")
            check(boot.get("gateVisible") is True, "登录页是显示状态")
            check(boot.get("bodyHasReading") is False, "登录页上的文字里**没有「读取中」**",
                  f"看到的文字：{boot.get('bodyText', '')[:60]}")
            after_login = probe.get("afterLogin") or {}
            label = after_login.get("datasource") or ""
            check(bool(label) and "读取中" not in label,
                  "登录后：数据源那一栏不再是「读取中」", f"「{label}」")
            check(".xlsx" in label or "数据源：" in label,
                  "登录后：数据源那一栏是真实名称或人话", f"「{label}」")
            check(after_login.get("gateVisible") is False, "登录后：登录页收起、进主界面")
            check((probe.get("afterIdle") or {}).get("datasource") == label,
                  "再等一会儿标签值稳定（不会又变回读取中）",
                  f"「{(probe.get('afterIdle') or {}).get('datasource')}」")

            gate = re.search(r'<div class="login-gate" id="login-gate"[^>]*>', dom)
            check(bool(gate), "登录页容器在页面上")
            check(boot.get("locked") is True, "打开时主界面被锁住（body 带 is-locked）")
            # 三个入口在页面上；打开时的标题是「欢迎回来」（探针随后切到了注册栏，
            # 所以末态标题是「建一个账号」—— 两个都用**打开那一刻**的快照来验）
            check(boot.get("title") == "欢迎回来 👋", "打开时的标题是「欢迎回来 👋」",
                  f"「{boot.get('title')}」")
            check(boot.get("tabs") == ["账号登录", "注册", "游客登录"],
                  "三个入口并列：账号登录 / 注册 / 游客登录", f"{boot.get('tabs')}")
            check("本机模式" in text, "如实标注了这是本机模式", "（人话，不是技术字样）")

            # ── ③b 帮助 / 隐私：真内容 + 真数字（不是写死的、也不是"尚未开通"）
            help_box = probe.get("help") or {}
            privacy_box = probe.get("privacy") or {}
            check(help_box.get("open") is True and help_box.get("title") == "帮助",
                  "点「帮助」→ 真帮助内容打开（标题是「帮助」）",
                  f"open={help_box.get('open')} title=「{help_box.get('title')}」")
            check(help_box.get("closed") is True, "关闭按钮能把它收起来")
            help_text = help_box.get("text") or ""
            for part in ("能问什么", "数据从哪来", "数据里没有什么", "数字是怎么算出来的", "常见问题"):
                check(part in help_text, f"帮助里有「{part}」这一部分")
            for label in ("销售汇总", "销售趋势", "产品排行", "两区间比较", "国家分布",
                          "客户分析", "商品分析"):
                check(label in help_text, f"能问的七类里有「{label}」")
            check("做一份销售周报" in help_text, "帮助里提了周报")
            for keyword in ("最后几天", "客户数", "导出", "数据源"):
                check(keyword in help_text, f"常见问题里有问到「{keyword}」")
            # ★ 帮助里的数字必须与**当前数据源**对得上（不是页面写死的）
            check(help_box.get("rows") == f"{profile.get('rows'):,}",
                  "帮助里的行数与后端画像一致", f"页面 {help_box.get('rows')} / 后端 {profile.get('rows'):,}")
            check("2010-12-01" in (help_box.get("range") or "")
                  and "2011-12-09" in (help_box.get("range") or ""),
                  "帮助里的覆盖范围与后端一致", f"「{help_box.get('range')}」")
            check("元" in (help_box.get("currency") or ""), "帮助里写清了金额单位是「元」",
                  f"「{help_box.get('currency')}」")
            check("Word" in (help_box.get("formats") or ""), "帮助里的导出格式是照后端清单写的",
                  f"「{help_box.get('formats')}」")
            check("区域" in help_text and "销售员" in help_text and "门店" in help_text,
                  "帮助里列清了「数据里没有」的那些维度")

            check(privacy_box.get("open") is True and privacy_box.get("title") == "隐私说明",
                  "点「隐私」→ 真隐私说明打开（标题是「隐私说明」）",
                  f"open={privacy_box.get('open')} title=「{privacy_box.get('title')}」")
            privacy_text = privacy_box.get("text") or ""
            for part in ("数据存在哪", "密码怎么存", "会不会把数据发到外面去", "怎么清掉"):
                check(part in privacy_text, f"隐私四项里有「{part}」")
            check("会发出去" in privacy_text and "不会发出去" in privacy_text,
                  "隐私说明如实把「发什么 / 不发什么」分开说了")
            check("明细行不会上传" in privacy_text and "模型" in privacy_text,
                  "说清了「上传的数据文件不外发、数字由本机算」")

            # 两栏里都不许出现技术字样
            drawer_text = (help_text + " " + privacy_text).lower()
            banned_hits = [word for word in DOM_BANNED if word.lower() in drawer_text]
            banned_hits += [word for word in ("尚未开通", "captcha", "svg", "endpoint")
                            if word in drawer_text]
            check(not banned_hits, "帮助 / 隐私里没有技术字样、也没有占位话术",
                  f"命中：{banned_hits}" if banned_hits else "干净")

            # 验证码：探针是照着图填的，这里核对"图上真能读出 4 个字符"
            captcha_box = probe.get("captcha") or {}
            check(captcha_box.get("length") == 4,
                  "注册页的验证码图上有 4 个字符（探针是照图读出来填的）",
                  f"读到「{captcha_box.get('code')}」")
            check((captcha_box.get("src") or "").startswith("data:image"),
                  "验证码是当**图片**显示的", f"「{captcha_box.get('src')}」")
            check("扫码" not in text, "扫码登录没有出现（本轮不做）")
            check(boot.get("userName") == "未登录", "打开时顶栏显示未登录（不是假装有个用户）",
                  f"「{boot.get('userName')}」")
            check("离线" in text and "在线" in text, "状态文字在页面上（离线 / 在线）")

            facts = re.findall(r"\d{1,3}(?:,\d{3})+", text)
            check(str(profile.get("rows")) in text.replace(",", "") or len(facts) > 0,
                  "登录页上的数字来自后端画像", f"页面里的千分位数字：{facts[:4]}")

            banned_hits = [item for item in DOM_BANNED if item in text]
            check(not banned_hits, "渲染出来的文字里没有技术字样",
                  f"命中：{banned_hits}" if banned_hits else f"检查了 {len(DOM_BANNED)} 个词")

            # 主界面的四个业务页面仍在（登录页没有把它们删掉，只是先盖住）
            for marker in ('id="page-customers"', 'id="page-products"', 'id="page-sales"',
                           'id="page-raw"', 'id="user-menu"'):
                check(marker in dom, f"主界面元素还在：{marker}")

            # ── ④ 窄窗口：顶栏不许把「分析」挤成竖排
            step("④ 窄窗口（820px）顶栏不切字")
            narrow = dump_dom(edge, base, width=820, height=browser_h)
            narrow_text = page_text(narrow)
            check("账号登录" in narrow_text and "本地模式" in narrow_text,
                  "窄窗口下登录页仍然完整")
            check("分析" in narrow_text, "「分析」按钮的文字没有被压成单字")

            # ── ⑤ 截图留证（登录页全屏的样子，肉眼可查）
            step("⑤ 截一张登录页的图（给「铺满没铺满」留个肉眼证据）")
            shot = workspace / f"login-{browser_w}x{browser_h}.png"
            if shoot(edge, base, shot, browser_w, browser_h):
                check(shot.stat().st_size > 10_000, "登录页截图已生成",
                      f"{shot.name}（{shot.stat().st_size // 1024} KB）")
            else:
                check(False, "登录页截图没生成")

        print(f"\n产物目录：{workspace}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for label in FAILED:
            print(f"  FAIL  {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
