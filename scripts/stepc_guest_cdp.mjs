// stepc_guest_cdp.mjs · 游客限制 + 管理员审批的 **真浏览器** 实测（真 Edge，零依赖）
//
//   node scripts/stepc_guest_cdp.mjs http://127.0.0.1:8536
//
// 跑之前先起一个真服务（沙箱目录只是别把本次注册写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_run/state SRA_DOC_DIR=outputs/_cdp_run/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_run/uploads SRA_OUTPUT_DIR=outputs/_cdp_run/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8536
//
// 为什么要有这一条：用户点名的两条硬要求都是**真交互**的事，静态检查答不了 ——
//   · ④「游客真的被拦住，不是只弹窗」：这里数**真网络请求**。点受限入口前后
//     `window.fetch` 的调用次数必须**一次都不涨**（弹窗不算数，没发生才算数）；
//   · ④「⚠ 只在受限处、不许满屏」：数真 DOM 里 `.guard-ico` 的个数，并逐个回到它
//     的宿主元素上核对：有记号的必须带 data-guard，没带的一个都不许有；
//   · ③「管理员在哪审批」：真点进「系统设置 → 账号管理」，真按「批准」，
//     再去登那个刚注册的账号 —— 看它是不是真的能进来了。
// 截图落在 outputs/_cdp_shots/（命令行第二个参数可改）。
//
// ⚠️ 本脚本会**真的注册两个账号**（这正是它要验的事）。开跑前先清掉
//    outputs/_cdp_run/state/accounts.json 这个**沙箱**账号表（真实 state/ 永远不碰）。

const BASE = process.argv[2] || "http://127.0.0.1:8536";
// 调试端口按进程号错开：上一次跑留下的 Edge 还占着 9336 时，下一次照样能跑
// （不能去杀 msedge 进程 —— 那会把用户自己开着的浏览器一起关掉）。
const PORT = 9336 + (process.pid % 200);
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];

const { spawn } = await import("node:child_process");
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

let ready = false;
for (let i = 0; i < 300; i++) {
  try {
    const r = await fetch(BASE + "/api/health");
    if (r.status === 200) { ready = true; break; }
  } catch { /* 还没起来 */ }
  await sleep(1000);
}
if (!ready) {
  console.log(`❌ 服务没起来（${BASE}/api/health 一直没返回 200）`);
  process.exit(1);
}

if (/127\.0\.0\.1|localhost/.test(BASE)) {
  // 账号文件在哪，**问服务自己**（/api/health 里有 state_dir）—— 不自己拼路径。
  // 拼过一次就吃过亏：起服务时环境变量用的是 git-bash 风格的 /d/... ，
  // 结果服务把账号写到了另一处，脚本清的是这里、服务写的是那里，
  // 于是"每次都从零开始"根本没生效，第二次跑就撞上"账号已被注册"。
  const health = await (await fetch(BASE + "/api/health")).json();
  const stateDir = (health.state || {}).state_dir || "";
  const sandboxAccounts = stateDir ? path.join(stateDir, "accounts.json") : "";
  if (sandboxAccounts && fs.existsSync(sandboxAccounts)) {
    fs.rmSync(sandboxAccounts);
    console.log(`已清空沙箱账号表（每次跑都从"这台机器上一个账号都没有"开始）：${sandboxAccounts}`);
  } else if (sandboxAccounts) {
    console.log(`沙箱账号表本来就不存在（干净的一次）：${sandboxAccounts}`);
  }
}

console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) {
    console.log(`❌ 数据预热失败（${warm.status}）`);
    process.exit(1);
  }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
  process.exit(1);
}

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_guest_cdp_"));
const browser = spawn(edge, [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${udd}`,
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--window-size=1600,1000", BASE + "/",
], { stdio: "ignore" });
browser.unref();

let passed = 0;
let failed = 0;
function check(ok, label, extra = "") {
  if (ok) passed += 1; else failed += 1;
  console.log(`   ${ok ? "✅" : "❌"} ${label}${extra ? " | " + extra : ""}`);
}

let ws;
try {
  let targets = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      targets = await r.json();
      if (targets.some((t) => t.type === "page")) break;
    } catch { /* 还没起来 */ }
    await sleep(500);
  }
  const page = targets?.find((t) => t.type === "page" && t.url.startsWith(BASE));
  if (!page) throw new Error("没找到页面 target：" + JSON.stringify(targets?.map((t) => t.url)));

  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let seq = 0;
  const pending = new Map();
  const consoleErrors = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
      consoleErrors.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    }
    if (msg.method === "Runtime.exceptionThrown") {
      consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  };
  const send = (method, params = {}) => new Promise((res) => {
    const id = ++seq;
    pending.set(id, (m) => res(m.result ?? m.error));
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r?.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed");
    return r?.result?.value;
  };
  const shot = async (name) => {
    const r = await send("Page.captureScreenshot", { format: "png" });
    if (r?.data) fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(r.data, "base64"));
  };
  const boxOf = (expr) => evalJs(`(() => {
    const el = ${expr};
    if (!el) return null;
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
  })()`);
  // 真鼠标点击（走 Input 域，不是替它调 el.click()）：先量矩形，再按下 + 抬起。
  // 为什么非要这样：`el.click()` 绕过了"按钮是不是被盖住 / 是不是在屏幕外"这类问题，
  // 而用户点的就是那个位置。
  const clickAt = async (box, label) => {
    if (!box) throw new Error(`找不到可点的元素：${label}`);
    if (box.w < 2 || box.h < 2) throw new Error(`元素不可点（宽高 ${box.w}x${box.h}）：${label}`);
    for (const type of ["mousePressed", "mouseReleased"]) {
      await send("Input.dispatchMouseEvent", {
        type, x: box.x, y: box.y, button: "left", clickCount: 1,
      });
    }
    await sleep(140);
  };
  const clickSel = async (selector) =>
    clickAt(await boxOf(`document.querySelector(${JSON.stringify(selector)})`), selector);
  // 按**文字**找一个按钮再点它（业务表格的导出按钮没有 id，只有文字）
  // 按文字找按钮再点 —— 要等：业务表格是先拿数据再画工具栏的（数据没回来之前页面上没有这个按钮）。
  // 等不到就把**页面上现有的按钮文字**报出来，好一眼看出是"页面没渲染"还是"文字对不上"。
  const clickText = async (rootSelector, text, tries = 40) => {
    for (let i = 0; i < tries; i += 1) {
      // 按钮文字要**去掉那个 ⚠ 记号**再比：记号是画在按钮里的（游客态才有），
      // 所以游客看到的按钮写着「导出 Excel ⚠」——按用户真看到的那个样子点是点不着的。
      const box = await boxOf(`[...document.querySelector(${JSON.stringify(rootSelector)}).querySelectorAll("button")]
        .find((b) => b.textContent.replace(/⚠/g, "").trim() === ${JSON.stringify(text)})`);
      if (box) return clickAt(box, `${rootSelector} 里的「${text}」`);
      await sleep(250);
    }
    const seen = await evalJs(`(() => {
      const root = document.querySelector(${JSON.stringify(rootSelector)});
      return root ? [...root.querySelectorAll("button")]
        .map((b) => b.textContent.replace(/⚠/g, "").trim()) : null;
    })()`);
    throw new Error(`${rootSelector} 里一直没出现「${text}」；现有按钮：${JSON.stringify(seen)}`);
  };
  // 填一个输入框：点进去 → **全选** → 输入。
  // 为什么必须全选：登录失败时账号名是**故意留在框里**的（用户只要重敲密码），
  // 直接 insertText 会变成"小李老板"这种拼起来的东西，报出来的错会指向错的地方。
  const type = async (selector, text) => {
    await clickSel(selector);
    await send("Input.dispatchKeyEvent", {
      type: "keyDown", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65,
      commands: ["selectAll"],
    });
    await send("Input.dispatchKeyEvent", {
      type: "keyUp", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65,
    });
    await send("Input.insertText", { text });
    await sleep(60);
  };
  // 表单现场（提交没成功时用得上）：每个框的值长度 + 就地提示的原话 + 哪个栏在显示
  const formDump = () => evalJs(`(() => ({
    fields: ["reg-name", "reg-display", "reg-pwd", "reg-pwd2", "reg-captcha"].map((id) => {
      const el = document.getElementById(id);
      const msg = document.getElementById(id + "-msg");
      return id + "=" + (el ? el.value.length : "无") + (msg && !msg.hidden ? " ！" + msg.textContent : "");
    }),
    registerHidden: document.getElementById("register-form").hidden,
    pendingHidden: document.getElementById("register-pending").hidden,
    gateHidden: document.getElementById("login-gate").hidden,
    toast: document.getElementById("toast").textContent,
  }))()`);
  const visible = (selector) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return el.hidden === false && r.width > 0 && r.height > 0;
  })()`);
  const outer = (selector) => evalJs(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    return el ? el.innerText.replace(/\\s+/g, " ").trim() : "";
  })()`);
  // 从**页面上那张图**里读出那 4 个字符 —— 等于用户"看图填字"：
  // 图是 SVG，在 <img> 的 data: URL 里，每个字符一个独立 <text> 元素。
  // 先点一下图换一张（用户看不清时也是这么干），再把新图上的字填进去。
  // 传进来的多半是**裸 id**（reg-captcha-btn）：这里统一补上 # —— 少了这个，
  // querySelector("reg-captcha-btn") 会被当成"找一个叫这名字的标签"，
  // 返回 null，报出来的却是"找不到元素"，很容易查错方向。
  const asSelector = (value) => (value.startsWith("#") ? value : `#${value}`);
  const captchaFor = async (imgId, btnId, inputId) => {
    await clickSel(asSelector(btnId)).catch(async (err) => {
      // 找不到就先把现场 dump 出来：是哪一栏在显示、那几个 id 到底在不在
      const dump = await evalJs(`(() => ({
        hash: location.hash,
        registerHidden: document.getElementById("register-form").hidden,
        pendingHidden: document.getElementById("register-pending").hidden,
        paneHidden: document.getElementById("guest-pane").hidden,
        loginHidden: document.getElementById("login-form").hidden,
        regIds: [...document.querySelectorAll("[id^=reg-]")].map((e) => e.id),
        loginIds: [...document.querySelectorAll("[id^=login-]")].map((e) => e.id),
      }))()`);
      throw new Error(`${err.message}
现场：${JSON.stringify(dump)}`);
    });
    await sleep(400);
    const code = await evalJs(`(() => {
      const src = document.getElementById(${JSON.stringify(imgId.replace(/^#/, ""))}).src;
      const svg = decodeURIComponent(src.split(",").slice(1).join(","));
      return [...svg.matchAll(/>([0-9A-Z])<\\/text>/g)].map((m) => m[1]).join("");
    })()`);
    await type(asSelector(inputId), code);
    return code;
  };

  await send("Runtime.enable");
  await send("Page.enable");

  let domReady = false;
  for (let i = 0; i < 150; i++) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("guard-modal"))
      && Boolean(document.getElementById("guest-bar"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页 / 弹窗 / 提示条的 DOM");

  // ★ 数真网络请求：把 fetch 包一层计数（api.js 每次请求都现取 window.fetch，包得住）
  await evalJs(`(() => {
    window.__sraFetches = 0;
    const real = window.fetch;
    window.fetch = (...args) => { window.__sraFetches += 1; return real(...args); };
    return true;
  })()`);
  const fetchCount = () => evalJs("window.__sraFetches");

  console.log("\n【1】以游客身份进来");
  await clickSel("#ltab-guest");
  await clickSel("#btn-guest");
  await sleep(700);
  check(await evalJs("Boolean(window.Session) && Session.loggedIn() === true"), "游客进来了");
  check(await evalJs("Session.isGuest() === true && Session.isAdmin() === false"),
    "身份是游客（不是管理员）");
  check(await visible("#guest-bar"), "顶部出现「游客模式」提示条");
  const barText = await outer("#guest-bar-text");
  check(barText.includes("游客模式") && barText.includes("部分功能受限"), "提示条上的话", `「${barText}」`);
  const barBox = await evalJs(`(() => {
    const r = document.getElementById("guest-bar").getBoundingClientRect();
    return { h: r.height, vh: window.innerHeight };
  })()`);
  check(barBox.h > 0 && barBox.h < 60, "提示条是一条细条（不是盖住整页的那层）",
    `高 ${Math.round(barBox.h)}px / 视口 ${barBox.vh}px`);
  check(await evalJs("document.getElementById('login-gate').hidden === true"), "已经进了主界面");
  await shot("guest-01-entry.png");

  console.log("\n【2】⚠ 记号：只出现在受限入口上");
  const marks = await evalJs(`(() => {
    const all = [...document.querySelectorAll(".guard-ico")];
    const guarded = [...document.querySelectorAll("[data-guard]")];
    return {
      marks: all.length, guarded: guarded.length,
      everyMarkGuarded: all.every((m) => m.closest("[data-guard]") !== null),
      everyGuardedMarked: guarded.every((g) => g.querySelector(".guard-ico") !== null),
      stray: all.filter((m) => m.closest("[data-guard]") === null).length,
      ids: guarded.map((g) => g.id),
    };
  })()`);
  // 受限入口分两种：index.html 里**静态**标好的 9 个（有 id），
  // 以及 app.js 画表格时**动态**挂上的（导出按钮之类，没有 id）。
  const STATIC_GUARDED = ["menu-item-import", "btn-ds-inspect", "btn-ds-import", "btn-upload",
                          "btn-doc-upload", "btn-create-task", "btn-run-task", "btn-download",
                          "btn-accounts-locked"];
  const staticMarked = marks.ids.filter((id) => id);
  check(STATIC_GUARDED.every((id) => staticMarked.includes(id)),
    "9 个静态受限入口都在", `实际 ${staticMarked.join(", ")}`);
  check(marks.guarded >= STATIC_GUARDED.length,
    "受限入口不少于 9 个（表格里的导出按钮是动态挂上的）",
    `${marks.guarded} 个（静态 ${staticMarked.length} + 动态 ${marks.guarded - staticMarked.length}）`);
  check(marks.marks === marks.guarded, "每个受限入口上都有 ⚠（不多不少）", `记号 ${marks.marks} 个`);
  check(marks.everyMarkGuarded && marks.everyGuardedMarked, "记号与受限入口一一对应");
  check(marks.stray === 0, "不受限的地方一个 ⚠ 都没有（不许满屏）");
  for (const selector of ["#nl-ask", "#nav-weekly", "#btn-user", "#nav-customers"]) {
    check(await evalJs(`document.querySelector("${selector}").querySelector(".guard-ico") === null`),
      `${selector} 上没有 ⚠（它不受限）`);
  }

  console.log("\n【3】★ 点受限入口：弹窗 + 一个请求都不发（不是只弹窗）");
  await clickSel("#nav-customers");
  await sleep(2500);                       // 这一页要先算一次聚合，等它画完
  const beforeExport = await fetchCount();
  await clickText("#tbl-customers", "导出 Excel");
  check(await visible("#guard-modal"), "点「导出 Excel」→ 弹出「需要您先登录…」");
  const title = await outer("#guard-title");
  check(title.includes("需要您先登录才能使用完整服务"), "弹窗那句就是用户要的", `「${title}」`);
  const why = await outer("#guard-text");
  check(why.includes("导出报表"), "弹窗说清了是哪一项受限", `「${why}」`);
  const afterExport = await fetchCount();
  check(afterExport === beforeExport, "★ 前后网络请求次数一次都没涨（动作真的没发生）",
    `${beforeExport} → ${afterExport}`);
  check(await evalJs("Boolean(document.getElementById('guard-login'))"), "弹窗上有「去登录」按钮");
  await shot("guest-02-blocked-export.png");

  await clickSel("#guard-cancel");
  await sleep(200);
  check((await visible("#guard-modal")) === false, "点「先不用」→ 弹窗收起");
  await clickSel("#btn-menu");
  await sleep(200);
  const beforeImport = await fetchCount();
  await clickSel("#menu-item-import");
  check(await visible("#guard-modal"), "点「导入数据」→ 也是弹窗拦下");
  const afterImport = await fetchCount();
  check(afterImport === beforeImport, "★ 导入这条前后请求次数也没涨", `${beforeImport} → ${afterImport}`);
  check((await evalJs("location.hash")) !== "#/data", "也没跳到「数据管理」页（动作就地打住）",
    `hash=${await evalJs("location.hash")}`);
  // 这一条不关弹窗：紧接着就用它验「去登录」——弹窗是从**导入**这条路弹出来的，
  // 而两条路弹出的是同一个弹窗、同一个按钮（只有一个弹窗，不是每个功能一个）。
  check(await evalJs('Session.guard("chat") === true && Session.guard("read_table") === true'),
    "提问 / 看表格这两类动作不受限（guard 返回 true）");

  // ③ 系统设置里的「账号管理」：游客点它也要弹窗、也要一次请求都不发
  await evalJs("document.getElementById('guard-modal').hidden = true");
  await clickSel("#nav-settings");
  await sleep(900);
  check(await visible("#set-accounts-locked"), "游客在系统设置里看到「需要管理员身份」那块");
  check((await visible("#set-accounts-card")) === false, "游客看不到真正的账号列表那块");
  const beforeAccounts = await fetchCount();
  await clickSel("#btn-accounts-locked");
  check(await visible("#guard-modal"), "点「账号管理」→ 弹窗拦下（不是「有个记号却点不动」）");
  const accountWhy = await outer("#guard-text");
  check(accountWhy.includes("账号管理"), "弹窗说清是账号管理受限", `「${accountWhy}」`);
  const afterAccounts = await fetchCount();
  check(afterAccounts === beforeAccounts, "★ 账号管理这条前后请求次数也没涨",
    `${beforeAccounts} → ${afterAccounts}`);
  await shot("guest-02b-blocked-accounts.png");
  await clickSel("#guard-cancel");
  await sleep(200);

  console.log("\n【4】去登录 → 注册第一个账号（自动是管理员）");
  // 弹窗刚才被「先不用」关掉了：再点一次受限入口把它叫出来，验「去登录」
  await clickSel("#nav-settings");
  await sleep(600);
  await clickSel("#btn-accounts-locked");
  check(await visible("#guard-modal"), "再点一次受限入口 → 弹窗又出来了");
  await clickSel("#guard-login");
  await sleep(300);
  check(await evalJs("Session.loggedIn() === false"), "点「去登录」→ 退出游客");
  check(await visible("#login-gate"), "回到了登录页");
  check(await evalJs('document.getElementById("ltab-account").classList.contains("is-on") '
    + '&& !document.getElementById("login-form").hidden'), "落在「账号登录」那一栏");
  check(await evalJs("document.getElementById('guest-bar').hidden === true"), "游客提示条跟着收走");

  const FIRST = "老板";
  const SECOND = "小李";
  const PLAIN = "Sup3r-mi-2026";
  await clickSel("#ltab-register");
  await type("#reg-name", FIRST);
  await type("#reg-pwd", PLAIN);
  await type("#reg-pwd2", PLAIN);
  const code1 = await captchaFor("reg-captcha-img", "reg-captcha-btn", "reg-captcha");
  check(code1.length === 4, "从图里读到了那 4 个字符（等于用户看图填字）", `「${code1}」`);
  await clickSel("#btn-register");
  await sleep(1600);
  if (!(await evalJs("Boolean(window.Session) && Session.loggedIn() === true"))) {
    console.log("     表单现场：" + JSON.stringify(await formDump()));
  }
  check(await evalJs("Boolean(window.Session) && Session.loggedIn() === true"),
    "第一个账号：注册完直接进来了");
  check(await evalJs("Session.isAdmin() === true"), "而且身份是管理员");
  check((await outer("#user-name")) === FIRST, "顶栏显示的是刚注册的账号", `「${await outer("#user-name")}」`);
  const marksAfterLogin = await evalJs("document.querySelectorAll('.guard-ico').length");
  check(marksAfterLogin === 0, "登录之后所有 ⚠ 记号都摘掉了", `还剩 ${marksAfterLogin} 个`);
  check(await evalJs("document.getElementById('guest-bar').hidden === true"),
    "不是游客了，提示条不显示");
  await shot("guest-03-admin-in.png");

  console.log("\n【5】★ 管理员在哪审批：系统设置 → 账号管理");
  await clickSel("#nav-settings");
  await sleep(1200);
  check(await visible("#set-accounts-card"), "「系统设置」里出现了「账号管理」卡片");
  check((await visible("#set-accounts-locked")) === false,
    "管理员看到的是管账号那块（不是「需要管理员身份」）");
  check((await outer("#set-pending-count")) === "0", "现在还没有等待批准的账号",
    `pending=${await outer("#set-pending-count")}`);

  console.log("\n【6】第二个人注册 → 等批准 → 管理员批准 → 他能登了");
  await clickSel("#btn-user");
  await sleep(200);
  await clickSel("#um-logout");
  await sleep(500);
  await clickSel("#ltab-register");
  await type("#reg-name", SECOND);
  await type("#reg-pwd", PLAIN);
  await type("#reg-pwd2", PLAIN);
  await captchaFor("reg-captcha-img", "reg-captcha-btn", "reg-captcha");
  await clickSel("#btn-register");
  await sleep(1600);
  if (!(await evalJs("Session.loggedIn() === false"))) {
    console.log("     表单现场：" + JSON.stringify(await formDump()));
  }
  check(await evalJs("Session.loggedIn() === false"), "★ 注册成功不再直接进系统");
  check(await visible("#register-pending"), "就地显示「注册成功，等待管理员批准」");
  const pendingText = await outer("#register-pending");
  check(pendingText.includes("注册成功，等待管理员批准"), "结果卡上的话", `「${pendingText.slice(0, 40)}…」`);
  await shot("guest-04-register-pending.png");

  await clickSel("#btn-reg-pending-back");
  await sleep(300);
  await type("#login-name", SECOND);
  await type("#login-pwd", PLAIN);
  await captchaFor("login-captcha-img", "login-captcha-btn", "login-captcha");
  await clickSel("#btn-login");
  await sleep(1600);
  check(await evalJs("Session.loggedIn() === false"), "待批准的账号登不进来");
  const loginMsg = await outer("#login-pwd-msg");
  check(loginMsg.includes("等待管理员批准"),
    "★ 说的是「正在等待管理员批准」（不是「账号或密码不对」）", `「${loginMsg}」`);

  await evalJs("document.getElementById('login-pwd').value = ''");
  await type("#login-name", FIRST);
  await type("#login-pwd", PLAIN);
  await captchaFor("login-captcha-img", "login-captcha-btn", "login-captcha");
  await clickSel("#btn-login");
  await sleep(1800);
  if (!(await evalJs("Session.isAdmin() === true"))) {
    console.log("     登录现场：" + JSON.stringify(await formDump()));
  }
  check(await evalJs("Session.isAdmin() === true"), "管理员登录成功");
  await clickSel("#nav-settings");
  await sleep(1400);
  check((await outer("#set-pending-count")) === "1",
    "待批准列表里有 1 个账号", `pending=${await outer("#set-pending-count")}`);
  const pendingRow = await outer("#set-pending-body");
  check(pendingRow.includes(SECOND), "待批准列表里能看到是谁在申请", `「${pendingRow}」`);
  check(pendingRow.includes("批准") && pendingRow.includes("拒绝"), "每一行都有「批准」「拒绝」两个动作");
  await shot("guest-05-admin-pending.png");
  await clickText("#set-pending-body", "批准");
  await sleep(1600);
  const approvedRow = await outer("#set-approved-body");
  check(approvedRow.includes(SECOND), "批准之后他出现在「已批准的账号」里", `「${approvedRow}」`);
  check((await outer("#set-pending-count")) === "0", "待批准列表空了");
  check(approvedRow.includes("停用") && approvedRow.includes("删除"),
    "已批准那一行有「停用」「删除」", `「${approvedRow}」`);
  await shot("guest-06-admin-approved.png");

  console.log("\n【7】刚被批准的那个人，现在真能登了");
  await clickSel("#btn-user");
  await sleep(200);
  await clickSel("#um-logout");
  await sleep(500);
  await type("#login-name", SECOND);
  await type("#login-pwd", PLAIN);
  await captchaFor("login-captcha-img", "login-captcha-btn", "login-captcha");
  await clickSel("#btn-login");
  await sleep(1800);
  check(await evalJs("Boolean(window.Session) && Session.loggedIn() === true"), "★ 批准之后真的能登进来了");
  check(await evalJs("Session.isAdmin() === false"), "他是普通账号（不是管理员）");
  await clickSel("#nav-settings");
  await sleep(1200);
  check((await visible("#set-accounts-card")) === false,
    "普通账号看不到「账号管理」那张卡（里面没有账号名与审批按钮）");
  check(await visible("#set-accounts-locked"), "他看到的是「需要管理员身份」那块");
  await shot("guest-07-member-settings.png");

  console.log("\n【8】全程没有 JS 报错");
  check(consoleErrors.length === 0, "控制台没有报错", consoleErrors.slice(0, 3).join(" | "));
} finally {
  try { ws?.close(); } catch { /* 关不掉也无所谓 */ }
  try { browser.kill(); } catch { /* 已经退了 */ }
}

console.log(`\n共 ${passed + failed} 项：通过 ${passed}，未通过 ${failed}`);
console.log(`截图目录：${SHOT_DIR}`);
process.exit(failed === 0 ? 0 : 1);
