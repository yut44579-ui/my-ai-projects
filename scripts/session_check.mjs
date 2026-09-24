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
  return {
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
    setAttribute(key, value) { this[key] = value; },
    getAttribute(key) { return this[key]; },
    addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
  };
}

// 页面里真实存在的 id 一律从 index.html 里读出来（不另抄一份清单，抄了就会和页面走散）
const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
const htmlIds = [...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
const elements = new Map();
for (const id of htmlIds) elements.set(id, makeElement(id));

const documentHandlers = {};
const documentStub = {
  readyState: "complete",
  body: makeElement("body"),
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
  },
};
const APIStub = { chatCapabilities: () => Promise.resolve(capsPayload) };

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
step("B-05 只做形态校验：名字太短 / 密码太短 → 标红 + 就地提示");
// ════════════════════════════════════════════════════════════════════════
el("login-name").value = "唐";
el("login-pwd").value = "123";
fire(el("login-form"), "submit");
await flush();
check(el("login-name-msg").hidden === false && el("login-name-msg").textContent.length > 0,
  "名字太短：就地提示", `「${el("login-name-msg").textContent}」`);
check(el("login-pwd-msg").hidden === false, "密码太短：就地提示", `「${el("login-pwd-msg").textContent}」`);
check(el("login-name").classList.has("is-bad"), "出错的输入框标红（is-bad）");
check(el("login-gate").hidden === false, "校验不过不许放行");
check(store.has("sra.who") === false, "校验不过不写本机记录");

// ════════════════════════════════════════════════════════════════════════
step("B-02 账号登录：填「唐宇」进主界面，顶栏显示名字");
// ════════════════════════════════════════════════════════════════════════
el("login-name").value = "唐宇";
el("login-pwd").value = "123456";
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
fire(el("um-logout"), "click");
check(el("login-gate").hidden === false && Session.status() === "offline",
  "退出登录：回登录页 + 状态离线");
check(store.has("sra.who") === false, "本机记录被清掉");
check(el("user-name").textContent === "未登录", "顶栏回到未登录");
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
