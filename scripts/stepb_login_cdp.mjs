// stepb_login_cdp.mjs · 登录页「账号」表单 + 铺满视口的 CDP 实测（真 Edge，零依赖）
//
//   node scripts/stepb_login_cdp.mjs http://127.0.0.1:8535
//
// 跑之前先起一个真服务（沙箱目录只是别把本次登录写进 state/）：
//   SRA_STATE_DIR=outputs/_cdp_run/state SRA_DOC_DIR=outputs/_cdp_run/documents \
//   SRA_UPLOAD_DIR=outputs/_cdp_run/uploads SRA_OUTPUT_DIR=outputs/_cdp_run/outputs \
//   .venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8535
// （本脚本自己会等 /api/health 就绪，数据预热那 100 多秒不用手动等）
//
// 为什么要有这一条：用户反馈两点，都是**像素级 / 真交互**的事，静态检查看不出来 ——
//   ① 登录页必须仍**铺满整个视口**（`#login-gate` 的 rect == 视口，left/top = 0）；
//   ② 登录表单要是「账号 + 密码」那种常见形态（账号 / 记住账号 / 登 录 / 忘记密码 / 注册账号）。
// 做法：真 Edge + CDP，量真几何、读真 innerText、真点按钮（不是 dump-dom 猜）。
//
// 量什么：
//   ① 七种窗口尺寸下：登录页 rect == 视口、左上角归零、没有横向滚动条；
//   ② 表单真的在卡片里（输入框 / 按钮可见、按钮单行、不被压扁）；
//   ③ 表单文字：标签「账号」、提示语「手机号 / 邮箱 / 用户名」、上限 40、
//      「记住账号」「登 录」「忘记密码」「注册账号」，且页面上不再有「姓名」；
//   ④ **密码框的眼睛图标**：只有图标没有文字、热区 ≥ 24×24、掩码态=睁眼（标签「显示密码」）/
//      明文态=闭眼加斜线（标签「隐藏密码」）、hover 从弱化色变品牌色（真鼠标事件），两态各留一张图；
//   ⑤ 校验：1 个字符 / 41 个字符都被拦下并就地提示；2 个字符 + 6 位密码放行；
//   ⑥ 真点「注册账号」→ 给人话提示（不是点不动的死按钮）；
//   ⑦ 登录成功后：登录页收起、顶栏显示账号名、身份说明是账号口径；
//   ⑧ 图标型按钮（刷新任务列表 / 刷新执行记录 / 收起正文）：只画图标 + title 与 aria-label 都在；
//   ⑨ 整个过程没有 JS 报错。
// 截图落在 outputs/_cdp_shots/（命令行第二个参数可改目录）：眼睛两态、登录页整屏、图标按钮。

const BASE = process.argv[2] || "http://127.0.0.1:8535";
const PORT = 9334;
const EDGE_CANDIDATES = [
  String.raw`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`,
  String.raw`C:\Program Files\Microsoft\Edge\Application\msedge.exe`,
];
// 宽窗口 / 分栏断点（900px）/ 窄窗口 / 手机竖屏：铺满不许在这些尺寸上露馅
const SIZES = [[1600, 1000], [1440, 900], [1280, 800], [1024, 760], [900, 700], [820, 660], [375, 720]];

const { spawn } = await import("node:child_process");
const os = await import("node:os");
const path = await import("node:path");
const fs = await import("node:fs");

const edge = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.log("❌ 没找到 Edge，无法实测");
  process.exit(1);
}
// 截图落哪儿（命令行第二个参数可改）：默认 outputs/_cdp_shots/ —— 验收产物，不进版本库
const SHOT_DIR = process.argv[3] || path.join(process.cwd(), "outputs", "_cdp_shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 先等服务真起来（登录页的数字要打后端接口；等进程就绪，不猜固定秒数）
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

const udd = fs.mkdtempSync(path.join(os.tmpdir(), "sra_login_cdp_"));
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

  await send("Runtime.enable");
  await send("Page.enable");

  // 每个尺寸量一次登录页的几何（登录页此时还没登录，是本机新 profile → 一定先给登录页）
  const MEASURE = `(() => {
    const gate = document.getElementById("login-gate");
    const r = gate.getBoundingClientRect();
    const round = (n) => Math.round(n);
    const btn = document.getElementById("btn-login");
    const br = btn.getBoundingClientRect();
    const field = document.getElementById("login-name");
    const fr = field.getBoundingClientRect();
    return {
      viewport: [window.innerWidth, window.innerHeight],
      rect: { w: round(r.width), h: round(r.height), left: round(r.left), top: round(r.top) },
      docScrollWidth: document.documentElement.scrollWidth,
      gateVisible: !gate.hidden,
      locked: document.body.classList.contains("is-locked"),
      btn: { w: round(br.width), h: round(br.height), text: (btn.textContent || "").trim() },
      field: { w: round(fr.width), h: round(fr.height) },
      innerWidth: window.innerWidth,
    };
  })()`;

  console.log(`\n=== ① 登录页铺满视口：${SIZES.length} 种窗口尺寸实测（真 Edge + CDP）===`);
  for (const [width, height] of SIZES) {
    await send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(500);
    const m = await evalJs(MEASURE);
    const [vw, vh] = m.viewport;
    console.log(`\n  ── ${width}x${height}（innerWidth=${vw} innerHeight=${vh}）`);
    check(m.gateVisible, "登录页是显示状态（全新用户先看到登录页）");
    check(m.rect.w === vw && m.rect.h === vh,
      "登录页铺满整个视口（宽高 == 视口）",
      `gate ${m.rect.w}x${m.rect.h} / 视口 ${vw}x${vh}`);
    check(m.rect.left === 0 && m.rect.top === 0, "左上角归零（left/top = 0）",
      `left=${m.rect.left} top=${m.rect.top}`);
    check(m.docScrollWidth <= m.innerWidth + 1, "没有横向溢出（scrollWidth ≤ 视口宽）",
      `doc=${m.docScrollWidth} ≤ ${m.innerWidth}`);
    check(m.field.w > 0 && m.field.h > 0, "账号输入框可见", `${m.field.w}x${m.field.h}`);
    check(m.btn.w > 0 && m.btn.h <= 56, "登录按钮可见且单行（没被压成竖排）",
      `${m.btn.w}x${m.btn.h} 文字=「${m.btn.text}」`);
  }

  // ── ② 表单文字（渲染出来的，不是源码）────────────────────────────────
  console.log("\n=== ② 表单文字：是不是「账号」那种形态 ===");
  await send("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await sleep(400);
  const COPY = `(() => {
    const t = (el) => (el ? (el.textContent || "").trim() : null);
    const label = document.querySelector('label[for="login-name"]');
    const field = document.getElementById("login-name");
    const remember = document.querySelector('label.check');
    return {
      label: t(label),
      placeholder: field ? field.placeholder : null,
      maxlength: field ? field.getAttribute("maxlength") : null,
      remember: t(remember),
      submit: t(document.getElementById("btn-login")),
      forgot: t(document.getElementById("link-forgot")),
      register: t(document.getElementById("link-register")),
      body: document.body.innerText,
      // 游客那栏默认是 hidden，innerText 里读不到 —— 单独取它的 textContent
      guestPane: (document.getElementById("guest-pane") || {}).textContent || "",
      guestPaneHidden: document.getElementById("guest-pane").hidden,
      cardVisible: document.querySelector(".login-card").getBoundingClientRect().width > 0,
    };
  })()`;
  const copy = await evalJs(COPY);
  check(copy.label === "账号", "字段标签是「账号」", `「${copy.label}」`);
  check(copy.placeholder === "手机号 / 邮箱 / 用户名", "提示语是「手机号 / 邮箱 / 用户名」", `「${copy.placeholder}」`);
  check(copy.maxlength === "40", "账号上限 40（和校验口径一致）", `maxlength=${copy.maxlength}`);
  check(copy.remember === "记住账号", "勾选项是「记住账号」", `「${copy.remember}」`);
  check(copy.submit === "登 录", "主按钮是「登 录」", `「${copy.submit}」`);
  check(copy.forgot === "忘记密码？", "「忘记密码？」在", `「${copy.forgot}」`);
  check(copy.register === "注册账号", "「注册账号」在", `「${copy.register}」`);
  check(copy.body.includes("姓名") === false, "渲染出来的文字里没有「姓名」");
  check(copy.body.includes("登录后即可开始分析"), "副标题是正式话术「登录后即可开始分析」");
  check(copy.guestPane.includes("不留身份") && copy.guestPaneHidden === true,
    "游客那栏（默认收起）说清「不留身份」、退出后名字会变",
    copy.guestPane.replace(/\s+/g, " ").trim().slice(0, 70));
  check(copy.cardVisible, "表单卡片可见（账号 / 密码两块都在）");

  // ── ②b 密码框的眼睛图标：两态 + 热区 + hover 颜色 + 无障碍标签 ──────────
  console.log("\n=== ②b 密码框的眼睛图标（不写字，两态切换）===");
  const EYE = `(() => {
    const btn = document.getElementById("btn-pwd-eye");
    const r = btn.getBoundingClientRect();
    const open = btn.querySelector(".eye-open"), off = btn.querySelector(".eye-off");
    const show = (el) => { const s = getComputedStyle(el); return s.display !== "none" && s.visibility !== "hidden"; };
    return {
      w: Math.round(r.width), h: Math.round(r.height),
      left: Math.round(r.left), top: Math.round(r.top),
      text: (btn.textContent || "").trim(),
      svgCount: btn.querySelectorAll("svg").length,
      openShown: show(open), offShown: show(off),
      ariaLabel: btn.getAttribute("aria-label"),
      title: btn.getAttribute("title"),
      ariaPressed: btn.getAttribute("aria-pressed"),
      color: getComputedStyle(btn).color,
      inputType: document.getElementById("login-pwd").type,
    };
  })()`;
  // 截一小块（围绕某个元素放大 3 倍），文件名给个能看懂的名字。
  // 先把元素滚进视野，再把视图坐标换算成**页面坐标**（captureScreenshot 的 clip 按整页算，
  // 不换算的话，长页面下半部分的元素会截出一张空白图）。
  const shotClip = async (name, id, pad = 22) => {
    await evalJs(`document.getElementById(${JSON.stringify(id)}).scrollIntoView({ block: "center" })`);
    await sleep(300);
    const box = await evalJs(`(() => {
      const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
      return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height };
    })()`);
    const clip = {
      x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad),
      width: box.w + pad * 2, height: box.h + pad * 2, scale: 3,
    };
    const shot = await send("Page.captureScreenshot", { format: "png", clip });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
    return file;
  };
  const shotPage = async (name) => {
    const shot = await send("Page.captureScreenshot", { format: "png" });
    const file = path.join(SHOT_DIR, name);
    fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
    return file;
  };

  let eye = await evalJs(EYE);
  check(eye.text === "" && eye.svgCount === 2, "按钮里只有图标、没有「显示 / 隐藏」文字",
    `文字=「${eye.text}」 svg=${eye.svgCount} 个`);
  check(eye.w >= 24 && eye.h >= 24, "点击热区 ≥ 24×24（不是小得难点的图标）",
    `${eye.w}x${eye.h}`);
  check(eye.openShown && !eye.offShown && eye.inputType === "password" && eye.ariaLabel === "显示密码",
    "初始态：睁眼 + 密码是掩码的，标签「显示密码」",
    `睁眼=${eye.openShown} 闭眼=${eye.offShown} type=${eye.inputType} label=「${eye.ariaLabel}」`);
  check(eye.title === "显示密码" && eye.ariaPressed === "false", "title 与 aria-pressed 同步",
    `title=「${eye.title}」 pressed=${eye.ariaPressed}`);
  check(eye.color === "rgba(255, 255, 255, 0.35)", "弱化色（--td-font-white-3）", eye.color);
  const hiddenShot = await shotClip("eye-hidden.png", "btn-pwd-eye");
  check(true, "留证：掩码态截图", path.basename(hiddenShot));
  check(fs.existsSync(await shotPage("login-full.png")), "留证：登录页整屏截图", "login-full.png");

  // 真 hover 一下，看颜色是不是切到品牌色
  await send("Input.dispatchMouseEvent", {
    type: "mouseMoved", x: eye.left + eye.w / 2, y: eye.top + eye.h / 2, button: "none",
  });
  await sleep(250);
  const hoverColor = await evalJs(`getComputedStyle(document.getElementById("btn-pwd-eye")).color`);
  check(hoverColor === "rgb(69, 130, 230)", "鼠标悬停变品牌色（--td-brand-color）", hoverColor);
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 2, y: 2, button: "none" });
  await sleep(150);

  await evalJs(`document.getElementById("btn-pwd-eye").click()`);
  await sleep(250);
  eye = await evalJs(EYE);
  check(!eye.openShown && eye.offShown && eye.inputType === "text" && eye.ariaLabel === "隐藏密码",
    "点一下：闭眼（加斜线）+ 明文显示，标签变「隐藏密码」",
    `睁眼=${eye.openShown} 闭眼=${eye.offShown} type=${eye.inputType} label=「${eye.ariaLabel}」`);
  const shownShot = await shotClip("eye-shown.png", "btn-pwd-eye");
  check(true, "留证：明文态截图", path.basename(shownShot));

  await evalJs(`document.getElementById("btn-pwd-eye").click()`);
  await sleep(250);
  eye = await evalJs(EYE);
  check(eye.openShown && !eye.offShown && eye.inputType === "password" && eye.ariaLabel === "显示密码",
    "再点一下：回到掩码 + 睁眼（图标跟着状态走，不是固定一个眼睛）",
    `type=${eye.inputType} label=「${eye.ariaLabel}」`);

  // ── ③ 未开通的入口：真点一下，有没有人话提示 ──────────────────────────
  console.log("\n=== ③ 真点「注册账号」/「忘记密码？」===");
  await evalJs(`document.getElementById("link-register").click()`);
  await sleep(200);
  const regToast = await evalJs(`(() => { const b = document.getElementById("toast"); return b.hidden ? null : b.textContent.trim(); })()`);
  check(Boolean(regToast) && regToast.includes("尚未开通"), "点「注册账号」给人话提示（不是死按钮）", `「${regToast}」`);
  await evalJs(`document.getElementById("link-forgot").click()`);
  await sleep(200);
  const forgotToast = await evalJs(`(() => { const b = document.getElementById("toast"); return b.hidden ? null : b.textContent.trim(); })()`);
  check(Boolean(forgotToast) && forgotToast.includes("尚未开通"), "点「忘记密码？」给人话提示", `「${forgotToast}」`);

  // ── ④ 校验：太短 / 太长拦下，正常放行 ─────────────────────────────────
  console.log("\n=== ④ 形态校验：太短 / 太长 / 正常 ===");
  const typeAndSubmit = `
    (account, password) => {
      const a = document.getElementById("login-name");
      const p = document.getElementById("login-pwd");
      a.value = account; p.value = password;
      a.dispatchEvent(new Event("input", { bubbles: true }));
      p.dispatchEvent(new Event("input", { bubbles: true }));
      document.getElementById("btn-login").click();
      return true;
    }`;
  const readErr = `(() => {
    const a = document.getElementById("login-name-msg"), p = document.getElementById("login-pwd-msg");
    return {
      accountMsg: a.hidden ? "" : a.textContent.trim(),
      pwdMsg: p.hidden ? "" : p.textContent.trim(),
      bad: document.getElementById("login-name").classList.contains("is-bad"),
      gateHidden: document.getElementById("login-gate").hidden,
      who: document.getElementById("user-name").textContent.trim(),
    };
  })()`;

  await evalJs(`(${typeAndSubmit})("唐", "123456")`);
  await sleep(300);
  let after = await evalJs(readErr);
  check(Boolean(after.accountMsg) && after.bad && !after.gateHidden,
    "账号只有 1 个字符：就地标红提示，不放行", `「${after.accountMsg}」`);

  await evalJs(`(${typeAndSubmit})("a".repeat(41), "123456")`);
  await sleep(300);
  after = await evalJs(readErr);
  check(Boolean(after.accountMsg) && !after.gateHidden,
    "账号 41 个字符：拦下并提示", `「${after.accountMsg}」`);

  await evalJs(`(${typeAndSubmit})("13800000000", "123")`);
  await sleep(300);
  after = await evalJs(readErr);
  check(Boolean(after.pwdMsg) && !after.gateHidden,
    "密码 3 位：拦下并提示", `「${after.pwdMsg}」`);

  // ── ⑤ 正常登录：登录页收起、顶栏显示账号 ─────────────────────────────
  console.log("\n=== ⑤ 真登录一次：账号「13800000000」===");
  await evalJs(`(${typeAndSubmit})("13800000000", "123456")`);
  await sleep(3500);                       // 等页面上那次真实的数据读取
  const afterLogin = await evalJs(`(() => {
    const t = (id) => { const el = document.getElementById(id); return el ? (el.textContent || "").trim() : null; };
    const menu = document.getElementById("user-menu");
    // 菜单要先打开才读得到里面的文字
    if (menu && menu.hidden) document.getElementById("btn-user").click();
    return {
      gateHidden: document.getElementById("login-gate").hidden,
      locked: document.body.classList.contains("is-locked"),
      userName: t("user-name"),
      avatar: t("avatar"),
      status: t("user-status-text"),
      kind: t("um-kind"),
      menuOpen: menu ? !menu.hidden : null,
    };
  })()`);
  check(afterLogin.gateHidden, "登录成功：登录页收起");
  check(afterLogin.locked === false, "主界面解锁（body 不再 is-locked）");
  check(afterLogin.userName === "13800000000", "顶栏显示的就是登录时填的账号", `「${afterLogin.userName}」`);
  check(afterLogin.avatar === "1", "头像取账号首字符", `「${afterLogin.avatar}」`);
  check(afterLogin.status === "在线", "状态：在线", `「${afterLogin.status}」`);
  check(String(afterLogin.kind || "").startsWith("账号（") && !String(afterLogin.kind).includes("姓名"),
    "用户菜单里的身份说明是账号口径（不叫姓名）", `「${afterLogin.kind}」`);
  const mainText = await evalJs(`document.body.innerText`);
  check(mainText.includes("姓名") === false, "登录后的页面文字里也没有「姓名」");

  // ── ⑥ 其它"图标型"按钮：只画图标 + tooltip（刷新 / 收起）──────────────
  console.log("\n=== ⑥ 图标型按钮：只画图标 + 悬停有 tooltip ===");
  await evalJs(`location.hash = "#/data"`);      // 任务口径 / 执行记录 / 文档正文这三张卡都在数据管理页
  await sleep(2500);
  const ICONS = `(() => {
    const read = (id) => {
      const b = document.getElementById(id);
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), text: (b.textContent || "").trim(),
               svgs: b.querySelectorAll("svg").length, title: b.getAttribute("title"),
               aria: b.getAttribute("aria-label"), visible: r.width > 0 && r.height > 0 };
    };
    return { tasks: read("btn-reload-tasks"), exec: read("btn-refresh-exec"),
             docClose: read("btn-doc-close"),
             hash: location.hash,
             shownPage: (document.querySelector("section.page:not([hidden])") || {}).id,
             display: getComputedStyle(document.getElementById("btn-reload-tasks")).display };
  })()`;
  const icons = await evalJs(ICONS);
  console.log(`   诊断：hash=${icons.hash} 当前页=${icons.shownPage} 按钮 display=${icons.display}`);
  for (const [key, label] of [["tasks", "刷新任务列表"], ["exec", "刷新执行记录"]]) {
    const button = icons[key];
    check(button && button.visible && button.text === "" && button.svgs === 1,
      `${label}：图标按钮（页面上没有文字）`, button ? `文字=「${button.text}」 svg=${button.svgs}` : "没找到");
    check(button && button.w >= 24 && button.h >= 24, `${label}：热区 ≥ 24×24`,
      button ? `${button.w}x${button.h}` : "");
    check(button && button.title === label && button.aria === label,
      `${label}：tooltip 与无障碍标签都在`, button ? `title=「${button.title}」 aria=「${button.aria}」` : "");
  }
  check(icons.docClose && icons.docClose.svgs === 1 && icons.docClose.text === ""
       && icons.docClose.title === "收起正文" && icons.docClose.aria === "收起正文",
    "收起正文：图标按钮 + tooltip（它在折叠的正文查看器里，只查结构）",
    icons.docClose ? `文字=「${icons.docClose.text}」 title=「${icons.docClose.title}」` : "没找到");
  const iconShot = await shotClip("icon-tasks.png", "btn-reload-tasks", 120);
  check(fs.existsSync(iconShot), "留证：刷新任务列表图标截图", path.basename(iconShot));
  const execShot = await shotClip("icon-exec.png", "btn-refresh-exec", 120);
  check(fs.existsSync(execShot), "留证：刷新执行记录图标截图", path.basename(execShot));

  // 刷新一次：本机记录还在 → 直接进主界面，不该又弹登录页（STEP B-UI 既有能力不回归）
  await send("Page.reload", { ignoreCache: false });
  await sleep(4000);
  const afterReload = await evalJs(`(() => ({
    gateHidden: document.getElementById("login-gate").hidden,
    who: document.getElementById("user-name").textContent.trim(),
  }))()`);
  check(afterReload.gateHidden && afterReload.who === "13800000000",
    "刷新后仍是登录态（本机记录没被这次改动弄丢）", `「${afterReload.who}」`);

  check(consoleErrors.length === 0, "整个过程没有 JS 报错", consoleErrors.slice(0, 2).join(" | "));
  console.log(`\n通过 ${passed} 项，失败 ${failed} 项`);
  process.exitCode = failed ? 1 : 0;
} catch (err) {
  console.log(`❌ 探针失败：${err}`);
  process.exitCode = 1;
} finally {
  if (ws) ws.close();
  try { browser.kill(); } catch { /* 已经退出 */ }
  try { fs.rmSync(udd, { recursive: true, force: true }); } catch { /* 留给系统清 temp */ }
}
