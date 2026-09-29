// fr005_cover_cdp.mjs · FR-005「封面去营销化 + 工作台视觉打磨」的真浏览器实测（真 Edge，零依赖）
//
//   node scripts/fr005_cover_cdp.mjs http://127.0.0.1:8535
//
// 跑之前先起一个真服务（沙箱目录只是别把本次操作写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_run/state SRA_DOC_DIR=outputs/_cdp_run/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_run/uploads SRA_OUTPUT_DIR=outputs/_cdp_run/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8535
// （本脚本自己会等 /api/health + /api/chat/capabilities 就绪，那 100 多秒的数据预热不用手动等。）
//
// 为什么要有这一条：本 TASK 的验收标准全是**看着对不对**的事，pytest 只能扫源码。
// 这条用真浏览器量真几何、读真文字、真点按钮：
//   E1 封面上 slogan / 特性胶囊 / 统计数字三样**一个都不出现**（连 "541,909" 这种数字也不出现）
//   E2 信息层级：品牌 → 产品名 + 定位 → 表单 → 辅助入口（用真实 y 坐标排序，不看源码顺序）
//   E4/E5 桌面 1600 / 1280 与窄屏 1024 / 375 都不破版（无横向溢出、控件不被压扁）
//   E11 功能清单：9 个业务页都打得开；提问框在；对话三段式在；表格页工具条齐全
//   E12 ★ 筛选 / 排序 / 切维度 / 翻页**不触发全页刷新**（页面上埋一个变量，刷新会把它擦掉）
//   E13 界面上不出现技术细节（意图名 / 工具名 / 耗时 / JSON / 后端字段名）
//   E6  注册 / 登录 / 游客登录三个入口都能用（真点、真提交）
//   E7  故意输错密码 → 就地人话错误
//   E9  键盘登录：在密码框里按真回车能提交
// 截图落在 outputs/_cdp_shots/（第二个参数可改目录）。
//
// 边界：E8（loading 反馈）/ E10（忘记密码 4 步）由既有探针验，不在这里重做 ——
//   E8 见 `scripts/stepb_login_cdp.mjs`，E10 见 `scripts/fr001a_reset_cdp.mjs`。

const BASE = process.argv[2] || "http://127.0.0.1:8535";
const PORT = 9337;
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
// 桌面两档 + 窄屏两档（900px 是登录页的分栏断点）
const SIZES = [[1600, 1000], [1280, 800], [1024, 760], [375, 720]];

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
  console.log(`❌ 服务没起来（${BASE}/api/health 一直没返回 200），先起服务再跑本脚本`);
  process.exit(1);
}

// 本脚本会真的注册一个账号 → 每次跑都要干净的一次：只清**沙箱**里的账号表，
// 真实 state/ 永远不碰（与 stepb_login_cdp.mjs 同一条约定）。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const sandboxAccounts = path.join(process.cwd(), "outputs", "_cdp_run", "state", "accounts.json");
  if (fs.existsSync(sandboxAccounts)) {
    fs.rmSync(sandboxAccounts);
    console.log(`已清空沙箱账号表：${sandboxAccounts}`);
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

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_fr005_cdp_"));
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
function step(title) {
  console.log(`\n── ${title} ${"─".repeat(Math.max(2, 56 - title.length))}`);
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
  if (!page) throw new Error("没找到页面 target");

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
    const s = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(s.data, "base64"));
  };
  const viewport = async (w, h) => {
    await send("Emulation.setDeviceMetricsOverride", {
      width: w, height: h, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(400);
  };

  await send("Runtime.enable");
  await send("Page.enable");

  let domReady = false;
  for (let i = 0; i < 150; i++) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("register-form"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页的 DOM");

  // ══════════════════════════════════════════════════════════════════
  // ① 封面：去营销化 + 信息层级 + 各种尺寸不破版（E1 / E2 / E4 / E5 / E13）
  // ══════════════════════════════════════════════════════════════════
  const COVER = `(() => {
    const rect = (sel) => {
      const e = document.querySelector(sel);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), h: Math.round(r.height), w: Math.round(r.width), text: (e.innerText||"").replace(/\\s+/g," ").trim() };
    };
    const gate = document.getElementById("login-gate");
    const brand = document.querySelector(".login-brand");
    return {
      gateHidden: gate.hidden,
      gateText: gate.innerText.replace(/\\s+/g, " ").trim(),
      brandText: brand ? brand.innerText.replace(/\\s+/g, " ").trim() : "",
      headline: !!document.querySelector(".login-headline"),
      caps: !!document.querySelector(".login-caps"),
      facts: !!document.getElementById("login-facts"),
      logo: rect(".login-brand .brand-logo"),
      name: rect(".login-brand .brand-title"),
      tagline: rect(".login-tagline"),
      form: rect("#login-form"),
      submit: rect("#btn-login"),
      foot: rect(".login-foot"),
      docW: document.documentElement.scrollWidth,
      vw: window.innerWidth,
      productName: (document.querySelector(".login-brand .brand-title") || {}).textContent || "",
    };
  })()`;

  for (const [w, h] of SIZES) {
    await viewport(w, h);
    const c = await evalJs(COVER);
    console.log(`\n  ── ${w}x${h}（innerWidth=${c.vw}）`);
    check(!c.gateHidden, "登录页是显示状态");
    check(c.docW <= c.vw + 1, "没有横向溢出（不破版）", `doc=${c.docW} ≤ ${c.vw}`);
    check(c.submit && c.submit.w > 0 && c.submit.h > 0, "登录按钮可见",
      `${c.submit ? c.submit.w + "x" + c.submit.h : "null"}`);
    check(c.submit && c.submit.h <= 56, "登录按钮没被压成竖排", `高 ${c.submit ? c.submit.h : "-"}`);
    // E1：三样营销元素一个都不许在（连"占位数字"也不行）
    check(!c.headline && !c.caps && !c.facts, "slogan / 特性胶囊 / 统计卡三样都不在",
      `headline=${c.headline} caps=${c.caps} facts=${c.facts}`);
    check(!/541,?909|4,372/.test(c.gateText), "封面上没有那串统计数字（541,909 / 4,372）");
    check(!/领先|智能|企业级|一句话|自动生成/.test(c.brandText), "左栏没有营销话术",
      `左栏文字「${c.brandText}」`);
    // E2：层级顺序（用真实 y 坐标，不看源码顺序）
    check(c.logo && c.name && c.tagline && c.form && c.foot,
      "品牌 / 产品名 / 定位 / 表单 / 辅助入口五件都在");
    if (c.logo && c.name && c.tagline && c.form && c.foot) {
      check(c.logo.y <= c.name.y && c.name.y < c.tagline.y,
        "品牌 → 产品名 → 一句定位（同一个品牌区里从上到下）",
        `logo.y=${c.logo.y} name.y=${c.name.y} tagline.y=${c.tagline.y}`);
      // 宽屏是左右分栏（品牌区在左、表单在右），窄屏才上下堆叠 —— 两种都算"层级在先"
      const brandFirst = c.form.x > c.tagline.x || c.tagline.y < c.form.y;
      check(brandFirst, "品牌区在表单**之前**（宽屏在左、窄屏在上），不是把表单塞进品牌区",
        `form.x=${c.form.x} tagline.x=${c.tagline.x} / form.y=${c.form.y} tagline.y=${c.tagline.y}`);
      check(c.foot.x >= c.form.x - 1 && c.foot.y > c.form.y + c.form.h - 1,
        "辅助入口在表单**正下方**（同一栏，且在按钮之下）",
        `foot=(${c.foot.x},${c.foot.y}) form 底=${c.form.y + c.form.h}`);
    }
    check(c.productName.includes("销售分析平台"), "产品名是「销售分析平台」", `「${c.productName}」`);
    // E13：界面上不出现技术细节
    check(!/意图|intent|tool|耗时|JSON|api\//i.test(c.gateText), "封面上没有技术细节字样");
  }
  await viewport(1600, 1000);
  await shot("fr005_cover_1600.png");
  await viewport(375, 720);
  await shot("fr005_cover_375.png");
  await viewport(1600, 1000);

  // ══════════════════════════════════════════════════════════════════
  // ② 忘记密码入口还在（完整四步由 fr001a_reset_cdp.mjs 验）
  // ══════════════════════════════════════════════════════════════════
  step("封面辅助入口：忘记密码 / 注册账号 / 帮助 · 隐私都在且点得动");
  const aux = await evalJs(`(() => {
    const foot = document.querySelector(".login-foot");
    const ids = ["link-forgot", "link-register", "link-help", "link-privacy"];
    return {
      footText: foot ? foot.innerText.replace(/\\s+/g, " ").trim() : "",
      present: ids.map((id) => Boolean(document.getElementById(id))),
    };
  })()`);
  check(aux.present.every(Boolean), "四个辅助入口的 id 都在页面上", JSON.stringify(aux.present));
  check(/忘记密码/.test(aux.footText) || aux.present[0], "「忘记密码」还在（第四步流程的入口）");
  check(/注册账号/.test(aux.footText) && /帮助/.test(aux.footText) && /隐私/.test(aux.footText),
    "底部有「注册账号 · 帮助 · 隐私」", `「${aux.footText}」`);

  // ══════════════════════════════════════════════════════════════════
  // ③ E6：三个入口都能用（注册 → 登录 → 游客）；E7 错密码；E9 回车提交
  // ══════════════════════════════════════════════════════════════════
  step("E6/E7/E8/E9：注册 → 错密码 → 回车登录 → 游客登录");
  const setField = `(id, value) => {
    const el = document.getElementById(id);
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }`;
  const readCaptchaCode = (imgId) => evalJs(`(() => {
    const img = document.getElementById(${JSON.stringify(imgId)});
    if (!img || !img.src) return "";
    const svg = decodeURIComponent((img.src.split(",")[1] || ""));
    const found = [];
    svg.split("</text>").forEach((part) => {
      const at = part.lastIndexOf(">");
      if (at >= 0 && part.length - at === 2) found.push(part.slice(at + 1));
    });
    return found.join("");
  })()`);
  const fillCaptcha = async (imgId, inputId) => {
    const code = await readCaptchaCode(imgId);
    await evalJs(`(${setField})(${JSON.stringify(inputId)}, ${JSON.stringify(code)})`);
    return code;
  };

  // 切到注册栏，真提交一份注册（本机第一个账号 = 管理员，注册完直接进系统）
  await evalJs(`document.getElementById("ltab-register").click()`);
  await sleep(300);
  await evalJs(`(${setField})("reg-name", "fr005")`);
  await evalJs(`(${setField})("reg-display", "验收员")`);
  await evalJs(`(${setField})("reg-pwd", "fr005pass123")`);
  await evalJs(`(${setField})("reg-pwd2", "fr005pass123")`);
  await fillCaptcha("reg-captcha-img", "reg-captcha");
  await evalJs(`document.getElementById("reg-name").dispatchEvent(new Event("blur", { bubbles: true }))`);
  await sleep(900);
  await evalJs(`document.getElementById("btn-register").click()`);
  await sleep(2500);
  let state = await evalJs(`(() => {
    const gate = document.getElementById("login-gate");
    return { gateHidden: gate.hidden,
             msg: (document.querySelector("#register-form .field-msg:not([hidden])") || {}).textContent || "",
             who: (document.getElementById("user-name") || {}).textContent || "",
             pending: !document.getElementById("register-pending").hidden };
  })()`);
  const registered = state.gateHidden;
  check(registered || state.pending, "注册入口真的能用（要么直接进系统，要么如实说等待批准）",
    `进系统=${registered} 待批准=${state.pending} 提示=「${state.msg}」`);
  if (registered) check(state.who.includes("验收员"), "注册后顶栏显示显示名「验收员」", `「${state.who}」`);

  // 退出 → 用**错密码**试一次（E7），再在密码框里按**真回车**（E9）
  if (registered) {
    await evalJs(`(() => { document.getElementById("btn-user").click(); return true; })()`);
    await sleep(300);
    await evalJs(`document.getElementById("um-logout").click()`);
    await sleep(1200);
    await evalJs(`document.getElementById("ltab-account").click()`);
    await sleep(300);
    await evalJs(`(${setField})("login-name", "fr005")`);
    await evalJs(`(${setField})("login-pwd", "wrong-pass-xxx")`);
    await fillCaptcha("login-captcha-img", "login-captcha");
    await evalJs(`document.getElementById("btn-login").click()`);
    await sleep(1800);
    let wrong = await evalJs(`(() => {
      const el = document.getElementById("login-pwd-msg");
      return { msg: el.hidden ? "" : el.textContent.trim(),
               gateHidden: document.getElementById("login-gate").hidden };
    })()`);
    check(!wrong.gateHidden && wrong.msg.length > 0, "E7 故意输错密码 → 就地人话提示，且不放行",
      `「${wrong.msg}」`);

    // E8/E9：loading 反馈 + 真回车提交
    // ★ 回车必须走 **CDP 原生按键**（Input.dispatchKeyEvent）：JS 里 dispatchEvent 造的
    //   KeyboardEvent 是"合成事件"，浏览器**不会**替它走隐式提交 —— 那样验到的是假的。
    await evalJs(`(${setField})("login-pwd", "fr005pass123")`);
    await fillCaptcha("login-captcha-img", "login-captcha");
    const focused = await evalJs(`(() => {
      const el = document.getElementById("login-pwd");
      el.focus();
      return document.activeElement === el;
    })()`);
    check(focused, "密码框拿到键盘焦点（接下来发的回车才是真按键）");
    await send("Input.dispatchKeyEvent", {
      type: "keyDown", key: "Enter", code: "Enter", text: "\r",
      windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
    });
    await send("Input.dispatchKeyEvent", {
      type: "keyUp", key: "Enter", code: "Enter",
      windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
    });
    // loading 态存在的时间 = 一次网络往返（可能只有几十毫秒），所以用**高频采样**去看它，
    // 而不是固定睡 300ms 再瞄一眼（那样测到的是"登录完了没"，不是"过程里有没有反馈"）。
    let everBusy = false;
    for (let i = 0; i < 80; i++) {
      const b = await evalJs(`(() => {
        const btn = document.getElementById("btn-login");
        const spin = document.getElementById("login-spinner");
        return { disabled: btn.disabled, spinner: !spin.hidden };
      })()`);
      if (b.disabled && b.spinner) { everBusy = true; break; }
      await sleep(20);
    }
    check(everBusy, "E8 提交期间按钮真的进过 loading 态（禁用 + 转圈，采样 80 次捕获）",
      everBusy ? "捕获到" : "一次都没观测到（要么太快，要么没有 loading 反馈）");
    await sleep(2500);
    let after = await evalJs(`(() => {
      const msg = (id) => { const e = document.getElementById(id); return e && !e.hidden ? e.textContent.trim() : ""; };
      return { gateHidden: document.getElementById("login-gate").hidden,
               who: (document.getElementById("user-name") || {}).textContent || "",
               nameMsg: msg("login-name-msg"), pwdMsg: msg("login-pwd-msg"),
               capMsg: msg("login-captcha-msg") };
    })()`);
    check(after.gateHidden, "E9 在密码框里按回车就能提交（登录成功进了系统）",
      `顶栏=「${after.who}」 提示=「${after.nameMsg}${after.pwdMsg}${after.capMsg}」`);
  }

  // ══════════════════════════════════════════════════════════════════
  // ④ 工作台：功能清单逐项 + 不触发全页刷新（E11 / E12 / E13）
  // ══════════════════════════════════════════════════════════════════
  step("E11 功能清单：9 个业务页 / 提问框 / 对话三段式 / 快捷入口");
  // E12 的观测点：页面上埋一个变量。任何形式的整页刷新（reload / 跳转）都会把它擦掉，
  // 而"原地更新"（重新请求 + 重画 DOM）不会。比看截图可靠，也不用猜。
  await evalJs(`window.__fr005Mark = "alive"; true`);
  const PAGES = [
    ["overview", "首页"], ["sales", "销售分析"], ["customers", "客户分析"], ["products", "产品分析"],
    ["anomaly", "异常发现"], ["weekly", "周报中心"], ["raw", "原始数据"], ["data", "数据管理"],
    ["settings", "系统设置"],
  ];
  const dash = await evalJs(`(() => ({
    heroInput: Boolean(document.getElementById("hero-nl-input")),
    heroBtn: Boolean(document.getElementById("hero-nl-btn")),
    topInput: Boolean(document.getElementById("nl-input")),
    steps: ["chat-step-question", "chat-step-facts", "chat-step-answer"]
      .map((id) => Boolean(document.getElementById(id))),
    chips: ["chip-summary", "chip-trend", "chip-products", "chip-customers", "chip-returns"]
      .map((id) => Boolean(document.getElementById(id))),
    navCount: document.querySelectorAll(".sidebar .nav-item").length,
  }))()`);
  check(dash.heroInput && dash.topInput, "提问框在（顶栏 + 首页各一个）");
  check(dash.steps.every(Boolean), "对话三段式还在（① 你问的问题 ② 算出来的事实 ③ 回答）",
    JSON.stringify(dash.steps));
  check(dash.chips.every(Boolean), "5 个快捷入口 chip 都在", JSON.stringify(dash.chips));
  check(dash.navCount === 9, "导航有 9 个业务页", `实际 ${dash.navCount}`);

  for (const [route, name] of PAGES) {
    await evalJs(`document.getElementById("nav-${route}").click()`);
    await sleep(500);
    const open = await evalJs(`(() => {
      const page = document.getElementById("page-${route}");
      return { open: page && !page.hidden,
               active: (document.getElementById("nav-${route}") || {}).className || "" };
    })()`);
    check(open.open && open.active.includes("active"), `业务页「${name}」打得开`,
      `open=${open.open}`);
    // E12 的一半：路由切换是原地换页，不许整页重载（重载会擦掉下面那个变量）
    const mark = await evalJs(`window.__fr005Mark`);
    check(mark === "alive", `切到「${name}」没有触发全页刷新`, `mark=${mark}`);
  }

  // 真提问一次：走 chip → 对话三段式出结果（观察"不刷新"）
  step("真提一次问：chip → 三段式出数（期间不刷新页面）");
  await evalJs(`document.getElementById("nav-overview").click()`);
  await sleep(400);
  await evalJs(`window.__fr005NavCount = performance.getEntriesByType("navigation").length`);
  await evalJs(`document.getElementById("chip-summary").click()`);
  let answered = false;
  for (let i = 0; i < 150; i++) {
    const s = await evalJs(`(() => {
      const body = document.getElementById("chat-body");
      const facts = document.getElementById("chat-facts");
      return { shown: body && !body.hidden,
               factsLen: facts ? facts.innerText.trim().length : 0 };
    })()`);
    if (s.shown && s.factsLen > 30) { answered = true; break; }
    await sleep(500);
  }
  check(answered, "点「销售汇总」快捷入口 → 对话三段式真的算出数字（② 算出来的事实有内容）");
  const navCount = await evalJs(`performance.getEntriesByType("navigation").length`);
  check(navCount === 1, "整个提问过程**没有**整页导航（navigation 条目始终只有 1 条）", `实际 ${navCount}`);
  check(await evalJs(`window.__fr005Mark`) === "alive", "提问不会把页面重载掉");
  await shot("fr005_workbench_after_chat.png");

  // 表格页：切维度 / 切指标 / 点表头排序 / 翻页 —— 全部原地更新（E12）
  step("E12 表格页：切维度 / 切指标 / 排序 / 翻页都不触发全页刷新");
  await evalJs(`document.getElementById("nav-sales").click()`);
  await sleep(1500);
  const salesUi = await evalJs(`(() => {
    const find = (label) => {
      const wraps = [...document.querySelectorAll("#tbl-sales .field-inline")];
      const hit = wraps.find((w) => (w.querySelector(".field-inline-label") || {}).textContent === label);
      return hit ? hit.querySelector("select") : null;
    };
    const dim = find("维度");
    const metric = find("指标");
    return {
      dimOptions: dim ? [...dim.options].map((o) => o.textContent) : null,
      dimValue: dim ? dim.value : "",
      metricOptions: metric ? [...metric.options].map((o) => o.textContent) : null,
      rows: document.querySelectorAll("#tbl-sales tbody tr").length,
      sortableTh: document.querySelectorAll("#tbl-sales th.sortable").length,
      pagerButtons: [...document.querySelectorAll("#tbl-sales button")].map((b) => b.textContent.trim()),
      tableText: (document.querySelector("#tbl-sales") || {}).innerText || "",
    };
  })()`);
  check(salesUi.rows > 0, "销售表真的有数据行", `${salesUi.rows} 行`);
  check(salesUi.dimOptions && salesUi.dimOptions.length >= 2, "维度下拉在",
    JSON.stringify(salesUi.dimOptions));
  check(salesUi.dimOptions && !salesUi.dimOptions.some((t) => t.includes("国家")),
    "★ 维度里**没有**「按国家」（内置数据源没有地区字段 → 地理维度整块不出现）",
    JSON.stringify(salesUi.dimOptions));
  check(salesUi.metricOptions && salesUi.metricOptions.length === 4, "指标下拉有四档",
    JSON.stringify(salesUi.metricOptions));
  check(salesUi.sortableTh > 0, "表头可排序（点一下重新向后端要数据）", `${salesUi.sortableTh} 个`);
  check(!/国家|United Kingdom|France/.test(salesUi.tableText), "销售表里不出现「国家」字样/国名");

  // 切指标 → 原地更新
  await evalJs(`(() => {
    const wraps = [...document.querySelectorAll("#tbl-sales .field-inline")];
    const hit = wraps.find((w) => (w.querySelector(".field-inline-label") || {}).textContent === "指标");
    const sel = hit.querySelector("select");
    sel.value = "order_count";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
  await sleep(1500);
  check(await evalJs(`window.__fr005Mark`) === "alive", "切指标是原地更新（没有全页刷新）");
  const metricLabel = await evalJs(`(() => {
    const note = document.getElementById("tbl-sales-status");
    return note ? note.innerText.trim() : "";
  })()`);
  console.log(`      （表格状态行：${metricLabel}）`);

  // 点表头排序 → 原地更新
  await evalJs(`document.querySelector("#tbl-sales th.sortable").click()`);
  await sleep(1500);
  check(await evalJs(`window.__fr005Mark`) === "alive", "点表头排序是原地更新（没有全页刷新）");
  check(await evalJs(`performance.getEntriesByType("navigation").length`) === 1,
    "排序后整页导航条目仍然只有 1 条");

  // 翻页 → 原地更新（先看有没有下一页按钮）
  const paged = await evalJs(`(() => {
    const btn = [...document.querySelectorAll("#tbl-sales button")]
      .find((b) => b.textContent.trim() === "下一页");
    if (!btn || btn.disabled) return false;
    btn.click();
    return true;
  })()`);
  if (paged) {
    await sleep(1500);
    check(await evalJs(`window.__fr005Mark`) === "alive", "翻页是原地更新（没有全页刷新）");
  } else {
    check(true, "翻页按钮当前不可用（该维度下只有一页）—— 原地更新的证据由切指标 / 排序两条给出");
  }

  // E13：界面上不出现技术细节。
  //   ① 工作台整体：意图键名 / 接口路径 / JSON / 栈信息一个都不许有；
  //   ② 对话面板（AI 链路唯一可能出现"意图名 · 工具名 · 模型耗时"的地方）：这三样也不许有。
  //   （注意「执行耗时 X 秒」是**用户自己那条任务跑了多久**，属业务数字，不在禁用之列 ——
  //     禁的是"这次 LLM 调用花了多少毫秒"这种实现细节。）
  const allText = await evalJs(`document.querySelector(".workspace").innerText.replace(/\\s+/g, " ")`);
  const techHits = ["sales_summary", "sales_trend", "top_products", "sales_compare",
                    "sales_breakdown_by_country", "customer_analysis", "product_analysis",
                    "intent", "Traceback", "stderr", "/api/", "JSON", "sha256", "file_id"]
    .filter((w) => allText.includes(w));
  check(techHits.length === 0, "E13① 工作台文字里没有意图键名 / 接口路径 / JSON / 栈信息",
    techHits.join(","));
  const chatText = await evalJs(`(document.querySelector("#chat-panel") || document.body).innerText.replace(/\\s+/g, " ")`);
  const chatHits = ["意图", "工具", "耗时", "intent", "tool", "JSON"]
    .filter((w) => chatText.includes(w));
  check(chatHits.length === 0, "E13② 对话面板里不出现意图名 / 工具名 / 模型耗时 / JSON",
    chatHits.join(","));
  await shot("fr005_workbench_sales.png");

  // ══════════════════════════════════════════════════════════════════
  // ⑤ 游客登录（E6 的第三个入口）
  // ══════════════════════════════════════════════════════════════════
  step("E6 游客登录");
  await evalJs(`(() => { document.getElementById("btn-user").click(); return true; })()`);
  await sleep(300);
  await evalJs(`document.getElementById("um-logout").click()`);
  await sleep(1500);
  await evalJs(`document.getElementById("ltab-guest").click()`);
  await sleep(300);
  await evalJs(`document.getElementById("btn-guest").click()`);
  await sleep(1500);
  const guest = await evalJs(`(() => ({
    gateHidden: document.getElementById("login-gate").hidden,
    who: (document.getElementById("user-name") || {}).textContent || "",
  }))()`);
  check(guest.gateHidden && /^游客/.test(guest.who), "游客登录能进系统（顶栏是随机游客名）",
    `「${guest.who}」`);

  step("JS 报错");
  check(consoleErrors.length === 0, "整个过程没有 JS 报错", consoleErrors.slice(0, 3).join(" / "));

  console.log(`\n共 ${passed + failed} 项检查：通过 ${passed}，未通过 ${failed}`);
  console.log(`截图目录：${SHOT_DIR}`);
  ws.close();
  browser.kill();
  process.exit(failed === 0 ? 0 : 1);
} catch (err) {
  console.log(`\n❌ 探针自己崩了：${err && err.stack ? err.stack : err}`);
  try { ws?.close(); } catch { /* ignore */ }
  try { browser.kill(); } catch { /* ignore */ }
  process.exit(2);
}
