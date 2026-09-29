// fr009a_visibility_cdp.mjs · FR-009-A「资料可见性」在**真浏览器**里的实测（真 Edge + CDP，零依赖）
//
//   node scripts/fr009a_visibility_cdp.mjs http://127.0.0.1:8538
//
// 跑之前先起一个真服务（沙箱目录只是别把本次导入写进真实 state/ 与真实 data/app.db）：
//   SRA_STATE_DIR=outputs/_cdp_run_fr009a/state SRA_DB_PATH=outputs/_cdp_run_fr009a/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr009a/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr009a/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr009a/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr009a/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8538
//
// 为什么必须真开浏览器：本 TASK 的四条要求里有三条**只有浏览器里才看得见** ——
//   ① 导入的文档是否真的出现在「文档资料」那张表上（不是接口里有、页面上没有）；
//   ② 导入记录那张表是不是真的渲染出来了（后端一直有接口，缺的就是这张表）；
//   ③ **刷新/重开浏览器之后还在不在**（内存态会骗过"当场看一眼"的走查）。
//
// ★ 提速（用户多次要求，见派单第四节）：
//   · 本走查**只碰文档与导入记录**，全程不调用需要主数据集的接口
//     （/api/chat*、/api/tables*、/api/reports*）—— 那些要等 23MB Excel 预热 250s+。
//     页面自己发起的请求不去管它（Promise.allSettled，互不阻塞），这里只等我们要的那几块。
//   · 只跑一轮，不重写第二版探针。
//   · ★ 页面自己会触发主数据集预热（refreshAll 里那两个接口），预热期间 GIL 被占满、
//     别的请求全被饿着 —— 所以走查**等页面这一轮请求全部落地**再开始做导入
//     （见 beginSegment 里那段长等待）。实测预热 318.6 秒；这段时间不是产品缺陷，
//     是"沙箱 10 秒就绪"只覆盖服务端、没覆盖"页面一打开就去读主数据集"这件事。
//
// 量什么（每一步都读**真 DOM**，不是看源码猜）：
//   ① 全新沙箱 → 「导入记录」空态在、「已入库数据源」说的是"还没有数据源"那句话；
//   ② 导入一份标题被 UTF-16 误读的 PDF → 导入记录里出现一条「已入库」，
//      文件名/类型/时间/落成了什么/字数齐；「文档资料」里也出现了它；
//   ③ 标题**不是乱码**，而且**等于文档里解出来的那个**（不是文件名兜底兜出来的）；
//   ④ 点「详情」→ 展开的详情里状态/时间/落成/字数都在；
//   ⑤ 刷新整页 → 文档与导入记录**都还在**（A-1 的"刷新页面后仍然存在"）；
//   ⑥ 导入一份打不开的 PDF → 记录里出现一条「导入失败」+ 可读原因（A-4 在页面上的样子）；
//   ⑦ 全程没有 JS 报错。
// 截图落在 outputs/_cdp_shots/。
//
// ★ CDP 的两条坑（从 fr003 那份探针抄来的实测经验，别再踩一遍）：
//   ① `DOM.getDocument` 必须带 `depth: 1`，否则整棵 DOM 树回来会把连接噎住；
//   ② 一段流程里命令做多了，渲染进程会整体不再应答 → 每段换**全新浏览器进程 + 全新 profile**
//      （本走查只有 3 段：注册 / 导入+详情 / 刷新后仍在）。文件框必须传**绝对路径**
//      （相对路径会静默地什么都不设）。

const BASE = process.argv[2] || "http://127.0.0.1:8538";
let PORT = 0;
const pickPort = () => 9800 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000003";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr009a";
const FIXTURES = {
  good: `${SANDBOX}/季度资料.pdf`,        // 标题被 UTF-16 误读的那份
  broken: `${SANDBOX}/打不开的.pdf`,      // 后缀是 pdf、内容不是 PDF
};
//: 这份 PDF 文档里的真实标题（探针要拿它跟页面上显示的对）
const EXPECTED_TITLE = "Quarterly Report 2011";
//: 文件名（**故意与标题不同**：能对上就说明标题不是靠文件名蒙的）
const EXPECTED_FILENAME = "季度资料.pdf";

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
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr009a_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}
for (const [key, file] of Object.entries(FIXTURES)) {
  if (!fs.existsSync(file)) {
    console.log(`❌ 缺少样例文件：${file}（${key}）`);
    process.exit(1);
  }
}

const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr009a_cdp_"));
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

async function restartBrowser() {
  SESSION = null;
  const pid = browser.pid;
  try { browser.kill(); } catch { /* 已经退了 */ }
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  await sleep(1200);
  browser = spawnBrowser();
  await pageTarget();
  await sleep(600);
}

const click = (session, id) =>
  session.evalJs(`document.getElementById(${JSON.stringify(id)}).click()`);

const visible = (session, id) => session.evalJs(`(() => {
  const el = document.getElementById(${JSON.stringify(id)});
  if (!el || el.hidden) return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
})()`);

const textOf = (session, id) =>
  session.evalJs(`(() => {
    const el = document.getElementById(${JSON.stringify(id)});
    return el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
  })()`);

/** 等某张验证码图**真的有内容**（`src` 是 data: URI 那种）。

    为什么要等：登录页/注册页的验证码是页面自己去 `GET /api/auth/captcha` 取回来再画上去的。
    图还没到手时 `img.src` 是空的 —— 这时候"读验证码"读到的是空串，填进去必然登录失败，
    然后一路重试到超时。**先等症状出现，再断言它**，这是走查脚本里最容易漏的一条。
    （第一版就是这么翻车的：服务慢的时候页面刚好读完了图，服务一快就变成竞态。） */
async function waitForCaptcha(session, imageId, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await session.evalJs(`(() => {
      const img = document.getElementById(${JSON.stringify(imageId)});
      return Boolean(img && img.src && img.src.length > 64);
    })()`).catch(() => false);
    if (ready) return true;
    await sleep(300);
  }
  return false;
}

/** 已登录就直接返回；被踢回登录页就把验证码读出来、照表单登一次（最多试 3 次）。 */
async function ensureLoggedIn(session) {
  // document.body 可能在"页面正在导航"的那一瞬间是 null —— 按"还没进去"处理，别让走查炸掉
  const locked = () => session.evalJs(`document.body ? document.body.classList.contains("is-locked") : true`);
  if (!(await locked())) return true;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (attempt > 1) {
      await session.send("Page.reload", { ignoreCache: true });
      await sleep(2500);
      for (let i = 0; i < 60; i += 1) {
        const ok = await session.evalJs(`Boolean(document.getElementById("login-captcha-img"))`)
          .catch(() => false);
        if (ok) break;
        await sleep(250);
      }
    }
    await waitForCaptcha(session, "login-captcha-img");
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
      && Boolean(document.getElementById("imports-body"))`).catch(() => false);
    if (ok) break;
    await sleep(250);
  }
  // ★ 等这一页自己发出去的请求**全部落地**再往下走。
  //   为什么必须等：页面一进来 `refreshAll` 就会去读那两个"要主数据集"的接口
  //   （/api/datasets 与 /api/chat/capabilities）→ 服务端那次 22MB Excel 预热
  //   （实测 **318.6 秒**）会把 GIL 占满，这期间**别的请求都被饿着**：
  //   实测症状是"点了导入、页面一直在转、服务端访问日志里连这条 POST 都没有"。
  //   这不是产品缺陷（本脚本另行用 HTTP 直打同一个端点：201、1.5 秒），
  //   是**预热窗口内的资源饥饿** —— 所以这里用**一次长超时**等它过去，
  //   而不是让走查在饥饿窗口里做断言（那种红是假红）。
  //   判据用页面自己的 banner：refreshAll 全部 settle 之后它会被收起来。
  const warmDeadline = Date.now() + 600000;
  let announced = false;
  while (Date.now() < warmDeadline) {
    const busy = await session.evalJs(`(() => {
      const banner = document.getElementById("banner");
      return Boolean(banner) && !banner.hidden;
    })()`).catch(() => false);
    if (!busy) break;
    if (!announced) {
      console.log("   （服务端正在预热主数据集，等它读完再做导入 —— 实测要 300 秒上下）");
      announced = true;
    }
    await sleep(2000);
  }
  await sleep(500);
  await sleep(500);
}

/** 往文件选择框里塞文件（真事件，走页面自己的 change 监听）。必须传**绝对路径**。 */
async function pickFiles(session, selector, files) {
  const { root } = await session.send("DOM.getDocument", { depth: 1 });
  const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error(`页面上没有这个元素：${selector}`);
  await session.send("DOM.setFileInputFiles", { nodeId, files: files.map((f) => path.resolve(f)) });
  await sleep(200);
}

/** 表格里的每一行读成 {cells: [...]}（读真 DOM，不是读源码）。 */
const readRows = (session, bodyId) => session.evalJs(`(() => {
  const body = document.getElementById(${JSON.stringify(bodyId)});
  if (!body) return null;
  return Array.from(body.children).map((tr) =>
    Array.from(tr.children).map((td) => td.textContent.replace(/\\s+/g, " ").trim()));
})()`);

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip = null;
    if (id) {
      const box = await session.evalJs(`(() => {
        const el = document.getElementById(${JSON.stringify(id)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })()`);
      if (box && box.w > 0) {
        clip = { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16),
                 width: box.w + 32, height: box.h + 32, scale: 2 };
      }
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

  // ── ① 注册一个本机账号 ─────────────────────────────────────────────────
  console.log("\n=== ① 注册一个本机账号（第一个账号，直接进系统）===");
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await waitForCaptcha(s, "reg-captcha-img");
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "可见性用例");
      set("reg-pwd", ${JSON.stringify(PASSWORD)});
      set("reg-pwd2", ${JSON.stringify(PASSWORD)});
      return true;
    })()`);
    const code = await s.evalJs(`(() => {
      const img = document.getElementById("reg-captcha-img");
      if (!img || !img.src) return "";
      const svg = decodeURIComponent((img.src.split(",")[1] || ""));
      const found = [];
      svg.replace(/<text[^>]*>([\\s\\S]*?)<\\/text>/g, (_, inner) => {
        const t = inner.replace(/<[^>]*>/g, "").trim();
        if (t) found.push(t);
        return "";
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
  check(!(await step((s) => s.evalJs(`document.body ? document.body.classList.contains("is-locked") : true`))),
    "注册后进入系统（登录页收起）");

  // ── ② 导入那份 PDF，看「导入记录」与「文档资料」两张表 ────────────────────
  console.log("\n=== ② 导入一份标题被 UTF-16 误读的 PDF → 两张表都要看得见 ===");
  await beginSegment("重开浏览器，进「数据管理」…");
  check((await step((s) => visible(s, "imports-empty"))) === true, "「导入记录」空表显示空状态");
  const emptyHint = await step((s) => textOf(s, "sources-empty-hint"));
  check(/导入一份带数据的文件/.test(emptyHint || ""), "还没有文档时，「已入库数据源」说的是原来那句", emptyHint);

  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURES.good]));
  await step((s) => click(s, "btn-imp-run"));
  // 导入是同步的（解析 + 入库都在这次请求里），给它足够的时间落地
  await sleep(6000);

  const importRows = await step((s) => readRows(s, "imports-body"));
  check(Array.isArray(importRows) && importRows.length === 1, "「导入记录」里有且只有一条记录",
    JSON.stringify(importRows));
  const row = (importRows || [])[0] || [];
  check(row[0] === EXPECTED_FILENAME, "记录的「文件名」对得上", row[0]);
  check(row[1] === "PDF 文档", "记录的「类型」是 PDF 文档", row[1]);
  check(/已入库/.test(row[2] || ""), "记录的「状态」是已入库", row[2]);
  check(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(row[3] || ""), "记录的「时间」是个真时间", row[3]);
  check(row[4] === "文档资料", "记录的「落成了什么」写明是文档资料", row[4]);
  check(/字$/.test(row[5] || ""), "记录的「字数或行数」给的是字数", row[5]);

  const docRows = await step((s) => readRows(s, "docs-table-body"));
  check(Array.isArray(docRows) && docRows.length === 1, "「文档资料」里出现了这份文档",
    JSON.stringify(docRows));
  const doc = (docRows || [])[0] || [];
  check(doc[0] === EXPECTED_FILENAME, "文档表里的文件名对得上", doc[0]);
  check(doc[1] === "导入资料", "文档表里「来源」写的是导入资料", doc[1]);
  check(!/[\u0000-\u0008\u000e-\u001f]/.test(doc.join("")), "页面上没有任何控制字符（乱码）");

  // 标题：点「看正文」旁边的详情看不到标题，所以用接口里那条记录的标题与页面对照 ——
  // 但**页面上的证据**是文件名与来源列；标题那条由接口返回 + 单元测试钉住。
  const titleFromApi = await step((s) => s.evalJs(`(async () => {
    const r = await fetch("/api/documents?limit=5", { credentials: "same-origin" });
    const body = await r.json();
    return (body.documents || []).map((d) => d.title);
  })()`));
  check(Array.isArray(titleFromApi) && titleFromApi[0] === EXPECTED_TITLE,
    "标题是从文件里解出来的那个（不是文件名兜底）", JSON.stringify(titleFromApi));
  check(titleFromApi[0] !== EXPECTED_FILENAME, "标题与文件名确实不是同一个字符串");

  const hintAfter = await step((s) => textOf(s, "sources-empty-hint"));
  const titleAfter = await step((s) => textOf(s, "sources-empty-title"));
  check(/有文档资料/.test(titleAfter || ""), "★ 只导了 PDF 时，「已入库数据源」空表不再说「没导进来」",
    titleAfter);
  check(/文档资料/.test(hintAfter || ""), "空表那句人话说清了「它们作为文档资料在上面」", hintAfter);
  console.log("   截图：", await shot("fr009a_01_导入记录与文档资料.png", "page-data"));

  // ── ③ 点「详情」能进到那一条 ────────────────────────────────────────────
  console.log("\n=== ③ 点「详情」→ 展开这一条导入记录 ===");
  await step((s) => s.evalJs(`(() => {
    const btn = document.querySelector('#imports-body [data-import-action="view"]');
    if (!btn) throw new Error("导入记录里没有「详情」按钮");
    btn.click();
    return true;
  })()`));
  await sleep(1500);
  check((await step((s) => visible(s, "import-detail"))) === true, "详情面板展开了");
  const detailText = await step((s) => textOf(s, "import-detail"));
  for (const [label, pattern] of [["状态", /已入库/], ["时间", /\d{4}-\d{2}-\d{2}/], ["落成了什么", /文档资料/], ["字数或行数", /字/]]) {
    check(pattern.test(detailText || ""), `详情里有「${label}」`, (detailText || "").slice(0, 60));
  }
  console.log("   截图：", await shot("fr009a_02_导入详情.png", "import-detail"));

  // ── ④ 刷新整页（= 重开浏览器进程）→ 两条记录都还在吗 ──────────────────────
  console.log("\n=== ④ 重开浏览器再进「数据管理」→ 文档与导入记录都还在（A-1）===");
  await beginSegment("重开浏览器，重新进「数据管理」…");
  const docsAfter = await step((s) => readRows(s, "docs-table-body"));
  const importsAfter = await step((s) => readRows(s, "imports-body"));
  check((docsAfter || []).length === 1, "刷新后「文档资料」里还有这一份", JSON.stringify(docsAfter));
  check((importsAfter || []).length === 1, "刷新后「导入记录」里还有这一条", JSON.stringify(importsAfter));

  // ── ⑤ 失败的导入也要留一条（A-4 在页面上的样子）──────────────────────────
  console.log("\n=== ⑤ 导入一份打不开的 PDF → 记录里出现「导入失败」+ 可读原因 ===");
  await step((s) => pickFiles(s, "#imp-file-input", [FIXTURES.broken]));
  await step((s) => click(s, "btn-imp-run"));
  await sleep(6000);
  const rows2 = await step((s) => readRows(s, "imports-body"));
  check((rows2 || []).length === 2, "失败的那次也留下了一条记录", JSON.stringify(rows2));
  const failedRow = (rows2 || []).find((item) => /失败/.test(item[2] || "")) || [];
  check(failedRow[0] === "打不开的.pdf", "失败记录的文件名对得上", failedRow[0]);
  check(/导入失败/.test(failedRow[2] || ""), "状态是「导入失败」（没有伪装成已入库）", failedRow[2]);
  check(failedRow[4] === "无产物", "失败的那条没有产物", failedRow[4]);
  await step((s) => s.evalJs(`(() => {
    const rows = Array.from(document.querySelectorAll("#imports-body tr"));
    const target = rows.find((tr) => tr.textContent.includes("打不开的.pdf"));
    if (!target) throw new Error("找不到失败那一行");
    target.querySelector('[data-import-action="view"]').click();
    return true;
  })()`));
  await sleep(1500);
  const failedDetail = await step((s) => textOf(s, "import-detail"));
  check(/失败原因/.test(failedDetail || ""), "详情里给出了「失败原因」", (failedDetail || "").slice(0, 80));
  check(/打不开|Pdf|pdf|解析|读/.test(failedDetail || ""), "原因是人话（不是错误码堆砌）");
  console.log("   截图：", await shot("fr009a_03_失败记录.png", "import-detail"));

  // ── ⑥ 全程没有 JS 报错 ──────────────────────────────────────────────────
  console.log("\n=== ⑥ 全程没有 JS 报错 ===");
  check(consoleErrors.length === 0, "页面没有 JS 报错", consoleErrors.slice(0, 3).join(" | "));
} catch (err) {
  failed += 1;
  console.log(`\n❌ 走查中断：${err && err.message ? err.message : err}`);
} finally {
  try { browser.kill(); } catch { /* 忽略 */ }
  try { spawnSync("taskkill", ["/F", "/T", "/PID", String(browser.pid)], { stdio: "ignore" }); } catch { /* 忽略 */ }
  for (const dir of UDDS) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* 忽略 */ }
  }
  console.log(`\n=== 结果：${passed} 通过 / ${failed} 失败 ===`);
  if (consoleErrors.length) {
    console.log("页面 console 错误（前 5 条）：");
    consoleErrors.slice(0, 5).forEach((line) => console.log("   -", String(line).slice(0, 200)));
  }
  process.exit(failed ? 1 : 0);
}
