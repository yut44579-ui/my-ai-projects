/* session_check.mjs · 用 node 真的把 web/session.js 跑一遍（本机逻辑验收）
 *
 * ════════════════════════════════════════════════════════════════════════
 * 【为什么要有这个脚本】
 * 登录页与四态状态机是**浏览器里的交互**，pytest 只能静态扫源码（"有没有这个 id"），
 * 扫不出"点错了顺序会不会错"。这里给 session.js 一个**最小假 DOM**（只实现它真用到的那几样），
 * 然后把该走的流程走一遍：登录 → 校验失败 → 登录成功 → 提问（忙→在线）→ 超时离开 → 手动切态
 * → 退出。跑的是**同一份 web/session.js 源文件**，不是重写一份逻辑。
 *
 * 【边界（别把它当浏览器验收）】
 * 它证明的是"session.js 的逻辑与状态迁移"，**不是**"样子好不好看"：
 * 真浏览器走查（CDP）由 Hermes 做，静态契约由 pytest 扫。
 *
 * 【零依赖】只用 node 内置模块（node:vm / node:fs）。node 不在的环境里，
 * 对应 pytest 用例会 skip，不影响全量测试。
 * ════════════════════════════════════════════════════════════════════════ */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WEB = path.join(ROOT, "web");

let failed = 0;
let total = 0;
function check(condition, label, extra = "") {
  total += 1;
  const line = `${condition ? "  PASS" : "  FAIL"}  ${label}${extra ? `  ${extra}` : ""}`;
  if (!condition) failed += 1;
  console.log(line);
}
function step(title) {
  console.log(`\n── ${title} ${"─".repeat(Math.max(2, 60 - title.length))}`);
}

// ════════════════════════════════════════════════════════════════════════
// 最小假 DOM / 假环境
// ════════════════════════════════════════════════════════════════════════
function makeElement(id) {
  const classes = new Set();
  const node = {
    id,
    hidden: false,
    textContent: "",
    className: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    type: "text",
    handlers: {},
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle(name, on) {
        const want = on === undefined ? !classes.has(name) : !!on;
        if (want) classes.add(name); else classes.delete(name);
        return want;
      },
      has: (name) => classes.has(name),
    },
    handlers: {},
    children: [],
    appendChild(child) { this.children.push(child); return child; },
    setAttribute(key, value) { this[key] = value; },
    getAttribute(key) { return this[key]; },
    addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
  };
  // innerHTML 的 setter：真 DOM 里 `innerHTML = ""` 会清空子节点，这里也要一样 ——
  // 否则"每次打开帮助都重渲染一遍清单"会越堆越多，检查出来的条数是假的。
  let markup = "";
  Object.defineProperty(node, "innerHTML", {
    get: () => markup,
    set: (value) => {
      markup = value;
      if (value === "") node.children.length = 0;
    },
  });
  return node;
}

// 页面里真实存在的 id 一律从 index.html 里读出来（不另抄一份清单，抄了就会和页面走散）
const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
const htmlIds = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
const elements = new Map();
for (const id of htmlIds) {
  const node = makeElement(id);
  // 起手把 hidden 属性读进来：不然假 DOM 里"默认全是展开的"，
  // 会漏掉"这东西本来就该是收起的"这类问题（抽屉就吃过这个亏）
  const at = html.indexOf(`id="${id}"`);
  const tag = html.slice(html.lastIndexOf("<", at), html.indexOf(">", at));
  if (/\shidden(\s|>|$)/.test(tag)) node.hidden = true;
  elements.set(id, node);
}

const documentHandlers = {};
const documentStub = {
  readyState: "complete",
  body: makeElement("body"),
  createElement: (tag) => makeElement(`created-${tag}`),
  getElementById: (id) => elements.get(id) || null,
  addEventListener(type, fn) {
    (documentHandlers[type] = documentHandlers[type] || []).push(fn);
  },
};

// 真浏览器里 click 会从目标往外**冒泡**，途经的容器有机会 stopPropagation。
// 页面里唯一的这种容器就是顶栏那两个菜单（点菜单里的项不该被"点外面关掉"收走），
// 所以这里把嵌套关系从 index.html 里数出来，而不是在脚本里另手抄一份 —— 抄了就会和页面走散。
const CONTAINERS = ["user-menu", "topbar-menu"];

function tagDepthAt(containerPos, targetPos) {
  // 从容器自己的起始标签开始数开合：<div> 进一层、</div> 出一层，数到目标位置为止
  let depth = 0;
  const tags = /<(\/?)(div|span|button|form|section|aside|header|nav|ul|li|p|label|h2|h3|pre|table|tbody|tr|td|th|a|summary)\b[^>]*?>/g;
  tags.lastIndex = html.lastIndexOf("<", containerPos);
  let match;
  while ((match = tags.exec(html))) {
    if (match.index > targetPos) break;
    if (match[1] === "/") depth -= 1;
    else if (!/\/>$/.test(match[0])) depth += 1;
  }
  return depth;
}

function ancestorsOf(id) {
  const at = html.indexOf(`id="${id}"`);
  if (at < 0) return [];
  return CONTAINERS.filter((container) => {
    const start = html.indexOf(`id="${container}"`);
    return start >= 0 && start < at && tagDepthAt(start, at) > 0;
  });
}

function fire(element, type, extra = {}) {
  const event = {
    type,
    target: element,
    defaultPrevented: false,
    stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...extra,
  };
  for (const fn of element.handlers[type] || []) fn(event);
  for (const id of ancestorsOf(element.id)) {           // 冒泡：先经过菜单这类容器
    if (event.stopped) break;
    for (const fn of (elements.get(id).handlers[type] || [])) fn(event);
  }
  if (!event.stopped) for (const fn of documentHandlers[type] || []) fn(event);
  return event;
}

const store = new Map();
const localStorageStub = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
};

const clock = { now: 1_700_000_000_000 };
class FakeDate extends Date {
  static now() { return clock.now; }
}

let tickFn = null;
const setIntervalStub = (fn) => { tickFn = fn; return 1; };

const locationStub = { search: "" };
const scrollTo = () => {};
const bodyStub = () => {};

// 后端能力清单的**形状**照抄真实响应（数字是对着当前数据源编的假值 —— 这里测的是"渲染逻辑"，
// 不是数字本身；真数字由登录页跑真请求拿，由 pytest 的静态检查盯着"不许写死在页面里"）。
const capsPayload = {
  data_profile: {
    rows: 541909, customer_count: 4372, country_count: 38,
    first_day: "2010-12-01", last_day: "2011-12-09",
    currency: { code: "CNY", symbol: "¥", name: "元" },
  },
  unsupported: { dimensions: ["区域/大区/片区", "省份/城市", "门店/渠道"], reason: "没有这些字段" },
  report: { export_formats: [{ format: "docx", label: "Word" }, { format: "xlsx", label: "Excel" }] },
};
// 后端账号端点的**形状**照抄真实响应（就是那几个字段）。这里不测"密码对不对"——
// 那是后端的事（由 tests/test_auth.py 拿真服务验）；这里测的是"前端拿到响应之后怎么走"：
// 注册成功要直接进来、401 要就地提示并清空密码框、409 要把话说在账号框下面。
const accountsStub = { "唐宇": "123456", "tangyu": "s3cret-1" };
const registeredLog = [];
// 验证码：跟真后端一个脾气 —— 一次性的、会过期、错了有话说。
// 这里**固定**一个答案（真后端是随机图），因为这份检查验的是"前端拿到各种响应之后怎么走"。
const CAPTCHA_CODE = "AB23";
let captchaSeq = 0;
const liveCaptchas = new Set();      // 还活着的那些编号（登录一张、注册一张，各是各的）
const captchaIssued = [];
const apiError = (status, code, message) => {
  const err = new Error(message);
  err.status = status;
  err.code = code;
  return err;
};
const checkCaptchaStub = ({ captcha_id, captcha_text }) => {
  if (!captcha_id || !String(captcha_text || "").trim()) {
    return apiError(400, "captcha_missing", "请先填写验证码。");
  }
  if (!liveCaptchas.has(captcha_id)) {
    return apiError(400, "captcha_expired", "验证码已过期，请点一下刷新。");
  }
  if (String(captcha_text).trim().toUpperCase() !== CAPTCHA_CODE) {
    return apiError(400, "captcha_wrong", "验证码不对，请重新输入。");
  }
  liveCaptchas.delete(captcha_id);          // 用一次即作废（与真后端一致：防重放）
  return null;
};
const APIStub = {
  chatCapabilities: () => Promise.resolve(capsPayload),
  health: () => Promise.resolve({ data_snapshot: { path: "data/Online Retail.xlsx", match: true } }),
  captcha: () => {
    captchaSeq += 1;
    const id = `cap-${captchaSeq}`;
    liveCaptchas.add(id);
    captchaIssued.push(id);
    return Promise.resolve({
      captcha_id: id,
      image_svg: '<svg xmlns="http://www.w3.org/2000/svg"><text>AB23</text></svg>',
      expires_in: 120,
    });
  },
  accountExists: (username) => Promise.resolve({
    exists: Object.keys(accountsStub).some((n) => n.toLowerCase() === String(username || "").toLowerCase()),
    account_count: Object.keys(accountsStub).length,
  }),
  authLogin: ({ username, password, captcha_id, captcha_text }) => {
    const captchaProblem = checkCaptchaStub({ captcha_id, captcha_text });
    if (captchaProblem) return Promise.reject(captchaProblem);
    const hit = Object.keys(accountsStub).find((n) => n.toLowerCase() === String(username).toLowerCase());
    if (!hit || accountsStub[hit] !== password) {
      return Promise.reject(apiError(401, "bad_credentials", "账号或密码不对。"));
    }
    return Promise.resolve({ account: { username: hit, display_name: hit, created_at: "", last_login_at: null } });
  },
  authRegister: ({ username, password, display_name, captcha_id, captcha_text }) => {
    const captchaProblem = checkCaptchaStub({ captcha_id, captcha_text });
    if (captchaProblem) return Promise.reject(captchaProblem);
    if (Object.keys(accountsStub).some((n) => n.toLowerCase() === String(username).toLowerCase())) {
      return Promise.reject(apiError(409, "username_taken", "这个账号已被注册，换一个吧。"));
    }
    accountsStub[username] = password;
    registeredLog.push(username);
    return Promise.resolve({
      account: { username, display_name: display_name || username, created_at: "", last_login_at: null },
    });
  },
};

const context = vm.createContext({
  window: {},
  document: documentStub,
  localStorage: localStorageStub,
  location: locationStub,
  API: APIStub,
  setInterval: setIntervalStub,
  Date: FakeDate,
  Math, JSON, Promise, Number, String, Object, Array, console, URLSearchParams,
});
context.globalThis = context;

const source = fs.readFileSync(path.join(WEB, "session.js"), "utf8");
// 顺带当语法检查：解析不过就直接抛，报错里能看到行号
new vm.Script(source, { filename: "web/session.js" });
vm.runInContext(source, context, { filename: "web/session.js" });

const Session = context.window.Session;
const el = (id) => elements.get(id);
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));   // 让已排队的 promise 跑完
const loginCallbackLog = [];
// "照着图把字填进去"：真人是看图抄，这里是照 stub 里那张图的答案填
const fillCaptcha = (which) => {
  el(which === "account" ? "login-captcha" : "reg-captcha").value = CAPTCHA_CODE;
};

console.log("session_check · 用真源码跑 session.js 的登录与状态机\n");
console.log(`index.html 里的 id 共 ${htmlIds.length} 个；session.js 引用的 id 必须都在里面。`);

// 先自检"数出来的嵌套关系"：数错了的话，下面模拟冒泡就是白演一场
check(ancestorsOf("um-logout").includes("user-menu"),
  "菜单项确实套在用户菜单里（嵌套关系是从 index.html 数出来的）");
check(ancestorsOf("um-status-away").includes("user-menu"), "四态菜单项也在菜单里");
check(ancestorsOf("btn-user").includes("user-menu") === false,
  "用户区按钮是菜单的兄弟节点，不在菜单里（不该被当成菜单内部）");

// ════════════════════════════════════════════════════════════════════════
step("B-01 没登录时：先给登录页，主界面锁住");
// ════════════════════════════════════════════════════════════════════════
Session.requireLogin(() => loginCallbackLog.push("init"));
check(el("login-gate").hidden === false, "登录页显示出来");
check(documentStub.body.classList.has("is-locked"), "主界面被锁住（body.is-locked）");
check(Session.loggedIn() === false, "会话状态：未登录");
check(Session.status() === "offline", "状态：离线", `实际 ${Session.status()}`);
check(el("user-name").textContent === "未登录", "顶栏名字：未登录");
check(loginCallbackLog.length === 0, "主界面初始化被挂住（登录前不读后端数据）");

// ════════════════════════════════════════════════════════════════════════
step("B-01 表单是「账号」形态（不是填姓名）；注册是真栏目，未开通的入口点了有话说");
// ════════════════════════════════════════════════════════════════════════
check(html.includes(">账号<") && html.includes('placeholder="手机号 / 邮箱 / 用户名"'),
  "字段是「账号」+ 手机号 / 邮箱 / 用户名 的提示语");
check(html.includes("记住账号") && html.includes("记住我") === false, "「记住我（只记名字）」已换成「记住账号」");
check(html.includes("登 录") && html.includes("进入系统") === false, "主按钮是「登 录」");
check(html.includes("忘记密码") && html.includes("注册账号"), "忘记了密码 / 注册账号两个入口都在");
check(html.includes("姓名") === false && source.includes("姓名") === false, "页面上不再有「姓名」这个说法");
check(html.includes("不留身份") && source.includes("不留身份"), "游客那栏说清「不留身份」");
check(html.includes(">账号登录<") && html.includes(">注册<") && html.includes(">游客登录<"),
  "三个入口并列：账号登录 / 注册 / 游客登录");
fire(el("link-register"), "click");
check(el("register-form").hidden === false && el("login-form").hidden === true,
  "点底部「注册账号」切到注册栏（不再是要不来的提示）");
check(el("ltab-register").classList.has("is-on") && el("ltab-account").classList.has("is-on") === false,
  "「注册」标签点亮，「账号登录」熄灭");
fire(el("ltab-account"), "click");        // 切回来，后面的登录流程照旧
check(el("login-form").hidden === false && el("register-form").hidden === true, "点「账号登录」切回登录栏");

// ════════════════════════════════════════════════════════════════════════
step("B-01 密码框的眼睛图标：睁眼 ↔ 闭眼两态，标签跟着状态走");
// ════════════════════════════════════════════════════════════════════════
const eyeBtn = () => el("btn-pwd-eye");
check(html.includes(">显示<") === false && html.includes(">隐藏<") === false,
  "按钮里没有「显示 / 隐藏」两个字");
check(html.includes('class="eye eye-open"') && html.includes('class="eye eye-off"'),
  "两个图标都是页面里现成的内联 SVG");
check(html.includes('aria-label="显示密码"') && html.includes('title="显示密码"'),
  "初始态的无障碍标签与提示语都在（「显示密码」）");
check(eyeBtn().classList.has("is-shown") === false && eyeBtn().getAttribute("aria-label") === "显示密码",
  "初始态：睁眼、标签「显示密码」", `label=「${eyeBtn().getAttribute("aria-label")}」`);
check(el("login-pwd").type === "password", "初始态：密码是掩码的");
fire(eyeBtn(), "click");
check(el("login-pwd").type === "text" && eyeBtn().classList.has("is-shown"),
  "点一下：明文显示，切到闭眼（is-shown）");
check(eyeBtn().getAttribute("aria-label") === "隐藏密码" && eyeBtn().getAttribute("title") === "隐藏密码",
  "标签同步成「隐藏密码」（读屏与悬停都读得到当前动作）",
  `label=「${eyeBtn().getAttribute("aria-label")}」`);
check(eyeBtn().getAttribute("aria-pressed") === "true", "aria-pressed 跟着变 true");
fire(eyeBtn(), "click");
check(el("login-pwd").type === "password" && eyeBtn().classList.has("is-shown") === false
  && eyeBtn().getAttribute("aria-label") === "显示密码",
  "再点一下：回到掩码 + 睁眼（图标跟着状态走，不是固定一个眼睛）");

// ════════════════════════════════════════════════════════════════════════
step("验证码：图真的取到了、点一下能换、填错有人话、没填也拦得住");
// ════════════════════════════════════════════════════════════════════════
await flush();                             // 等 boot 里那两次"取图"落地（它们是异步的）
const captchaImgSrc = () => el("login-captcha-img").src || "";
check(captchaImgSrc().startsWith("data:image/svg+xml"),
  "登录页的验证码是**一张图**（当图片显示，不是页面上的文字）", captchaImgSrc().slice(0, 32));
check(el("reg-captcha-img").src.startsWith("data:image/svg+xml"), "注册页同样有一张图");
check(el("login-captcha").getAttribute("maxlength") === undefined
  || html.includes('id="login-captcha" type="text" maxlength="4"'), "验证码输入框限 4 位");
check(html.includes('alt="验证码图片，点一下换一张"'), "验证码图有 alt（读屏能念）");

// 点图换一张：编号要换成新的，输入框里旧的字符要清掉
const beforeReload = captchaIssued.length;
el("login-captcha").value = "AB23";
fire(el("login-captcha-img"), "click");
await flush();
check(captchaIssued.length === beforeReload + 1, "点图换了一张（又取了一张新图）",
  `${beforeReload} → ${captchaIssued.length}`);
check(el("login-captcha").value === "", "换图后输入框清空（旧的那 4 个字符已经作废）");
check(el("login-captcha-msg").hidden === true, "换图顺手把上一条红字收走");

// 填错 → 就地人话 + **自动换一张**
el("login-name").value = "唐宇";
el("login-pwd").value = "123456";
el("login-captcha").value = "ZZZZ";
const wrongCaptchaIssued = captchaIssued.length;
fire(el("login-form"), "submit");
await flush();
check(el("login-captcha-msg").hidden === false
  && el("login-captcha-msg").textContent.includes("验证码不对"),
  "验证码填错：就地人话", `「${el("login-captcha-msg").textContent}」`);
check(captchaIssued.length === wrongCaptchaIssued + 1, "填错之后**自动换一张**（不让人对着作废的图重试）");
check(el("login-gate").hidden === false && Session.loggedIn() === false, "验证码不对不放行");

// 没填 → 就地拦下，**一个请求都不发**
el("login-captcha").value = "";
const missingIssued = captchaIssued.length;
fire(el("login-form"), "submit");
await flush();
check(el("login-captcha-msg").hidden === false
  && el("login-captcha-msg").textContent.includes("4 个字符"),
  "没填验证码：就地人话", `「${el("login-captcha-msg").textContent}」`);
check(captchaIssued.length === missingIssued, "没填就不发请求（省一次往返）");
check(el("login-gate").hidden === false, "没填验证码不放行");

// 过期（图放了太久 / 后端那边已经把它作废了）→ 提示"已帮你换一张"，并且真的换一张
liveCaptchas.clear();                      // 模拟"这一张在服务端已经失效"
el("login-captcha").value = CAPTCHA_CODE;  // 用户照着旧图填的字，其实已经没用了
const expiredIssued = captchaIssued.length;
fire(el("login-form"), "submit");
await flush();
check(el("login-captcha-msg").hidden === false
  && el("login-captcha-msg").textContent.includes("已过期"),
  "验证码过期：提示「已过期，已帮你换一张」", `「${el("login-captcha-msg").textContent}」`);
check(captchaIssued.length === expiredIssued + 1, "过期之后确实换了一张新图");

// 连续失败进了冷却 → 照后端说的"等多久"显示，而不是含糊一句"失败了"
const realAuthLogin = APIStub.authLogin;
APIStub.authLogin = () => Promise.reject(
  apiError(429, "too_many_attempts", "尝试次数过多，请 60 秒后再试。"));
fillCaptcha("account");
fire(el("login-form"), "submit");
await flush();
check(el("login-captcha-msg").hidden === false
  && el("login-captcha-msg").textContent.includes("尝试次数过多")
  && el("login-captcha-msg").textContent.includes("60 秒"),
  "冷却中：把人话原样显示出来（含还要等多久）", `「${el("login-captcha-msg").textContent}」`);
check(el("login-gate").hidden === false && Session.loggedIn() === false, "冷却中不放行");
APIStub.authLogin = realAuthLogin;

// ════════════════════════════════════════════════════════════════════════
step("B-05 只做形态校验：账号太短 / 太长 / 密码太短 → 标红 + 就地提示");
// ════════════════════════════════════════════════════════════════════════
el("login-name").value = "唐";
el("login-pwd").value = "123";
fillCaptcha("account");                    // 先把验证码填上：这一段测的是账号/密码的形态，不是验证码
fire(el("login-form"), "submit");
await flush();
check(el("login-name-msg").hidden === false && el("login-name-msg").textContent.length > 0,
  "账号太短：就地提示", `「${el("login-name-msg").textContent}」`);
check(el("login-pwd-msg").hidden === false, "密码太短：就地提示", `「${el("login-pwd-msg").textContent}」`);
check(el("login-name").classList.has("is-bad"), "出错的输入框标红（is-bad）");
check(el("login-gate").hidden === false, "校验不过不许放行");
check(store.has("sra.who") === false, "校验不过不写本机记录");
// 上限也照新口径走：40 个字符能过，41 个不行
el("login-name").value = "a".repeat(41);
el("login-pwd").value = "123456";
fire(el("login-form"), "submit");
await flush();
check(el("login-name-msg").hidden === false && el("login-name").classList.has("is-bad"),
  "账号 41 个字符：拦下并提示", `「${el("login-name-msg").textContent}」`);

// ════════════════════════════════════════════════════════════════════════
step("B-02 密码不对：就地人话 + 清空密码框（账号留着），不放行");
// ════════════════════════════════════════════════════════════════════════
el("login-name").value = "唐宇";
el("login-pwd").value = "wrong-password";
fillCaptcha("account");                    // 上一次失败后图已经换过一张，这里重新照新图填
fire(el("login-form"), "submit");
await flush();
check(el("login-pwd-msg").hidden === false && el("login-pwd-msg").textContent.includes("账号或密码不对"),
  "登录失败：就地人话「账号或密码不对」", `「${el("login-pwd-msg").textContent}」`);
check(el("login-pwd").value === "" && el("login-name").value === "唐宇",
  "失败后：密码框清空、账号保留（重试只要再敲一次密码）",
  `pwd=「${el("login-pwd").value}」 name=「${el("login-name").value}」`);
check(el("login-gate").hidden === false && Session.loggedIn() === false, "登录失败不放行（还在登录页）");
check(el("login-pwd").classList.has("is-bad"), "密码框标红（红字落点就在那个框下面）");

// ════════════════════════════════════════════════════════════════════════
step("B-02 账号登录：填「唐宇」进主界面，顶栏显示名字");
// ════════════════════════════════════════════════════════════════════════
el("login-name").value = "唐宇";
el("login-pwd").value = "123456";
fillCaptcha("account");                    // 失败那条路把上一张交出去了，这里用新的一张
el("login-remember").checked = true;
const submitEvent = fire(el("login-form"), "submit");
check(submitEvent.defaultPrevented, "提交被拦截（不会真的跳转走）");
check(Session.status() === "busy", "提交瞬间：忙碌中（按钮 loading 与状态联动）",
  `实际 ${Session.status()}`);
await flush();
check(el("login-gate").hidden === true, "登录成功：登录页收起");
check(documentStub.body.classList.has("is-locked") === false, "主界面解锁");
check(el("user-name").textContent === "唐宇", "顶栏名字 = 登录时填的", `实际「${el("user-name").textContent}」`);
check(el("avatar").textContent === "唐", "头像取名字首字");
check(el("user-status-text").textContent === "在线" && el("user-dot").className.includes("st-online"),
  "状态：在线（绿）", `实际 ${el("user-status-text").textContent}`);
check(Session.status() === "online", "会话状态：online");
check(loginCallbackLog.length === 1, "主界面初始化只放行一次");
const stored = JSON.parse(store.get("sra.who"));
check(stored.name === "唐宇" && stored.kind === "account", "本机记录：名字 + 入口类型");
check(("password" in stored) === false && store.get("sra.remembered-name") === "唐宇",
  "本机只记名字，不记密码（记录里没有密码字段）");

// ════════════════════════════════════════════════════════════════════════
step("B-06 分析请求驱动「忙碌中」：发出→红，回来→绿（失败也一样回到在线）");
// ════════════════════════════════════════════════════════════════════════
let release = null;
const pendingRequest = new Promise((resolve) => { release = resolve; });
const tracked = Session.trackRequest(pendingRequest);
check(Session.status() === "busy", "请求发出后：忙碌中", `实际 ${Session.status()}`);
check(el("user-dot").className.includes("st-busy"), "状态点变红（st-busy）");
release({ status: "ok" });
await tracked;
check(Session.status() === "online", "响应回来后：在线", `实际 ${Session.status()}`);
const rejected = Session.trackRequest(Promise.reject(new Error("后端 500")));
check(Session.status() === "busy", "请求失败也先进入忙碌中");
await rejected.catch(() => {});
check(Session.status() === "online", "失败回来同样回到在线（不会卡在忙碌中）");

// ════════════════════════════════════════════════════════════════════════
step("B-07 离开态：无操作超阈值 → 离开；有交互 → 回到在线（用短阈值演示）");
// ════════════════════════════════════════════════════════════════════════
locationStub.search = "?away=1";          // 验收开关：1 秒没操作就算离开
check(el("user-dot").className.includes("st-online"), "起点：在线");
clock.now += 1500;                        // 时间往前走，但没人操作
tickFn();
check(Session.status() === "away", "超时未操作：离开", `实际 ${Session.status()}`);
check(el("user-dot").className.includes("st-away"), "状态点变橙（st-away）");
fire(el("btn-user"), "click");
clock.now += 10;
check(Session.status() === "away", "只是点了下用户菜单：不算业务操作，仍是离开");
fire(documentStub.body, "click");
check(Session.status() === "online", "真操作了一下：回到在线", `实际 ${Session.status()}`);

// ════════════════════════════════════════════════════════════════════════
step("B-09 手动切状态：先听用户的，不被立刻覆盖");
// ════════════════════════════════════════════════════════════════════════
fire(el("um-status-busy"), "click");
check(Session.status() === "busy", "手动切成「忙碌中」", `实际 ${Session.status()}`);
check(el("um-status-busy").classList.has("is-on"), "菜单里当前状态打勾");
check(el("toast").hidden === false && el("toast").textContent.includes("忙碌中"),
  "给一句人话提示", `「${el("toast").textContent}」`);
clock.now += 60_000;
tickFn();
check(Session.status() === "busy", "过了很久也不会被自动规则顶掉（手动优先）");
fire(el("um-status-away"), "click");
check(Session.status() === "away", "再手动切成「离开」");
fire(documentStub.body, "click");
check(Session.status() === "online", "用户又开始操作后才回到自动规则");
// 提示会在几秒后自己收走（同一个计时器负责，另起一个定时器会多一套心跳）
clock.now += 4000;
tickFn();
check(el("toast").hidden === true, "人话提示几秒后自己收走");

// ════════════════════════════════════════════════════════════════════════
step("B-03 / B-04 游客登录：本机随机名，退出后重进换一个");
// ════════════════════════════════════════════════════════════════════════
el("login-pwd").value = "123456";
fire(eyeBtn(), "click");                  // 退出前先把眼睛切到"明文显示"
check(eyeBtn().classList.has("is-shown"), "退出前：眼睛处于明文态（好验复位）");
fire(el("um-logout"), "click");
check(el("login-gate").hidden === false && Session.status() === "offline",
  "退出登录：回登录页 + 状态离线");
check(store.has("sra.who") === false, "本机记录被清掉");
check(el("user-name").textContent === "未登录", "顶栏回到未登录");
check(eyeBtn().classList.has("is-shown") === false && eyeBtn().getAttribute("aria-label") === "显示密码"
  && el("login-pwd").type === "password" && el("login-pwd").value === "",
  "退出登录后：密码框清空 + 眼睛复位成睁眼",
  `type=${el("login-pwd").type} label=「${eyeBtn().getAttribute("aria-label")}」`);
fire(el("um-status-online"), "click");    // 退出后再点菜单项也不该把状态拉回在线
check(Session.status() === "offline", "退出后点状态菜单也不会「又上线」");
fire(el("ltab-guest"), "click");
check(el("login-form").hidden === true && el("guest-pane").hidden === false, "切到游客入口");
const shownGuest = el("guest-name").textContent;
check(/^游客[a-z0-9]{5}$/.test(shownGuest), "游客名格式：游客 + 5 位随机串", `「${shownGuest}」`);
fire(el("btn-guest"), "click");
await flush();
const firstGuest = Session.name();
check(/^游客[a-z0-9]{5}$/.test(firstGuest), "登录后顶栏名字就是它", `「${firstGuest}」`);
check(el("user-name").textContent === firstGuest, "顶栏渲染的是同一个名字");
check(Session.status() === "online", "游客进来同样是「在线」");
fire(el("um-logout"), "click");
fire(el("ltab-guest"), "click");
fire(el("btn-guest"), "click");
await flush();
const secondGuest = Session.name();
check(/^游客[a-z0-9]{5}$/.test(secondGuest), "再进一次还是随机名", `「${secondGuest}」`);
check(secondGuest !== firstGuest, "退出后重进：名字会变（本机不持久化游客身份）");

// ════════════════════════════════════════════════════════════════════════
step("B-01 刷新保持登录态：本机记录还在 → 直接进主界面");
// ════════════════════════════════════════════════════════════════════════
check(store.has("sra.who"), "本机记录里有当前身份");
Session._boot();                          // 模拟刷新（同一份 localStorage）
check(el("login-gate").hidden === true, "刷新后不再要求重新登录");
check(Session.name() === secondGuest, "名字还是刚才那个");
check(Session.status() === "online", "刷新后状态：在线");

// ════════════════════════════════════════════════════════════════════════
step("登录页的数字：来自当前数据源（读不到就不摆一排「—」充数）");
// ════════════════════════════════════════════════════════════════════════
check(el("fact-rows").textContent === "541,909", "交易行数按后端给的渲染", `「${el("fact-rows").textContent}」`);
check(el("fact-customers").textContent === "4,372", "客户编号数", `「${el("fact-customers").textContent}」`);
check(el("login-facts-note").textContent.includes("2010-12-01"), "数据范围也写出来");

// ════════════════════════════════════════════════════════════════════════
step("注册：真表单 → 查重 → 成功即进来（中文显示名 → 中文首字头像）");
// ════════════════════════════════════════════════════════════════════════
fire(el("um-logout"), "click");
check(el("login-gate").hidden === false, "先退出，回到登录页");
for (const id of ["reg-name", "reg-display", "reg-pwd", "reg-pwd2", "btn-register", "reg-strength"]) {
  check(html.includes(`id="${id}"`), `注册表单里有 ${id}`);
}
check(html.includes('<form class="login-form" id="register-form"')
  && html.includes('id="btn-register" type="submit"'),
  "注册是 <form> + type=submit 按钮 —— 光标在输入框里按回车就是提交（浏览器隐式提交走同一条路）");
check(html.includes('<form class="login-form" id="login-form"')
  && html.includes('id="btn-login" type="submit"'), "登录同样是 <form> + type=submit（回车能提交）");
fire(el("ltab-register"), "click");
check(el("register-form").hidden === false && el("login-form").hidden === true, "切到注册栏");

// ① 两次密码不一致 → 就地人话，且**一个请求都不发**
el("reg-name").value = "唐小宇";
el("reg-display").value = "唐小宇（销售）";
el("reg-pwd").value = "abc12345";
el("reg-pwd2").value = "abc12346";
const beforeRegister = registeredLog.length;
fire(el("register-form"), "submit");
await flush();
check(el("reg-pwd2-msg").hidden === false && el("reg-pwd2-msg").textContent.includes("不一样"),
  "两次不一致：就地人话", `「${el("reg-pwd2-msg").textContent}」`);
check(registeredLog.length === beforeRegister && el("login-gate").hidden === false,
  "不一致就不发注册请求、不放行");

// ② 密码强度提示（弱/中/强，纯前端提醒，不拦人）
fire(el("reg-pwd"), "input");
check(el("reg-strength").hidden === false && el("reg-strength-text").textContent.includes("强度"),
  "边打边给强度提示", `「${el("reg-strength-text").textContent}」`);
check(el("reg-strength").className.includes("lv-"), "强度条按等级换长度");

// ③ 改一致 → 注册成功 → **直接进来**
el("reg-pwd2").value = "abc12345";
fire(el("reg-pwd2"), "input");
fillCaptcha("register");                   // 注册也要验证码（与登录一致）
check(el("reg-pwd2-msg").hidden === true, "改一致后那条红字自己消失");
fire(el("register-form"), "submit");
await flush();
check(el("login-gate").hidden === true && Session.loggedIn() === true, "注册成功直接进系统（不用再登一次）");
check(el("user-name").textContent === "唐小宇（销售）", "顶栏显示的是**显示名**",
  `「${el("user-name").textContent}」`);
check(el("avatar").textContent === "唐", "中文显示名 → 中文首字做头像", `「${el("avatar").textContent}」`);
check(Session.name() === "唐小宇" && Session.displayName() === "唐小宇（销售）", "账号名与显示名分开记");
check(el("toast").textContent.includes("已经建好"), "注册成功后给一句人话", `「${el("toast").textContent}」`);
const storedReg = JSON.parse(store.get("sra.who"));
check(storedReg.display_name === "唐小宇（销售）" && ("password" in storedReg) === false,
  "本机记录里有显示名、没有密码");

// ④ 账号失焦查重：两种提示都要对
fire(el("um-logout"), "click");
fire(el("ltab-register"), "click");
el("reg-name").value = "唐宇";                        // 这个名字在（stub 里）已经被占
fire(el("reg-name"), "blur");
await flush();
check(el("reg-name-msg").hidden === false && el("reg-name-msg").textContent.includes("已被注册"),
  "失焦查重：已被注册", `「${el("reg-name-msg").textContent}」`);
check(el("reg-name-msg").classList.has("is-ok") === false && el("reg-name").classList.has("is-bad"),
  "是红字，不是绿字");
el("reg-name").value = "谁都没用过";
fire(el("reg-name"), "input");                        // 改过名字：上一次的结论作废
fire(el("reg-name"), "blur");
await flush();
check(el("reg-name-msg").textContent.includes("可用") && el("reg-name-msg").classList.has("is-ok"),
  "失焦查重：没人用过 → 绿色「✓ 可用」", `「${el("reg-name-msg").textContent}」`);

// ⑤ 重复注册（409）：没建出第二条同名记录
el("reg-name").value = "唐宇";
el("reg-pwd").value = "abc12345";
el("reg-pwd2").value = "abc12345";
fillCaptcha("register");
const namesBefore = Object.keys(accountsStub).filter((n) => n === "唐宇").length;
fire(el("register-form"), "submit");
await flush();
check(el("reg-name-msg").textContent.includes("已被注册"), "重复账号被拦下，话说在账号框下面",
  `「${el("reg-name-msg").textContent}」`);
check(Object.keys(accountsStub).filter((n) => n === "唐宇").length === namesBefore,
  "没有建出第二条同名记录");
check(el("login-gate").hidden === false && Session.loggedIn() === false, "重复注册不放行");

// ⑥ 首次使用引导：一个账号都没有时才亮（这里把账号数临时改成 0 验一次）
const realExists = APIStub.accountExists;
APIStub.accountExists = () => Promise.resolve({ exists: false, account_count: 0 });
Session._boot();
await flush();
check(el("login-firstrun").hidden === false, "账号数为 0 → 亮出「先注册一个」引导");
fire(el("login-firstrun"), "click");
check(el("register-form").hidden === false, "点引导 → 直接切到注册栏");
APIStub.accountExists = realExists;

// ════════════════════════════════════════════════════════════════════════
step("帮助 / 隐私：同一只抽屉，内容是真内容（数字还是从后端读的）");
// ════════════════════════════════════════════════════════════════════════
check(el("info-drawer").hidden === true, "抽屉默认是收起的");
fire(el("link-help"), "click");
await flush();
check(el("info-drawer").hidden === false && el("info-pane-help").hidden === false,
  "点「帮助」→ 打开抽屉并显示帮助那一栏");
check(el("info-pane-privacy").hidden === true, "另一栏没有同时冒出来");
check(el("info-title").textContent === "帮助", "标题跟着换", `「${el("info-title").textContent}」`);
check(el("toast").textContent.includes("尚未开通") === false, "不再是「尚未开通」那条提示");
for (const label of ["销售汇总", "销售趋势", "产品排行", "两区间比较", "国家分布", "客户分析", "商品分析"]) {
  check(html.includes(`<b>${label}</b>`), `帮助里「${label}」这一类在页面上`);
}
check(el("help-rows").textContent === "541,909", "帮助里的行数是**从后端读的**",
  `「${el("help-rows").textContent}」`);
check(el("help-source").textContent.includes("Online Retail.xlsx"), "数据源名也是读出来的",
  `「${el("help-source").textContent}」`);
check(el("help-range").textContent.includes("2010-12-01"), "覆盖范围是读出来的",
  `「${el("help-range").textContent}」`);
check(el("help-currency").textContent.includes("元"), "币种是读出来的",
  `「${el("help-currency").textContent}」`);
check(el("help-last-day").textContent === "2011-12-09", "最后一天也是读出来的");
check(el("help-unsupported").children.length === 3, "「数据里没有什么」照后端清单渲染（不手抄）",
  `${el("help-unsupported").children.length} 条`);
check(el("help-report-formats").textContent.includes("Word"), "报告导出格式也是读出来的",
  `「${el("help-report-formats").textContent}」`);

fire(el("link-privacy"), "click");
check(el("info-pane-privacy").hidden === false && el("info-pane-help").hidden === true,
  "点「隐私」→ 换成隐私那一栏");
check(el("info-title").textContent === "隐私说明", "标题跟着换", `「${el("info-title").textContent}」`);

fire(el("btn-info-close"), "click");
check(el("info-drawer").hidden === true, "点关闭 → 抽屉收起");
fire(el("link-privacy"), "click");
fire(el("info-backdrop"), "click");
check(el("info-drawer").hidden === true, "点背后那层 → 收起");
fire(el("link-help"), "click");
fire(documentStub.body, "keydown", { key: "Escape" });
check(el("info-drawer").hidden === true, "按 Esc → 收起（三条关闭路都通）");

// ════════════════════════════════════════════════════════════════════════
step("B-11 本机记录里没有密码；界面文案里没有技术字样");
// ════════════════════════════════════════════════════════════════════════
const rawSource = source;
for (const banned of ["TASK-", "/api/", "鉴权", "RBAC", "未启用", "sha256", "file_id",
                      "openapi", "数字闸门", "白名单", "setTimeout", "占位", "TODO", "mock"]) {
  check(rawSource.includes(banned) === false, `session.js 里没有「${banned}」`);
}
const storageValues = [...store.values()].join(" ");
check(/123456|password|passwd|hash|token/i.test(storageValues) === false,
  "本机存的记录里没有密码 / 令牌这类东西");

console.log(`\n共 ${total} 项检查：通过 ${total - failed}，未通过 ${failed}`);
process.exit(failed === 0 ? 0 : 1);
