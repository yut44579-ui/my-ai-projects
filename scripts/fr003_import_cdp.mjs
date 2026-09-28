// fr003_import_cdp.mjs · FR-003「统一导入 + 地区维度」在**真浏览器**里的实测（真 Edge + CDP，零依赖）
//
//   node scripts/fr003_import_cdp.mjs http://127.0.0.1:8537
//
// 跑之前先起一个真服务（沙箱目录只是别把本次导入写进真实 state/ 与真实 data/app.db）：
//   SRA_STATE_DIR=outputs/_cdp_run_fr003/state SRA_DB_PATH=outputs/_cdp_run_fr003/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr003/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr003/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr003/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr003/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8537
//
// 为什么要真开浏览器：FR-003E 的几条要求**只有浏览器里才看得见** ——
//   ① 「按地区」这个入口**没有地区字段时不许出现**（评审 #5：禁止假能力入口）；
//   ② 导入后那份文件**必须逐条说清**落成了什么（不许笼统的「导入成功」）；
//   ③ 地区下拉是**按数据源动态出**的，且选完点「查看」真的能拿到分地区的数字。
// 静态检查（tests/test_fr003e_api.py）只能证明"代码里写了"，这里证明"浏览器里真的是这样"。
//
// 量什么（每一步都读**真 DOM**，不是看源码猜）：
//   ① 全新沙箱 → 「按地区」整张卡**不可见**（不是"点了才说没有"）；
//   ② 上传只有 Country 的表 → 预览里"地区字段：无" → 导入后地区卡**仍然不可见**；
//   ③ 上传带「省份」的表 → 预览里"地区字段：省份" → 导入后地区卡**出现**；
//   ④ 地区下拉里**只有**带地区字段的那个数据源，维度项是「省份」；
//   ⑤ 点「查看」→ 表格出现 广东 100 / 浙江 200 / 江苏 300（数字来自后端）；
//   ⑥ 全程没有 JS 报错。
// 截图落在 outputs/_cdp_shots/：预览那一段 + 按地区的结果表。
//
// ★ 关于 CDP 连接（踩过的坑，别再改回去）：
//   ① `DOM.getDocument` 必须带 `depth: 1`。不带就等于把整棵 DOM 树序列化回来，
//      这个页面上万个节点，那条响应会把连接噎住。
//   ② **一段流程里"命令越做越多"之后，渲染进程会整体不再应答**（Runtime/Page 调用全部超时，
//      连新开一条 WebSocket 也一样超时 —— 因为卡的是渲染进程，不是那条连接）。
//      而同一时刻页面自己推过来的事件（Log/Page）还在流 —— 浏览器进程没死，是渲染进程哑了。
//   所以走查的每一段（②③④⑤）都**换一个全新的浏览器进程 + 全新 profile** 再开始
//   （`restartBrowser()` → `beginSegment()`）：
//      · 新进程是"没登录过的浏览器"，所以每段开头 `ensureLoggedIn()` 重新登一次
//        —— 这反而更接近真用户：重新打开浏览器 → 登录 → 看数据；
//      · 每段要断言的东西全部**存在服务端**（导入记录、数据源、地区维度），换个进程读到的
//        是同一份真相 —— 重开浏览器不削弱证据，反而证明"不是内存里的残留"。
//   顺带：这一段"渲染进程越做越哑"的实测记录，也是"为什么走查要分段重开"的唯一理由，
//   不是为了让走查好看。

const BASE = process.argv[2] || "http://127.0.0.1:8537";
// ★ 调试端口**每个浏览器进程换一个**：换段时要杀进程再起新的，端口还是原来那个的话，
//   老进程的监听还没完全释放，新进程会静默地绑不上 —— 表现为"连不上调试端口"。
let PORT = 0;
const pickPort = () => 9800 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000003";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr003";
const FIXTURES = {
  country: `${SANDBOX}/国家分布.xlsx`,
  region: `${SANDBOX}/地区销售.xlsx`,
};

const { spawn, spawnSync } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const listUrl = () => `http://127.0.0.1:${PORT}/json/list`;

let ready = false;
for (let i = 0; i < 300; i++) {
  try {
    const r = await fetch(BASE + "/api/health");
    if (r.status === 200) { ready = true; break; }
  } catch { /* 还没起来 */ }
  await sleep(1000);
}
if (!ready) {
  console.log(`❌ 服务没起来（${BASE}/api/health 一直没返回 200），先起服务再跑本脚本`);
  process.exit(1);
}

// 每次跑都要是干净的一次：重置**本脚本沙箱**（真实 state/ 与真实 data/app.db 永远不碰）。
// ★ 重置交给 python 那个小脚本做，**不能在这里直接删 app.db**：服务起着的时候删库文件，
//   服务不会重建表（`init_schema` 只在启动时跑），下一次请求会连上新建的空库 →
//   满屏 "no such table: datasets"。（本脚本第一版就是这么把自己坑了的。）
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr003_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}
for (const [key, file] of Object.entries(FIXTURES)) {
  if (!fs.existsSync(file)) {
    console.log(`❌ 缺少样例文件：${file}（${key}）—— 先跑 .venv/Scripts/python.exe scripts/fr003_cdp_reset.py`);
    process.exit(1);
  }
}

console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) { console.log(`❌ 数据预热失败（${warm.status}）`); process.exit(1); }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
  process.exit(1);
}

// ★ 每起一个浏览器进程都给它**自己的 profile 目录**（绝不复用）。
//   踩过的坑：复用同一个 --user-data-dir 时，第二个进程会走 Edge 的"单例转发"——
//   它发现这个 profile 已经有实例，就把 URL 交给老进程、自己直接退出，
//   于是调试端口根本没绑上，`/json/list` 永远等不到页面 target。
//   代价是每个新进程都是"没登录过的浏览器"，所以每一段都要 `ensureLoggedIn()` 重新登一次
//   （这反而更接近真用户：重新打开浏览器 → 登录 → 看数据）。
const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr003_cdp_"));
  UDDS.push(dir);
  PORT = pickPort();
  const child = spawn(edge, [
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${dir}`,
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--window-size=1440,1100", BASE + "/",
  ], { stdio: "ignore" });
  child.unref();
  return child;
}
let browser = spawnBrowser();

let passed = 0, failed = 0;
function check(ok, label, extra = "") {
  if (ok) passed += 1; else failed += 1;
  console.log(`   ${ok ? "✅" : "❌"} ${label}${extra ? " | " + extra : ""}`);
}

const consoleErrors = [];
const pageTarget = async () => {
  let last = "（一次都没连上调试端口）";
  for (let i = 0; i < 120; i += 1) {
    try {
      const list = await (await fetch(listUrl())).json();
      last = JSON.stringify(list.map((t) => `${t.type}:${t.url}`));
      const found = list.find((t) => t.type === "page" && t.url.startsWith(BASE));
      if (found) return found;
    } catch (err) { last = `（连不上调试端口：${err.message}）`; }
    await sleep(500);
  }
  throw new Error(`没找到页面 target —— 调试端口上最后看到的是：${last}`);
};

/** 开一条**新**的 CDP 连接（见文件头：长流程里连接会哑掉，每步换一条最稳）。 */
async function openSession(onEvent) {
  const target = await pageTarget();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { socket.onopen = res; socket.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  socket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method && typeof onEvent === "function") onEvent(msg.method, msg.params || {});
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}, ms = 20000) => new Promise((res, rej) => {
    const id = ++seq;
    const timer = setTimeout(() => { pending.delete(id); rej(new Error(`CDP 调用超时：${method}`)); }, ms);
    pending.set(id, (m) => { clearTimeout(timer); res(m.result ?? m.error); });
    socket.send(JSON.stringify({ id, method, params }));
  });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("DOM.enable");
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r?.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed");
    return r?.result?.value;
  };
  return { send, evalJs, close: () => { try { socket.close(); } catch { /* 忽略 */ } } };
}

/** ★ 一条**长连接**贯穿一段流程（见文件头 ②：段与段之间换浏览器进程，段内换连接）。 */
let SESSION = null;
async function currentSession() {
  if (!SESSION) SESSION = await openSession();
  return SESSION;
}
async function step(fn) {
  try {
    return await fn(await currentSession());
  } catch (err) {
    if (!String(err.message).includes("CDP 调用超时")) throw err;
    console.log(`   （连接哑掉，换一条继续：${err.message}）`);
    SESSION = null;
    return await fn(await currentSession());
  }
}

/** 关掉当前浏览器、换一个**全新的进程 + 全新 profile** 再起 —— 渲染进程和登录态都从头来。 */
async function restartBrowser() {
  SESSION = null;
  const pid = browser.pid;
  try { browser.kill(); } catch { /* 已经退了 */ }
  // 杀掉整棵进程树：Edge 会派生一堆子进程，只杀父进程的话调试端口还被抓着。
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  await sleep(1200);
  browser = spawnBrowser();
  await pageTarget();       // 等新进程的页面 target 出现
  await sleep(600);
}

/** 已登录就直接返回；被踢回登录页就把验证码里的字读出来、照表单登一次（最多试 3 次）。 */
async function ensureLoggedIn(session) {
  const locked = () => session.evalJs(`document.body.classList.contains("is-locked")`);
  if (!(await locked())) return true;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (attempt > 1) {
      // 换一张验证码：整页重载最省事 —— 图、表单、提示一起回到初始态。
      await session.send("Page.reload", { ignoreCache: true });
      await sleep(2500);
      for (let i = 0; i < 60; i += 1) {
        const ok = await session.evalJs(`Boolean(document.getElementById("login-captcha-img"))`)
          .catch(() => false);
        if (ok) break;
        await sleep(250);
      }
    }
    // 验证码是服务端画的 SVG（data: URI），字就在 <text> 里。
    // ★ 这里**不能**用"取最后一个 > 后面那个字符"的土办法：<text> 里可能还嵌 <tspan>，
    //   那种写法会读出空串 —— 空验证码会被前端直接拦下、连登录请求都不发
    //   （服务端日志里一条 POST /api/auth/login 都没有，很难查）。
    const info = await session.evalJs(`(() => {
      const img = document.getElementById("login-captcha-img");
      if (!img || !img.src) return { svg: "", code: "" };
      const svg = decodeURIComponent((img.src.split(",")[1] || ""))
        .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
        .replace(/&#(\\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
        .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
      const chars = [];
      svg.replace(/<text[^>]*>([\\s\\S]*?)<\\/text>/g, (_, inner) => {
        const t = inner.replace(/<[^>]*>/g, "").trim();
        if (t) chars.push(t);
        return "";
      });
      return { svg: svg.slice(0, 120), code: chars.join("") };
    })()`);
    if (!info.code) console.log(`   ⚠ 读不出登录验证码，SVG 头 120 字：${info.svg}`);
    await session.evalJs(`(() => {
      document.getElementById("ltab-account").click();
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("login-name", ${JSON.stringify(ACCOUNT)});
      set("login-pwd", ${JSON.stringify(PASSWORD)});
      set("login-captcha", ${JSON.stringify(info.code)});
      return true;
    })()`);
    await click(session, "btn-login");
    await sleep(3000);
    if (!(await locked())) {
      console.log(`   （换进程后重新登录：ok${attempt > 1 ? `，第 ${attempt} 次` : ""}）`);
      return true;
    }
    console.log(`   （登录第 ${attempt} 次没成，换张验证码重来）`);
  }
  console.log("   ⚠ 登录连续 3 次都没成功");
  return false;
}

/** 统一的开段动作：重启浏览器 → 等骨架 → 补登录 → 进「数据管理」并等它渲染完。 */
async function beginSegment(label) {
  console.log(`   ${label}`);
  await restartBrowser();
  const session = await currentSession();
  for (let i = 0; i < 150; i += 1) {
    const ok = await session.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`);
    if (ok) break;
    await sleep(200);
  }
  await ensureLoggedIn(session);
  await session.send("Page.navigate", { url: BASE + "/#/data" });
  for (let i = 0; i < 120; i += 1) {
    const ok = await session.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("sources-body"))`).catch(() => false);
    if (ok) break;
    await sleep(250);
  }
  await sleep(1200);   // 让 refreshAll 里的几个 GET 落地
}

const visible = (session, id) => session.evalJs(`(() => {
  const el = document.getElementById(${JSON.stringify(id)});
  if (!el || el.hidden) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
})()`);

const textOf = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).textContent.replace(/\\s+/g, " ")`);

const click = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).click()`);

/** 往文件选择框里塞文件（真事件，走页面自己的 change 监听）。 */
async function pickFiles(session, selector, files) {
  const { root } = await session.send("DOM.getDocument", { depth: 1 });
  const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error(`页面上没有这个元素：${selector}`);
  // ★ 必须传**绝对路径**。相对路径是相对"浏览器进程自己的 cwd"解析的，不是相对本脚本 ——
  //   实测：传 `outputs/.../国家分布.xlsx` 时 setFileInputFiles 不报错、也不抛异常，
  //   只是**静默地什么都没设**；于是点击"预览"时页面读到空文件列表、连请求都不发
  //   （服务端日志里一条 POST /api/imports/preview 都没有），排查起来极其费劲。
  await session.send("DOM.setFileInputFiles", { nodeId, files: files.map((f) => path.resolve(f)) });
  await sleep(200);
}

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip;
    if (id) {
      await session.evalJs(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({ block: "center" })`);
      await sleep(250);
      const box = await session.evalJs(`(() => {
        const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
        return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
      })()`);
      clip = { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16),
               width: box.w + 32, height: box.h + 32, scale: 2 };
    }
    const image = await session.send("Page.captureScreenshot", clip ? { format: "png", clip } : { format: "png" });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(image.data, "base64"));
    return file;
  } finally {
    session.close();
  }
}

try {
  // 等页面骨架就绪
  for (let i = 0; i < 150; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`));
    if (ok) break;
    await sleep(200);
  }

  // ── ① 真注册一个账号（第一个账号直接进系统）─────────────────────────────
  console.log("\n=== ① 注册一个本机账号（第一个账号，直接进系统）===");
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "导入用例");
      set("reg-pwd", ${JSON.stringify(PASSWORD)});
      set("reg-pwd2", ${JSON.stringify(PASSWORD)});
      return true;
    })()`);
    const code = await s.evalJs(`(() => {
      const img = document.getElementById("reg-captcha-img");
      if (!img || !img.src) return "";
      const svg = decodeURIComponent((img.src.split(",")[1] || ""));
      const found = [];
      svg.split("</text>").forEach((part) => {
        const at = part.lastIndexOf(">");
        if (at >= 0 && part.length - at === 2) found.push(part.slice(at + 1));
      });
      return found.join("");
    })()`);
    await s.evalJs(`(() => {
      const el = document.getElementById("reg-captcha");
      el.value = ${JSON.stringify(code)};
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await click(s, "btn-register");
  });
  await sleep(3500);
  check(!(await step((s) => s.evalJs(`document.body.classList.contains("is-locked")`))),
    "注册后进入系统（登录页收起）");

  // ── ② 数据管理页：「按地区」不该出现（还没有任何已入库数据源）──────────────
  console.log("\n=== ② 没有数据源时，「按地区」整张卡不该出现 ===");
  // ★ 这一段开始就换成"全新浏览器进程 + 全新文档"（见文件头 ②）：
  //   注册那一段已经在同一个渲染进程里做了不少命令，接着往下做会把它做哑。
  await beginSegment("重开浏览器，进「数据管理」…");
  check((await step((s) => visible(s, "sources-empty"))) === true, "「已入库数据源」显示空状态");
  check((await step((s) => visible(s, "region-card"))) === false,
    "「按地区」整张卡不可见（没有点了才说没有的假入口）");

  // ── ③ 导入一份只有 Country 的表 → 地区维度仍然不存在 ────────────────────
  console.log("\n=== ③ 导入只有 Country 的表：地区字段应当显示「无」，地区卡仍然不出现 ===");
  // 观察者：一条**长连接**专门监听页面事件（弹窗 / 报错 / 导航）。
  // 为什么要它：短连接"问一句答一句"，页面**主动**推过来的事件（比如 window.alert 打开、
  // JS 抛异常）在两次连接之间发生就全丢了 —— 而"渲染线程被一个原生弹窗卡住"正是那种事件。
  const events = [];
  const watcher = await openSession((method, params) => {
    events.push(method);
    if (method === "Page.javascriptDialogOpening") {
      console.log(`   ⚠ 页面弹出了原生对话框：${params.type} / ${params.message}`);
    }
    if (method === "Runtime.consoleAPICalled") {
      console.log(`   ⚠ 页面 console.${params.type}：`,
        (params.args || []).map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 120));
    }
    if (method === "Runtime.exceptionThrown") {
      console.log("   ⚠ 页面抛异常：",
        (params.exceptionDetails?.exception?.description || "").slice(0, 160));
    }
    if (method === "Log.entryAdded") {
      console.log(`   ⚠ 浏览器日志：${params.entry?.source}/${params.entry?.level} ${params.entry?.text}`);
    }
    if (method === "Page.frameNavigated") {
      console.log(`   ⚠ 页面导航到：${params.frame?.url}`);
    }
  });
  await watcher.send("Page.enable");
  await watcher.send("Runtime.enable");
  await watcher.send("Log.enable").catch(() => {});
  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURES.country]));
  await step((s) => click(s, "btn-imp-preview"));
  await sleep(4000);
  console.log(`   页面事件（共 ${events.length} 条）：`, events.slice(-8).join(" → ") || "（无）");
  const countryPreview = await step((s) => textOf(s, "imp-preview"));
  check(countryPreview.includes("地区字段：无"), "预览里写明「地区字段：无」");
  check(countryPreview.includes("会落成"), "预览里写明会落成什么", countryPreview.slice(0, 80));

  await step((s) => click(s, "btn-imp-run"));
  await sleep(6000);
  const countryResult = await step((s) => textOf(s, "imp-result"));
  check(countryResult.includes("已导入"), "导入回执逐条说清落成了什么", countryResult.slice(0, 80));
  await sleep(1500);
  check((await step((s) => visible(s, "region-card"))) === false,
    "只有国家字段 → 「按地区」仍然不出现");
  watcher.close();   // 观察完毕就关掉，别让它跟着后面几段一起挂着

  // ── ④ 导入带「省份」的表 → 地区维度出现 ────────────────────────────────
  console.log("\n=== ④ 导入带「省份」的表：地区卡出现，下拉里只有它 ===");
  await beginSegment("重开浏览器（第三份表已经在库里了，这里读的是服务端的真相）…");
  check((await step((s) => visible(s, "region-card"))) === false,
    "只有国家字段的那份数据源，进来后「按地区」依然不出现");
  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURES.region]));
  await step((s) => click(s, "btn-imp-preview"));
  await sleep(4000);
  const regionPreview = await step((s) => textOf(s, "imp-preview"));
  check(regionPreview.includes("地区字段：省份"), "预览里认出「省份」是地区字段");
  await shot("fr003-import-preview.png", "imp-preview");

  await step((s) => click(s, "btn-imp-run"));
  await sleep(6000);
  const regionResult = await step((s) => textOf(s, "imp-result"));
  check(regionResult.includes("数据集") && regionResult.includes("地区字段：省份"),
    "导入回执说明这是数据集 + 地区字段是省份", regionResult.slice(0, 110));
  await sleep(1500);

  check((await step((s) => visible(s, "region-card"))) === true, "有地区字段的数据源进来后，「按地区」出现");
  const options = await step((s) => s.evalJs(`Array.from(document.getElementById("region-source").options)
    .map((o) => o.textContent.trim())`));
  check(options.length === 1, "下拉里只列**带地区字段**的数据源", JSON.stringify(options));
  const dims = await step((s) => s.evalJs(`Array.from(document.getElementById("region-dimension").options)
    .map((o) => o.textContent.trim())`));
  check(dims.length >= 1 && dims[0].includes("省份"), "地区维度按数据源动态出", JSON.stringify(dims));

  // ── ⑤ 点「查看」→ 真的拿到分地区的数字 ─────────────────────────────────
  console.log("\n=== ⑤ 点「查看」：广东 100 / 浙江 200 / 江苏 300 ===");
  await beginSegment("重开浏览器 —— 下面这张表是**重新加载后**重新点出来的，不是上一步的残留…");
  check((await step((s) => visible(s, "region-card"))) === true,
    "重新打开页面，「按地区」自己又出来了（维度是数据源本身给的）");
  await step((s) => click(s, "btn-region-query"));
  await sleep(4000);
  const rows = await step((s) => s.evalJs(`Array.from(document.querySelectorAll("#region-body tr")).map((tr) =>
    Array.from(tr.children).map((td) => td.textContent.trim()))`));
  const numeric = await step((s) => s.evalJs(`(() => {
    const out = {};
    document.querySelectorAll("#region-body tr").forEach((tr) => {
      const cells = Array.from(tr.children).map((td) => td.textContent.trim());
      out[cells[0]] = Number(cells[1].replace(/[^0-9.]/g, ""));
    });
    return out;
  })()`));
  check(rows.length === 3, "表格出现 3 行", JSON.stringify(rows));
  check(numeric["广东"] === 100 && numeric["浙江"] === 200 && numeric["江苏"] === 300,
    "三行数字与后端一致（前端没自己算）", JSON.stringify(numeric));
  const note = await step((s) => textOf(s, "region-note"));
  check(note.includes("600"), "合计也由后端给", note.slice(0, 80));
  await shot("fr003-region-result.png", "region-card");

  check(consoleErrors.length === 0, "全程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
} catch (err) {
  failed += 1;
  console.log(`\n❌ 走查中断：${err}`);
} finally {
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(browser.pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  try { browser.kill(); } catch { /* 忽略 */ }
  for (const dir of UDDS) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* 删不掉就算了 */ }
  }
}

console.log(`\n${failed === 0 ? "✅ 全部通过" : "❌ 有失败项"}：通过 ${passed} 项，失败 ${failed} 项`);
process.exit(failed === 0 ? 0 : 1);
