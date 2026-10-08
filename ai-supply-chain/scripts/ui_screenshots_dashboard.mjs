/**
 * 工作台首页（TASK-008）验收截图：驱动本机 Edge（headless + CDP）打开真实前端，
 * 断言页面结构真的画出来了，并把浏览器**真实发出的** /api/dashboard/summary 响应体存下来当证据。
 *
 * ★ 零新增依赖：复用 scripts/lib/cdp.mjs（与 TASK-001~007 截图脚本同一套 CDP 客户端）。
 * ★ 只读：不写入任何业务数据，不点任何会改库的按钮。
 * ★ 会先做 DOM 断言再截图 —— 断言不过就抛错，避免"截到一张白屏还说成功了"。
 *
 * 用法（后端 8000、前端 5173 都已启动）：
 *   node scripts/ui_screenshots_dashboard.mjs
 *   APP_URL=http://127.0.0.1:5174/ CDP_PORT=9400 node scripts/ui_screenshots_dashboard.mjs
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launchEdge, sleep } from './lib/cdp.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SHOT_DIR = join(ROOT, 'docs', 'screenshots')
const EVIDENCE_DIR = join(ROOT, 'docs', 'evidence')
const RUN_ID = `${process.pid}-${Date.now()}`
const TMP_PROFILE = join(ROOT, '.tmp-edge', `dashboard-${RUN_ID}`)
const APP_URL = process.env.APP_URL ?? 'http://127.0.0.1:5173/'
const DEBUG_PORT = Number(process.env.CDP_PORT ?? 0) || 9600 + (process.pid % 200)

/** 页面结构断言：每一项都必须为真，否则视为没画出来（对齐复刻验收表） */
const CHECKS = [
  // ① 结构：深色侧栏 + 顶部搜索 + 三栏
  ['侧栏品牌名', `document.body.innerText.includes('AI 商业项目助理')`],
  ['侧栏副标题', `document.body.innerText.includes('让商业更简单，让增长更高效')`],
  ['侧栏分组「数据中心」', `document.body.innerText.includes('数据中心')`],
  ['侧栏分组「AI 能力」', `document.body.innerText.includes('AI 能力')`],
  ['侧栏分组「输出中心」', `document.body.innerText.includes('输出中心')`],
  ['侧栏分组「系统管理」', `document.body.innerText.includes('系统管理')`],
  ['顶部全局搜索框', `!!document.querySelector('input[placeholder*="搜索客户"]')`],
  ['侧栏是深色', `(() => { const el = document.querySelector('.ant-layout-sider'); if(!el) return false; const bg = getComputedStyle(el).backgroundColor; const m = bg.match(/\\d+/g); return m && (Number(m[0])+Number(m[1])+Number(m[2])) < 200 })()`],

  // ② 欢迎区 + 今日待处理
  // ★ 不再断言具体人名（原先写死"张三"）：TASK-019 后问候语读**真实登录者**，
  //   写死人名会让断言在换账号时误报。改为断言"问候语结构正确 + 不是未登录占位"。
  ['问候语已渲染', `/(早上好|上午好|中午好|下午好|晚上好|凌晨好|你好)/.test(document.body.innerText)`],
  ['问候语不是未登录占位', `!document.body.innerText.includes('问候语占位')`],
  ['今日待处理', `document.body.innerText.includes('今日待处理')`],

  // ③ KPI 五卡
  ['KPI 客户总数', `document.body.innerText.includes('客户总数')`],
  ['KPI 新增客户', `document.body.innerText.includes('新增客户')`],
  ['KPI 商机数量', `document.body.innerText.includes('商机数量')`],
  ['KPI 成交订单', `document.body.innerText.includes('成交订单')`],
  ['KPI 风险客户', `document.body.innerText.includes('风险客户')`],

  // ④ 今日重点任务
  ['今日重点任务卡', `document.body.innerText.includes('今日重点任务')`],
  ['AI 智能排序标签', `document.body.innerText.includes('AI 智能排序')`],
  ['任务筛选「待跟进」', `document.body.innerText.includes('待跟进')`],

  // ⑤ 中央右侧图表
  ['客户分布卡', `document.body.innerText.includes('客户分布')`],
  ['客户增长趋势卡', `document.body.innerText.includes('客户增长趋势')`],
  ['客户来源卡', `document.body.innerText.includes('客户来源')`],

  // ⑥ 中央底部
  ['最近客户动态卡', `document.body.innerText.includes('最近客户动态')`],
  ['动态筛选「商机动态」', `document.body.innerText.includes('商机动态')`],
  ['数据连接状态卡', `document.body.innerText.includes('数据连接状态')`],
  ['未连接渠道如实标注', `document.body.innerText.includes('未连接')`],

  // ⑦ 右栏
  ['AI 助手面板', `document.body.innerText.includes('AI 助手')`],
  ['AI 助手企业版徽标', `document.body.innerText.includes('企业版')`],
  ['AI 助手输入框', `!!document.querySelector('input[placeholder*="尽管问我"]')`],
  ['快捷入口卡', `document.body.innerText.includes('快捷入口')`],
  ['快捷入口四格', `document.body.innerText.includes('连接数据源') && document.body.innerText.includes('导入文件') && document.body.innerText.includes('生成报告') && document.body.innerText.includes('创建任务')`],
  ['今日汇报卡', `document.body.innerText.includes('今日汇报')`],

  // ⑧ 反假数据 / 反退化断言
  ['不含技术栈泄漏', `!/FastAPI|SQLAlchemy|localhost|127\\.0\\.0\\.1:\\d/i.test(document.body.innerText)`],
  ['无 [object Object]', `!document.body.innerText.includes('[object Object]')`],
  ['无 undefined', `!document.body.innerText.includes('undefined')`],
  ['无 NaN', `!document.body.innerText.includes('NaN')`],
]

async function main() {
  mkdirSync(SHOT_DIR, { recursive: true })
  mkdirSync(EVIDENCE_DIR, { recursive: true })

  const watchdog = setTimeout(() => {
    console.error('看门狗超时（240s），强制退出')
    process.exit(2)
  }, 240_000)
  watchdog.unref?.()

  const { cdp, close } = await launchEdge({
    profileDir: TMP_PROFILE,
    port: DEBUG_PORT,
    windowSize: '1600,1100',
  })

  try {
    const captured = []
    await cdp.send('Network.enable')
    await cdp.send('Page.enable')
    cdp.on('Network.responseReceived', async (params) => {
      const url = params.response.url
      if (!url.includes('/api/')) return
      try {
        const { body, base64Encoded } = await cdp.send('Network.getResponseBody', {
          requestId: params.requestId,
        })
        captured.push({
          url,
          status: params.response.status,
          body: base64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body,
        })
      } catch {
        /* 取不到响应体的忽略 */
      }
    })

    console.log(`① 打开工作台首页 ${APP_URL}`)
    // ★ TASK-019 起业务页面需要登录：先注入令牌，否则会被守卫送到登录页
    const testToken = process.env.DSH_TEST_TOKEN
    if (testToken) {
      await cdp.send('Page.navigate', { url: new URL(APP_URL).origin + '/login' })
      await cdp.waitFor(`document.readyState === 'complete'`, { label: '登录页加载', timeout: 20_000 })
      // ★ 令牌之外还要写用户信息：顶栏与问候语读 auth_user 缓存
      let userJs = "'{}'"
      try {
        const api = process.env.DSH_API_BASE ?? 'http://127.0.0.1:8000'
        const r = await fetch(`${api}/api/auth/me`, {
          headers: { Authorization: `Bearer ${testToken}` },
        })
        if (r.ok) userJs = JSON.stringify(JSON.stringify(await r.json()))
      } catch {
        /* 拿不到就只注入令牌 */
      }
      await cdp.eval(
        `localStorage.setItem('auth_token', ${JSON.stringify(testToken)});` +
          `localStorage.setItem('auth_user', ${userJs}); true`,
      )
      console.log('   （已注入登录令牌与用户信息）')
    }
    await cdp.send('Page.navigate', { url: APP_URL })
    // 等「今日重点任务」出现（= 前端真的拿到后端数据并渲染完）
    await cdp.waitFor(`document.body.innerText.includes('今日重点任务')`, {
      label: '工作台首屏',
      timeout: 30_000,
    })
    await sleep(1500)

    console.log('② 逐项断言页面结构')
    const failures = []
    for (const [label, expr] of CHECKS) {
      const ok = await cdp.eval(`!!(${expr})`)
      console.log(`   ${ok ? '✅' : '❌'} ${label}`)
      if (!ok) failures.push(label)
    }

    await cdp.shot('task008-dashboard', SHOT_DIR)

    // ⑨ AI 助手真问答：真输入 + 真回车 + 断言事实区出现（证明不是静态摆设）
    console.log('③ AI 助手真问答…')
    await cdp.eval(`(() => {
      const el = document.querySelector('input[placeholder*="尽管问我"]')
      if (!el) return false
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(el, '今天有哪些客户需要我处理？')
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })()`)
    await sleep(400)
    await cdp.eval(`(() => {
      const el = document.querySelector('input[placeholder*="尽管问我"]')
      if (!el) return false
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
      el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
      return true
    })()`)
    await cdp.waitFor(`document.body.innerText.includes('事实（来自后端计算，可核对）')`, {
      label: 'AI 问答事实区',
      timeout: 30_000,
    })
    const aiOk = await cdp.eval(`document.body.innerText.includes('事实（来自后端计算，可核对）')`)
    const aiBasis = await cdp.eval(`document.body.innerText.includes('依据：')`)
    console.log(`   ${aiOk ? '✅' : '❌'} AI 回答含事实区`)
    console.log(`   ${aiBasis ? '✅' : '❌'} AI 回答标注依据`)
    if (!aiOk) failures.push('AI 助手未返回事实区')
    await cdp.shot('task008-dashboard-ai', SHOT_DIR)

    // 额外截一张**整页**图：首屏放不下图表区，需要证明它们真的渲染了（不是只存在于 DOM）
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1600,
      height: 2600,
      deviceScaleFactor: 1,
      mobile: false,
    })
    await sleep(800)
    const full = await cdp.send('Page.captureScreenshot', {
      format: 'png',
      captureBeyondViewport: true,
    })
    writeFileSync(join(SHOT_DIR, 'task008-dashboard-full.png'), Buffer.from(full.data, 'base64'))
    console.log(`  整页截图 → ${join(SHOT_DIR, 'task008-dashboard-full.png')}`)
    await cdp.send('Emulation.clearDeviceMetricsOverride')

    const evidenceFile = join(EVIDENCE_DIR, 'task008-api-calls.json')
    writeFileSync(evidenceFile, JSON.stringify(captured, null, 2), 'utf8')
    console.log(`  API 响应证据 → ${evidenceFile}（${captured.length} 条）`)

    const summary = captured.find((c) => c.url.includes('/api/dashboard/summary'))
    if (!summary) {
      failures.push('没有捕获到 /api/dashboard/summary 响应')
    } else {
      console.log(`\n③ /api/dashboard/summary 真实响应（HTTP ${summary.status}）`)
      console.log(summary.body.slice(0, 900))
    }

    if (failures.length) {
      console.error(`\n❌ 首页验收有 ${failures.length} 项未通过：`)
      failures.forEach((f) => console.error(`   · ${f}`))
      process.exitCode = 1
      return
    }
    console.log('\n🎉 工作台首页结构验收全部通过')
  } finally {
    clearTimeout(watchdog)
    close()
  }
}

main().catch((err) => {
  console.error('脚本失败：', err)
  process.exit(1)
})
