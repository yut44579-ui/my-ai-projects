// fr010c_joint_cdp.mjs · FR-010-C「资料 + 销售数据联合分析」在**真浏览器**里的实测
//                        （真 Edge + CDP，零依赖）
//
//   node scripts/fr010c_joint_cdp.mjs http://127.0.0.1:8542
//
// 跑之前先起一个真服务（沙箱目录只是别把本次导入写进真实 state/ 与真实 data/app.db）：
//   .venv/Scripts/python.exe scripts/fr010c_cdp_reset.py      # 先把沙箱建好（表要建在服务启动前）
//   SRA_STATE_DIR=outputs/_cdp_run_fr010c/state SRA_DB_PATH=outputs/_cdp_run_fr010c/app.db \
//   SRA_ORIGINAL_DIR=outputs/_cdp_run_fr010c/original SRA_UPLOAD_DIR=outputs/_cdp_run_fr010c/uploads \
//   SRA_DOC_DIR=outputs/_cdp_run_fr010c/documents SRA_OUTPUT_DIR=outputs/_cdp_run_fr010c/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8542
//
// 为什么必须真开浏览器（派单 §四 验收 11：真浏览器走一遍 1/4/5 并截图）：
//   ① 三段（资料 / 数据 / 推断）在页面上是不是**真的按这个顺序画出来**的；
//   ② 「投入产出比给不了」那句在页面上到底长什么样（正文里不能混进任何数字）；
//   ③ 导入带注入正文的资料前后，**同一句话的数字在页面上逐位相同**；
//   ④ ★ C6：长提示条**默认一行**（量几何，不是看代码）、点开之后**全文与后端给的逐字相同**。
// 量几何只能用 CDP 的 `getBoundingClientRect` —— `dump-dom` 量不了（这是踩过的坑）。
//
// ★ 提速（派单 §五）：本走查只跑两段；需要主数据集的那几条提问**每条只发一次**；
//   页面一打开会 refreshAll 去读主数据集（实测预热 ~300 秒，这期间 GIL 被占满）——
//   用**一次长超时**等它过去（判据是页面 banner 收起来），而不是 sleep 分段干等。
// 截图落在 outputs/_cdp_shots/。
//
// ★ CDP 的两条坑（从 fr003/fr009a/fr010a/fr010b 那几份探针抄来的实测经验，别再踩一遍）：
//   ① `DOM.getDocument` 必须带 `depth: 1`，否则整棵 DOM 树回来会把连接噎住；
//   ② 一段流程里命令做多了，渲染进程会整体不再应答 → 每段换**全新浏览器进程 + 全新 profile**。
//      文件框必须传**绝对路径**（相对路径会静默地什么都不设）。

const BASE = process.argv[2] || "http://127.0.0.1:8542";
let PORT = 0;
const pickPort = () => 9900 + Math.floor(Math.random() * 400);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
const ACCOUNT = "13900000007";
const PASSWORD = "abc12345";
const SANDBOX = "outputs/_cdp_run_fr010c";
const GAME_DOC = "游戏资料.md";
const INJECTION_DOC = "注入测试.md";
const FIXTURES = [GAME_DOC, INJECTION_DOC].map((n) => `${SANDBOX}/${n}`);

const SINGLE_QUESTION = "这个月卖了多少？";                        // C6 的提示条 + C4 的数字对比
const JOINT_QUESTION = "结合这个游戏资料和现在的销售数据，分析一下这个游戏后面应该怎么发展";
const ROI_QUESTION = "结合资料算一下我们的投入产出比";

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
for (const file of FIXTURES) {
  if (!fs.existsSync(file)) {
    console.log(`❌ 缺少样例文件：${file}（先跑 scripts/fr010c_cdp_reset.py）`);
    process.exit(1);
  }
}

// 每次跑都要是干净的一次：重置**本脚本沙箱**（真实 state/ 与真实 data/app.db 永远不碰）。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const python = process.env.SRA_PYTHON || path.join(process.cwd(), ".venv", "Scripts", "python.exe");
  const reset = spawnSync(python, [path.join("scripts", "fr010c_cdp_reset.py")],
    { cwd: process.cwd(), encoding: "utf-8" });
  if (reset.status !== 0) {
    console.log("❌ 沙箱重置失败：", reset.stdout, reset.stderr);
    process.exit(1);
  }
  console.log(reset.stdout.trim());
}

// ── CDP 基建（与 fr009a/fr010a/fr010b 探针同一套）────────────────────────
const UDDS = [];
function spawnBrowser() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr010c_cdp_"));
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

/** 等某张验证码图**真的有内容**（`src` 是 data: URI 那种）。 */
async function waitForCaptcha(session, imageId, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await session.evalJs(`(() => {
      const img = document.getElementById(${JSON.stringify(imageId)});
      return Boolean(img && img.src && img.src.length > 64);
    })()`).catch(() => false);
    if (ok) return true;
    await sleep(300);
  }
  return false;
}

async function readCaptcha(session, imageId) {
  return session.evalJs(`(() => {
    const img = document.getElementById(${JSON.stringify(imageId)});
    if (!img || !img.src) return "";
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
    return chars.join("");
  })()`);
}

/** 已登录就直接返回；被踢回登录页就把验证码读出来、照表单登一次（最多试 3 次）。 */
async function ensureLoggedIn(session) {
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
    const code = await readCaptcha(session, "login-captcha-img");
    if (!code) console.log("   ⚠ 读不出登录验证码");
    await session.evalJs(`(() => {
      document.getElementById("ltab-account").click();
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("login-name", ${JSON.stringify(ACCOUNT)});
      set("login-pwd", ${JSON.stringify(PASSWORD)});
      set("login-captcha", ${JSON.stringify(code)});
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

/** 统一的开段动作：重启浏览器 → 等骨架 → 补登录 → 等页面自己的请求全部落地。 */
async function beginSegment(label, hash = "#/overview") {
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
  await session.send("Page.navigate", { url: BASE + "/" + hash });
  for (let i = 0; i < 120; i += 1) {
    const ok = await session.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("nl-input"))`).catch(() => false);
    if (ok) break;
    await sleep(250);
  }
  await waitForWarmup(session);
}

/** 等服务端把主数据集预热完（判据是页面 banner 收起来）——一次长超时，不分段干等。 */
async function waitForWarmup(session) {
  const deadline = Date.now() + 900000;
  let announced = false;
  while (Date.now() < deadline) {
    const busy = await session.evalJs(`(() => {
      const banner = document.getElementById("banner");
      return Boolean(banner) && !banner.hidden;
    })()`).catch(() => false);
    if (!busy) break;
    if (!announced) {
      console.log("   （服务端正在预热主数据集，等它读完再提问 —— 实测 300 秒上下）");
      announced = true;
    }
    await sleep(2000);
  }
  await sleep(500);
}

/** 提一个问题，等**这一条**的回答渲染出来（先认问题、再认回答，避免读到上一条）。 */
async function ask(session, question, timeoutMs = 900000) {
  await session.evalJs(`(() => {
    const el = document.getElementById("nl-input");
    el.value = ${JSON.stringify(question)};
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
  await click(session, "nl-ask");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const seen = await session.evalJs(`(() => {
      const q = document.getElementById("chat-question");
      const box = document.getElementById("chat-answer");
      const first = box ? box.querySelector(".answer-head b") : null;
      return { q: q ? q.textContent : "", title: first ? first.textContent.trim() : "",
               text: box ? box.textContent.trim().length : 0 };
    })()`).catch(() => null);
    if (seen && seen.q.includes(question) && seen.title && seen.text > 0) {
      await sleep(500);
      return true;
    }
    await sleep(500);
  }
  return false;
}

async function shot(name, id) {
  const session = await openSession();
  try {
    let clip = null;
    if (id) {
      // ★ 先把元素滚进视口再量：clip 用的是**视口坐标**（不带 captureBeyondViewport），
      //   元素在首屏之外时量出来的是屏幕外的坐标，截出来是一张空图。
      const box = await session.evalJs(`(() => {
        let el = document.getElementById(${JSON.stringify(id)});
        if (!el) return null;
        if (el.tagName === "TBODY" && el.closest("table")) el = el.closest("table");
        el.scrollIntoView({ block: "center", inline: "nearest" });
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })()`);
      await sleep(500);
      if (box && box.w > 0 && box.h > 0) {
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

/** 往文件选择框里塞文件（真事件，走页面自己的 change 监听）。必须传**绝对路径**。 */
async function pickFiles(session, selector, files) {
  const { root } = await session.send("DOM.getDocument", { depth: 1 });
  const { nodeId } = await session.send("DOM.querySelector", { nodeId: root.nodeId, selector });
  if (!nodeId) throw new Error(`页面上没有这个元素：${selector}`);
  await session.send("DOM.setFileInputFiles", { nodeId, files: files.map((f) => path.resolve(f)) });
  await sleep(200);
}

// ── 本 TASK 专用量尺 ─────────────────────────────────────────────────────
/** 三段的几何 + 标题 + 来源 + 正文（量真 DOM，不看接口）。 */
const answerShape = (session) => session.evalJs(`(() => {
  const box = document.getElementById("chat-answer");
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) }; };
  return {
    body: box ? box.textContent.replace(/\\s+/g, " ").trim() : "",
    sections: box ? Array.from(box.querySelectorAll(".answer-section")).map((s) => {
      const head = s.querySelector(".answer-head b");
      const tag = s.querySelector(".src-tag");
      const text = s.querySelector(".answer-text");
      return { title: head ? head.textContent.trim() : "",
               tag: tag ? tag.textContent.trim() : "",
               text: text ? text.textContent : "",
               height: text ? rect(text).h : 0 };
    }) : [],
  };
})()`);

/** 提示条的形状：收起时的高度 / 摘要文字 / 全文 / 展开状态 + 顶部状态条的高度。 */
const noticeShape = (session) => session.evalJs(`(() => {
  const el = document.getElementById("chat-notice");
  const state = document.getElementById("chat-state");
  const h = (node) => node ? Math.round(node.getBoundingClientRect().height) : 0;
  const fold = el ? el.querySelector(".notice-fold") : null;
  const summary = fold ? fold.querySelector("summary") : null;
  const full = fold ? fold.querySelector(".notice-full") : null;
  const style = el ? getComputedStyle(el) : null;
  return {
    noticeHeight: h(el), stateHeight: h(state),
    lineHeight: style ? (parseFloat(style.lineHeight) || 0) : 0,
    noticeVisible: Boolean(el) && h(el) > 0,
    hasFold: Boolean(fold), open: fold ? fold.open : null,
    summaryText: summary ? summary.textContent.trim() : "",
    fullText: full ? full.textContent : "",
    containerText: el ? el.textContent.replace(/\\s+/g, " ").trim() : "",
    fullHeight: h(full),
  };
})()`);

/** 后端那条记录里的提示语原文（拿它跟"展开后的全文"逐字比）。 */
const latestNotice = (session) => session.evalJs(
  `fetch("/api/conversations?limit=1").then((r) => r.json())
     .then((d) => (d.conversations && d.conversations[0] ? d.conversations[0].notice : null))`);

/** 正文里的数字（与判据一致：英文/字母前缀不算，千分位归一后再比）。 */
function digitsOf(text) {
  const found = String(text || "").match(/(?<![A-Za-z])\d[\d,]*(?:\.\d+)?/g) || [];
  return [...new Set(found.map((t) => t.replace(/,/g, "").replace(/\.$/, "")))].sort();
}

try {
  // ── ① 注册 + 真上传两份资料 ───────────────────────────────────────────
  console.log("\n=== ① 注册 + 从页面真上传 2 份资料 ===");
  for (let i = 0; i < 150; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate")) && Boolean(document.getElementById("btn-register"))`));
    if (ok) break;
    await sleep(200);
  }
  await step(async (s) => {
    await click(s, "link-register");
    await sleep(300);
    await waitForCaptcha(s, "reg-captcha-img");
    await s.evalJs(`(() => {
      const set = (id, v) => { const el = document.getElementById(id); el.value = v;
        el.dispatchEvent(new Event("input", { bubbles: true })); };
      set("reg-name", ${JSON.stringify(ACCOUNT)});
      set("reg-display", "联合分析用例");
      set("reg-pwd", ${JSON.stringify(PASSWORD)});
      set("reg-pwd2", ${JSON.stringify(PASSWORD)});
      return true;
    })()`);
    const code = await readCaptcha(s, "reg-captcha-img");
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

  await beginSegment("重开浏览器，进「数据管理」上传资料…", "#/data");
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("imp-file-input"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  await step((s) => pickFiles(s, "#imp-file-input", FIXTURES));
  await step((s) => click(s, "btn-imp-run"));
  const deadline = Date.now() + 180000;
  let rows = [];
  while (Date.now() < deadline) {
    rows = await step((s) => s.evalJs(`(() => {
      const body = document.getElementById("imports-body");
      return body ? Array.from(body.children).map((tr) =>
        Array.from(tr.children).map((td) => td.textContent.replace(/\\s+/g, " ").trim())) : [];
    })()`).catch(() => [])) || [];
    if (rows.length >= FIXTURES.length) break;
    await sleep(1000);
  }
  const names = rows.map((row) => row[0]);
  for (const file of FIXTURES) {
    check(names.includes(path.basename(file)), `导入记录里有《${path.basename(file)}》`, names.join(" / "));
  }
  console.log("   截图：", await shot("fr010c_01_导入记录.png", "imports-body"));

  // ── ② C6：长提示条默认一行 + 点开全文逐字相同 ─────────────────────────
  console.log("\n=== ② C6 提示条：默认一行摘要 / 点开是全文（单值回答）===");
  await beginSegment("重开浏览器，提问一个单值问题…");
  check(await step((s) => ask(s, SINGLE_QUESTION)), `「${SINGLE_QUESTION}」拿到了回答`);
  const collapsed = await step((s) => noticeShape(s));
  check(collapsed.noticeVisible, "★ 提示条仍然在回答下方（有高度，不是被藏了）",
    `h=${collapsed.noticeHeight}`);
  check(collapsed.hasFold && collapsed.open === false, "★ 默认是**收起**状态（可展开的提示条）");
  check(collapsed.noticeHeight <= Math.ceil(collapsed.lineHeight * 2) + 6,
    "★ 收起后只有**一行**那么高（几何证据）",
    `h=${collapsed.noticeHeight} lineHeight=${collapsed.lineHeight}`);
  check(collapsed.summaryText.length > 0 && collapsed.summaryText.length <= 30,
    "摘要是一句话，不是把全文顶上来", collapsed.summaryText);
  check(!/排除|数据范围|含首尾全天/.test(collapsed.summaryText)
        && !/…$/.test(collapsed.summaryText)
        && collapsed.fullText !== collapsed.summaryText,
    "★ 摘要不是把口径截半截顶上来（完整的口径与假设只在展开里）", collapsed.summaryText);
  check(/「这个月」/.test(collapsed.containerText),
    "B8② 的相对时间提示**仍然在**（B 的能力一个字没弱化）");
  check(collapsed.stateHeight <= Math.ceil(collapsed.lineHeight * 2) + 6,
    "顶部状态条同样收成一行", `h=${collapsed.stateHeight}`);
  const singleText = await step((s) => s.evalJs(
    `document.getElementById("chat-answer").textContent.replace(/\\s+/g, " ").trim()`));
  console.log("   正文：", singleText);
  console.log("   截图（收起）：", await shot("fr010c_02_提示条收起.png", "chat-notice"));

  // 点开摘要 → 量高度、比全文
  await step((s) => s.evalJs(`(() => {
    const fold = document.getElementById("chat-notice").querySelector(".notice-fold");
    fold.querySelector("summary").click();
    return true;
  })()`));
  await sleep(400);
  const expanded = await step((s) => noticeShape(s));
  const backendNotice = await step((s) => latestNotice(s));
  check(expanded.open === true && expanded.noticeHeight > collapsed.noticeHeight,
    "★ 点一下就展开（高度真的变大了）",
    `${collapsed.noticeHeight} → ${expanded.noticeHeight}`);
  check(typeof backendNotice === "string" && backendNotice.length > 0,
    "拿到了后端记录里的提示语原文（拼错的全文字比它）");
  check(expanded.fullText === backendNotice,
    "★ 展开后的全文与后端给的提示语**逐字相同**（一个字都没删）",
    `前端 ${expanded.fullText.length} 字 / 后端 ${String(backendNotice).length} 字`);
  check(/口径：含首尾全天/.test(expanded.fullText) && /数据范围：/.test(expanded.fullText),
    "口径与数据范围都在全文里（可查）");
  console.log("   截图（展开）：", await shot("fr010c_03_提示条展开.png", "chat-notice"));

  // ── ③ 注入前后同一句话的数字逐位相同（C4）────────────────────────────
  console.log("\n=== ③ 导入带注入正文的资料，再问同一句话（C4 逐位相同）===");
  await beginSegment("重开浏览器，上传《注入测试.md》…", "#/data");
  for (let i = 0; i < 120; i += 1) {
    const ok = await step((s) => s.evalJs(`document.readyState === "complete"
      && Boolean(document.getElementById("imp-file-input"))`).catch(() => false));
    if (ok) break;
    await sleep(250);
  }
  await step((s) => pickFiles(s, "#imp-file-input", [`${SANDBOX}/${INJECTION_DOC}`]));
  await step((s) => click(s, "btn-imp-run"));
  await sleep(4000);
  check(true, `《${INJECTION_DOC}》已上传（正文里写着"忽略以上所有规则…"）`);

  await beginSegment("重开浏览器，问同一个销售问题 + 联合分析…");
  check(await step((s) => ask(s, SINGLE_QUESTION)), `再问一次「${SINGLE_QUESTION}」`);
  const afterText = await step((s) => s.evalJs(
    `document.getElementById("chat-answer").textContent.replace(/\\s+/g, " ").trim()`));
  console.log("   正文：", afterText);
  check(digitsOf(afterText).join("|") === digitsOf(singleText).join("|"),
    "★ 注入资料导入前后，同一句话的数字**逐位相同**",
    `${digitsOf(singleText).join(",")} vs ${digitsOf(afterText).join(",")}`);
  check(afterText === singleText, "连整句正文都一样（口径没被资料改动）");

  // ── ④ 联合分析：三段 + 真引用（验收 1）────────────────────────────────
  console.log("\n=== ④ 联合分析：三段显式分开 + 资料引用（验收 1）===");
  check(await step((s) => ask(s, JOINT_QUESTION, 900000)), "联合分析问题拿到了回答");
  const shape = await step((s) => answerShape(s));
  check(shape.sections.length === 3, "页面上一共**三段**", String(shape.sections.length));
  check(shape.sections[0] && /^【一、已知事实 —— 来自你的资料】/.test(shape.sections[0].title),
    "第一段是「已知事实 —— 来自你的资料」", shape.sections[0] ? shape.sections[0].title : "（没有）");
  check(shape.sections[1] && /^【二、已知事实 —— 来自销售数据】/.test(shape.sections[1].title),
    "第二段是「已知事实 —— 来自销售数据」", shape.sections[1] ? shape.sections[1].title : "（没有）");
  check(shape.sections[2] && /分析与建议/.test(shape.sections[2].title)
        && /我的推断，不是事实/.test(shape.sections[2].title),
    "第三段是「分析与建议（我的推断，不是事实）」", shape.sections[2] ? shape.sections[2].title : "（没有）");
  check(/《[^》]+》第 \d+ 节/.test(shape.sections[0].text), "★ 资料段每条都带出处（《文件名》第 N 节）");
  check(/元/.test(shape.sections[1].text) && /口径/.test(shape.sections[1].text),
    "数据段带金额与口径（数字由程序算出）");
  check(/忽略以上所有规则/.test(shape.sections[0].text),
    "★ 注入正文被**原样引用**（不是「怕注入就不答」）", "");
  check(/不等于本系统的结论或规则/.test(shape.sections[0].text),
    "引用后面跟着属性声明（资料的说法 ≠ 本系统的规则）");
  const jointText = shape.sections.map((s) => s.text).join("\n");
  check(/我的推断，不是事实/.test(shape.sections[2].text), "分析与建议段自己声明是推断");
  check(!/推断/.test(shape.sections[0].text) && !/推断/.test(shape.sections[1].text),
    "前两段（事实）里没有「推断」这类话");
  const earlier = digitsOf(shape.sections[0].text + shape.sections[1].text);
  const inAnalysis = digitsOf(shape.sections[2].text);
  check(inAnalysis.every((token) => earlier.includes(token)),
    "★ 分析段里的每个数字都能在前两段找到出处（机器逐段扫）",
    `分析段 ${inAnalysis.join(",") || "（无数字）"}`);
  console.log("   截图：", await shot("fr010c_04_联合分析三段.png", "chat-answer"));
  console.log("   （正文首屏）", jointText.slice(0, 120));
  check(digitsOf(shape.sections[1].text).includes(digitsOf(afterText)[0] || ""),
    "联合分析的数据段与单值题的数字同源（同一套计算）");

  // ── ⑤ 投入产出比：明说给不了、正文零数字（验收 4）────────────────────
  console.log("\n=== ⑤ 缺成本侧数据：投入产出比（验收 4）===");
  check(await step((s) => ask(s, ROI_QUESTION)), `「${ROI_QUESTION}」拿到了回答`);
  const roi = await step((s) => answerShape(s));
  const roiBody = roi.sections.map((s) => s.text).join("\n");
  check(/我给不了/.test(roi.sections[1] ? roi.sections[1].text : ""), "★ 明说给不了");
  check(/投放成本|采购成本/.test(roiBody), "★ 说清缺的是哪类字段（成本侧）");
  check(/InvoiceNo/.test(roiBody), "说清数据里真有哪些字段");
  check(digitsOf(roiBody).length === 0, "★ 正文里**一个数字都没有**（零 ROI 数字）",
    digitsOf(roiBody).join(",") || "（无）");
  check(!/约|大概|估计|行业平均/.test(roiBody), "没有用「大约/行业平均」把算不出来的东西包装出来");
  console.log("   截图：", await shot("fr010c_05_投入产出比给不了.png", "chat-answer"));
  console.log("   正文：", roiBody.replace(/\s+/g, " ").slice(0, 160));

  // ── ⑥ 全程没有 JS 报错 ────────────────────────────────────────────────
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
