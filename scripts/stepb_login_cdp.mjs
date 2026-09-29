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
//   ⑥ **真注册**：底部链接切到注册栏 → 账号失焦查重（✓ 可用 / 已被注册两种都要对）→
//      两次密码不一致就地人话 → **在密码框里按回车提交** → 注册成功直接进系统，
//      顶栏显示显示名、中文显示名取中文首字做头像；退出后重复注册被 409 拦下；
//      「忘记密码？」给的是实话（本机没法找回），不是"尚未开通"那套；
//   ⑦ 登录：密码不对 → 401 人话 + 清空密码框（账号留着）；对了 → 登录页收起、顶栏显示显示名；
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

// ⚠️ 本脚本会**真的注册一个账号**（注册已经是真功能了，这正是它要验的事）。
//    为了让每次跑都是干净的一次，先清掉**本脚本文档里那个沙箱**的账号表：
//    只认 outputs/_cdp_run/state/accounts.json（脚本自己指的那个目录），
//    而且只在打本机地址时才动 —— 真实 state/ 永远不碰。
if (/127\.0\.0\.1|localhost/.test(BASE)) {
  const sandboxAccounts = path.join(process.cwd(), "outputs", "_cdp_run", "state", "accounts.json");
  if (fs.existsSync(sandboxAccounts)) {
    fs.rmSync(sandboxAccounts);
    console.log(`已清空沙箱账号表（每次跑都是干净的一次）：${sandboxAccounts}`);
  }
}

// 健康检查只说明**进程**起来了；`/api/chat/capabilities` 才说明**数据读进内存了**。
// 登录页上的数字与帮助里的数字都靠它 —— 不等它，页面上那几个槽位要一两分钟才出现，
// 检查就会把它们误判成"没填出来"。这里等一次（首次要读那份 22MB 的表），等到了再开浏览器。
console.log("等数据预热（首次要读那张大表，可能要一两分钟）…");
try {
  const warm = await fetch(BASE + "/api/chat/capabilities", { signal: AbortSignal.timeout(900000) });
  if (!warm.ok) {
    console.log(`❌ 数据预热失败（${warm.status}），先看看服务日志`);
    process.exit(1);
  }
  console.log("数据已就绪，开始走查。");
} catch (err) {
  console.log(`❌ 等数据预热超时/失败：${err}`);
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

  // 等页面把 DOM 真的建出来再开始量。不然第一条 measure 读到的是 null，
  // 报出来的错看着像"页面少了个元素"，其实是探针跑早了（与被测代码无关）。
  let domReady = false;
  for (let i = 0; i < 150; i++) {
    domReady = await evalJs(`document.readyState !== "loading"
      && Boolean(document.getElementById("login-gate"))
      && Boolean(document.getElementById("register-form"))`);
    if (domReady) break;
    await sleep(200);
  }
  if (!domReady) throw new Error("页面 30 秒内没建出登录页的 DOM（先看看服务是否正常返回 / 与 /session.js）");

  // 真的按一下回车（不是"替它调 submit()"）：先聚焦到输入框，再发一对 keyDown/keyUp，
  // 由**浏览器自己**走隐式提交 —— 这样验到的是"回车能不能提交"，不是"我替它提交了"。
  const pressEnter = async (selector) => {
    const focused = await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return false;
      el.focus();
      return document.activeElement === el;
    })()`);
    if (!focused) throw new Error(`没能聚焦到 ${selector}`);
    await send("Input.dispatchKeyEvent", {
      type: "keyDown", key: "Enter", code: "Enter", text: "\r",
      windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
    });
    await send("Input.dispatchKeyEvent", {
      type: "keyUp", key: "Enter", code: "Enter",
      windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
    });
  };

  // 把光标从某个输入框移走（触发失焦查重）。做法跟人一样：点下一个框。
  // 万一浏览器不给焦点（headless 下偶尔如此），就补发一个 blur 事件 —— 被测代码监听的
  // 就是 blur 这件事，补发的与真的在语义上没有区别（这一步不该因为"探针没拿到焦点"而误报）。
  const blurField = async (selector, nextSelector) => {
    await evalJs(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      el.focus();
      const next = document.querySelector(${JSON.stringify(nextSelector)});
      if (next) next.focus();
      if (document.activeElement === el) el.dispatchEvent(new FocusEvent("blur"));
      return true;
    })()`);
  };

  // 从**图**上把 4 个字符读出来 —— 这一步就是"人看图抄字"的机器版：
  // 页面里的图是 data: 地址，把地址解回 SVG 文本，逐个取出 <text> 里的字符。
  // （真后端每个字符各占一个 <text>，所以取出来是有序的 4 个。）
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

  // 照着图填进去（填之前先确认这张图真的显示出来了 —— 图不可见就没法"看"）
  const fillCaptcha = async (imgId, inputId) => {
    const box = await evalJs(`(() => {
      const img = document.getElementById(${JSON.stringify(imgId)});
      const r = img ? img.getBoundingClientRect() : null;
      return r ? { w: Math.round(r.width), h: Math.round(r.height), src: img.src } : null;
    })()`);
    const code = await readCaptchaCode(imgId);
    await evalJs(`(() => {
      const input = document.getElementById(${JSON.stringify(inputId)});
      input.value = ${JSON.stringify(code)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    return { box, code };
  };

  // 点"退出登录"：先打开用户菜单，再点菜单里那一项（跟人操作一样）
  const logout = async () => {
    await evalJs(`(() => {
      const menu = document.getElementById("user-menu");
      if (menu && menu.hidden) document.getElementById("btn-user").click();
      return true;
    })()`);
    await sleep(150);
    await evalJs(`document.getElementById("um-logout").click()`);
    await sleep(300);
  };

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
  check(copy.body.includes("登录后使用完整功能"), "副标题是正式话术「登录后使用完整功能」");
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

  // ── ②c 图形验证码：图看得见、点一下能换、填错有人话 ────────────────────
  console.log("\n=== ②c 图形验证码：能看清、能换、填错有人话 ===");
  const captchaState = `(() => {
    const read = (imgId, inputId, msgId) => {
      const img = document.getElementById(imgId);
      const r = img ? img.getBoundingClientRect() : null;
      const msg = document.getElementById(msgId);
      const input = document.getElementById(inputId);
      const ir = input ? input.getBoundingClientRect() : null;
      return {
        visible: Boolean(r && r.width > 0 && r.height > 0),
        w: r ? Math.round(r.width) : 0, h: r ? Math.round(r.height) : 0,
        src: img ? img.src : "",
        alt: img ? img.getAttribute("alt") : "",
        inputH: ir ? Math.round(ir.height) : 0,
        msg: msg && !msg.hidden ? msg.textContent.trim() : "",
      };
    };
    return { login: read("login-captcha-img", "login-captcha", "login-captcha-msg"),
             reg: read("reg-captcha-img", "reg-captcha", "reg-captcha-msg") };
  })()`;
  let captcha = await evalJs(captchaState);
  check(captcha.login.visible,
    "登录页的验证码图真的显示出来了", `${captcha.login.w}x${captcha.login.h}`);
  check(captcha.login.w >= 80 && captcha.login.h >= 28,
    "图够大、看得清（不是糊成一小条）", `${captcha.login.w}x${captcha.login.h}`);
  check(captcha.login.h === captcha.login.inputH,
    "图与输入框**等高对齐**", `图 ${captcha.login.h} / 框 ${captcha.login.inputH}`);
  check(captcha.login.alt.includes("可点击更换"), "图有无障碍说明（读屏能念）", `「${captcha.login.alt}」`);
  check(/^data:image/.test(captcha.login.src), "图是当图片显示的（data: 地址），不是页面上的文字");
  const firstCode = await readCaptchaCode("login-captcha-img");
  check(firstCode.length === 4, "从图上能读出 4 个字符（人眼可读的机器版）", `「${firstCode}」`);

  const firstSrc = captcha.login.src;
  await evalJs(`document.getElementById("login-captcha-img").click()`);
  await sleep(600);
  captcha = await evalJs(captchaState);
  check(captcha.login.src !== firstSrc, "点一下图就换了一张（看不清有出路）",
    captcha.login.src === firstSrc ? "图没换（还是同一张）" : "换过了");
  check(captcha.login.msg === "", "换图顺手把上一条提示收走");

  const wrongSrc = captcha.login.src;
  await evalJs(`(() => {
    const set = (id, value) => {
      const el = document.getElementById(id);
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    set("login-name", "13800000000"); set("login-pwd", "whatever-123");
    set("login-captcha", "ZZZZ");
    document.getElementById("btn-login").click();
    return true;
  })()`);
  await sleep(1500);
  captcha = await evalJs(captchaState);
  check(captcha.login.msg.includes("验证码不对"), "验证码填错：就地人话", `「${captcha.login.msg}」`);
  check(captcha.login.src !== wrongSrc && captcha.login.src !== "",
    "填错之后自动换一张（不让人对着作废的图重试）");
  check(await evalJs(`document.getElementById("login-gate").hidden === false`),
    "验证码不对不放行（还在登录页）");

  // ── ③ 真注册一个账号（注册已经是真功能，不再是"尚未开通"提示）──────────
  console.log("\n=== ③ 真注册：链接切栏 → 失焦查重 → 两次不一致 → 回车提交 → 第一个账号直接进来 ===");
  await evalJs(`document.getElementById("link-register").click()`);
  await sleep(250);
  const regPane = await evalJs(`(() => {
    const form = document.getElementById("register-form");
    const r = form.getBoundingClientRect();
    return {
      visible: !form.hidden && r.width > 0 && r.height > 0,
      loginHidden: document.getElementById("login-form").hidden,
      guestHidden: document.getElementById("guest-pane").hidden,
      tabOn: document.getElementById("ltab-register").classList.contains("is-on"),
      fields: ["reg-name", "reg-display", "reg-pwd", "reg-pwd2", "btn-register"]
        .every((id) => Boolean(document.getElementById(id))),
      submit: (document.getElementById("btn-register").textContent || "").trim(),
    };
  })()`);
  check(regPane.visible && regPane.loginHidden && regPane.guestHidden,
    "点底部「注册账号」→ 切到**真注册表单**（不再是「尚未开通」的提示条）");
  check(regPane.tabOn, "「注册」标签点亮（三个入口并列：账号登录 / 注册 / 游客登录）");
  check(regPane.fields && regPane.submit.length > 0,
    "注册表单四块齐全：账号 / 显示名 / 密码 / 确认密码 + 提交按钮", `按钮=「${regPane.submit}」`);
  // 注册页的字段名与提示语：一行一行量（用户点名的几条，别被改回口语版）
  const regCopy = await evalJs(`(() => {
    const label = (id) => {
      const el = document.querySelector('label[for="' + id + '"]');
      return el ? el.textContent.trim() : "";
    };
    const ph = (id) => (document.getElementById(id) || {}).placeholder || "";
    const hint = document.querySelector("#register-form .login-hint");
    return {
      title: document.getElementById("login-title").textContent.trim(),
      sub: document.getElementById("login-sub").textContent.trim(),
      display: label("reg-display"), displayPh: ph("reg-display"),
      pwd2: label("reg-pwd2"), pwd2Ph: ph("reg-pwd2"),
      hint: hint ? hint.textContent.replace(/\\s+/g, "").trim() : "",
    };
  })()`);
  check(regCopy.title === "注册账号" && regCopy.sub === "填写账号信息完成注册",
    "注册那一栏的标题 / 副标题是「注册账号 / 填写账号信息完成注册」",
    `「${regCopy.title}」「${regCopy.sub}」`);
  check(regCopy.display === "显示名（选填）", "显示名标签是「显示名（选填）」", `「${regCopy.display}」`);
  check(regCopy.displayPh === "不填则与账号相同", "显示名提示语是「不填则与账号相同」", `「${regCopy.displayPh}」`);
  check(regCopy.pwd2 === "确认密码", "确认密码那格写着「确认密码」", `「${regCopy.pwd2}」`);
  check(regCopy.pwd2Ph === "请再次输入密码", "确认密码的提示语是「请再次输入密码」", `「${regCopy.pwd2Ph}」`);
  check(regCopy.hint.includes("注册后需管理员批准方可登录"),
    "注册页那行说明是「注册后需管理员批准方可登录。」", `「${regCopy.hint}」`);
  await shotPage("register-form.png");
  const regCaptchaBox = await evalJs(`(() => {
    const img = document.getElementById("reg-captcha-img");
    const input = document.getElementById("reg-captcha");
    const r = img.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height),
             inputH: Math.round(input.getBoundingClientRect().height) };
  })()`);
  check(regCaptchaBox.w > 0 && regCaptchaBox.h > 0,
    "注册页也有自己的验证码图（切过来才看得到）", `${regCaptchaBox.w}x${regCaptchaBox.h}`);
  check(regCaptchaBox.h === regCaptchaBox.inputH,
    "注册页的图与输入框同样等高对齐",
    `图 ${regCaptchaBox.h} / 框 ${regCaptchaBox.inputH}`);

  // 填表用**真事件**（input 冒泡），跟人敲键盘走同一条路
  const fillRegister = `(account, display, pwd, pwd2) => {
    const set = (id, value) => {
      const el = document.getElementById(id);
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
    };
    set("reg-name", account); set("reg-display", display);
    set("reg-pwd", pwd); set("reg-pwd2", pwd2);
    return true;
  }`;
  const readRegMsgs = `(() => {
    const read = (id) => {
      const el = document.getElementById(id);
      return { text: el.hidden ? "" : el.textContent.trim(), ok: el.classList.contains("is-ok") };
    };
    return {
      name: read("reg-name-msg"), pwd: read("reg-pwd-msg"), again: read("reg-pwd2-msg"),
      gateHidden: document.getElementById("login-gate").hidden,
      strength: document.getElementById("reg-strength").hidden
        ? "" : document.getElementById("reg-strength-text").textContent.trim(),
    };
  })()`;

  // ③a 账号失焦查重：这个名字还没人用 → 绿的「✓ 可用」
  await evalJs(`(${fillRegister})("13800000000", "唐宇", "", "")`);
  await blurField("#reg-name", "#reg-display");
  await sleep(900);
  let regMsgs = await evalJs(readRegMsgs);
  check(regMsgs.name.text.includes("可用") && regMsgs.name.ok,
    "账号失焦查重：没人用过 → 绿的「✓ 可用」", `「${regMsgs.name.text}」`);

  // ③b 两次密码不一致 → 就地人话，且**不放行**
  await evalJs(`(${fillRegister})("13800000000", "唐宇", "abc12345", "abc12346")`);
  await evalJs(`document.getElementById("btn-register").click()`);
  await sleep(500);
  regMsgs = await evalJs(readRegMsgs);
  check(regMsgs.again.text.includes("不一致") && regMsgs.gateHidden === false,
    "两次密码不一致：就地人话、不放行", `「${regMsgs.again.text}」`);
  check(regMsgs.strength.includes("强度"), "边打边给密码强度提示（弱 / 中 / 强，不拦人）",
    `「${regMsgs.strength}」`);

  // ③c 改一致 → 照着图填验证码 → **用回车提交**（真键盘事件，浏览器隐式提交）
  await evalJs(`(${fillRegister})("13800000000", "唐宇", "abc12345", "abc12345")`);
  const regCaptcha = await fillCaptcha("reg-captcha-img", "reg-captcha");
  check(regCaptcha.box && regCaptcha.box.w > 0 && regCaptcha.box.h > 0,
    "注册页的验证码图**真的显示出来了**（能看才能填）",
    regCaptcha.box ? `${regCaptcha.box.w}x${regCaptcha.box.h}` : "没找到图");
  check(regCaptcha.code.length === 4, "图上正好 4 个字符（人眼能认、不是乱码）",
    `读到「${regCaptcha.code}」`);
  await pressEnter("#reg-pwd2");
  await sleep(4500);                       // 真注册请求 + 注册完把主界面数据读起来
  const afterRegister = await evalJs(`(() => {
    const t = (id) => { const el = document.getElementById(id); return el ? (el.textContent || "").trim() : null; };
    return {
      gateHidden: document.getElementById("login-gate").hidden,
      locked: document.body.classList.contains("is-locked"),
      userName: t("user-name"), avatar: t("avatar"), status: t("user-status-text"),
    };
  })()`);
  check(afterRegister.gateHidden && afterRegister.locked === false,
    "在密码框里按**回车**就提交了；这是本机第一个账号 → 自动是管理员、直接进系统"
    + "（之后的账号是「等待批准」，见 scripts/stepc_guest_cdp.mjs）");
  check(afterRegister.userName === "唐宇", "顶栏显示的是**显示名**「唐宇」", `「${afterRegister.userName}」`);
  check(afterRegister.avatar === "唐", "中文显示名 → 中文首字做头像（不是拉丁首字母）",
    `「${afterRegister.avatar}」`);
  check(afterRegister.status === "在线", "进来之后状态是「在线」", `「${afterRegister.status}」`);
  const regShot = await shotPage("registered.png");
  check(fs.existsSync(regShot), "留证：注册成功后直接进主界面的截图", path.basename(regShot));

  // ── ③d 退出 → 重复注册被拦（409）+ 忘记密码给的是实话 ─────────────────
  console.log("\n=== ③d 重复注册 / 忘记密码 ===");
  await logout();
  const afterLogout = await evalJs(`(() => ({
    gateHidden: document.getElementById("login-gate").hidden,
    who: document.getElementById("user-name").textContent.trim(),
  }))()`);
  check(afterLogout.gateHidden === false && afterLogout.who === "未登录", "退出登录 → 回登录页");

  await evalJs(`document.getElementById("ltab-register").click()`);
  await evalJs(`(${fillRegister})("13800000000", "", "", "")`);
  // 装上观察器：这条提示被谁、在什么时候改过，全部记下来（查"为什么是空的"用）
  await evalJs(`(() => {
    const msg = document.getElementById("reg-name-msg");
    window.__msgLog = [];
    const stamp = () => new Date().toISOString().slice(17, 23);
    window.__msgLog.push([stamp(), "初始", JSON.stringify(msg.textContent), msg.hidden]);
    new MutationObserver(() => {
      window.__msgLog.push([stamp(), "变化", JSON.stringify(msg.textContent), msg.hidden]);
    }).observe(msg, { childList: true, characterData: true, subtree: true, attributes: true });
    const input = document.getElementById("reg-name");
    input.addEventListener("blur", () => window.__msgLog.push([stamp(), "失焦", "", null]));
    input.addEventListener("input", () => window.__msgLog.push([stamp(), "输入", "", null]));
    return true;
  })()`);
  await blurField("#reg-name", "#reg-display");
  await sleep(1200);
  regMsgs = await evalJs(readRegMsgs);
  if (!regMsgs.name.text) {
    const diag = await evalJs(`(async () => {
      const input = document.getElementById("reg-name");
      const msg = document.getElementById("reg-name-msg");
      return {
        value: input.value, focused: document.activeElement === input,
        msgHidden: msg.hidden, msgText: msg.textContent,
        log: window.__msgLog,
      };
    })()`);
    console.log(`   诊断：${JSON.stringify(diag, null, 1)}`);
  }
  check(regMsgs.name.text.includes("已被注册") && regMsgs.name.ok === false,
    "同一个账号再来一次：失焦查重立刻说「已被注册」（红字）", `「${regMsgs.name.text}」`);

  await evalJs(`(${fillRegister})("13800000000", "冒名顶替", "abc12345", "abc12345")`);
  await fillCaptcha("reg-captcha-img", "reg-captcha");     // 注册页的验证码也要填（与用户走同一条路）
  await evalJs(`document.getElementById("btn-register").click()`);
  await sleep(900);
  regMsgs = await evalJs(readRegMsgs);
  check(regMsgs.name.text.includes("已被注册") && regMsgs.gateHidden === false,
    "绕过查重直接提交：后端也拦下并说在账号框下面，不放行", `「${regMsgs.name.text}」`);

  // 三栏的标题 / 副标题跟着栏目走（用户点名要的三句，切一栏量一栏）
  const paneCopy = async () => evalJs(`(() => ({
    title: document.getElementById("login-title").textContent.trim(),
    sub: document.getElementById("login-sub").textContent.trim(),
  }))()`);
  await evalJs(`document.getElementById("ltab-account").click()`);
  await sleep(150);
  let pane = await paneCopy();
  check(pane.title === "账号登录" && pane.sub === "登录后使用完整功能",
    "账号栏标题 / 副标题是「账号登录 / 登录后使用完整功能」", `「${pane.title}」「${pane.sub}」`);
  await evalJs(`document.getElementById("ltab-register").click()`);
  await sleep(150);
  pane = await paneCopy();
  check(pane.title === "注册账号" && pane.sub === "填写账号信息完成注册",
    "注册栏标题 / 副标题是「注册账号 / 填写账号信息完成注册」", `「${pane.title}」「${pane.sub}」`);
  await evalJs(`document.getElementById("ltab-guest").click()`);
  await sleep(150);
  pane = await paneCopy();
  check(pane.title === "游客登录" && pane.sub === "无需账号，部分功能受限",
    "游客栏标题 / 副标题是「游客登录 / 无需账号，部分功能受限」", `「${pane.title}」「${pane.sub}」`);
  await shotPage("guest-pane.png");
  await evalJs(`document.getElementById("ltab-register").click()`);   // 回到注册栏，下面几条接着走
  await sleep(150);

  await evalJs(`document.getElementById("link-forgot").click()`);
  await sleep(250);
  const forgotToast = await evalJs(`(() => { const b = document.getElementById("toast"); return b.hidden ? null : b.textContent.trim(); })()`);
  check(Boolean(forgotToast) && (forgotToast.includes("找回") || forgotToast.includes("重置")),
    "点「忘记密码？」：如实说本机没法找回（不假装能发邮件）", `「${forgotToast}」`);
  check(Boolean(forgotToast) && !forgotToast.includes("尚未开通"),
    "「忘记密码？」给的是**实话**，不是「尚未开通」那套说辞");

  // ── ④ 校验：太短 / 太长拦下，正常放行 ─────────────────────────────────
  console.log("\n=== ④ 形态校验：太短 / 太长 / 正常 ===");
  // 上一步停留在注册栏，切回登录栏（真实用户也是这么切的）
  await evalJs(`document.getElementById("ltab-account").click()`);
  await sleep(200);
  check(await evalJs(`document.getElementById("login-form").hidden === false`),
    "点「账号登录」切回登录表单");
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

  // ── ④c 密码不对：就地人话 + 清空密码框（账号留着）──────────────────────
  console.log("\n=== ④c 密码不对：401 的人话 + 清空密码框 ===");
  await fillCaptcha("login-captcha-img", "login-captcha");
  await evalJs(`(${typeAndSubmit})("13800000000", "wrong-password")`);
  await sleep(1200);
  const wrongPwd = await evalJs(`(() => {
    const msg = document.getElementById("login-pwd-msg");
    return {
      text: msg.hidden ? "" : msg.textContent.trim(),
      pwdValue: document.getElementById("login-pwd").value,
      nameValue: document.getElementById("login-name").value,
      gateHidden: document.getElementById("login-gate").hidden,
    };
  })()`);
  check(wrongPwd.text.includes("账号或密码不对"), "密码不对：就地人话「账号或密码不对」", `「${wrongPwd.text}」`);
  check(wrongPwd.pwdValue === "" && wrongPwd.nameValue === "13800000000",
    "失败后**密码框清空、账号保留**（重试只要再敲一次密码）",
    `pwd=「${wrongPwd.pwdValue}」 name=「${wrongPwd.nameValue}」`);
  check(wrongPwd.gateHidden === false, "密码不对不放行（还在登录页）");

  // ── ⑤ 正常登录：登录页收起、顶栏显示显示名 ───────────────────────────
  // 这一步用**回车**提交（④c 那条走的是点按钮）—— 两条提交路径都要能用。
  console.log("\n=== ⑤ 真登录一次：账号「13800000000」（在密码框里按回车提交）===");
  const typeLogin = `(account, password) => {
    const a = document.getElementById("login-name");
    const p = document.getElementById("login-pwd");
    a.value = account; p.value = password;
    a.dispatchEvent(new Event("input", { bubbles: true }));
    p.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }`;
  await evalJs(`(${typeLogin})("13800000000", "abc12345")`);
  const loginCaptcha = await fillCaptcha("login-captcha-img", "login-captcha");
  check(loginCaptcha.code.length === 4, "登录页的图也能认出 4 个字符（人眼可读，AC-9）",
    `读到「${loginCaptcha.code}」`);
  await pressEnter("#login-pwd");
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
  check(afterLogin.gateHidden, "在密码框里按**回车**就登录了：登录页收起");
  check(afterLogin.locked === false, "主界面解锁（body 不再 is-locked）");
  check(afterLogin.userName === "唐宇", "顶栏显示的是**显示名**（账号是 13800000000）",
    `「${afterLogin.userName}」`);
  check(afterLogin.avatar === "唐", "头像取显示名的首字（中文显示名 → 中文字）", `「${afterLogin.avatar}」`);
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
  check(afterReload.gateHidden && afterReload.who === "唐宇",
    "刷新后仍是登录态（本机记录没被这次改动弄丢）", `「${afterReload.who}」`);

  // ── ⑥b 帮助 / 隐私：真内容，数字是当前数据源的真数字 ─────────────────────
  console.log("\n=== ⑥b 帮助 / 隐私抽屉（内容与真数据核对）===");
  await evalJs(`document.getElementById("link-help").click()`);
  // 帮助里的数字要等后端把数据读进内存（冷启动那一次要 100 多秒），
  // 所以这里**等它把数字填出来**再核对 —— 有上限，超了就当"没读到"报出来。
  let helpReady = false;
  for (let i = 0; i < 30; i += 1) {
    helpReady = await evalJs(`(() => {
      const el = document.getElementById("help-rows");
      return Boolean(el) && el.textContent.trim() !== "—" && el.textContent.trim() !== "读取中…";
    })()`);
    if (helpReady) break;
    await sleep(2000);
  }
  check(helpReady, "帮助里的数字在超时之前填了出来（后端数据已就绪）");
  const help = await evalJs(`(() => {
    const read = (id) => {
      const el = document.getElementById(id);
      return el ? (el.textContent || "").trim() : null;
    };
    const drawer = document.getElementById("info-drawer");
    const r = drawer.getBoundingClientRect();
    const panel = drawer.querySelector(".info-panel").getBoundingClientRect();
    return {
      open: !drawer.hidden, h: Math.round(r.height), panelW: Math.round(panel.width),
      title: read("info-title"),
      helpShown: !document.getElementById("info-pane-help").hidden,
      privacyShown: !document.getElementById("info-pane-privacy").hidden,
      source: read("help-source"), range: read("help-range"), rows: read("help-rows"),
      customers: read("help-customers"), countries: read("help-countries"),
      regionRowHidden: (() => {
        const row = document.getElementById("help-region-row");
        return row ? row.hidden : null;
      })(),
      currency: read("help-currency"), lastDay: read("help-last-day"),
      formats: read("help-report-formats"),
      body: drawer.innerText.replace(/\s+/g, " "),
    };
  })()`);
  check(help.open && help.helpShown && !help.privacyShown,
    "点「帮助」→ 右侧抽屉打开、帮助那一栏显示（不是尚未开通的提示）");
  check(help.h >= 700 && help.panelW >= 360,
    "抽屉占用整条右侧栏（够宽够高，读得下）", `面板宽 ${help.panelW} 高 ${help.h}`);
  check(help.title === "帮助", "标题是「帮助」", `「${help.title}」`);
  check(help.rows === "541,909" && help.customers === "4,372",
    "帮助里的数字是从当前数据源读出来的（不是写死的）",
    `行数=${help.rows} 客户=${help.customers}`);
  // 地理维度按数据源动态生成：内置数据集只有 Country、没有地区字段 → 「地区数」这一行**不显示**
  // （绝不把国家数标成地区数）。带地区字段的数据源才出现这一行。
  check(help.countries === "" && help.regionRowHidden === true,
    "没有地区字段 → 帮助里不出现「地区数」这一行（不拿国家数顶替）",
    `地区数值=「${help.countries}」 hidden=${help.regionRowHidden}`);
  check((help.source || "").includes("Online Retail.xlsx"), "数据源名是读出来的", `「${help.source}」`);
  check((help.range || "").includes("2010-12-01") && (help.range || "").includes("2011-12-09"),
    "覆盖范围是读出来的", `「${help.range}」`);
  check((help.currency || "").includes("元"), "币种是读出来的", `「${help.currency}」`);
  check(help.lastDay === "2011-12-09", "最后一天是读出来的", `「${help.lastDay}」`);
  check((help.formats || "").includes("Word"), "报告能导出的格式是读出来的", `「${help.formats}」`);
  check(help.body.includes("区域") && help.body.includes("销售员") && help.body.includes("门店"),
    "「数据里没有什么」把不能问的维度都列了出来");
  check(help.body.includes("同一套口径") && help.body.includes("客户编号"),
    "「数字怎么算的」讲了口径（含客户维度只算有客户号的成交）");
  for (const word of ["最后几天", "客户数", "导出", "数据源"]) {
    check(help.body.includes(word), `常见问题里有「${word}」那一条`);
  }
  // FR-007：帮助示例不再列「国家分布」（用户要求界面不出现国家维度），改成地区口径的条件说明
  for (const label of ["销售汇总", "销售趋势", "产品排行", "两区间比较", "客户分析", "商品分析", "地区分布"]) {
    check(help.body.includes(label), `能问的几类里有「${label}」`);
  }

  await evalJs(`document.getElementById("link-privacy").click()`);
  await sleep(400);
  const privacy = await evalJs(`(() => {
    const pane = document.getElementById("info-pane-privacy");
    return {
      title: document.getElementById("info-title").textContent.trim(),
      shown: !pane.hidden,
      helpHidden: document.getElementById("info-pane-help").hidden,
      text: pane.innerText.replace(/\s+/g, " "),
    };
  })()`);
  check(privacy.shown && privacy.helpHidden, "点「隐私」→ 换成隐私那一栏");
  check(privacy.title === "隐私说明", "标题跟着换", `「${privacy.title}」`);
  for (const part of ["数据存在哪", "密码怎么存", "会不会把数据发到外面去", "怎么清掉"]) {
    check(privacy.text.includes(part), `隐私四项里有「${part}」`);
  }
  check(privacy.text.includes("会发出去") && privacy.text.includes("不会发出去"),
    "如实把发什么 / 不发什么分开说清了");
  check(privacy.text.includes("明细行不会上传") && privacy.text.includes("模型"),
    "说清了上传的数据文件不外发、数字由本机算");
  const bannedInDrawer = ["TASK-", "/api/", "openapi", "白名单", "sha256", "鉴权",
                          "endpoint", "svg", "captcha", "尚未开通"];
  const drawerText = (help.body + " " + privacy.text).toLowerCase();
  const hits = bannedInDrawer.filter((word) => drawerText.includes(word.toLowerCase()));
  check(hits.length === 0, "帮助/隐私里没有技术字样、也没有尚未开通那套话", hits.join(", "));

  // 先切回帮助、拍一张**打开着**的截图（留证要拍它开着的样子）
  await evalJs(`document.getElementById("link-help").click()`);
  await sleep(600);
  const infoShot = await shotPage("help-drawer.png");
  check(fs.existsSync(infoShot), "留证：帮助抽屉打开时的截图", path.basename(infoShot));
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape",
                                         windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape",
                                         windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  await sleep(300);
  check(await evalJs(`document.getElementById("info-drawer").hidden === true`),
    "按 Esc 能关上（另有关闭按钮与点背景两条路）");

  // ── ⑦ 账号文件里到底存了什么（真落盘的那份，不是响应）─────────────────
  console.log("\n=== ⑦ 沙箱账号表：明文密码 0 命中 / 游客不在里面 ===");
  if (/127\.0\.0\.1|localhost/.test(BASE)) {
    const accountsPath = path.join(process.cwd(), "outputs", "_cdp_run", "state", "accounts.json");
    if (!fs.existsSync(accountsPath)) {
      check(false, "账号表应当已被注册写出来", accountsPath);
    } else {
      const raw = fs.readFileSync(accountsPath, "utf8");
      const records = JSON.parse(raw).accounts || [];
      check(records.length === 1, "账号表里就注册的这一个（游客没进来占名额）",
        `共 ${records.length} 条：${records.map((r) => r.username).join(", ")}`);
      check(records.some((r) => r.username === "13800000000" && r.display_name === "唐宇"),
        "记录里有账号名与显示名");
      check(raw.includes("abc12345") === false, "**明文密码 0 命中**（grep 注册时用的那串）");
      check(raw.includes("游客") === false, "账号表里没有任何「游客XXXXX」（游客是本机随机名，不占账号）");
      check(records.every((r) => r.pwd_algo && r.pwd_salt && r.pwd_hash && r.pwd_iterations),
        "每条记录都有：算法名 + 每账号独立的盐 + 校验值 + 迭代次数",
        records.map((r) => `${r.pwd_algo}/${String(r.pwd_salt).length}位盐/${String(r.pwd_hash).length}位校验值/x${r.pwd_iterations}`).join(" "));
    }
  } else {
    console.log("   （远程地址：不检查本机沙箱文件）");
  }

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
